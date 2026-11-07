"""
title: ECHO Agent Orchestration
author: ECHO Framework
version: 5.233.0.1
description: Composant système interne : ECHO Agent Orchestration.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.233.0.1: Contrainte de sortie JSON via response_schema (SkillRouterOutput et SupervisorCriticOutput), décoration @echo_tool et simplification des retours.
# 5.231.43.1: Ajout du paramètre multiplier (1..5) dans consult_council, renommage COUNCIL_MAX_SKILLS=7, COUNCIL_ROUNDS_MIN=3, COUNCIL_ROUNDS_MAX=10, COUNCIL_MAX_PR_COMPLEXITY=60, validation arithmétique PAS et droit d'abstention.
# 5.231.20.1: Parallélisation asyncio.gather des ouvriers dans consult_supervised_workers, correction du parsing de task_id (sup_*) dans list/close_supervised_task, verrou par council_id et sécurisation async des skills.
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides) et purge des directives IMPLIQUE.
# 5.226.18.0: Alignement sur le versioning global ECHO.

import sys
import re
import orjson as json
import asyncio
import uuid
import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal, Tuple

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool, wrap_tool_output
from echo_protocol import SkillRouterOutput, SupervisorCriticOutput
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_constants import (
    ECHO_API_KEY_RETRIES, ECHO_API_MAX_RETRIES, get_generation_config
)
from echo_skills import get_all_skills, get_skill_content, save_skill, parse_skill_metadata, delete_skill
from echo_paths import sanitize_echo_id, get_user_skill_lock
from echo_ui import EchoUI
from echo_prompts import (
    SYS_ORCHESTRATOR_SKILL_ROUTER,
    SYS_ORCHESTRATOR_COUNCIL_EXPERT,
    SYS_ORCHESTRATOR_COUNCIL_SYNTHESIS,
    SYS_ORCHESTRATOR_CRITIC,
    SYS_ORCHESTRATOR_CONSOLIDATOR
)

_COUNCIL_LOCKS: dict[str, asyncio.Lock] = {}


def _get_council_lock(user_id: str, chat_id: str, council_id: str) -> asyncio.Lock:
    key = f"{sanitize_echo_id(user_id)}:{sanitize_echo_id(chat_id)}:{sanitize_echo_id(council_id)}"
    if key not in _COUNCIL_LOCKS:
        _COUNCIL_LOCKS[key] = asyncio.Lock()
    return _COUNCIL_LOCKS[key]

class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_RETRIES, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        COGNITIVE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour la délégation cognitive.")
        DEBUG_COUNCIL: bool = Field(default=False, description="Si activé, conserve les traces des réflexions internes et affiche plus de détails.")

    class UserValves(BaseModel):
        COUNCIL_ROUNDS_MIN: int = Field(default=3, description="Nombre de tours de parole minimum et par défaut pour un conseil d'experts.")
        COUNCIL_ROUNDS_MAX: int = Field(default=10, description="Limite haute du nombre de tours de parole.")
        COUNCIL_MAX_SKILLS: int = Field(default=7, description="Nombre maximum d'expertises (skills) distinctes au conseil (hors synthétiseur).")
        COUNCIL_EXPERT_MAX_CALLS_PER_ROUND: int = Field(default=3, ge=0, le=10, description="Budget max d'appels d'outils par expert et par tour de conseil.")
        COUNCIL_MAX_PR_COMPLEXITY: int = Field(default=60, description="Complexité maximale (participants * rounds) d'un conseil.")
        SUPERVISOR_MAX_CORRECTION_ROUNDS: int = Field(default=3, ge=1, le=5, description="Nombre maximum de tours de correction du superviseur.")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # ==========================================================================
    # 1. GESTION DES SKILLS
    # ==========================================================================

    @echo_tool
    async def forge_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        instructions: str,
        require_web_grounding: bool = False,
        __user__: Optional[dict] = None,
        __metadata__: dict = {}
    ) -> str:
        """Crée ou met à jour une expertise spécialisée (Skill) pour les sous-agents."""
        if require_web_grounding:
            grounding_directive = (
                "\n\n<directive_obligatoire>\n"
                "Le Modèle DOIT systématiquement utiliser ses outils de recherche Web pour extraire, vérifier "
                "et intégrer l'état de l'art le plus récent, ainsi que les méthodologies les plus validées dans "
                "ce domaine d'expertise, avant de formuler toute réponse ou d'exécuter une action.\n"
                "</directive_obligatoire>"
            )
            instructions += grounding_directive

        user_id = __user__.get("id", "system") if __user__ else "system"
        try:
            async with get_user_skill_lock(user_id):
                success = await asyncio.to_thread(save_skill, user_id, skill_id, name, description, instructions)
        except ValueError as e:
            return f"❌ {e}"

        if success:
            return f"✅ Skill '{name}' ({skill_id}) forgé avec succès."
        return f"❌ Échec de la forge du skill '{skill_id}'."

    @echo_tool
    async def list_skills(
        self,
        __user__: Optional[dict] = None,
        __metadata__: dict = {}
    ) -> str:
        """Liste les expertises (Skills) disponibles dans le catalogue utilisateur."""
        user_id = __user__.get("id", "system") if __user__ else "system"
        skills = await asyncio.to_thread(get_all_skills, user_id)

        if not skills:
            return "ℹ️ Aucune expertise (Skill) n'est actuellement forgée."

        res = "### EXPERTISES DISPONIBLES\n"
        for s in skills:
            res += f"- **ID:** `{s['id']}` | **Nom:** {s['name']}\n"
            res += f"  > *Description:* {s['description']}\n"

        return res

    @echo_tool
    async def read_skill(
        self,
        skill_id: str,
        __user__: Optional[dict] = None,
        __metadata__: dict = {}
    ) -> str:
        """Lit le contenu brut et les directives d'une expertise (Skill) existante."""
        user_id = __user__.get("id", "system") if __user__ else "system"

        content = await asyncio.to_thread(get_skill_content, user_id, skill_id)

        if not content:
            return f"❌ Le skill '{skill_id}' n'existe pas."

        return f"### CONTENU DU SKILL `{skill_id}`\n\n```markdown\n{content}\n```"

    @echo_tool
    async def search_skill(
        self,
        need_description: str,
        __user__: Optional[dict] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Identifie jusqu'à 3 expertises (Skills) pertinentes pour un besoin spécifique."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"

        skills = get_all_skills(user_id)
        if not skills:
            return "ℹ️ Aucune expertise (Skill) n'est actuellement forgée."

        catalog = [{"id": s["id"], "name": s.get("name", ""), "description": s.get("description", "")} for s in skills]
        catalog_json = json.dumps(catalog).decode('utf-8') if isinstance(json.dumps(catalog), bytes) else json.dumps(catalog)

        system_prompt = SYS_ORCHESTRATOR_SKILL_ROUTER.format(catalog_json=catalog_json)

        await events.status("🔍 Recherche sémantique de l'expertise requise...")

        res, _, _ = await EchoGeminiClient.call_cascade(
            target_model_key="MODEL_APOLLO",
            payload={
                "contents": [{"role": "user", "parts": [{"text": f"Besoin : {need_description}"}]}],
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium"),
            },
            user_id=user_id,
            metadata=(__metadata__ or {}),
            events=events,
            timeout=30,
            include_thoughts=False,
            response_schema=SkillRouterOutput,
        )

        if not res:
            return "❌ Échec de la délégation de recherche sémantique."

        candidates = res.get("candidates", [])
        raw_response = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []) if "text" in p and not p.get("thought")) if candidates else "{}"

        try:
            parsed = SkillRouterOutput.model_validate_json(raw_response).model_dump()
            best_matches = parsed.get("best_matches", [])
        except Exception:
            best_matches = []

        if not best_matches:
            return wrap_tool_output(
                text="ℹ️ Aucun skill correspondant trouvé pour ce besoin.",
                status={"status": "warning"}
            )

        matched_catalog = [s for s in catalog if s["id"] in best_matches]

        md_res = "### SKILLS CORRESPONDANTS\n"
        for s in matched_catalog:
            md_res += f"- **ID:** `{s['id']}` | **Nom:** {s['name']}\n  > *Description:* {s['description']}\n"

        return md_res

    @echo_tool
    async def delete_user_skill(
        self,
        skill_id: str,
        timeout_seconds: int = 300,
        __user__: dict = {},
        __event_emitter__: callable = None,
        __event_call__: callable = None,
        __metadata__: dict = {}
    ) -> str:
        """Supprime une expertise (Skill) après confirmation interactive de l'utilisateur."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"

        content = await asyncio.to_thread(get_skill_content, user_id, skill_id)
        if not content:
            return f"❌ Le skill '{skill_id}' n'existe pas."

        msg_html = f'''
        <div style="margin-bottom:15px; font-size:15px; font-weight:600;">
            ⚠️ Confirmez-vous la suppression définitive de l'expertise <b>{skill_id}</b> ?
        </div>
        <pre style="
            background: rgba(0,0,0,0.1);
            padding: 10px;
            border-radius: 5px;
            white-space: pre-wrap;
            word-break: break-word;
            max-height: 40vh; /* Ascenseur interne */
            overflow-y: auto;
            font-family: monospace;
            font-size: 12px;
            border: 1px solid rgba(128,128,128,0.2);
        ">{content}</pre>
        '''

        # orjson.dumps retourne des bytes, il faut décoder en utf-8 pour l'injection JS
        msg_escaped = json.dumps(msg_html).decode('utf-8')
        modals_injection = EchoUI.get_custom_modals_js()

        js_code = f"""
        {modals_injection}
        return await new Promise((resolve) => {{
            window.echoCustomConfirm({msg_escaped}, {timeout_seconds}, (result) => resolve(result));
        }});
        """

        if __event_emitter__:
            await events.status(f"Validation requise pour supprimer le skill {skill_id}...", done=False)

        from echo_constants import ECHO_SUBAGENT_CONTEXT
        if ECHO_SUBAGENT_CONTEXT.get().get("is_subagent"):
            return "Erreur : L'environnement d'exécution (Headless/Sous-agent) ne permet pas de requérir une confirmation de suppression."

        user_confirmed = await events.call_execute(js_code)

        if user_confirmed:
            async with get_user_skill_lock(user_id):
                success = await asyncio.to_thread(delete_skill, user_id, skill_id)
            if success:
                if __event_emitter__:
                    await events.status(f"Skill {skill_id} supprimé avec succès.", done=True)
                return f"Le skill '{skill_id}' a été supprimé."
            else:
                if __event_emitter__:
                    await events.status(f"Erreur système lors de la suppression de {skill_id}.", done=True)
                return f"❌ Impossible de supprimer le skill '{skill_id}'."
        else:
            if __event_emitter__:
                await events.status("Suppression non confirmée par l'utilisateur.", done=True)
            return wrap_tool_output(text="L'utilisateur a refusé ou délai expiré.", status={"status": "cancelled"})

    # ==========================================================================
    # 2. CONSEIL D'EXPERTS (Protocole Delphi via delegate_to_agent)
    # ==========================================================================

    @echo_tool
    async def consult_council(
        self,
        question: str,
        participants: List[str],
        council_id: str,
        target_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS"] = "MODEL_PROMETHEUS",
        synthesis_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS"] = "MODEL_PROMETHEUS",
        rounds: Optional[int] = None,
        multiplier: int = 1,
        close_on_finish: bool = False,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Convoque un conseil d'experts en parallèle (méthode Delphi, avec facteur multiplicateur 1 à 5 par skill_id) et synthétise leurs délibérations."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return "❌ Erreur: Aucun chat_id détecté."

        # ── Phase 0 : Validation & Chargement ──
        if multiplier is None:
            multiplier = 1
        if isinstance(multiplier, bool) or not isinstance(multiplier, int) or multiplier < 1 or multiplier > 5:
            return (
                f"❌ Erreur : La valeur du paramètre `multiplier` ({multiplier}) est hors limites.\n"
                f"Le Modèle DOIT fournir un entier strictement compris entre 1 et 5."
            )

        skill_ids = list(dict.fromkeys(s.strip() for s in participants if s.strip()))
        max_skills = self.user_valves.COUNCIL_MAX_SKILLS
        max_participants = max_skills * multiplier
        total_participants = len(skill_ids) * multiplier

        if len(skill_ids) > max_skills:
            return (
                f"❌ Erreur : Le nombre d'expertises distinctes ({len(skill_ids)}) dépasse la limite autorisée ({max_skills}, "
                f"soit {max_participants} participants maximum avec `multiplier={multiplier}`).\n"
                f"Le Modèle DOIT soumettre au maximum {max_skills} `skill_id` dans `participants`."
            )

        if total_participants < 2:
            available = await asyncio.to_thread(get_all_skills, user_id)
            skill_list = ", ".join(f"`{s['id']}`" for s in available) if available else "aucun"
            return (
                f"❌ Erreur : Le nombre total de participants ({len(skill_ids)} × {multiplier} = {total_participants}) "
                f"est inférieur au minimum requis (2).\n\n"
                f"**Skills disponibles :** {skill_list}\n\n"
                f"Le Modèle DOIT soumettre au moins 2 `skill_id` ou définir `multiplier` >= 2 "
                f"(et appeler `forge_skill` au préalable si une expertise est manquante)."
            )

        min_rounds = self.user_valves.COUNCIL_ROUNDS_MIN
        max_rounds = self.user_valves.COUNCIL_ROUNDS_MAX
        effective_rounds = max(
            min_rounds,
            min(
                rounds if rounds is not None else min_rounds,
                max_rounds,
            ),
        )

        max_complexity = self.user_valves.COUNCIL_MAX_PR_COMPLEXITY
        complexity = total_participants * effective_rounds
        if complexity > max_complexity:
            max_r_adm = min(max_rounds, max_complexity // total_participants)
            max_m_adm = min(5, max_complexity // (len(skill_ids) * effective_rounds))
            max_s_adm = min(max_skills, max_complexity // (multiplier * effective_rounds))

            opt_r = (
                f"`rounds` <= {max_r_adm} (avec {len(skill_ids)} skills et `multiplier={multiplier}`)"
                if max_r_adm >= min_rounds
                else (
                    f"Réduction de `rounds` seule insuffisante (minimum {min_rounds} tours, "
                    f"soit {total_participants} × {min_rounds} = {total_participants * min_rounds} > {max_complexity})"
                )
            )
            opt_m = (
                f"`multiplier` <= {max_m_adm} (avec {len(skill_ids)} skills et `rounds={effective_rounds}`)"
                if max_m_adm >= 1 and (len(skill_ids) * max_m_adm) >= 2
                else "Réduction de `multiplier` seule insuffisante"
            )
            opt_s = (
                f"`participants` <= {max_s_adm} skills (avec `multiplier={multiplier}` et `rounds={effective_rounds}`)"
                if max_s_adm >= 1 and (max_s_adm * multiplier) >= 2
                else "Réduction de `participants` seule insuffisante"
            )

            return (
                f"❌ Erreur : La combinaison des paramètres soumis dépasse la capacité maximale du conseil.\n"
                f"- Limite arithmétique : (nb_skills × multiplier) × rounds <= {max_complexity}\n"
                f"- Calcul courant : ({len(skill_ids)} × {multiplier}) × {effective_rounds} = {complexity} > {max_complexity}\n"
                f"- Bornes des paramètres : nb_skills in [1..{max_skills}], multiplier in [1..5], rounds in [{min_rounds}..{max_rounds}].\n\n"
                f"Le Modèle DOIT ajuster ses paramètres en respectant l'un des plafonds suivants :\n"
                f"- Option A (`rounds`) : {opt_r}.\n"
                f"- Option B (`multiplier`) : {opt_m}.\n"
                f"- Option C (`participants`) : {opt_s}."
            )

        # Chargement et validation de chaque skill, puis démultiplication par multiplier
        roster = []
        participant_idx = 1
        for sid in skill_ids:
            content = await asyncio.to_thread(get_skill_content, user_id, sid)
            if not content:
                return f"❌ Skill '{sid}' introuvable. Le Modèle DOIT utiliser l'outil forge_skill au préalable."
            meta = parse_skill_metadata(content)
            base_name = meta.get("name", sid)
            for m_idx in range(1, multiplier + 1):
                inst_id = f"{sid}_{m_idx}" if multiplier > 1 else sid
                inst_name = f"{base_name} #{m_idx}" if multiplier > 1 else base_name
                roster.append({
                    "instance_id": inst_id,
                    "skill_id": sid,
                    "name": inst_name,
                    "instructions": content,
                    "alias": f"Participant {participant_idx}",
                })
                participant_idx += 1

        # Résolution et normalisation du council_id (évite toute collision avec le séparateur '_')
        cid = sanitize_echo_id(council_id, fallback="council").replace("_", "-")
        max_calls_per_round = self.user_valves.COUNCIL_EXPERT_MAX_CALLS_PER_ROUND

        await events.status(
            f"🏛️ Conseil [{cid}] convoqué : {len(roster)} experts, {effective_rounds} tours, "
            f"{max_calls_per_round} outils/tour..."
        )

        # ── Phase 1 : Résolution du agent_engine_tool ──
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls:
            return "❌ Module agent_engine_tool introuvable dans sys.modules."
        delegate = _delegate_cls()
        # Propager les valves infra du parent
        delegate.valves.KEY_SWITCH_THRESHOLD = self.valves.KEY_SWITCH_THRESHOLD
        delegate.valves.MAX_RETRIES = self.valves.MAX_RETRIES

        # ── Phase 2 : Boucle des tours (protocole Delphi via delegate_to_agent, verrouillé par council_id) ──
        all_rounds: List[Dict[str, str]] = []
        council_sids: List[str] = []  # Pour la purge finale

        async with _get_council_lock(user_id, chat_id, cid):
            for round_num in range(1, effective_rounds + 1):
                await events.status(
                    f"🏛️ Conseil [{cid}] — Tour {round_num}/{effective_rounds}..."
                )

                # Construction du prompt par participant
                if round_num == 1:
                    base_prompt = f"### QUESTION SOUMISE AU CONSEIL\n{question}"
                    round_prompts = {p["instance_id"]: base_prompt for p in roster}
                else:
                    prev = all_rounds[-1]
                    round_prompts = {}
                    for p in roster:
                        inst_id = p["instance_id"]
                        others = "\n\n".join(
                            f"**{r['alias']}** ({r['name']}) :\n{prev[r['instance_id']]}"
                            for r in roster
                            if r["instance_id"] != inst_id and r["instance_id"] in prev
                        )
                        round_prompts[inst_id] = (
                            f"### CONTRIBUTIONS DU TOUR {round_num - 1}\n{others}\n\n"
                            f"### MISSION DU MODÈLE\n"
                            f"Le Modèle est {p['alias']} ({p['name']}). Il DOIT prendre connaissance des contributions ci-dessus "
                            f"pour exprimer son analyse, ses accords, ses désaccords et ses compléments. "
                            f"Le Modèle peut signifier qu'il préfère ne pas répondre."
                        )

                # Appels parallèles — chaque participant est un agent indépendant
                async def _call_participant(participant: dict, prompt: str) -> Tuple[str, str]:
                    """Appelle un participant via delegate_to_agent et retourne (instance_id, texte)."""
                    sid = participant["skill_id"]
                    inst_id = participant["instance_id"]
                    agent_sid = f"thread_council_{cid}_{inst_id}"
                    if agent_sid not in council_sids:
                        council_sids.append(agent_sid)

                    # Prompt système enrichi avec le contexte du conseil
                    members = "\n".join(f"- {p['alias']} : {p['name']}" for p in roster)
                    current_time = datetime.datetime.now().isoformat()
                    council_system = SYS_ORCHESTRATOR_COUNCIL_EXPERT.format(
                        participant_alias=f"{participant['alias']} ({participant['name']})",
                        roster_length=len(roster),
                        members=members,
                        round_num=round_num,
                        effective_rounds=effective_rounds,
                        max_calls_per_round=max_calls_per_round,
                        current_time=current_time
                    )

                    result = await delegate.delegate_to_agent(
                        task=prompt,
                        system_prompt=council_system,
                        skill_id=sid,
                        sub_sid=agent_sid,
                        target_model_key=target_model,
                        max_calls_override=max_calls_per_round,
                        __user__=__user__,
                        __chat_id__=chat_id,
                        __metadata__=__metadata__,
                        __event_emitter__=__event_emitter__,
                        __event_call__=__event_call__,
                    )

                    # Extraire le texte de la réponse
                    if isinstance(result, str):
                        try:
                            parsed = json.loads(result)
                            text = parsed.get("text", result)
                        except Exception:
                            text = result
                    elif isinstance(result, dict):
                        text = result.get("text", str(result))
                    else:
                        text = str(result)

                    return inst_id, text

                tasks = [
                    _call_participant(p, round_prompts[p["instance_id"]])
                    for p in roster
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Collecte — un participant qui échoue ne bloque pas le conseil
                round_responses: Dict[str, str] = {}
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        inst_id = roster[i]["instance_id"]
                        alias = roster[i]["alias"]
                        round_responses[inst_id] = f"⚠️ {alias} n'a pas pu répondre."
                        if self.valves.DEBUG_COUNCIL:
                            print(f"[Council] {alias} error: {result}")
                    else:
                        inst_id, text = result
                        round_responses[inst_id] = text or "(Pas de réponse)"

                all_rounds.append(round_responses)

            # ── Phase 3 : Synthèse finale ──
            await events.status("📝 Synthèse du conseil en cours...")

            transcript = f"### QUESTION\n{question}\n\n"
            for rnd, responses in enumerate(all_rounds, 1):
                transcript += f"---\n### TOUR {rnd}\n\n"
                for p in roster:
                    inst_id = p["instance_id"]
                    if inst_id in responses:
                        transcript += f"**{p['alias']}** ({p['name']}) :\n{responses[inst_id]}\n\n"

            synthesis_system = SYS_ORCHESTRATOR_COUNCIL_SYNTHESIS

            synthesis_payload = {
                "contents": [{"role": "user", "parts": [{"text": transcript}]}],
                "systemInstruction": {"parts": [{"text": synthesis_system}]},
                "generationConfig": {**get_generation_config(synthesis_model, override_thinking="high"), "maxOutputTokens": 16000}
            }

            res, _, _ = await EchoGeminiClient.call_cascade(
                target_model_key=synthesis_model,
                payload=synthesis_payload,
                user_id=user_id,
                metadata=(__metadata__ or {}),
                events=events,
                timeout=self.valves.COGNITIVE_TIMEOUT,
                include_thoughts=False,
            )

            if not res:
                synthesis_text = "❌ Cascade épuisée : aucun modèle disponible pour la synthèse."
            else:
                candidates = res.get("candidates", [])
                synthesis_text = "".join(
                    p.get("text", "") for p in candidates[0]["content"]["parts"]
                ) if candidates else "❌ Erreur : synthèse vide."

            # ── Phase 4 : Clôture ──
            if close_on_finish:
                state = EchoStateManager(user_id=user_id, chat_id=chat_id)
                for agent_sid in council_sids:
                    state.delete_thread(agent_sid)

        roster_summary = ", ".join(f"{p['alias']} ({p['name']})" for p in roster)
        await events.status("🏛️ Conseil terminé.", done=True)
        return (
            f"### SYNTHÈSE DU CONSEIL [{cid}] ({len(roster)} experts, {effective_rounds} tours)\n"
            f"**Participants** : {roster_summary}\n\n"
            f"{synthesis_text}"
        )

    # ==========================================================================
    # 3. SUPERVISEUR (Boucle critique / correction récursive)
    # ==========================================================================

    @echo_tool
    async def consult_supervised_workers(
        self,
        objective: str,
        workers: dict,
        target_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS"] = "MODEL_APOLLO",
        critic_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS"] = "MODEL_PROMETHEUS",
        max_correction_rounds: Optional[int] = None,
        close_on_finish: bool = False,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Délègue des sous-tâches à des workers supervisés par un critique itératif avant consolidation."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return "❌ Erreur: Aucun chat_id détecté."

        # Parsing du dict workers
        try:
            workers_dict = json.loads(workers) if isinstance(workers, str) else workers
        except Exception as e:
            return f"❌ Format JSON invalide pour workers : {e}"

        if not workers_dict or not isinstance(workers_dict, dict):
            return "❌ workers doit être un dict JSON non vide."

        task_id = f"sup_{uuid.uuid4().hex[:8]}"
        max_rounds = max_correction_rounds or self.user_valves.SUPERVISOR_MAX_CORRECTION_ROUNDS

        await events.status(
            f"📋 Superviseur [{task_id}] : {len(workers_dict)} workers, max {max_rounds} corrections..."
        )

        # Résolution du agent_engine_tool
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls:
            return "❌ Module agent_engine_tool introuvable dans sys.modules."
        delegate = _delegate_cls()
        delegate.valves.KEY_SWITCH_THRESHOLD = self.valves.KEY_SWITCH_THRESHOLD
        delegate.valves.MAX_RETRIES = self.valves.MAX_RETRIES

        worker_sids = {}  # worker_id → sub_sid
        deliverables = {}  # worker_id → texte du livrable

        # ── Phase 1 : Délégation initiale (parallélisée via asyncio.gather) ──
        await events.status(f"📋 [{task_id}] Phase 1 — Délégation parallèle ({len(workers_dict)} workers)...")

        def _extract_worker_deliverable(result: Any) -> str:
            budget_exhausted = False
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    text_val = parsed.get("text", result)
                    if parsed.get("status", {}).get("warning") == "budget_exhausted":
                        budget_exhausted = True
                except Exception:
                    text_val = result
            elif isinstance(result, dict):
                text_val = result.get("text", str(result))
                if result.get("status", {}).get("warning") == "budget_exhausted":
                    budget_exhausted = True
            else:
                text_val = str(result)
            if budget_exhausted:
                text_val += "\n\n[⚠️ AVERTISSEMENT SYSTÈME : Ce worker a épuisé son budget d'appels d'outils et a été interrompu. Si la direction de son travail est bonne et que la tâche justifie d'être poursuivie, le Superviseur DOIT retourner un statut `needs_correction` avec des directives pour la suite. La relance lui octroiera automatiquement un nouveau budget.]"
            return text_val

        async def _run_initial_worker(w_id: str, w_config: dict) -> Tuple[str, str]:
            w_task = w_config.get("task", "")
            w_role = w_config.get("skill_id")
            w_sys = w_config.get("system_prompt", f"Le Modèle est un agent chargé de : {w_task}")
            agent_sid = f"thread_supervisor_{task_id}_{w_id}"
            worker_sids[w_id] = agent_sid
            res = await delegate.delegate_to_agent(
                task=f"### OBJECTIF GLOBAL\n{objective}\n\n### TA TÂCHE\n{w_task}",
                system_prompt=w_sys,
                skill_id=w_role,
                sub_sid=agent_sid,
                target_model_key=target_model,
                __user__=__user__,
                __chat_id__=chat_id,
                __metadata__=__metadata__,
                __event_emitter__=__event_emitter__,
                __event_call__=__event_call__,
            )
            return w_id, _extract_worker_deliverable(res)

        init_results = await asyncio.gather(
            *[_run_initial_worker(w_id, w_cfg) for w_id, w_cfg in workers_dict.items()],
            return_exceptions=True,
        )
        for idx, (w_id, _) in enumerate(workers_dict.items()):
            res_item = init_results[idx]
            if isinstance(res_item, Exception):
                deliverables[w_id] = f"⚠️ Erreur d'exécution du worker `{w_id}` : {res_item}"
            else:
                _, text_out = res_item
                deliverables[w_id] = text_out

        # ── Phase 2+3 : Boucle critique / correction ──
        correction_round = 0
        while correction_round < max_rounds:
            correction_round += 1
            await events.status(
                f"📋 [{task_id}] Phase 2 — Évaluation critique (round {correction_round}/{max_rounds})..."
            )

            # Construction du prompt critique
            deliverables_text = "\n\n---\n\n".join(
                f"### WORKER: {w_id}\n{text}" for w_id, text in deliverables.items()
            )

            current_time = datetime.datetime.now().isoformat()

            critic_prompt = SYS_ORCHESTRATOR_CRITIC.format(
                current_time=current_time,
                objective=objective,
                deliverables_text=deliverables_text
            )
            critic_res, _, _ = await EchoGeminiClient.call_cascade(
                target_model_key=critic_model,
                payload={
                    "contents": [{"role": "user", "parts": [{"text": critic_prompt}]}],
                    "generationConfig": get_generation_config(critic_model, override_thinking="high"),
                },
                user_id=user_id,
                metadata=(__metadata__ or {}),
                events=events,
                timeout=self.valves.COGNITIVE_TIMEOUT,
                include_thoughts=False,
                response_schema=SupervisorCriticOutput,
            )

            if not critic_res:
                await events.status(f"⚠️ [{task_id}] Critique indisponible — validation forcée.", done=True)
                break

            # Extraction du verdict JSON
            critic_text = ""
            candidates = critic_res.get("candidates", [])
            if candidates:
                critic_text = "".join(
                    p.get("text", "") for p in candidates[0].get("content", {}).get("parts", [])
                    if "text" in p and not p.get("thought")
                )

            try:
                critic_parsed = SupervisorCriticOutput.model_validate_json(critic_text.strip())
            except Exception:
                await events.status(f"⚠️ [{task_id}] Verdict non parseable — validation forcée.", done=True)
                break

            verdict = critic_parsed.verdict
            if verdict == "APPROVED":
                await events.status(f"✅ [{task_id}] Verdict : APPROVED (round {correction_round})")
                break

            # REJECTED — Phase 3 : Correction
            worker_feedback = {
                item.worker_id: {"status": item.status, "feedback": item.feedback}
                for item in critic_parsed.worker_feedback
                if item.worker_id in deliverables
            }
            workers_to_correct = [
                w_id for w_id, fb in worker_feedback.items()
                if fb.get("status") == "needs_correction"
            ]

            if not workers_to_correct:
                break  # REJECTED mais personne à corriger

            await events.status(
                f"📋 [{task_id}] Phase 3 — Correction parallèle de {len(workers_to_correct)} workers..."
            )

            async def _run_correction_worker(w_id: str) -> Tuple[str, str]:
                feedback = worker_feedback[w_id].get("feedback", "Corrige ton livrable.")
                agent_sid = worker_sids[w_id]
                w_config = workers_dict.get(w_id, {})
                w_role = w_config.get("skill_id", w_config.get("role_name"))
                w_sys = w_config.get("system_prompt", "")
                res = await delegate.delegate_to_agent(
                    task=f"### FEEDBACK DU CRITIQUE\n{feedback}\n\n### OBJECTIF\nLe Modèle DOIT corriger son livrable précédent.",
                    system_prompt=w_sys,
                    skill_id=w_role,
                    sub_sid=agent_sid,
                    target_model_key=target_model,
                    __user__=__user__,
                    __chat_id__=chat_id,
                    __metadata__=__metadata__,
                    __event_emitter__=__event_emitter__,
                    __event_call__=__event_call__,
                )
                return w_id, _extract_worker_deliverable(res)

            valid_workers_to_correct = [w_id for w_id in workers_to_correct if w_id in worker_sids]
            corr_results = await asyncio.gather(
                *[_run_correction_worker(w_id) for w_id in valid_workers_to_correct],
                return_exceptions=True,
            )
            for idx, w_id in enumerate(valid_workers_to_correct):
                res_item = corr_results[idx]
                if isinstance(res_item, Exception):
                    deliverables[w_id] = f"⚠️ Erreur de correction du worker `{w_id}` : {res_item}"
                else:
                    _, text_out = res_item
                    deliverables[w_id] = text_out

        # ── Phase 4 : Consolidation ──
        await events.status(f"📋 [{task_id}] Phase 4 — Consolidation...")

        consolidation_text = "\n\n---\n\n".join(
            f"### WORKER: {w_id}\n{text}" for w_id, text in deliverables.items()
        )

        current_time = datetime.datetime.now().isoformat()

        consolidation_prompt = SYS_ORCHESTRATOR_CONSOLIDATOR.format(
            current_time=current_time,
            objective=objective,
            consolidation_text=consolidation_text
        )

        consolidation_res, _, _ = await EchoGeminiClient.call_cascade(
            target_model_key="MODEL_APOLLO",
            payload={
                "contents": [{"role": "user", "parts": [{"text": consolidation_prompt}]}],
                "generationConfig": {**get_generation_config("MODEL_APOLLO", override_thinking="medium"), "maxOutputTokens": 16000},
            },
            user_id=user_id,
            metadata=(__metadata__ or {}),
            events=events,
            timeout=self.valves.COGNITIVE_TIMEOUT,
            include_thoughts=False,
        )

        if not consolidation_res:
            final_text = "❌ Cascade épuisée pour la consolidation."
        else:
            candidates = consolidation_res.get("candidates", [])
            final_text = "".join(
                p.get("text", "") for p in candidates[0].get("content", {}).get("parts", [])
                if "text" in p
            ) if candidates else "❌ Consolidation vide."

        # Clôture
        if close_on_finish:
            state = EchoStateManager(user_id=user_id, chat_id=chat_id)
            for agent_sid in worker_sids.values():
                state.delete_thread(agent_sid)

        await events.status(f"📋 Superviseur [{task_id}] terminé.", done=True)
        return wrap_tool_output(
            text=(
                f"### RÉSULTAT SUPERVISÉ [{task_id}] ({len(workers_dict)} workers, "
                f"{correction_round} round(s) de critique)\n\n{final_text}"
            ),
            status={
                "status": "success",
                "task_id": task_id,
                "rounds_remaining": max_rounds - correction_round,
                "workers_count": len(workers_dict)
            }
        )

    # ==========================================================================
    # 4. OUTILS D'ADMINISTRATION (Conseils & Superviseurs)
    # ==========================================================================

    @echo_tool
    async def list_councils(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """Liste les conseils d'experts actifs dans la session courante."""
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return "❌ Aucun chat_id détecté."

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        threads = state.list_threads(chat_id)
        known_skills = sorted([s["id"] for s in await asyncio.to_thread(get_all_skills, user_id)], key=len, reverse=True)

        councils: Dict[str, list] = {}
        for t in threads:
            sid = t["sub_sid"]
            if sid.startswith("thread_council_"):
                rem = sid[len("thread_council_"):]
                cid_found, skill_found = None, None
                for k_sid in known_skills:
                    if rem.endswith(f"_{k_sid}"):
                        cid_found = rem[: -(len(k_sid) + 1)]
                        skill_found = k_sid
                        break
                if not cid_found:
                    m_inst = re.match(r"^(.+)_(\d+)$", rem)
                    if m_inst:
                        rem_base, inst_num = m_inst.group(1), m_inst.group(2)
                        for k_sid in known_skills:
                            if rem_base.endswith(f"_{k_sid}"):
                                cid_found = rem_base[: -(len(k_sid) + 1)]
                                skill_found = f"{k_sid} #{inst_num}"
                                break
                if not cid_found and "_" in rem:
                    cid_found, skill_found = rem.split("_", 1)
                if cid_found:
                    councils.setdefault(cid_found, []).append(skill_found or "?")

        if not councils:
            return "ℹ️ Aucun conseil actif."

        md = "### 🏛️ CONSEILS ACTIFS\n\n"
        for cid, skills in councils.items():
            md += f"- **ID:** `{cid}` | **Participants:** {', '.join(skills)} | **Sessions:** {len(skills)}\n"

        return wrap_tool_output(text=md, status={"status": "success", "councils": list(councils.keys())})

    @echo_tool
    async def close_council(
        self,
        council_id: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """Ferme définitivement un conseil d'experts et purge les sessions de ses participants."""
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return "❌ Aucun chat_id détecté."

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        threads = state.list_threads(chat_id)
        norm_cid = sanitize_echo_id(council_id, fallback="council").replace("_", "-")
        prefixes = (f"thread_council_{council_id}_", f"thread_council_{norm_cid}_")
        to_delete = [t["sub_sid"] for t in threads if t["sub_sid"].startswith(prefixes)]

        if not to_delete:
            return f"❌ Conseil '{council_id}' introuvable."

        for sid in to_delete:
            state.delete_thread(sid)

        return f"✅ Conseil `{council_id}` fermé ({len(to_delete)} sessions purgées)."

    @echo_tool
    async def list_supervised_tasks(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """Liste les tâches supervisées actives dans la session courante."""
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return "❌ Aucun chat_id détecté."

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        threads = state.list_threads(chat_id)

        tasks: Dict[str, list] = {}
        for t in threads:
            sid = t["sub_sid"]
            if sid.startswith("thread_supervisor_"):
                # Format canonique : thread_supervisor_sup_<hex>_<worker_id>
                m = re.match(r"^thread_supervisor_(sup_[0-9a-fA-F]+)_(.+)$", sid)
                if m:
                    tid, wid = m.group(1), m.group(2)
                    tasks.setdefault(tid, []).append(wid)
                else:
                    rem = sid[len("thread_supervisor_"):]
                    if "_" in rem:
                        tid, wid = rem.split("_", 1)
                        tasks.setdefault(tid, []).append(wid)

        if not tasks:
            return "ℹ️ Aucune tâche supervisée active."

        md = "### 📋 TÂCHES SUPERVISÉES ACTIVES\n\n"
        for tid, worker_ids in tasks.items():
            md += f"- **ID:** `{tid}` | **Workers:** {', '.join(worker_ids)} | **Sessions:** {len(worker_ids)}\n"

        return wrap_tool_output(text=md, status={"status": "success", "tasks": list(tasks.keys())})

    @echo_tool
    async def close_supervised_task(
        self,
        task_id: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """Ferme définitivement une tâche supervisée et purge les sessions de ses workers."""
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return "❌ Aucun chat_id détecté."

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        threads = state.list_threads(chat_id)
        clean_tid = task_id[len("thread_supervisor_"):] if task_id.startswith("thread_supervisor_") else task_id
        prefix = f"thread_supervisor_{clean_tid}_"
        to_delete = [
            t["sub_sid"] for t in threads
            if t["sub_sid"].startswith(prefix) or t["sub_sid"] == f"thread_supervisor_critic_{clean_tid}"
        ]

        if not to_delete:
            return f"❌ Tâche '{task_id}' introuvable."

        for sid in to_delete:
            state.delete_thread(sid)

        return f"✅ Tâche supervisée `{task_id}` fermée ({len(to_delete)} sessions purgées)."
