# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Agent Orchestration
author: ECHO Framework
version: 6.23.1.1
description: Composant système interne : ECHO Agent Orchestration.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.23.1.1: Préfixage anti-collision m{idx}_{sid} sur multiplier > 1, bornage SSOT cid/clean_pid, consommation USR_ORCHESTRATOR_COUNCIL_* (echo_prompts) et garde [POSITION_STABILISEE] sans angle mort.
# 6.23.0.1: Concile de Delphes V2 (allowed_tools, cache KV chaud, historique 100%, unanimité [POSITION_STABILISEE], continuité multi-séances via council_state:<cid>).
# 6.20.3.2: Enrichissement reST (:param) des docstrings Skills (forge_skill, read_skill, search_skill, delete_user_skill).
# 6.20.3.1: Migration de la confirmation delete_user_skill vers events.confirm (Markdown et timeout_seconds).
# 6.20.2.1: Ajout de l'avertissement coût tokens et de l'accord préalable Utilisateur dans la docstring de consult_phalanx.

import sys
import orjson as json
import asyncio
import uuid
import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal, Tuple

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool, wrap_tool_output
from echo_protocol import SkillRouterOutput
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_subagent_hub import EchoSubagentController, get_subagent_system_prompt, save_subagent_meta
from echo_constants import (
    COUNCIL_STATE_PREFIX,
    ECHO_API_KEY_RETRIES,
    ECHO_API_MAX_RETRIES,
    ECHO_AUTONOMOUS_MAX_TURNS,
    ECHO_AUTONOMOUS_TIMEOUT_SEC,
    ECHO_COUNCIL_DEFAULT_TURNS_PER_ROUND,
    ECHO_COUNCIL_ID_MAX_LEN,
    ECHO_COUNCIL_INSTANCE_ID_MAX_LEN,
    ECHO_COUNCIL_ROUND_TIMEOUT_SEC,
    ECHO_COUNCIL_STABILIZED_MARKER,
    ECHO_LLM_CALL_TIMEOUT,
    ECHO_MAX_PHALANX_DEPTH,
    ECHO_PHALANX_ID_MAX_LEN,
    get_generation_config,
    get_headless_block_reason,
)
from echo_skills import get_all_skills, get_skill_content, save_skill, parse_skill_metadata, delete_skill
from echo_paths import sanitize_echo_id, get_user_skill_lock
from echo_prompts import (
    SYS_ORCHESTRATOR_SKILL_ROUTER,
    SYS_ORCHESTRATOR_COUNCIL_EXPERT,
    SYS_ORCHESTRATOR_COUNCIL_SYNTHESIS,
    SYS_ORCHESTRATOR_PHALANX,
    USR_ORCHESTRATOR_COUNCIL_FINAL_ROUND,
    USR_ORCHESTRATOR_COUNCIL_INTERMEDIATE,
    USR_ORCHESTRATOR_COUNCIL_R1_FOLLOWUP,
    USR_ORCHESTRATOR_COUNCIL_R1_INITIAL,
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
        COUNCIL_EXPERT_MAX_TOOL_TURNS_PER_ROUND: int = Field(
            default=ECHO_COUNCIL_DEFAULT_TURNS_PER_ROUND,
            ge=0,
            le=6,
            description="Nombre max de tours d'outils par expert et par tour de conseil (0 = délibération pure sans outil).",
        )
        COUNCIL_MAX_PR_COMPLEXITY: int = Field(default=60, description="Complexité maximale (participants * rounds) d'un conseil.")

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
        """Crée un nouveau Skill réutilisable pour les sous-agents ou remplace intégralement un Skill existant.
        Avant toute mise à jour, lire le Skill existant avec read_skill pour éviter de perdre des directives.

        :param skill_id: Identifiant unique en snake_case (ex: "expert_sec_cloud"). Un ID existant est écrasé.
        :param name: Titre court sur une seule ligne (ex: "Expert Sécurité Cloud").
        :param description: Résumé sur une seule ligne du domaine et des cas d'usage (utilisé par search_skill).
        :param instructions: Prompt système complet du sous-agent (protocole PAS, sans en-tête YAML '---').
        :param require_web_grounding: Si True, impose au sous-agent de vérifier l'état de l'art sur le Web avant toute réponse ou action.
        """
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
        """Lit le contenu brut et les directives d'une expertise (Skill) existante.

        :param skill_id: Identifiant du Skill à inspecter (obtenu via list_skills ou search_skill).
        """
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
        """Identifie jusqu'à 3 expertises (Skills) pertinentes pour un besoin spécifique.

        :param need_description: Description précise du besoin ou de l'expertise recherchée.
        """
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
        """Supprime une expertise (Skill) après confirmation interactive de l'utilisateur.

        :param skill_id: Identifiant du Skill à supprimer définitivement.
        :param timeout_seconds: Délai d'attente de la confirmation utilisateur en secondes (défaut 300).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"

        content = await asyncio.to_thread(get_skill_content, user_id, skill_id)
        if not content:
            return f"❌ Le skill '{skill_id}' n'existe pas."

        block_reason = get_headless_block_reason(__metadata__)
        if block_reason:
            return f"Erreur : {block_reason}"

        fence = "````" if "```" in content else "```"
        msg_md = (
            f"Confirmez-vous la suppression définitive de l'expertise **{skill_id}** ?\n\n"
            f"{fence}\n{content}\n{fence}"
        )

        if __event_emitter__:
            await events.status(f"Validation requise pour supprimer le skill {skill_id}...", done=False)

        user_confirmed = await events.confirm(
            f"Supprimer la compétence « {skill_id} »",
            msg_md,
            timeout_seconds=timeout_seconds,
        )

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
        allowed_tools: Optional[List[str]] = None,
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
        cid = sanitize_echo_id(council_id, fallback="council").replace("_", "-")[:ECHO_COUNCIL_ID_MAX_LEN]

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
                inst_id = (f"m{m_idx}_{sid}" if multiplier > 1 else sid)[:ECHO_COUNCIL_INSTANCE_ID_MAX_LEN]
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

        max_turns_per_round = self.user_valves.COUNCIL_EXPERT_MAX_TOOL_TURNS_PER_ROUND

        await events.status(
            f"🏛️ Conseil [{cid}] convoqué : {len(roster)} experts, {effective_rounds} tours, "
            f"{max_turns_per_round} tours d'outils/tour..."
        )

        # ── Phase 1 & 2 : Boucle des tours (protocole Delphi via EchoSubagentController, verrouillé par council_id) ──
        all_rounds: List[Dict[str, str]] = []
        council_sids: List[str] = []  # Pour la purge finale

        async with _get_council_lock(user_id, chat_id, cid):
            council_state_mgr = EchoStateManager(user_id=user_id, chat_id=chat_id)
            state_key = f"{COUNCIL_STATE_PREFIX}{cid}"
            session_num = 1
            prev_last_round: Dict[str, str] = {}
            session_started_at = datetime.datetime.now().replace(microsecond=0).isoformat()
            raw_council_state = council_state_mgr.get_session_setting(state_key)
            if raw_council_state:
                try:
                    parsed_state = json.loads(raw_council_state)
                    if (
                        isinstance(parsed_state, dict)
                        and int(parsed_state.get("session_num") or 0) >= 1
                    ):
                        session_num = int(parsed_state.get("session_num") or 0) + 1
                        raw_prev = parsed_state.get("last_round_responses")
                        if isinstance(raw_prev, dict):
                            prev_last_round = {str(k): str(v) for k, v in raw_prev.items()}
                        if parsed_state.get("session_started_at"):
                            session_started_at = str(parsed_state["session_started_at"])
                except Exception:
                    session_num = 1
                    prev_last_round = {}
                    session_started_at = datetime.datetime.now().replace(microsecond=0).isoformat()

            members = "\n".join(f"- {p['alias']} : {p['name']}" for p in roster)

            for round_num in range(1, effective_rounds + 1):
                await events.status(
                    f"🏛️ Conseil [{cid}] — Séance #{session_num}, Tour {round_num}/{effective_rounds}..."
                )

                # Construction du prompt par participant
                if round_num == 1:
                    if session_num == 1:
                        base_prompt = USR_ORCHESTRATOR_COUNCIL_R1_INITIAL.format(
                            effective_rounds=effective_rounds,
                            question=question,
                        )
                        round_prompts = {p["instance_id"]: base_prompt for p in roster}
                    else:
                        round_prompts = {}
                        for p in roster:
                            inst_id = p["instance_id"]
                            others_prev = "\n\n".join(
                                f"**{r['alias']}** ({r['name']}) :\n{prev_last_round[r['instance_id']]}"
                                for r in roster
                                if r["instance_id"] != inst_id and r["instance_id"] in prev_last_round
                            )
                            round_prompts[inst_id] = USR_ORCHESTRATOR_COUNCIL_R1_FOLLOWUP.format(
                                prev_session_num=session_num - 1,
                                others_prev=others_prev or "(Aucune contribution précédente)",
                                session_num=session_num,
                                effective_rounds=effective_rounds,
                                question=question,
                            )
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
                        if round_num == effective_rounds:
                            round_prompts[inst_id] = USR_ORCHESTRATOR_COUNCIL_FINAL_ROUND.format(
                                session_num=session_num,
                                round_num=round_num,
                                prev_round_num=round_num - 1,
                                effective_rounds=effective_rounds,
                                others=others,
                                participant_alias=p["alias"],
                                participant_name=p["name"],
                                stabilized_marker=ECHO_COUNCIL_STABILIZED_MARKER,
                            )
                        else:
                            round_prompts[inst_id] = USR_ORCHESTRATOR_COUNCIL_INTERMEDIATE.format(
                                session_num=session_num,
                                round_num=round_num,
                                prev_round_num=round_num - 1,
                                effective_rounds=effective_rounds,
                                others=others,
                                participant_alias=p["alias"],
                                participant_name=p["name"],
                                stabilized_marker=ECHO_COUNCIL_STABILIZED_MARKER,
                            )

                # Appels parallèles — chaque participant est un agent indépendant
                async def _call_participant(participant: dict, prompt: str) -> Tuple[str, str]:
                    """Appelle un participant via EchoSubagentController et retourne (instance_id, texte)."""
                    sid = participant["skill_id"]
                    inst_id = participant["instance_id"]
                    agent_sid = f"thread_council_{cid}_{inst_id}"
                    if agent_sid not in council_sids:
                        council_sids.append(agent_sid)

                    already_init = bool(
                        get_subagent_system_prompt(council_state_mgr, agent_sid)
                        or council_state_mgr.get_thread_history(agent_sid)
                    )
                    if not already_init:
                        council_system: Optional[str] = SYS_ORCHESTRATOR_COUNCIL_EXPERT.format(
                            participant_alias=f"{participant['alias']} ({participant['name']})",
                            roster_length=len(roster),
                            members=members,
                            max_calls_per_round=max_turns_per_round,
                            current_time=session_started_at,
                        )
                        call_skill_id: Optional[str] = sid
                    else:
                        council_system = None
                        call_skill_id = None

                    result = await EchoSubagentController.run_or_resume(
                        user_id=user_id,
                        chat_id=chat_id,
                        task=prompt,
                        system_prompt=council_system,
                        skill_id=call_skill_id,
                        sub_sid=agent_sid,
                        target_model_key=target_model,
                        allowed_tools=allowed_tools,
                        slice_max_turns=max_turns_per_round,
                        slice_timeout_sec=ECHO_COUNCIL_ROUND_TIMEOUT_SEC,
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
                round_had_exception = False
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        round_had_exception = True
                        inst_id = roster[i]["instance_id"]
                        alias = roster[i]["alias"]
                        round_responses[inst_id] = f"⚠️ {alias} n'a pas pu répondre."
                        if self.valves.DEBUG_COUNCIL:
                            print(f"[Council] {alias} error: {result}")
                    else:
                        inst_id, text = result
                        round_responses[inst_id] = text or "(Pas de réponse)"

                all_rounds.append(round_responses)

                if (
                    2 <= round_num < effective_rounds
                    and not round_had_exception
                    and all(
                        ECHO_COUNCIL_STABILIZED_MARKER in (round_responses.get(p["instance_id"]) or "")
                        for p in roster
                    )
                ):
                    await events.status(
                        f"🏛️ Conseil [{cid}] — Séance #{session_num} stabilisée à l'unanimité au tour {round_num}/{effective_rounds}."
                    )
                    break

            # ── Phase 3 : Synthèse finale ──
            await events.status("📝 Synthèse du conseil en cours...")

            transcript = f"### SÉANCE #{session_num} — QUESTION\n{question}\n\n"
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
                display_user_prompt=(
                    f"Synthèse du conseil [{cid}] — Séance #{session_num} "
                    f"({len(roster)} experts, {len(all_rounds)} tours) — Question : {question[:500]}"
                ),
                system_prompt=synthesis_system,
                group_id=cid,
                role_order=999,
                append_history=True,
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
                council_state_mgr.save_session_setting(state_key, "")
            else:
                state_payload = json.dumps({
                    "session_num": session_num,
                    "session_started_at": session_started_at,
                    "last_round_responses": all_rounds[-1] if all_rounds else {},
                }).decode("utf-8")
                council_state_mgr.save_session_setting(state_key, state_payload)

        roster_summary = ", ".join(f"{p['alias']} ({p['name']})" for p in roster)
        await events.status("🏛️ Conseil terminé.", done=True)
        return (
            f"### SYNTHÈSE DU CONSEIL [{cid}] — Séance #{session_num} "
            f"({len(roster)} experts, {len(all_rounds)} tours)\n"
            f"**Participants** : {roster_summary}\n\n"
            f"{synthesis_text}"
        )

    # ==========================================================================
    # 3. PHALANGE MULTI-NIVEAUX (Stratège -> Capitaines / Hoplites)
    # ==========================================================================

    @echo_tool
    async def consult_phalanx(
        self,
        objective: str = Field(..., description="Objectif global confié à la Phalange."),
        strategist_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_PROMETHEUS",
        worker_model: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_APOLLO",
        phalanx_id: Optional[str] = Field(default=None, description="Identifiant optionnel de la Phalange."),
        close_on_finish: bool = False,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Déploie une Phalange multi-niveaux (coût tokens élevé) pour un objectif complexe multi-fronts ; obtenir l'accord préalable de l'Utilisateur sauf demande explicite, /goal ou automatisation headless."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = (__user__ or {}).get("id", "system")
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""
        if not chat_id:
            return wrap_tool_output(
                text="❌ Erreur: Aucun chat_id détecté.",
                status={"status": "error", "message": "chat_id requis"},
            )

        clean_pid = sanitize_echo_id(
            phalanx_id or uuid.uuid4().hex[:8], fallback="phalanx"
        ).replace("_", "-")[:ECHO_PHALANX_ID_MAX_LEN]
        group_id = clean_pid if clean_pid.startswith("phx_") else f"phx_{clean_pid}"
        strategist_sid = f"thread_phalanx_{clean_pid}_strategist"

        phalanx_system = SYS_ORCHESTRATOR_PHALANX.format(
            phalanx_id=group_id,
            default_worker_model=worker_model,
            max_depth=ECHO_MAX_PHALANX_DEPTH,
            current_time=datetime.datetime.now().isoformat(),
        )

        await events.status(
            f"🛡️ Phalange [{group_id}] — Déploiement du Stratège ({strategist_model})..."
        )
        res = await EchoSubagentController.run_or_resume(
            user_id=user_id,
            chat_id=chat_id,
            task=objective,
            system_prompt=phalanx_system,
            sub_sid=strategist_sid,
            target_model_key=strategist_model,
            slice_max_turns=ECHO_AUTONOMOUS_MAX_TURNS,
            slice_timeout_sec=ECHO_AUTONOMOUS_TIMEOUT_SEC,
            autonomous_slice=False,
            engine="phalanx",
            group_id=group_id,
            role_name=f"Stratège ({clean_pid})",
            role_order=10,
            can_delegate=True,
            depth=1,
            default_worker_model=worker_model,
            key_switch_threshold=self.valves.KEY_SWITCH_THRESHOLD,
            max_retries=self.valves.MAX_RETRIES,
            __user__=__user__,
            __metadata__=__metadata__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )

        if (
            close_on_finish
            and isinstance(res, dict)
            and (res.get("status") or {}).get("subagent_status") == "completed"
        ):
            await EchoSubagentController.execute_control_action(
                action="close",
                user_id=user_id,
                chat_id=chat_id,
                target_ids=[group_id],
            )

        if isinstance(res, dict) and isinstance(res.get("status"), dict):
            res["status"]["phalanx_id"] = group_id
        return res
