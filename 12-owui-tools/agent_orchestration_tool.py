"""
title: ECHO Agent Orchestration
author: ECHO Framework
version: 5.26
description: Composant système interne : ECHO Agent Orchestration.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.23: Précision "multi-agentique" dans la docstring de consult_supervised_workers.
# 5.22: Ajout des recommandations (forge_skill et strategic_planner) dans la docstring de consult_supervised_workers.
# 5.21: Ajout du cas d'usage et de l'objectif dans la docstring de consult_supervised_workers.
# 5.20: Ajout du cas d'usage (sujets complexes/multidimensionnels) dans la docstring de consult_council.
# 5.19: council_id obligatoire, limite [p*r] via UserValve COUNCIL_MAX_PR_COMPLEXITY.
# 5.24: Ajout des arguments manquant (__metadata__, __user__) dans l'interface pour garantir l'injection.
# 5.25: Nettoyage du code : suppression des imports inutilisés (PEP8).

import sys
import orjson as json
import asyncio
import uuid
import datetime
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal, Tuple

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents, EchoGeminiClient, EchoStateManager
from echo_constants import (
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES, get_generation_config
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
        COUNCIL_MAX_PR_COMPLEXITY: int = Field(default=30, description="Complexité maximale (participants * rounds) d'un conseil.")
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
        __user__: Optional[dict] = None,
        __metadata__: dict = {}
    ) -> str:
        """Création/Mise à jour d'une expertise (Skill). Requis avant appel d'un agent inexistant.
        :param skill_id: Identifiant technique (snake_case).
        :param name: Titre lisible.
        :param instructions: Directives système détaillées.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        success = save_skill(user_id, skill_id, name, description, instructions)
        
        if success:
            return wrap_tool_output(text=f"✅ Skill '{name}' ({skill_id}) forgé avec succès.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        return wrap_tool_output(text=f"❌ Échec de la forge du skill '{skill_id}'.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def list_skills(
        self,
        __user__: Optional[dict] = None,
        __metadata__: dict = {}
    ) -> str:
        """Permet au Modèle de lister les expertises (Skills) forgées pour obtenir les skill_id valides avant délégation."""
        user_id = __user__.get("id", "system") if __user__ else "system"
        skills = get_all_skills(user_id)
        
        if not skills:
            return wrap_tool_output(text="ℹ️ Aucune expertise (Skill) n'est actuellement forgée.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            
        res = "### EXPERTISES DISPONIBLES\n"
        for s in skills:
            res += f"- **ID:** `{s['id']}` | **Nom:** {s['name']}\n"
            res += f"  > *Description:* {s['description']}\n"
            
        return wrap_tool_output(text=res, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    # ==========================================================================
    # 2. CONSEIL D'EXPERTS (Protocole Delphi via delegate_to_agent)
    # ==========================================================================

    async def consult_council(
        self,
        question: str,
        participants: List[str],
        council_id: str,
        target_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_PRO",
        synthesis_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_FLASH",
        rounds: Optional[int] = None,
        close_on_finish: bool = False,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Table ronde (N experts, tours parallèles). CAS D'USAGE PRINCIPAL : Lorsqu'un sujet est complexe, multidimensionnel ou incertain, cet outil permet un débat structuré entre plusieurs spécialistes pour dégager une solution consensuelle ou exhaustive.
        Rapport exhaustif multi-perspectives. Minimum 2 experts requis. IMPLIQUE appel à `forge_skill` si experts manquants.
        Le conseil reste ouvert (close_on_finish=False) pour permettre de le relancer avec le même council_id. Le Modèle DOIT utiliser close_council une fois la délibération définitivement terminée.
        DIRECTIVE ORCHESTRATEUR: Le résultat n'est pas automatiquement affiché. Le Modèle appelant DOIT restituer l'intégralité du rapport dans sa réponse finale.
        
        :param question: Sujet de délibération.
        :param participants: Chaîne CSV de `skill_id` (ex: expert_1, dev_py) (min 2, [ p * r ] <= COUNCIL_MAX_PR_COMPLEXITY).
        :param council_id: Identifiant obligatoire pour conserver et reprendre le conseil plus tard.
        :param rounds: Nombre d'itérations ([ p * r ] <= COUNCIL_MAX_PR_COMPLEXITY).
        :param close_on_finish: False par défaut pour reprise de contexte. Mettre à True pour détruire immédiatement.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # ── Phase 0 : Validation & Chargement ──
        skill_ids = [s.strip() for s in participants if s.strip()]

        max_p = self.user_valves.COUNCIL_MAX_PARTICIPANTS
        if len(skill_ids) > max_p:
            return wrap_tool_output(text=f"❌ Maximum {max_p} participants (reçu: {len(skill_ids)}).", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        if len(skill_ids) < 2:
            available = get_all_skills(user_id)
            skill_list = ", ".join(f"`{s['id']}`" for s in available) if available else "aucun"
            return wrap_tool_output(
                text=f"❌ Conseil : Minimum 2 participants (Reçu: {len(skill_ids)}).\n\n"
                     f"**Skills disponibles :** {skill_list}\n\n"
                     f"→ Si expert/skill manquant, appeler d'abord `forge_skill`.",
                status={"status": "error"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        effective_rounds = min(
            rounds or self.user_valves.COUNCIL_ROUNDS_DEFAULT,
            self.user_valves.COUNCIL_ROUNDS_MAX
        )

        if len(skill_ids) * effective_rounds > self.user_valves.COUNCIL_MAX_PR_COMPLEXITY:
            return wrap_tool_output(
                text=f"❌ La complexité (participants * rounds = {len(skill_ids) * effective_rounds}) dépasse la limite autorisée ({self.user_valves.COUNCIL_MAX_PR_COMPLEXITY}).",
                status={"status": "error"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Chargement et validation de chaque skill
        roster = []
        for i, sid in enumerate(skill_ids):
            content = get_skill_content(user_id, sid)
            if not content:
                return wrap_tool_output(
                    text=f"❌ Skill '{sid}' introuvable. Le Modèle DOIT utiliser l'outil forge_skill au préalable."
                , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            meta = parse_skill_metadata(content)
            roster.append({
                "skill_id": sid,
                "name": meta.get("name", sid),
                "instructions": content,
                "alias": f"Participant {i + 1}"
            })

        # Résolution du council_id
        cid = council_id
        max_calls_per_round = self.user_valves.COUNCIL_EXPERT_MAX_CALLS_PER_ROUND

        await events.status(
            f"🏛️ Conseil [{cid}] convoqué : {len(roster)} experts, {effective_rounds} tours, "
            f"{max_calls_per_round} outils/tour..."
        )

        # ── Phase 1 : Résolution du agent_engine_tool ──
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls:
            return wrap_tool_output(text="❌ Module agent_engine_tool introuvable dans sys.modules.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
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
                        f"### MISSION DU MODÈLE\n"
                        f"Le Modèle est {p['alias']}. Il DOIT réagir aux contributions ci-dessus. "
                        f"Il DOIT exprimer son analyse, ses accords, ses désaccords et ses compléments."
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
                current_time = datetime.datetime.now().isoformat()
                council_system = (
                    f"<persona>\n"
                    f"Le Modèle agit en tant que {participant['alias']} au sein d'un conseil composé de {len(roster)} experts.\n"
                    f"Ton : Professionnel, technique, sec. Le Modèle proscrit toute formule de politesse ou d'introduction (\"Bonjour\", \"Voici mon analyse\").\n"
                    f"</persona>\n\n"
                    f"<composition_conseil>\n"
                    f"{members}\n"
                    f"- Confidentialité : Le Modèle ignore les instructions détaillées (le code du Skill) des autres participants.\n"
                    f"</composition_conseil>\n\n"
                    f"<parametres_tour>\n"
                    f"- Tour actuel : {round_num}/{effective_rounds}.\n"
                    f"- Budget d'outils : {max_calls_per_round} appels maximum ce tour.\n"
                    f"</parametres_tour>\n\n"
                    f"<directives_rigueur>\n"
                    f"- Rigueur Factuelle : Le Modèle DOIT asseoir son raisonnement sur des certitudes.\n"
                    f"- Budget Maîtrisé : Si des outils de recherche sont disponibles, leur utilisation est ABSOLUMENT réservée à la levée d'un doute critique, la mise à jour temporelle d'une connaissance, la validation d'un pivot factuel, ou la réfutation d'une affirmation d'un autre expert. Le Modèle ne doit pas consommer son budget pour des faits triviaux.\n"
                    f"</directives_rigueur>\n\n"
                    f"<context_temporel>{current_time}</context_temporel>\n\n"
                    f"<format_reponse>\n"
                    f"Le Modèle DOIT structurer sa contribution EXCLUSIVEMENT avec les sections Markdown suivantes :\n\n"
                    f"### Analyse\n"
                    f"(Décorticage froid et technique des éléments soumis au conseil).\n\n"
                    f"### Dialectique\n"
                    f"(Positionnement critique face aux contributions précédentes : accords, désaccords justifiés, failles logiques identifiées chez les autres experts).\n\n"
                    f"### Réponse\n"
                    f"(Recommandation, solution ou conclusion propre à l'expertise du Modèle pour ce tour).\n"
                    f"</format_reponse>"
                )

                result = await delegate.delegate_to_agent(
                    task=prompt,
                    system_prompt=council_system,
                    skill_id=sid,
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
            "<persona>\n"
            "Le Modèle est le rapporteur officiel du conseil. Il n'est pas un participant, son ton est neutre et factuel.\n"
            "</persona>\n\n"
            "<mission>\n"
            "Le Modèle DOIT produire un rapport exhaustif et détaillé (et non une simple synthèse lissée) de l'ensemble de la délibération.\n"
            "Il DOIT retranscrire fidèlement l'intégralité de la substance des arguments de chaque expert, en isolant clairement les points d'accord et les zones de friction ou de désaccord.\n"
            "Le livrable final doit ressembler à un rapport de commission technique complet avant d'énoncer les recommandations finales.\n"
            "</mission>"
        )

        synthesis_payload = {
            "contents": [{"role": "user", "parts": [{"text": transcript}]}],
            "systemInstruction": {"parts": [{"text": synthesis_system}]},
            "generationConfig": {**get_generation_config("MODEL_DISTILLATION"), "maxOutputTokens": 16000}
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
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    # ==========================================================================
    # 3. SUPERVISEUR (Boucle critique / correction récursive)
    # ==========================================================================

    async def consult_supervised_workers(
        self,
        objective: str,
        workers: dict,
        target_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_FLASH",
        critic_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_PRO",
        max_correction_rounds: Optional[int] = None,
        close_on_finish: bool = False,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Boucle multi-agentique itérative et asynchrone avec supervision critique (Délégation, Évaluation, Correction, Consolidation).
        CAS D'USAGE PRINCIPAL : Délégation de tâches exigeant une qualité irréprochable (génération de code critique, architecture logicielle, rédaction formelle complexe).
        POURQUOI : Garantit via un superviseur indépendant que chaque livrable respecte strictement l'objectif par validations croisées avant consolidation, tout en préservant le contexte de l'Orchestrateur.
        IMPLIQUE appel à `forge_skill` si les experts assignés sont manquants.
        FORTEMENT RECOMMANDÉ : Inscrire cet objectif dans un plan formel via le `strategic_planner` (`build_plan` / `update_plan`) avant de lancer la délégation.
        La tâche reste ouverte par défaut (close_on_finish=False). Le Modèle DOIT utiliser close_supervised_task une fois définitivement terminée.
        
        :param objective: Mission globale.
        :param workers: Mapping JSON {worker_id_libre: {"task": "...", "skill_id": "..."}}.
        :param close_on_finish: False par défaut pour reprise de contexte. Mettre à True pour détruire immédiatement.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Parsing du dict workers
        try:
            workers_dict = json.loads(workers) if isinstance(workers, str) else workers
        except Exception as e:
            return wrap_tool_output(text=f"❌ Format JSON invalide pour workers : {e}", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        if not workers_dict or not isinstance(workers_dict, dict):
            return wrap_tool_output(text="❌ workers doit être un dict JSON non vide.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        task_id = f"sup_{uuid.uuid4().hex[:8]}"
        max_rounds = max_correction_rounds or self.user_valves.SUPERVISOR_MAX_CORRECTION_ROUNDS

        await events.status(
            f"📋 Superviseur [{task_id}] : {len(workers_dict)} workers, max {max_rounds} corrections..."
        )

        # Résolution du agent_engine_tool
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls:
            return wrap_tool_output(text="❌ Module agent_engine_tool introuvable dans sys.modules.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        delegate = _delegate_cls()
        delegate.valves.KEY_SWITCH_THRESHOLD = self.valves.KEY_SWITCH_THRESHOLD
        delegate.valves.MAX_RETRIES = self.valves.MAX_RETRIES

        worker_sids = {}  # worker_id → sub_sid
        deliverables = {}  # worker_id → texte du livrable

        # ── Phase 1 : Délégation initiale ──
        await events.status(f"📋 [{task_id}] Phase 1 — Délégation...")

        for w_id, w_config in workers_dict.items():
            w_task = w_config.get("task", "")
            w_role = w_config.get("skill_id")
            w_sys = w_config.get("system_prompt", f"Tu es un agent chargé de : {w_task}")
            agent_sid = f"thread_supervisor_{task_id}_{w_id}"
            worker_sids[w_id] = agent_sid

            result = await delegate.delegate_to_agent(
                task=f"### OBJECTIF GLOBAL\n{objective}\n\n### TA TÂCHE\n{w_task}",
                system_prompt=w_sys,
                skill_id=w_role,
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
            import datetime
            current_time = datetime.datetime.now().isoformat()
            
            critic_prompt = (
                "<persona>\n"
                "Le Modèle est un évaluateur critique. Ton : Professionnel, analytique, sec. Proscrire toute formule de politesse.\n"
                "</persona>\n\n"
                f"<context_temporel>{current_time}</context_temporel>\n\n"
                "<directives_rigueur>\n"
                "- Limite d'Expertise : N'étant pas nécessairement l'expert métier, le Modèle DOIT concentrer son évaluation sur la cohérence interne, la logique, et le respect strict des objectifs.\n"
                "- Exigence d'Évidences : Tout livrable contenant des affirmations vagues, contradictoires, incohérentes ou hors sujet DOIT entraîner un verdict REJECTED avec une consigne claire de clarification pour le travailleur.\n"
                "</directives_rigueur>\n\n"
                "<mission>\n"
                "Le Modèle doit évaluer la qualité, la logique formelle et la pertinence sémantique de chaque livrable fourni par les travailleurs (workers), par rapport à l'objectif global.\n"
                "</mission>\n\n"
                f"<objective>\n{objective}\n</objective>\n\n"
                f"<deliverables>\n{deliverables_text}\n</deliverables>\n\n"
                "<rules>\n"
                "1. Le Modèle DOIT analyser méticuleusement chaque livrable.\n"
                "2. Le Modèle DOIT identifier formellement toute erreur logique, omission ou déviation de l'objectif.\n"
                "3. FORMAT : Le Modèle DOIT retourner UNIQUEMENT un objet JSON valide, SANS bloc Markdown englobant (pas de ```json).\n"
                "</rules>\n\n"
                "<output_format>\n"
                "Le Modèle DOIT respecter STRICTEMENT ce schéma JSON exact :\n"
                "<example>\n"
                "{\n"
                '  "global_assessment": "(RÉFLEXION) Analyse des résultats, identification des points faibles et justification logique du verdict.",\n'
                '  "verdict": "APPROVED",\n'
                '  "worker_feedback": {\n'
                '    "worker_id_1": {\n'
                '      "status": "ok",\n'
                '      "feedback": "Directives précises pour la correction..."\n'
                "    }\n"
                "  }\n"
                "}\n"
                "</example>\n"
                "</output_format>"
            )
            critic_res, _, _ = await EchoGeminiClient.call_cascade(
                target_model_key=critic_model,
                payload={
                    "contents": [{"role": "user", "parts": [{"text": critic_prompt}]}],
                    "generationConfig": get_generation_config("MODEL_DISTILLATION"),
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
                w_role = w_config.get("skill_id", w_config.get("role_name"))
                w_sys = w_config.get("system_prompt", "")

                result = await delegate.delegate_to_agent(
                    task=f"### FEEDBACK DU CRITIQUE\n{feedback}\n\n### OBJECTIF\nLe Modèle DOIT corriger son livrable précédent.",
                    system_prompt=w_sys,
                    skill_id=w_role,
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
        import datetime
        current_time = datetime.datetime.now().isoformat()
        
        consolidation_prompt = (
            "<persona>\n"
            "Le Modèle est un architecte intégrateur expert. Ton : Professionnel, technique, sec. Proscrire toute formule de politesse.\n"
            "</persona>\n\n"
            f"<context_temporel>{current_time}</context_temporel>\n\n"
            "<directives_rigueur>\n"
            "- Intégrité des Données : Le Modèle DOIT s'en tenir strictement et exclusivement aux informations factuelles fournies dans les livrables validés.\n"
            "- Précision : Aucune invention, supposition ou extrapolation n'est tolérée. La redondance doit être éliminée avec concision.\n"
            "</directives_rigueur>\n\n"
            "<mission>\n"
            "Le Modèle DOIT produire une synthèse consolidée et actionnable de tous les livrables. Il DOIT fusionner les résultats, éliminer les redondances, et structurer la réponse finale de manière cohérente.\n"
            "</mission>\n\n"
            f"<objective>\n{objective}\n</objective>\n\n"
            f"<deliverables_finaux>\n{consolidation_text}\n</deliverables_finaux>"
        )

        consolidation_res, _, _ = await EchoGeminiClient.call_cascade(
            target_model_key=critic_model,
            payload={
                "contents": [{"role": "user", "parts": [{"text": consolidation_prompt}]}],
                "generationConfig": {**get_generation_config("MODEL_DISTILLATION"), "maxOutputTokens": 16000},
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
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    # ==========================================================================
    # 4. OUTILS D'ADMINISTRATION (Conseils & Superviseurs)
    # ==========================================================================

    async def list_councils(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """
        Liste les conseils d'experts actifs (non clôturés) pour ce chat.

        :return: Markdown listant les conseils avec leurs participants et état.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
            return wrap_tool_output(text="ℹ️ Aucun conseil actif.", status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        md = "### 🏛️ CONSEILS ACTIFS\n\n"
        for cid, members in councils.items():
            skills = [t["sub_sid"].split("_", 3)[3] if len(t["sub_sid"].split("_", 3)) >= 4 else "?" for t in members]
            md += f"- **ID:** `{cid}` | **Participants:** {', '.join(skills)} | **Sessions:** {len(members)}\n"

        return wrap_tool_output(text=md, status={"status": "success", "councils": list(councils.keys())}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def close_council(
        self,
        council_id: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """
        Ferme définitivement un conseil et purge toutes les sessions de ses participants.

        :param council_id: Identifiant du conseil à fermer.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        prefix = f"thread_council_{council_id}_"
        to_delete = [t["sub_sid"] for t in threads if t["sub_sid"].startswith(prefix)]

        if not to_delete:
            return wrap_tool_output(text=f"❌ Conseil '{council_id}' introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        for sid in to_delete:
            state.delete_thread(sid)

        return wrap_tool_output(
            text=f"✅ Conseil `{council_id}` fermé ({len(to_delete)} sessions purgées).",
            status={"status": "success"}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def list_supervised_tasks(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """
        Liste les tâches supervisées actives pour ce chat.

        :return: Markdown listant les tâches avec leurs workers et état.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
            return wrap_tool_output(text="ℹ️ Aucune tâche supervisée active.", status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        md = "### 📋 TÂCHES SUPERVISÉES ACTIVES\n\n"
        for tid, workers in tasks.items():
            worker_ids = [t["sub_sid"].split("_", 3)[3] if len(t["sub_sid"].split("_", 3)) >= 4 else "?" for t in workers]
            md += f"- **ID:** `{tid}` | **Workers:** {', '.join(worker_ids)} | **Sessions:** {len(workers)}\n"

        return wrap_tool_output(text=md, status={"status": "success", "tasks": list(tasks.keys())}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def close_supervised_task(
        self,
        task_id: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: dict = {},
    ) -> str:
        """
        Ferme définitivement une tâche supervisée et purge toutes les sessions de ses workers.

        :param task_id: Identifiant de la tâche supervisée à fermer.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        prefix = f"thread_supervisor_{task_id}_"
        to_delete = [t["sub_sid"] for t in threads if t["sub_sid"].startswith(prefix)]

        if not to_delete:
            return wrap_tool_output(text=f"❌ Tâche '{task_id}' introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        for sid in to_delete:
            state.delete_thread(sid)

        return wrap_tool_output(
            text=f"✅ Tâche supervisée `{task_id}` fermée ({len(to_delete)} sessions purgées).",
            status={"status": "success"}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
