"""
title: ECHO Strategic Planner
author: ECHO Framework
version: 1.13
description: Composant système interne : ECHO Strategic Planner.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.13: Refonte UX complète (zéro émoji), statut "proposed" par défaut, retrait des modales build/update, et ajout de l'outil analyze_plan.
# 1.11: Affichage des instructions de pointage dans l'évènement UI de update_tasks.
# 1.9: Création de process_plan, modale optionnelle, verrouillage frontend de update_plan contre les démarrages illicites.
# 1.6: Nettoyage du code : suppression des imports inutilisés (PEP8).
# 1.5: Refonte des system prompts (BUILD/UPDATE) avec balises XML, exemples yaml et ton impersonnel.
# 1.4: Registre Unifié V2 — Plans stockés dans le Codex (Git) au lieu du dossier plans/.

import sys
import orjson as json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional, Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output, wrap_cascade_output
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_prompts import (
    SYS_PLANNER_BUILD, SYS_PLANNER_UPDATE,
    SYS_PLANNER_UPDATE_TASKS, SYS_PLANNER_ANALYZE
)
from echo_codex_git import CodexRepo
from echo_ui import EchoUI
from echo_constants import (
    PLAN_STATUS, get_generation_config,
    PLANNER_MODEL_BUILD, PLANNER_MODEL_UPDATE,
)


# ==============================================================================
# PROMPTS SYSTÈME POUR L'AGENT PLANIFICATEUR
# ==============================================================================

# Les prompts ont été migrés vers echo_prompts.py



class Tools:
    """
    OUTILS DE PLANIFICATION STRATEGIQUE ET TACTIQUE (ECHO PLANNER)
    Permet a l'Orchestrateur de construire, consulter et maintenir un plan d'action formel.
    
    DIRECTIVE ORCHESTRATEUR (OBLIGATION DE SUIVI ET VALIDATION) :
    1. Validation : L'outil build_plan sauvegarde nativement la stratégie et les tâches dans le Codex (statut 'proposed'). L'Orchestrateur DOIT présenter le plan à l'Utilisateur.
    2. Amorçage : Le lancement de l'exécution DOIT OBLIGATOIREMENT ÊTRE EXPLICITEMENT DEMANDÉ PAR L'UTILISATEUR. L'Orchestrateur a l'OBLIGATION ABSOLUE d'invoquer `process_plan` pour déclencher la modale de validation officielle et basculer le système en exécution.
    3. Execution Sequentielle : L'Orchestrateur DOIT executer les phases du plan chronologiquement.
    4. Suivi Tactique : L'Orchestrateur a l'OBLIGATION STRICTE de pointer l'avancement via `update_tasks`.
    5. Pivot Strategique : Le modèle utilise `update_plan` pour modifier un plan (uniquement s'il est au statut 'proposed').
    """
    class Valves(BaseModel):
        PLANNER_TIMEOUT: int = Field(
            default=180,
            description="Timeout (secondes) pour l'appel LLM planificateur."
        )

    class UserValves(BaseModel):
        MAX_PLAN_DEPTH: int = Field(
            default=3, ge=1, le=5,
            description="Profondeur max des sous-tâches dans un plan (1=plat, 3=recommandé, 5=projets complexes)."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # ==========================================================================
    # HELPERS PRIVÉS
    # ==========================================================================

    @staticmethod
    def _slugify(text: str, max_length: int = 40) -> str:
        """Convertit un texte en slug ASCII pour les noms de fichiers."""
        # Normalisation Unicode → ASCII
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        text = text.lower().strip()
        # Remplacement des caractères non-alphanum par des tirets
        text = re.sub(r"[^a-z0-9]+", "-", text)
        # Nettoyage des tirets multiples et des extrémités
        text = re.sub(r"-+", "-", text).strip("-")
        return text[:max_length] if text else "plan"

    @staticmethod
    def _read_plan_from_codex(uid: str, chat_id: str, plan_id: str) -> Optional[dict]:
        """Lit un plan depuis le Codex via glob sur {plan_id}_*.md dans le repo Git."""
        repo = CodexRepo(uid, chat_id)
        files = repo.list_files()
        for f_dict in files:
            f_name = f_dict["filename"]
            if f_name.startswith(f"{plan_id}_") and f_name.endswith(".md"):
                data = repo.read_file(f_name)
                if data:
                    data["filename"] = f_name
                return data
        return None

    @staticmethod
    def _build_tools_summary(tools_dict: Optional[dict]) -> str:
        """Extrait les noms et descriptions des outils disponibles depuis __tools__."""
        if not tools_dict:
            return "Aucune information sur les outils disponibles."
        lines = []
        for func_name, tool_info in tools_dict.items():
            spec = tool_info.get("spec", {}) if isinstance(tool_info, dict) else {}
            desc = spec.get("description", "Pas de description.")
            # Première ligne significative de la description
            short_desc = desc.split("\n")[0][:120]
            lines.append(f"- `{func_name}` — {short_desc}")
        return "\n".join(lines) if lines else "Aucun outil disponible."

    @staticmethod
    def _extract_frontmatter_status(content: str) -> Optional[str]:
        """Extrait le champ status: du frontmatter YAML d'un plan."""
        match = re.search(r"^status:\s*(\w+)", content, re.MULTILINE)
        return match.group(1) if match else None

    @staticmethod
    def _extract_llm_text(res_json: dict) -> Optional[str]:
        """Extrait le texte de la réponse Gemini (après déballage enveloppe CA)."""
        candidates = res_json.get("candidates", [])
        if candidates and candidates[0].get("content"):
            parts = candidates[0]["content"].get("parts", [])
            # Filtrer les parties pensée (thought=True), ne garder que le texte final
            text_parts = [p.get("text", "") for p in parts if not p.get("thought")]
            return "".join(text_parts)
        return None

    # ==========================================================================
    # FUNCTION CALLS PUBLIQUES
    # ==========================================================================

    async def build_plan(
        self,
        goal: str,
        context: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __tools__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Création d'un plan d'action stratégique et de sa liste de tâches associée.
        ATTENTION ORCHESTRATEUR : L'outil génère automatiquement DEUX fichiers dans le Codex (Git), liés par le même identifiant :
        1. Le fichier de Stratégie (plan_xxx.md)
        2. Le fichier des Tâches (tasks_xxx.md)
        
        Une fois exécuté, le Modèle DOIT présenter les grandes lignes à l'Utilisateur et obtenir son accord explicite avant de démarrer l'exécution.
        Le `plan_id` retourné est la clé unique pour interagir ensuite avec `update_plan` (pour la stratégie) ou `update_tasks` (pour la tactique).
        
        :param goal: Objectif final mesurable.
        :param context: Contraintes et périmètre.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        await events.status("🗂️ Préparation du plan stratégique...")

        # Génération des identifiants
        plan_id = f"plan-{int(time.time())}"
        slug = self._slugify(goal)
        filename = f"{plan_id}_{slug}.md"
        iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        tools_summary = self._build_tools_summary(__tools__)
        if "Aucun" in tools_summary:
            import logging
            logging.getLogger("echo.planner").warning(
                f"build_plan: __tools__ injecté avec type={type(__tools__)}, "
                f"contenu={list(__tools__.keys()) if isinstance(__tools__, dict) else __tools__}"
            )
        max_depth = self.user_valves.MAX_PLAN_DEPTH

        # Construction du prompt système avec les variables injectées
        system_prompt = SYS_PLANNER_BUILD.format(
            max_depth=max_depth,
            plan_id=plan_id,
            chat_id=chat_id,
            iso_date=iso_date,
            goal=goal,
            author_model="{author_model}",  # Placeholder — remplacé après cascade
            tools_summary=tools_summary,
        )

        # Prompt utilisateur
        user_prompt = f"## Objectif\n{goal}\n\n## Contexte et contraintes\n{context}"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": get_generation_config(PLANNER_MODEL_BUILD),
            "systemInstruction": {"parts": [{"text": system_prompt}]},
        }

        # Appel cascade
        res_json, model_key_used, reason = await EchoGeminiClient.call_cascade(
            target_model_key=PLANNER_MODEL_BUILD,
            payload=payload,
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.PLANNER_TIMEOUT,
            chat_id=chat_id,
            include_thoughts=False,
        )

        if not res_json:
            await events.status("❌ Échec — tous les modèles sont indisponibles.", done=True)
            return wrap_tool_output(text="❌ Échec : aucun modèle disponible pour la planification.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Extraction du texte généré
        plan_content = self._extract_llm_text(res_json)
        if not plan_content:
            await events.status("❌ Réponse vide du planificateur.", done=True)
            return wrap_tool_output(text="❌ Erreur : le planificateur n'a produit aucun contenu.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Remplacement du placeholder author_model dans le frontmatter
        plan_content = plan_content.replace("{author_model}", model_key_used)

        # Séparation du contenu généré
        parts = plan_content.split("=== TASKS ===")
        plan_part = parts[0].replace("=== PLAN ===", "").strip()
        tasks_part = parts[1].strip() if len(parts) > 1 else "- [ ] Aucune tâche."

        plan_filename = filename
        tasks_filename = f"tasks_{plan_id}_{slug}.md"

        # Persistance dans le Codex (Git)
        repo = CodexRepo(user_id, chat_id)
        repo.commit_file(plan_filename, plan_part, f"Strategy {plan_id}: {goal[:60]}")
        repo.commit_file(tasks_filename, tasks_part, f"Tasks {plan_id}: {goal[:60]}")

        # Enregistrement dans le registre unifié
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.save_resource(
            id=plan_id, name=goal[:80], resource_type='plan', status='proposed',
            mime='text/markdown', plan_goal=goal[:200], author_model=model_key_used,
            git_tracked=True, storage_path=f"codex/{filename}",
        )

        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Plan {plan_id} créé.", "done": True}})
        
        user_decision = "Le plan a été créé au statut 'proposed'. L'Utilisateur a été informé implicitement. Le Modèle DOIT lui indiquer qu'il peut proposer des ajustements via le chat, modifier manuellement le plan dans le Codex, ou demander formellement son exécution via process_plan."
        final_status = "proposed"

        return wrap_cascade_output(
            text=f"### Plan stratégique créé — `{plan_id}`\n\n"
                 f"**Décision Utilisateur :** {user_decision}\n"
                 f"**Statut :** {final_status}\n"
                 f"**Modèle :** {model_key_used}\n"
                 f"**Fichier :** `{filename}`\n\n"
                 f"---\n\n{plan_content}",
            model_requested=PLANNER_MODEL_BUILD,
            model_used=model_key_used,
            reason=reason
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def read_plan(
        self,
        plan_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Lecture du contenu complet d'un plan stratégique existant.
        :param plan_id: Identifiant unique du plan (obtenu lors de la creation ou via query_registry).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable dans le Codex.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        plan_content = result["content"]
        tasks_filename = "tasks_" + result["filename"]
        
        # Lecture silencieuse des tâches
        repo = CodexRepo(user_id, chat_id)
        tasks_result = repo.read_file(tasks_filename)
        tasks_text = tasks_result["content"] if tasks_result else "- [ ] Fichier de tâches introuvable."
        
        full_content = f"=== STRATÉGIE (Fichier: {result['filename']}) ===\n{plan_content}\n\n=== TÂCHES (Fichier: {tasks_filename}) ===\n{tasks_text}"

        await events.status(f"📖 Plan `{plan_id}` et ses tâches lus.", done=True)
        return wrap_tool_output(text=full_content, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def update_plan(
        self,
        plan_id: str,
        instructions: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Outil STRATÉGIQUE pour modifier le plan d'action (stratégie).
        Ne DOIT PAS être utilisé pour le pointage des tâches (utiliser update_tasks).
        
        :param plan_id: Identifiant unique du plan (obtenu lors de la creation ou via query_registry).
        :param instructions: Ordres precis (ex: "Ajoute un resume de mise en oeuvre a la fin").
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable dans le Codex.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # 1. Lecture du plan actuel
        current_content = result["content"]
        plan_filename = result["filename"]

        await events.status(f"📝 Modification du plan `{plan_id}`...")

        # 2. Construction du prompt pour l'agent modificateur
        user_prompt = (
            f"## Plan actuel\n{current_content}\n\n"
            f"## Modifications demandées\n{instructions}"
        )

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": get_generation_config(PLANNER_MODEL_UPDATE),
            "systemInstruction": {"parts": [{"text": SYS_PLANNER_UPDATE}]},
        }

        # 3. Appel cascade
        res_json, model_key_used, reason = await EchoGeminiClient.call_cascade(
            target_model_key=PLANNER_MODEL_UPDATE,
            payload=payload,
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.PLANNER_TIMEOUT,
            chat_id=chat_id,
            include_thoughts=False,
        )

        if not res_json:
            await events.status("Échec — tous les modèles sont indisponibles.", done=True)
            return wrap_tool_output(text="Échec : aucun modèle disponible pour la modification.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        new_content = self._extract_llm_text(res_json)
        if not new_content:
            await events.status("Réponse vide du planificateur.", done=True)
            return wrap_tool_output(text="Erreur : le planificateur n'a produit aucun contenu.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        current_status = self._extract_frontmatter_status(current_content)
        new_status = self._extract_frontmatter_status(new_content)

        # Gatekeeper 1 : Verrouillage strict dès que le plan n'est plus "proposed"
        if current_status != 'proposed':
            await events.status("Action refusée : Le plan est verrouillé.", done=True)
            return wrap_tool_output(
                text=f"Action refusée : Un plan au statut '{current_status}' est verrouillé stratégiquement. Vous ne pouvez modifier qu'un plan au statut 'proposed'.",
                user_id=user_id, chat_id=chat_id, metadata=__metadata__
            )

        # Gatekeeper 2 : Interdiction absolue de bypass process_plan
        if new_status == 'executing':
            await events.status("ERREUR : Tentative de démarrage illicite bloquée.", done=True)
            return wrap_tool_output(
                text="ACTION INTERDITE : Le passage au statut 'executing' est verrouillé pour des raisons de sécurité. Vous n'avez pas le droit d'utiliser `update_plan` pour cela. Vous DEVEZ obligatoirement invoquer l'outil `process_plan` pour démarrer l'exécution d'un plan.",
                user_id=user_id, chat_id=chat_id, metadata=__metadata__
            )

        # Application silencieuse de la modification
        repo = CodexRepo(user_id, chat_id)
        repo.commit_file(plan_filename, new_content, f"Update plan {plan_id}")
        if new_status and new_status in PLAN_STATUS:
            state.update_resource_status(plan_id, new_status)
        
        user_decision = "Mise à jour effectuée silencieusement au statut 'proposed'. L'Utilisateur a été informé implicitement. Le Modèle DOIT lui indiquer qu'il peut proposer des ajustements via le chat, modifier manuellement le plan dans le Codex, ou demander formellement son exécution via process_plan."
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Plan {plan_id} mis à jour.", "done": True}})

        return wrap_cascade_output(
            text=f"### Tentative de mise à jour du plan `{plan_id}`\n\n"
                 f"**Décision Utilisateur :** {user_decision}\n"
                 f"**Modèle :** {model_key_used}\n\n"
                 f"---\n\n{new_content}",
            model_requested=PLANNER_MODEL_UPDATE,
            model_used=model_key_used,
            reason=reason
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def update_tasks(
        self,
        plan_id: str,
        instructions: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Outil TACTIQUE EXCLUSIF pour pointer l'état d'avancement des tâches (tasks_XXX.md).
        Permet de modifier le statut des tâches sans bloquer le flux d'exécution. Ne DOIT PAS être utilisé pour changer la stratégie globale.
        
        Codification stricte des statuts à respecter dans vos instructions :
        - [ ] : Tâche en attente (Non commencée)
        - [/] : Tâche en cours d'exécution
        - [x] : Tâche terminée avec succès
        - [!] : Tâche échouée ou bloquée (nécessite attention)
        - [-] : Tâche ignorée ou obsolète
        
        :param plan_id: Identifiant unique du plan (lie le plan et les tâches).
        :param instructions: Ordres précis de modification de statut (ex: 'Passe la sous-tâche 1.1 au statut [x] et la 1.2 au statut [/]').
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # 1. Vérifier que le plan existe pour obtenir le nom de fichier
        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"Plan `{plan_id}` introuvable dans le Codex.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        
        plan_filename = result["filename"]
        tasks_filename = "tasks_" + plan_filename

        # 2. Lecture des tâches
        repo = CodexRepo(user_id, chat_id)
        tasks_result = repo.read_file(tasks_filename)
        current_tasks = tasks_result["content"] if tasks_result else "- [ ] Aucune tâche trouvée."

        # 3. Appel du modèle
        user_prompt = (
            f"## Tâches actuelles\n{current_tasks}\n\n"
            f"## Instructions de pointage\n{instructions}"
        )

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": get_generation_config(PLANNER_MODEL_UPDATE),
            "systemInstruction": {"parts": [{"text": SYS_PLANNER_UPDATE_TASKS}]},
        }

        res_json, model_key_used, reason = await EchoGeminiClient.call_cascade(
            target_model_key=PLANNER_MODEL_UPDATE,
            payload=payload,
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.PLANNER_TIMEOUT,
            chat_id=chat_id,
            include_thoughts=False,
        )

        if not res_json:
            await events.status("Échec de la mise à jour des tâches.", done=True)
            return wrap_tool_output(text="Échec : aucun modèle disponible.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        new_tasks_content = self._extract_llm_text(res_json)
        if not new_tasks_content:
            await events.status("Réponse vide pour les tâches.", done=True)
            return wrap_tool_output(text="Erreur : aucune tâche générée.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # 4. Commit silencieux
        repo.commit_file(tasks_filename, new_tasks_content, f"Update tasks {plan_id}")

        # 5. Évènement de notification UI
        short_inst = instructions.replace('\n', ' ')
        if len(short_inst) > 80:
            short_inst = short_inst[:77] + "..."
        await events.status(f"Tâches (Plan {plan_id}) : {short_inst}", done=True)

        return wrap_cascade_output(
            text=f"### Tâches du plan `{plan_id}` mises à jour\n\n"
                 f"**Modèle :** {model_key_used}\n"
                 f"**Fichier :** `{tasks_filename}`\n\n"
                 f"---\n\n{new_tasks_content}",
            model_requested=PLANNER_MODEL_UPDATE,
            model_used=model_key_used,
            reason=reason
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def process_plan(
        self, plan_id: str, user_already_validated: bool = False,
        __user__: Optional[dict] = None, __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None, __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Outil D'AMORÇAGE OBLIGATOIRE. Déclenche l'exécution officielle d'un plan.
        Le Modèle DOIT invoquer cet outil AVANT de commencer la première tâche d'un plan.
        
        :param plan_id: Identifiant unique du plan.
        :param user_already_validated: Booléen (défaut False). Mettre à True UNIQUEMENT si l'Utilisateur a formellement validé le plan lors d'un échange explicite dans la conversation (ex: "OK pour le plan, vas-y"). Si False, une modale de confirmation demandera formellement l'accord de l'utilisateur.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            await events.status("Erreur : Aucun chat_id détecté.", done=True)
            return wrap_tool_output(text="Erreur : Aucun chat_id détecté.", user_id=user_id, chat_id=None, metadata=__metadata__)

        # 1. Vérification d'existence
        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            await events.status(f"Erreur : Plan {plan_id} introuvable.", done=True)
            return wrap_tool_output(text=f"Erreur : Plan `{plan_id}` introuvable.", user_id=user_id, chat_id=chat_id, metadata=__metadata__)

        plan_filename = result["filename"]
        tasks_filename = "tasks_" + plan_filename
        plan_content = result["content"]

        current_status = self._extract_frontmatter_status(plan_content)
        if current_status != 'proposed':
            await events.status(f"Erreur : Le statut doit être 'proposed' (actuel: {current_status}).", done=True)
            return wrap_tool_output(text=f"Erreur : Le plan doit être au statut 'proposed' pour être exécuté. Statut actuel: {current_status}", user_id=user_id, chat_id=chat_id, metadata=__metadata__)

        # 2. Modale de confirmation (Si non validé précédemment)
        is_subagent = (__metadata__ or {}).get("is_subagent", False)
        if not user_already_validated and not is_subagent:
            msg_html = f'''
            <div style="margin-bottom:15px; font-size:15px; font-weight:600;">
                Lancement du Plan : Confirmez-vous l'exécution de <b>{plan_id}</b> ?
            </div>
            '''
            msg_escaped = json.dumps(msg_html).decode('utf-8')
            modals_injection = EchoUI.get_custom_modals_js()
            js_code = f"""
            {modals_injection}
            return await new Promise((resolve) => {{
                window.echoCustomConfirm({msg_escaped}, (result) => resolve(result));
            }});
            """
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": f"Attente de confirmation pour lancer le plan {plan_id}...", "done": False}})
                
            user_confirmed = await __event_call__({"type": "execute", "data": {"code": js_code}})
            if not user_confirmed:
                await events.status(f"Lancement du plan {plan_id} refusé par l'utilisateur.", done=True)
                return wrap_tool_output(text="Refus : L'Utilisateur a refusé de lancer l'exécution du plan. Attendez ses consignes.", user_id=user_id, chat_id=chat_id, metadata=__metadata__)

        # 3. Remplacement du statut dans le frontmatter (proposed -> executing)
        import re
        new_plan_content = re.sub(r"^status:\s*(\w+)", "status: executing", plan_content, flags=re.MULTILINE)
        
        repo = CodexRepo(user_id, chat_id)
        repo.commit_file(plan_filename, new_plan_content, f"Start execution {plan_id}")

        # 4. Mise à jour de l'état SQLite
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.update_resource_status(plan_id, 'executing')

        # 5. Lecture silencieuse des tâches
        tasks_result = repo.read_file(tasks_filename)
        tasks_text = tasks_result["content"] if tasks_result else "- [ ] Fichier de tâches introuvable."

        # 6. Évènement UI
        await events.status(f"Exécution du plan {plan_id} amorcée.", done=True)

        # 7. Directive Cognitive
        directive = f"""=== STRATÉGIE ===
{new_plan_content}

=== TÂCHES ===
{tasks_text}

=== DIRECTIVES TACTIQUES ABSOLUES POUR LE MODÈLE ===
Le plan `{plan_id}` est officiellement EN COURS D'EXÉCUTION.

1. SÉQUENTIALITÉ : Le Modèle DOIT exécuter les tâches strictement dans l'ordre de la liste ci-dessus.
2. POINTAGE OBLIGATOIRE : Après CHAQUE tâche accomplie (ou échouée), le Modèle a l'OBLIGATION ABSOLUE d'utiliser l'outil `update_tasks` pour mettre à jour la liste.
3. ENDURANCE : Le Modèle DOIT POURSUIVRE l'exécution ininterrompue des tâches jusqu'à la finalisation intégrale du plan.
4. CLÔTURE : Une fois la dernière tâche terminée, le Modèle DOIT utiliser `update_plan` pour modifier le statut du plan en `success` (ou `failed`) ET ajouter une section `## Synthèse d'exécution` résumant les actions menées.
"""
        return wrap_tool_output(text=directive, user_id=user_id, chat_id=chat_id, metadata=__metadata__)

    async def delete_plan(
        self,
        plan_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """Supprime définitivement un plan stratégique (fichier + registre)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="Erreur: Aucun chat_id détecté.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"Plan `{plan_id}` introuvable dans le Codex.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        plan_filename = result["filename"]

        # 1. Suppression dans le Codex (Git)
        repo = CodexRepo(user_id, chat_id)
        repo.delete_file(plan_filename, f"Delete plan {plan_id}")

        # 2. Nettoyage du registre unifié
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.delete_resource(plan_id)

        await events.status(f"Plan `{plan_id}` supprimé.", done=True)

        return wrap_tool_output(
            text=f"Plan `{plan_id}` (`{plan_filename}`) supprimé définitivement."
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def analyze_plan(
        self,
        plan_id: str,
        focus: str = "Applicabilité, logique, dépendances et bonnes pratiques",
        context_info: str = "",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Outil D'ANALYSE NON MODIFICATRICE d'un plan stratégique.
        Permet à l'Orchestrateur de déléguer l'audit d'un plan pour vérifier sa robustesse et sa cohérence.
        
        :param plan_id: Identifiant unique du plan.
        :param focus: (Optionnel) Points d'attention spécifiques pour l'analyse.
        :param context_info: (Optionnel) Informations de contexte supplémentaires justifiant les choix du plan.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="Erreur: Aucun chat_id détecté.", user_id=user_id, chat_id=None, metadata=__metadata__)

        # 1. Vérification d'existence
        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"Plan `{plan_id}` introuvable.", user_id=user_id, chat_id=chat_id, metadata=__metadata__)

        # 2. Lecture des tâches associées
        plan_content = result["content"]
        tasks_filename = "tasks_" + result["filename"]
        repo = CodexRepo(user_id, chat_id)
        tasks_result = repo.read_file(tasks_filename)
        tasks_content = tasks_result["content"] if tasks_result else "- [ ] Aucune tâche."

        await events.status(f"Analyse du plan {plan_id} en cours...")

        # 3. Appel du LLM auditeur
        user_prompt = f"## Stratégie actuelle\n{plan_content}\n\n"
        if context_info:
            user_prompt += f"## Contexte d'exécution et contraintes métier\n{context_info}\n\n"
        user_prompt += f"## Tâches actuelles\n{tasks_content}\n\n"
        user_prompt += f"## Focus d'analyse demandé\n{focus}"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": get_generation_config(PLANNER_MODEL_BUILD),
            "systemInstruction": {"parts": [{"text": SYS_PLANNER_ANALYZE}]},
        }

        res_json, model_key_used, reason = await EchoGeminiClient.call_cascade(
            target_model_key=PLANNER_MODEL_BUILD,
            payload=payload,
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.PLANNER_TIMEOUT,
            chat_id=chat_id,
            include_thoughts=False,
        )

        if not res_json:
            await events.status("Échec : aucun modèle disponible pour l'analyse.", done=True)
            return wrap_tool_output(text="Échec : aucun modèle disponible.", user_id=user_id, chat_id=chat_id, metadata=__metadata__)

        analysis_content = self._extract_llm_text(res_json)
        await events.status(f"Analyse du plan {plan_id} terminée.", done=True)

        return wrap_cascade_output(
            text=f"### Audit du plan `{plan_id}`\n\n"
                 f"**Modèle Auditeur :** {model_key_used}\n"
                 f"**Focus :** {focus}\n"
                 f"**Contexte additionnel :** {'Oui' if context_info else 'Non'}\n\n"
                 f"---\n\n{analysis_content}",
            model_requested=PLANNER_MODEL_BUILD,
            model_used=model_key_used,
            reason=reason,
            user_id=user_id, chat_id=chat_id, metadata=__metadata__
        )
