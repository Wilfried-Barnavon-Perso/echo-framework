"""
title: ECHO Agent Orchestration
author: ECHO Framework
version: 5.15
description: 5.7: Résolution du conflit de nom get_all_skills (shadowing).
             5.8: Centralisation des niveaux de réflexion (THINKING_LEVEL_*) — suppression
             valves FLASH_THINKING et PRO_THINKING. Remplacement par constantes echo_constants.
             5.9: Renommage consult_council → consult_expert_consultant.
             5.10: Fix _iterative_loop : MODEL_LITE reçoit désormais THINKING_LEVEL_LITE.
             5.11: Ajout consult_council — Table Ronde Multi-Experts (protocole Delphi).
             5.12: consult_council — docstring prérequis 2 participants.
             5.13: Centralisation politique modèle Pipe. Migration call() → call_cascade().
             5.14: Suppression delegate_reasoning → remplacé par delegate_to_agent.
             5.15: Fusion expert-consultant / sous-agent.
             consult_expert_consultant supprimé → absorbé par delegate_to_agent.
             _iterative_loop, _distill_context, list_sub_chats supprimés (obsolètes).
             consult_council refactorisé → utilise delegate_to_agent en interne.
             Ajout consult_supervised_workers (superviseur avec boucle critique).
             Ajout outils d'administration : list_councils, close_council,
             list_supervised_tasks, close_supervised_task.
             Ajout UserValves COUNCIL_EXPERT_MAX_CALLS_PER_ROUND, SUPERVISOR_MAX_CORRECTION_ROUNDS.
             5.16: Renommage fichier → agent_orchestration_tool.py.
             5.17: Propagation target_model_key aux appels delegate_to_agent.
"""

import sys
import orjson as json
import asyncio
import uuid
import time
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal, Tuple

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, wrap_cascade_output, EchoEvents, EchoGeminiClient, EchoStateManager
from echo_constants import (
    MODEL_LITE, MODEL_FLASH, MODEL_PRO,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    TEMP_DEFAULT, TOP_P_DEFAULT, TEMP_DISTILLATION, TOP_P_DISTILLATION,
)
from echo_skills import get_all_skills, get_skill_content, save_skill, parse_skill_metadata

