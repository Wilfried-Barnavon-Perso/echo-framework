"""
title: ECHO Agent Orchestration
author: ECHO Framework
version: 6.7.1.2
description: Composant système interne : ECHO Agent Orchestration.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.7.1.2: Câblage run_cognitive_step sur synthèse/critique/consolidation, role_order déterministe, normalisation _normalize_workers_input et fail-fast Phase 1.
# 6.7.1.1: Mise en conformité PAS des prompts et directives.
# 6.6.30.1: Câblage de consult_council et consult_supervised_workers sur EchoSubagentController et suppression de list_councils, close_council, list_supervised_tasks et close_supervised_task.
# 6.6.2.1: Persistance save_subagent_meta et save_thread_step pour la synthèse consult_council et les phases critique/consolidation de consult_supervised_workers.
# 5.239.17.1: Alignement de la Valve COGNITIVE_TIMEOUT par défaut sur ECHO_LLM_CALL_TIMEOUT (600s).
# 5.238.1.1: Ajout de MODEL_ATHENA dans target_model, synthesis_model et critic_model de consult_council et consult_supervised_workers.

import sys
import orjson as json
import asyncio
import uuid
import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal, Tuple, Union

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool, wrap_tool_output
from echo_protocol import SkillRouterOutput, SupervisorCriticOutput
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_subagent_hub import EchoSubagentController, save_subagent_meta
from echo_constants import (
    ECHO_API_KEY_RETRIES,
    ECHO_API_MAX_RETRIES,
    ECHO_AUTONOMOUS_ROUND_MAX_CALLS,
    ECHO_COUNCIL_DEFAULT_CALLS_PER_ROUND,
    ECHO_LLM_CALL_TIMEOUT,
    get_generation_config,
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
        COGNITIVE_TIMEOUT: int = Field(default=ECHO_LLM_CALL_TIMEOUT, description="Délai d'attente maximum (secondes) pour la délégation cognitive.")
        DEBUG_COUNCIL: bool = Field(default=False, description="Si activé, conserve les traces des réflexions internes et affiche plus de détails.")

    class UserValves(BaseModel):
        COUNCIL_ROUNDS_MIN: int = Field(default=3, description="Nombre de tours de parole minimum et par défaut pour un conseil d'experts.")
        COUNCIL_ROUNDS_MAX: int = Field(default=10, description="Limite haute du nombre de tours de parole.")
        COUNCIL_MAX_SKILLS: int = Field(default=7, description="Nombre maximum d'expertises (skills) distinctes au conseil (hors synthétiseur).")
        COUNCIL_EXPERT_MAX_CALLS_PER_ROUND: int = Field(default=ECHO_COUNCIL_DEFAULT_CALLS_PER_ROUND, ge=0, le=10, description="Budget max d'appels d'outils par expert et par tour de conseil.")
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
        question: str = Field(..., description="Question ou problématique soumise au conseil."),
        participants: List[str] = Field(
            ...,
            description="Liste d'au moins 2 identifiants de skills existants (ou 1 skill avec multiplier >= 2). Appeler list_skills / forge_skill au préalable si nécessaire.",
        ),
        council_id: str = Field(..., description="Identifiant unique du conseil."),
        target_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_PROMETHEUS",
        synthesis_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_PROMETHEUS",
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

        # Résolution et normalisation du council_id (évite toute collision avec le séparateur '_')
        cid = sanitize_echo_id(council_id, fallback="council").replace("_", "-")

        # Chargement et validation de chaque skill, puis démultiplication par multiplier
        roster = []
        participant_idx = 1
        for sid in skill_ids:
            content = await asyncio.to_thread(get_skill_content, user_id, sid)
            if not content:
                err_msg = f"❌ Skill '{sid}' introuvable. Le Modèle DOIT utiliser l'outil forge_skill au préalable."
                err_sid = f"thread_council_{cid}_{sid}"
                err_state = EchoStateManager(user_id=user_id, chat_id=chat_id)
                save_subagent_meta(
                    err_state,
                    err_sid,
                    {
                        "sub_sid": err_sid,
                        "chat_id": chat_id,
                        "engine": "council",
                        "role_name": f"Erreur Skill ({sid})",
                        "group_id": cid,
                        "role_order": 100 + participant_idx,
                        "status": "error",
                    },
                )
                err_state.save_thread_step(
                    err_sid,
                    chat_id,
                    "council",
                    0,
                    "model",
                    [{"text": err_msg}],
                )
                return err_msg
            meta = parse_skill_metadata(content)
            base_name = meta.get("name", sid)
            for m_idx in range(1, multiplier + 1):
                inst_id = f"{sid}_{m_idx}" if multiplier > 1 else sid
                inst_name = f"{base_name} #{m_idx}" if multiplier > 1 else base_name
                roster.append({
                    "idx": participant_idx,
                    "instance_id": inst_id,
                    "skill_id": sid,
                    "name": inst_name,
                    "instructions": content,
                    "alias": f"Participant {participant_idx}",
                })
                participant_idx += 1

        max_calls_per_round = self.user_valves.COUNCIL_EXPERT_MAX_CALLS_PER_ROUND

        await events.status(
            f"🏛️ Conseil [{cid}] convoqué : {len(roster)} experts, {effective_rounds} tours, "
            f"{max_calls_per_round} outils/tour..."
        )

        # ── Phase 1 & 2 : Boucle des tours (protocole Delphi via EchoSubagentController, verrouillé par council_id) ──
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
                            f"Le Modèle PEUT signifier qu'il préfère ne pas répondre."
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

                    result = await EchoSubagentController.run_or_resume(
                        user_id=user_id,
                        chat_id=chat_id,
                        task=prompt,
                        system_prompt=council_system,
                        skill_id=sid,
                        sub_sid=agent_sid,
                        target_model_key=target_model,
                        slice_max_calls=max_calls_per_round,
                        autonomous_slice=True,
                        engine="council",
                        group_id=cid,
                        role_name=f"{participant['alias']} ({participant['name']})",
                        role_order=100 + participant["idx"],
                        key_switch_threshold=self.valves.KEY_SWITCH_THRESHOLD,
                        max_retries=self.valves.MAX_RETRIES,
                        __user__=__user__,
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

            synthesis_text, synth_model_used, _ = await EchoSubagentController.run_cognitive_step(
                user_id=user_id,
                chat_id=chat_id,
                sub_sid=f"thread_council_{cid}_synthesis",
                engine="council_synthesis",
                role_name=f"Synthèse du Conseil ({cid})",
                target_model_key=synthesis_model,
                user_prompt=transcript,
                display_user_prompt=f"Synthèse du conseil [{cid}] ({len(roster)} experts, {effective_rounds} tours) — Question : {question[:500]}",
                system_prompt=synthesis_system,
                group_id=cid,
                role_order=999,
                override_thinking="high",
                max_output_tokens=16000,
                timeout=self.valves.COGNITIVE_TIMEOUT,
                events=events,
                metadata=(__metadata__ or {}),
                fallback_error="❌ Cascade épuisée : aucun modèle disponible pour la synthèse.",
            )

            # ── Phase 4 : Clôture ──
            if close_on_finish:
                await EchoSubagentController.execute_control_action(
                    action="close", user_id=user_id, chat_id=chat_id, target_ids=[cid]
                )

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

    @staticmethod
    def _normalize_workers_input(raw_workers: Any) -> Dict[str, dict]:
        """Normalise l'entrée `workers` (dict, list ou JSON string) en `{worker_id: {task, ...}}`."""
        parsed = raw_workers
        if isinstance(raw_workers, str):
            try:
                parsed = json.loads(raw_workers)
            except Exception as e:
                raise ValueError(f"Format JSON invalide pour `workers` : {e}")

        if isinstance(parsed, list):
            if not parsed:
                raise ValueError("`workers` ne peut pas être une liste vide.")
            converted: Dict[str, dict] = {}
            for idx, item in enumerate(parsed, 1):
                if isinstance(item, str) and item.strip():
                    converted[f"worker_{idx}"] = {"task": item.strip()}
                elif isinstance(item, dict):
                    w_id = str(item.get("id") or item.get("worker_id") or f"worker_{idx}").strip()
                    w_task = str(item.get("task") or item.get("description") or "").strip()
                    if not w_task:
                        raise ValueError(
                            f"Entrée `{w_id}` invalide dans `workers` : le champ `task` est requis et non vide."
                        )
                    entry = dict(item)
                    entry["task"] = w_task
                    converted[w_id] = entry
                else:
                    raise ValueError(
                        f"Élément #{idx} invalide dans `workers` : dict ou str attendu."
                    )
            return converted

        if not parsed or not isinstance(parsed, dict):
            raise ValueError(
                "`workers` DOIT être un dictionnaire non vide de la forme "
                "`{'worker_id': {'task': '...', 'skill_id': '...'}}` ou `{'worker_id': 'description'}`."
            )

        normalized: Dict[str, dict] = {}
        for raw_id, raw_cfg in parsed.items():
            w_id = str(raw_id or "").strip()
            if not w_id:
                raise ValueError("Identifiant de worker vide interdit dans `workers`.")
            if isinstance(raw_cfg, str):
                if not raw_cfg.strip():
                    raise ValueError(f"La tâche du worker `{w_id}` ne peut pas être vide.")
                normalized[w_id] = {"task": raw_cfg.strip()}
            elif isinstance(raw_cfg, dict):
                w_task = str(raw_cfg.get("task") or raw_cfg.get("description") or "").strip()
                if not w_task:
                    raise ValueError(
                        f"Le worker `{w_id}` DOIT définir un champ `task` non vide."
                    )
                entry = dict(raw_cfg)
                entry["task"] = w_task
                normalized[w_id] = entry
            else:
                raise ValueError(
                    f"Configuration invalide pour le worker `{w_id}` : dict ou str attendu."
                )
        return normalized

    @echo_tool
    async def consult_supervised_workers(
        self,
        objective: str = Field(..., description="Objectif global de la mission supervisée."),
        workers: Union[dict, list, str] = Field(
            ...,
            description="Dictionnaire des workers {'worker_id': {'task': '...', 'skill_id': '(optionnel)', 'system_prompt': '(optionnel)'}} ou {'worker_id': 'description de la tâche'}.",
        ),
        target_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_APOLLO",
        critic_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_PROMETHEUS",
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

        try:
            workers_dict = self._normalize_workers_input(workers)
        except ValueError as e:
            return f"❌ Erreur de format pour `workers` : {e}"

        task_id = f"sup_{uuid.uuid4().hex[:8]}"
        max_rounds = max_correction_rounds or self.user_valves.SUPERVISOR_MAX_CORRECTION_ROUNDS

        await events.status(
            f"📋 Superviseur [{task_id}] : {len(workers_dict)} workers, max {max_rounds} corrections..."
        )

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

        # Validation gracieuse des skill_id éventuels (repli en rôle descriptif si absent)
        for w_id, w_cfg in workers_dict.items():
            w_role = w_cfg.get("skill_id")
            if w_role:
                skill_txt = await asyncio.to_thread(get_skill_content, user_id, str(w_role))
                if not skill_txt:
                    base_sys = w_cfg.get("system_prompt") or f"Le Modèle est un agent chargé de : {w_cfg.get('task', '')}"
                    w_cfg["system_prompt"] = f"[Rôle assigné : {w_role}]\n{base_sys}"
                    w_cfg["role_name"] = w_cfg.get("role_name") or str(w_role)
                    w_cfg["skill_id"] = None

        async def _run_initial_worker(w_idx: int, w_id: str, w_config: dict) -> Tuple[str, str]:
            w_task = w_config.get("task", "")
            w_role = w_config.get("skill_id")
            w_label = w_config.get("role_name") or w_id
            w_sys = w_config.get("system_prompt", f"Le Modèle est un agent chargé de : {w_task}")
            agent_sid = f"thread_supervisor_{task_id}_{w_id}"
            worker_sids[w_id] = agent_sid
            res = await EchoSubagentController.run_or_resume(
                user_id=user_id,
                chat_id=chat_id,
                task=f"### OBJECTIF GLOBAL\n{objective}\n\n### TÂCHE ASSIGNÉE\n{w_task}",
                system_prompt=w_sys,
                skill_id=w_role,
                sub_sid=agent_sid,
                target_model_key=target_model,
                slice_max_calls=ECHO_AUTONOMOUS_ROUND_MAX_CALLS,
                autonomous_slice=True,
                engine="supervisor",
                group_id=task_id,
                role_name=w_label,
                role_order=100 + w_idx,
                key_switch_threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=self.valves.MAX_RETRIES,
                __user__=__user__,
                __metadata__=__metadata__,
                __event_emitter__=__event_emitter__,
                __event_call__=__event_call__,
            )
            return w_id, _extract_worker_deliverable(res)

        init_results = await asyncio.gather(
            *[_run_initial_worker(idx, w_id, w_cfg) for idx, (w_id, w_cfg) in enumerate(workers_dict.items(), 1)],
            return_exceptions=True,
        )
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        for idx, (w_id, w_cfg) in enumerate(workers_dict.items(), 1):
            res_item = init_results[idx - 1]
            if isinstance(res_item, Exception):
                err_msg = f"⚠️ Erreur d'exécution du worker `{w_id}` : {res_item}"
                deliverables[w_id] = err_msg
                agent_sid = worker_sids.get(w_id) or f"thread_supervisor_{task_id}_{w_id}"
                worker_sids[w_id] = agent_sid
                save_subagent_meta(
                    state,
                    agent_sid,
                    {
                        "sub_sid": agent_sid,
                        "chat_id": chat_id,
                        "engine": "supervisor",
                        "role_name": w_cfg.get("role_name") or w_id,
                        "role_order": 100 + idx,
                        "group_id": task_id,
                        "model_key": target_model,
                        "status": "error",
                    },
                )
                state.save_thread_step(
                    agent_sid,
                    chat_id,
                    "supervisor",
                    0,
                    "model",
                    [{"text": err_msg}],
                )
            else:
                _, text_out = res_item
                deliverables[w_id] = text_out

        # Fail-Fast : si tous les workers ont échoué techniquement, court-circuiter Critique & Consolidation
        if init_results and all(isinstance(r, Exception) for r in init_results):
            await events.status(f"❌ [{task_id}] Échec fatal de tous les workers.", done=True)
            err_report = "\n\n".join(f"### WORKER: {w_id}\n{txt}" for w_id, txt in deliverables.items())
            return wrap_tool_output(
                text=f"❌ Échec technique de tous les workers [{task_id}] :\n\n{err_report}",
                status={
                    "status": "error",
                    "task_id": task_id,
                    "workers_count": len(workers_dict),
                },
            )

        # ── Phase 2+3 : Boucle critique / correction ──
        critic_sid = f"thread_supervisor_{task_id}_critic"
        critic_step_idx = 0
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
            critic_text, critic_used, critic_parsed = await EchoSubagentController.run_cognitive_step(
                user_id=user_id,
                chat_id=chat_id,
                sub_sid=critic_sid,
                engine="supervisor_critic",
                role_name=f"Critique ({task_id})",
                target_model_key=critic_model,
                user_prompt=critic_prompt,
                display_user_prompt=f"Évaluation critique round {correction_round}/{max_rounds} ({len(deliverables)} livrables) — Objectif : {objective[:400]}",
                group_id=task_id,
                role_order=900,
                step_index=critic_step_idx,
                override_thinking="high",
                max_output_tokens=8192,
                response_schema=SupervisorCriticOutput,
                timeout=self.valves.COGNITIVE_TIMEOUT,
                events=events,
                metadata=(__metadata__ or {}),
                fallback_error="",
            )
            critic_step_idx += 2

            if not critic_text or critic_parsed is None:
                await events.status(f"⚠️ [{task_id}] Critique indisponible ou non parseable — validation forcée.", done=True)
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
                feedback = worker_feedback[w_id].get("feedback", "Le Sous-Agent DOIT corriger le livrable.")
                agent_sid = worker_sids[w_id]
                w_config = workers_dict.get(w_id, {})
                w_role = w_config.get("skill_id", w_config.get("role_name"))
                w_sys = w_config.get("system_prompt", "")
                res = await EchoSubagentController.run_or_resume(
                    user_id=user_id,
                    chat_id=chat_id,
                    task=f"### FEEDBACK DU CRITIQUE\n{feedback}\n\n### OBJECTIF\nLe Modèle DOIT corriger son livrable précédent.",
                    system_prompt=w_sys,
                    skill_id=w_role,
                    sub_sid=agent_sid,
                    target_model_key=target_model,
                    slice_max_calls=ECHO_AUTONOMOUS_ROUND_MAX_CALLS,
                    autonomous_slice=True,
                    engine="supervisor",
                    group_id=task_id,
                    key_switch_threshold=self.valves.KEY_SWITCH_THRESHOLD,
                    max_retries=self.valves.MAX_RETRIES,
                    __user__=__user__,
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

        final_text, consol_used, _ = await EchoSubagentController.run_cognitive_step(
            user_id=user_id,
            chat_id=chat_id,
            sub_sid=f"thread_supervisor_{task_id}_consolidator",
            engine="supervisor_consolidator",
            role_name=f"Consolidation ({task_id})",
            target_model_key=target_model,
            user_prompt=consolidation_prompt,
            display_user_prompt=f"Consolidation [{task_id}] ({len(deliverables)} livrables) — Objectif : {objective[:400]}",
            group_id=task_id,
            role_order=999,
            override_thinking="medium",
            max_output_tokens=16000,
            timeout=self.valves.COGNITIVE_TIMEOUT,
            events=events,
            metadata=(__metadata__ or {}),
            fallback_error="❌ Cascade épuisée pour la consolidation.",
        )

        # Clôture
        if close_on_finish:
            await EchoSubagentController.execute_control_action(
                action="close", user_id=user_id, chat_id=chat_id, target_ids=[task_id]
            )

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