class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        COGNITIVE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour la délégation cognitive.")
        DEBUG_COUNCIL: bool = Field(default=False, description="Si activé, conserve les traces des réflexions internes et affiche plus de détails.")

    class UserValves(BaseModel):
        COUNCIL_ROUNDS_DEFAULT: int = Field(default=3, description="Nombre de tours de parole par défaut pour un conseil d'experts.")
        COUNCIL_ROUNDS_MAX: int = Field(default=5, description="Limite haute du nombre de tours de parole.")
        COUNCIL_MAX_PARTICIPANTS: int = Field(default=5, description="Nombre maximum de participants au conseil (hors synthétiseur).")
        COUNCIL_EXPERT_MAX_CALLS_PER_ROUND: int = Field(default=3, ge=0, le=10, description="Budget max d'appels d'outils par expert et par tour de conseil.")
        SUPERVISOR_MAX_CORRECTION_ROUNDS: int = Field(default=3, ge=1, le=5, description="Nombre maximum de tours de correction du superviseur.")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # ==========================================================================
    # 1. GESTION DES SKILLS
    # ==========================================================================

    async def forge_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        instructions: str,
        __user__: Optional[dict] = None
    ) -> str:
        """
        Créez ou modifiez une expertise cognitive (SKILL) au format SKILL.md.
        Pour un résultat optimal, concevez un profil riche : définissez un ton (ex: incisif), une méthodologie (ex: premiers principes), et des contraintes de sortie strictes. 
        Plus le Skill est détaillé, plus l'expert sera pertinent dans sa réflexion.
        
        :param skill_id: Identifiant unique du skill (ex: 'lead_dev_rust', 'expert_cyber').
        :param name: Nom lisible du rôle (ex: 'Lead Developer Rust').
        :param description: Brève description de l'expertise pour la découverte.
        :param instructions: Instructions système détaillées définissant le comportement du rôle.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        success = save_skill(user_id, skill_id, name, description, instructions)
        
        if success:
            return wrap_tool_output(text=f"✅ Skill '{name}' ({skill_id}) forgé avec succès.")
        return wrap_tool_output(text=f"❌ Échec de la forge du skill '{skill_id}'.")

    async def list_skills(
        self,
        __user__: Optional[dict] = None
    ) -> str:
        """
        Consultez la liste des expertises (SKILLS) disponibles pour les agents et conseils.
        Permet de découvrir les rôles déjà forgés et leurs descriptions.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        skills = get_all_skills(user_id)
        
        if not skills:
            return wrap_tool_output(text="ℹ️ Aucune expertise (Skill) n'est actuellement forgée.")
            
        res = "### EXPERTISES DISPONIBLES\n"
        for s in skills:
            res += f"- **ID:** `{s['id']}` | **Nom:** {s['name']}\n"
            res += f"  > *Description:* {s['description']}\n"
            
        return wrap_tool_output(text=res)

    # ==========================================================================
    # 2. CONSEIL D'EXPERTS (Protocole Delphi via delegate_to_agent)
    # ==========================================================================

    async def consult_council(
        self,
        question: str,
        participants: str,
        council_id: Optional[str] = None,
        target_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_PRO",
        synthesis_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_FLASH",
        rounds: Optional[int] = None,
        close_on_finish: bool = True,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Convoquez un conseil d'experts pour une délibération multi-tours.

        PRÉREQUIS : Un conseil exige AU MINIMUM 2 participants distincts.
        Avant d'appeler cet outil, vérifie via list_skills que tu disposes d'au
        moins 2 skills pertinents. Si ce n'est pas le cas, forge les skills
        manquants avec forge_skill AVANT de convoquer le conseil.

        Chaque expert est un agent ECHO autonome avec accès aux outils (web, codex, etc.).
        Il reçoit la question, utilise ses outils si besoin, puis réagit aux contributions
        des autres participants lors des tours suivants. Un synthétiseur produit la conclusion.

        :param question: La problématique ou question soumise au conseil.
        :param participants: Liste CSV des skill_ids (ex: "lead_dev,expert_secu,archi_cloud"). Min 2, Max 5.
        :param council_id: (Optionnel) Identifiant du conseil. Si omis, généré automatiquement.
        :param target_model: Modèle Gemini pour TOUS les experts (défaut: MODEL_PRO).
        :param synthesis_model: Modèle pour la synthèse finale (défaut: MODEL_FLASH).
        :param rounds: Nombre de tours de parole (défaut: 3, max: 5).
        :param close_on_finish: Si True, purge les sessions des experts à la fin (défaut: True).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        # ── Phase 0 : Validation & Chargement ──
        skill_ids = [s.strip() for s in participants.split(",") if s.strip()]

        max_p = self.user_valves.COUNCIL_MAX_PARTICIPANTS
        if len(skill_ids) > max_p:
            return wrap_tool_output(text=f"❌ Maximum {max_p} participants (reçu: {len(skill_ids)}).")
        if len(skill_ids) < 2:
            available = get_all_skills(user_id)
            skill_list = ", ".join(f"`{s['id']}`" for s in available) if available else "aucun"
            return wrap_tool_output(
                text=f"❌ Un conseil nécessite au minimum 2 participants (reçu: {len(skill_ids)}).\n\n"
                     f"**Skills disponibles :** {skill_list}\n\n"
                     f"→ Utilise `forge_skill` pour créer les experts manquants, puis rappelle `consult_council`.",
                status={"status": "error"}
            )

        effective_rounds = min(
            rounds or self.user_valves.COUNCIL_ROUNDS_DEFAULT,
            self.user_valves.COUNCIL_ROUNDS_MAX
        )

        # Chargement et validation de chaque skill
        roster = []
        for i, sid in enumerate(skill_ids):
            content = get_skill_content(user_id, sid)
            if not content:
                return wrap_tool_output(
                    text=f"❌ Skill '{sid}' introuvable. Utilisez forge_skill d'abord."
                )
            meta = parse_skill_metadata(content)
            roster.append({
                "skill_id": sid,
                "name": meta.get("name", sid),
                "instructions": content,
                "alias": f"Participant {i + 1}"
            })

        # Résolution du council_id
        cid = council_id or f"ccl_{uuid.uuid4().hex[:8]}"
        max_calls_per_round = self.user_valves.COUNCIL_EXPERT_MAX_CALLS_PER_ROUND

        await events.status(
            f"🏛️ Conseil [{cid}] convoqué : {len(roster)} experts, {effective_rounds} tours, "
            f"{max_calls_per_round} outils/tour..."
        )

        # ── Phase 1 : Résolution du agent_engine_tool ──
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls:
            return wrap_tool_output(text="❌ Module agent_engine_tool introuvable dans sys.modules.")
        delegate = _delegate_cls()
        # Propager les valves infra du parent
        delegate.valves.KEY_SWITCH_THRESHOLD = self.valves.KEY_SWITCH_THRESHOLD
        delegate.valves.MAX_RETRIES = self.valves.MAX_RETRIES

        # ── Phase 2 : Boucle des tours (protocole Delphi via delegate_to_agent) ──
        all_rounds: List[Dict[str, str]] = []
        council_sids: List[str] = []  # Pour la purge finale

        for round_num in range(1, effective_rounds + 1):
            await events.status(
                f"🏛️ Conseil [{cid}] — Tour {round_num}/{effective_rounds}..."
            )

            # Construction du prompt par participant
            if round_num == 1:
                base_prompt = f"### QUESTION SOUMISE AU CONSEIL\n{question}"
                round_prompts = {p["skill_id"]: base_prompt for p in roster}
            else:
                prev = all_rounds[-1]
                round_prompts = {}
                for p in roster:
                    sid = p["skill_id"]
                    others = "\n\n".join(
                        f"**{r['alias']}** :\n{prev[r['skill_id']]}"
                        for r in roster
                        if r["skill_id"] != sid and r["skill_id"] in prev
                    )
                    round_prompts[sid] = (
                        f"### CONTRIBUTIONS DU TOUR {round_num - 1}\n{others}\n\n"
                        f"### TA MISSION\n"
                        f"Tu es {p['alias']}. Réagis aux contributions ci-dessus. "
                        f"Exprime ton analyse, tes accords, tes désaccords et tes compléments."
                    )

            # Appels parallèles — chaque participant est un agent indépendant
            async def _call_participant(participant: dict, prompt: str) -> Tuple[str, str]:
                """Appelle un participant via delegate_to_agent et retourne (skill_id, texte)."""
                sid = participant["skill_id"]
                agent_sid = f"thread_council_{cid}_{sid}"
                if agent_sid not in council_sids:
                    council_sids.append(agent_sid)

                # Prompt système enrichi avec le contexte du conseil
                members = "\n".join(f"- {p['alias']} : {p['name']}" for p in roster)
                council_system = (
                    f"Tu es {participant['alias']} dans un conseil de {len(roster)} experts.\n\n"
                    f"### COMPOSITION DU CONSEIL\n{members}\n\n"
                    f"Tour {round_num}/{effective_rounds}. "
                    f"Budget outils ce tour : {max_calls_per_round} appels max.\n\n"
                    f"Les experts ne connaissent pas les instructions détaillées "
                    f"des autres participants. Tu ne connais que leur rôle déclaré ci-dessus."
                )

                result = await delegate.delegate_to_agent(
                    task=prompt,
                    system_prompt=council_system,
                    role_name=sid,
                    sub_sid=agent_sid,
                    target_model_key=target_model,
                    max_calls_override=max_calls_per_round,
                    __user__=__user__,
                    __chat_id__=__chat_id__,
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

                return sid, text

            tasks = [
                _call_participant(p, round_prompts[p["skill_id"]])
                for p in roster
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Collecte — un participant qui échoue ne bloque pas le conseil
            round_responses: Dict[str, str] = {}
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    sid = roster[i]["skill_id"]
                    alias = roster[i]["alias"]
                    round_responses[sid] = f"⚠️ {alias} n'a pas pu répondre."
                    if self.valves.DEBUG_COUNCIL:
                        print(f"[Council] {alias} error: {result}")
                else:
                    sid, text = result
                    round_responses[sid] = text or "(Pas de réponse)"

            all_rounds.append(round_responses)

        # ── Phase 3 : Synthèse finale ──
        await events.status("📝 Synthèse du conseil en cours...")

        transcript = f"### QUESTION\n{question}\n\n"
        for rnd, responses in enumerate(all_rounds, 1):
            transcript += f"---\n### TOUR {rnd}\n\n"
            for p in roster:
                sid = p["skill_id"]
                if sid in responses:
                    transcript += f"**{p['alias']}** ({p['name']}) :\n{responses[sid]}\n\n"

        synthesis_system = (
            "Tu es le rapporteur du conseil. Tu n'es pas un participant. "
            "Tu produis une synthèse structurée, objective et actionnable "
            "de la délibération. Identifie les consensus, les divergences et "
            "les recommandations clés. Sois exhaustif mais concis."
        )

        synthesis_payload = {
            "contents": [{"role": "user", "parts": [{"text": transcript}]}],
            "systemInstruction": {"parts": [{"text": synthesis_system}]},
            "generationConfig": {
                "temperature": TEMP_DISTILLATION,
                "topP": TOP_P_DISTILLATION,
                "maxOutputTokens": 16000,
            }
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
            state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
            for agent_sid in council_sids:
                state.delete_thread(agent_sid)

        roster_summary = ", ".join(f"{p['alias']} ({p['name']})" for p in roster)
        await events.status("🏛️ Conseil terminé.", done=True)
        return wrap_tool_output(
            text=(
                f"### SYNTHÈSE DU CONSEIL [{cid}] ({len(roster)} experts, {effective_rounds} tours)\n"
                f"**Participants** : {roster_summary}\n\n"
                f"{synthesis_text}"
            )
        )

    # ==========================================================================
    # 3. SUPERVISEUR (Boucle critique / correction récursive)
    # ==========================================================================

    async def consult_supervised_workers(
        self,
        objective: str,
        workers: str,
        target_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_FLASH",
        critic_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_PRO",
        max_correction_rounds: Optional[int] = None,
        close_on_finish: bool = True,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Orchestre plusieurs agents sur un objectif commun avec contrôle qualité récursif.

        Processus en 4 phases :
        1. DÉLÉGATION : Chaque worker exécute sa tâche via delegate_to_agent.
        2. ÉVALUATION : Un critique analyse tous les livrables et rend un verdict.
        3. CORRECTION : Les workers fautifs sont relancés avec le feedback du critique.
        4. CONSOLIDATION : Synthèse finale des livrables approuvés.

        Le critique retourne un JSON structuré :
        {"verdict": "APPROVED"|"REJECTED", "global_assessment": "...",
         "worker_feedback": {"worker_id": {"status": "ok"|"needs_correction", "feedback": "..."}}}

        :param objective: La mission globale à accomplir.
        :param workers: JSON dict. Chaque entrée :
                        - Clé = identifiant du worker.
                        - Valeur = dict avec "task" (obligatoire), "role_name" (Skill, optionnel)
                          et/ou "system_prompt" (optionnel si role_name fourni).
                        Exemple : {"dev": {"task": "Implémenter X", "role_name": "lead_dev"},
                                   "review": {"task": "Vérifier X", "system_prompt": "Tu es un reviewer strict."}}
        :param target_model: Modèle pour les workers (défaut: MODEL_FLASH).
        :param critic_model: Modèle pour l'évaluation critique (défaut: MODEL_PRO).
        :param max_correction_rounds: Surcharge la UserValve SUPERVISOR_MAX_CORRECTION_ROUNDS.
        :param close_on_finish: Si True, purge les sessions des workers à la fin.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        # Parsing du dict workers
        try:
            workers_dict = json.loads(workers) if isinstance(workers, str) else workers
        except Exception as e:
            return wrap_tool_output(text=f"❌ Format JSON invalide pour workers : {e}")

        if not workers_dict or not isinstance(workers_dict, dict):
            return wrap_tool_output(text="❌ workers doit être un dict JSON non vide.")

        task_id = f"sup_{uuid.uuid4().hex[:8]}"
        max_rounds = max_correction_rounds or self.user_valves.SUPERVISOR_MAX_CORRECTION_ROUNDS

        await events.status(
            f"📋 Superviseur [{task_id}] : {len(workers_dict)} workers, max {max_rounds} corrections..."
        )

        # Résolution du agent_engine_tool
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls:
            return wrap_tool_output(text="❌ Module agent_engine_tool introuvable dans sys.modules.")
        delegate = _delegate_cls()
        delegate.valves.KEY_SWITCH_THRESHOLD = self.valves.KEY_SWITCH_THRESHOLD
        delegate.valves.MAX_RETRIES = self.valves.MAX_RETRIES

        worker_sids = {}  # worker_id → sub_sid
        deliverables = {}  # worker_id → texte du livrable

        # ── Phase 1 : Délégation initiale ──
        await events.status(f"📋 [{task_id}] Phase 1 — Délégation...")

        for w_id, w_config in workers_dict.items():
            w_task = w_config.get("task", "")
            w_role = w_config.get("role_name")
            w_sys = w_config.get("system_prompt", f"Tu es un agent chargé de : {w_task}")
            agent_sid = f"thread_supervisor_{task_id}_{w_id}"
            worker_sids[w_id] = agent_sid

            result = await delegate.delegate_to_agent(
                task=f"### OBJECTIF GLOBAL\n{objective}\n\n### TA TÂCHE\n{w_task}",
                system_prompt=w_sys,
                role_name=w_role,
                sub_sid=agent_sid,
                target_model_key=target_model,
                __user__=__user__,
                __chat_id__=__chat_id__,
                __metadata__=__metadata__,
                __event_emitter__=__event_emitter__,
                __event_call__=__event_call__,
            )

            # Extraction du texte
            if isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    deliverables[w_id] = parsed.get("text", result)
                except Exception:
                    deliverables[w_id] = result
            elif isinstance(result, dict):
                deliverables[w_id] = result.get("text", str(result))
            else:
                deliverables[w_id] = str(result)

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
            critic_prompt = (
                f"### OBJECTIF GLOBAL\n{objective}\n\n"
                f"### LIVRABLES DES WORKERS\n{deliverables_text}\n\n"
                f"### TA MISSION\n"
                f"Évalue la qualité et la cohérence de chaque livrable par rapport à l'objectif global.\n"
                f"Retourne UNIQUEMENT un JSON valide (pas de markdown, pas de commentaire) :\n"
                f'{{"verdict": "APPROVED"|"REJECTED", "global_assessment": "...", '
                f'"worker_feedback": {{"worker_id": {{"status": "ok"|"needs_correction", "feedback": "..."}}}}}}'
            )

            critic_res, _, _ = await EchoGeminiClient.call_cascade(
                target_model_key=critic_model,
                payload={
                    "contents": [{"role": "user", "parts": [{"text": critic_prompt}]}],
                    "generationConfig": {
                        "temperature": TEMP_DISTILLATION,
                        "topP": TOP_P_DISTILLATION,
                    }
                },
                user_id=user_id,
                metadata=(__metadata__ or {}),
                events=events,
                timeout=self.valves.COGNITIVE_TIMEOUT,
                include_thoughts=False,
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
                    if "text" in p
                )

            try:
                # Nettoyage : extraire le JSON du texte (le modèle peut ajouter des backticks)
                json_match = critic_text.strip()
                if json_match.startswith("```"):
                    json_match = json_match.split("\n", 1)[-1].rsplit("```", 1)[0]
                verdict_data = json.loads(json_match)
            except Exception:
                await events.status(f"⚠️ [{task_id}] Verdict non parseable — validation forcée.", done=True)
                break

            verdict = verdict_data.get("verdict", "APPROVED")
            if verdict == "APPROVED":
                await events.status(f"✅ [{task_id}] Verdict : APPROVED (round {correction_round})")
                break

            # REJECTED — Phase 3 : Correction
            worker_feedback = verdict_data.get("worker_feedback", {})
            workers_to_correct = [
                w_id for w_id, fb in worker_feedback.items()
                if isinstance(fb, dict) and fb.get("status") == "needs_correction"
            ]

            if not workers_to_correct:
                break  # REJECTED mais personne à corriger

            await events.status(
                f"📋 [{task_id}] Phase 3 — Correction de {len(workers_to_correct)} workers..."
            )

            for w_id in workers_to_correct:
                if w_id not in worker_sids:
                    continue
                feedback = worker_feedback[w_id].get("feedback", "Corrige ton livrable.")
                agent_sid = worker_sids[w_id]

                # Reprise du même sub_sid (continuité de contexte)
                w_config = workers_dict.get(w_id, {})
                w_role = w_config.get("role_name")
                w_sys = w_config.get("system_prompt", "")

                result = await delegate.delegate_to_agent(
                    task=f"### FEEDBACK DU CRITIQUE\n{feedback}\n\n### OBJECTIF\nCorrige ton livrable précédent.",
                    system_prompt=w_sys,
                    role_name=w_role,
                    sub_sid=agent_sid,
                    target_model_key=target_model,
                    __user__=__user__,
                    __chat_id__=__chat_id__,
                    __metadata__=__metadata__,
                    __event_emitter__=__event_emitter__,
                    __event_call__=__event_call__,
                )

                if isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        deliverables[w_id] = parsed.get("text", result)
                    except Exception:
                        deliverables[w_id] = result
                elif isinstance(result, dict):
                    deliverables[w_id] = result.get("text", str(result))
                else:
                    deliverables[w_id] = str(result)

        # ── Phase 4 : Consolidation ──
        await events.status(f"📋 [{task_id}] Phase 4 — Consolidation...")

        consolidation_text = "\n\n---\n\n".join(
            f"### WORKER: {w_id}\n{text}" for w_id, text in deliverables.items()
        )
        consolidation_prompt = (
            f"### OBJECTIF\n{objective}\n\n"
            f"### LIVRABLES FINAUX\n{consolidation_text}\n\n"
            f"### TA MISSION\n"
            f"Produis une synthèse consolidée et actionnable de tous les livrables. "
            f"Fusionne les résultats, élimine les redondances, et structure la réponse finale."
        )

        consolidation_res, _, _ = await EchoGeminiClient.call_cascade(
            target_model_key=critic_model,
            payload={
                "contents": [{"role": "user", "parts": [{"text": consolidation_prompt}]}],
                "generationConfig": {
                    "temperature": TEMP_DISTILLATION,
                    "topP": TOP_P_DISTILLATION,
                    "maxOutputTokens": 16000,
                }
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
            state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
            for agent_sid in worker_sids.values():
                state.delete_thread(agent_sid)

        await events.status(f"📋 Superviseur [{task_id}] terminé.", done=True)
        return wrap_tool_output(
            text=(
                f"### RÉSULTAT SUPERVISÉ [{task_id}] ({len(workers_dict)} workers, "
                f"{correction_round} round(s) de critique)\n\n{final_text}"
            )
        )

    # ==========================================================================
    # 4. OUTILS D'ADMINISTRATION (Conseils & Superviseurs)
    # ==========================================================================

    async def list_councils(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
    ) -> str:
        """
        Liste les conseils d'experts actifs (non clôturés) pour ce chat.

        :return: Markdown listant les conseils avec leurs participants et état.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"})

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)

        councils: Dict[str, list] = {}
        for t in threads:
            sid = t["sub_sid"]
            if sid.startswith("thread_council_"):
                # thread_council_{council_id}_{skill_id}
                parts = sid.split("_", 3)  # ['thread', 'council', '{id}', '{skill}']
                if len(parts) >= 4:
                    cid = parts[2]
                    councils.setdefault(cid, []).append(t)

        if not councils:
            return wrap_tool_output(text="ℹ️ Aucun conseil actif.", status={"status": "success"})

        md = "### 🏛️ CONSEILS ACTIFS\n\n"
        for cid, members in councils.items():
            skills = [t["sub_sid"].split("_", 3)[3] if len(t["sub_sid"].split("_", 3)) >= 4 else "?" for t in members]
            md += f"- **ID:** `{cid}` | **Participants:** {', '.join(skills)} | **Sessions:** {len(members)}\n"

        return wrap_tool_output(text=md, status={"status": "success", "councils": list(councils.keys())})

    async def close_council(
        self,
        council_id: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
    ) -> str:
        """
        Ferme définitivement un conseil et purge toutes les sessions de ses participants.

        :param council_id: Identifiant du conseil à fermer.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"})

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        prefix = f"thread_council_{council_id}_"
        to_delete = [t["sub_sid"] for t in threads if t["sub_sid"].startswith(prefix)]

        if not to_delete:
            return wrap_tool_output(text=f"❌ Conseil '{council_id}' introuvable.", status={"status": "error"})

        for sid in to_delete:
            state.delete_thread(sid)

        return wrap_tool_output(
            text=f"✅ Conseil `{council_id}` fermé ({len(to_delete)} sessions purgées).",
            status={"status": "success"}
        )

    async def list_supervised_tasks(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
    ) -> str:
        """
        Liste les tâches supervisées actives pour ce chat.

        :return: Markdown listant les tâches avec leurs workers et état.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"})

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)

        tasks: Dict[str, list] = {}
        for t in threads:
            sid = t["sub_sid"]
            if sid.startswith("thread_supervisor_"):
                parts = sid.split("_", 3)  # ['thread', 'supervisor', '{task_id}', '{worker}']
                if len(parts) >= 4:
                    tid = parts[2]
                    tasks.setdefault(tid, []).append(t)

        if not tasks:
            return wrap_tool_output(text="ℹ️ Aucune tâche supervisée active.", status={"status": "success"})

        md = "### 📋 TÂCHES SUPERVISÉES ACTIVES\n\n"
        for tid, workers in tasks.items():
            worker_ids = [t["sub_sid"].split("_", 3)[3] if len(t["sub_sid"].split("_", 3)) >= 4 else "?" for t in workers]
            md += f"- **ID:** `{tid}` | **Workers:** {', '.join(worker_ids)} | **Sessions:** {len(workers)}\n"

        return wrap_tool_output(text=md, status={"status": "success", "tasks": list(tasks.keys())})

    async def close_supervised_task(
        self,
        task_id: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
    ) -> str:
        """
        Ferme définitivement une tâche supervisée et purge toutes les sessions de ses workers.

        :param task_id: Identifiant de la tâche supervisée à fermer.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"})

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        prefix = f"thread_supervisor_{task_id}_"
        to_delete = [t["sub_sid"] for t in threads if t["sub_sid"].startswith(prefix)]

        if not to_delete:
            return wrap_tool_output(text=f"❌ Tâche '{task_id}' introuvable.", status={"status": "error"})

        for sid in to_delete:
            state.delete_thread(sid)

        return wrap_tool_output(
            text=f"✅ Tâche supervisée `{task_id}` fermée ({len(to_delete)} sessions purgées).",
            status={"status": "success"}
        )
