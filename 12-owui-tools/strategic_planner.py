"""
title: ECHO Strategic Planner
author: ECHO Framework
version: 5.233.0.1
description: Composant système interne : ECHO Strategic Planner.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.233.0.1: Unification du wrapper dans wrap_tool_output, décoration @echo_tool sur les 7 méthodes publiques et simplification des retours.
# 5.232.5.1: Isolement et versionnement Git des plans et listes de tâches dans le workspace dédié CODEX_WORKSPACE_PLANS ("plans") avec synchronisation de last_commit dans echo_resources.
# 5.232.1.1: Harmonisation des notifications UI (events.status) avec emojis distinctifs pour le cycle de vie du plan et des tâches.
# 5.231.29.1: Alignement sur la constante SSOT CODEX_WORKSPACE_MAIN lors de l'indexation des plans dans echo_resources.
# 5.231.28.1: Normalisation de storage_path via format_codex_storage_path (codex/main/<filename>) et suppression des appels à la table obsolète plans.

import sys
import uuid
import asyncio
import orjson as json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional, Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output, echo_tool
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_prompts import (
    SYS_PLANNER_BUILD, SYS_PLANNER_UPDATE,
    SYS_PLANNER_UPDATE_TASKS, SYS_PLANNER_ANALYZE
)
from echo_codex_git import CodexRepo
from echo_paths import get_codex_lock, format_codex_storage_path
from echo_ui import EchoUI
from echo_constants import (
    CODEX_WORKSPACE_PLANS,
    PLAN_STATUS,
    PLANNER_MODEL_BUILD,
    PLANNER_MODEL_UPDATE,
    get_generation_config,
)


# ==============================================================================
# PROMPTS SYSTÈME POUR L'AGENT PLANIFICATEUR
# ==============================================================================

# Les prompts ont été migrés vers echo_prompts.py



class Tools:
    """Outils de planification stratégique et de suivi tactique (ECHO Planner)."""
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
        """Lit un plan depuis le Codex (workspace plans) via glob sur {plan_id}_*.md dans le repo Git."""
        repo = CodexRepo(uid, chat_id, CODEX_WORKSPACE_PLANS)
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
    async def _read_plan_from_codex_async(uid: str, chat_id: str, plan_id: str) -> Optional[dict]:
        """Version asynchrone protégée par le verrou Codex de session."""
        async with get_codex_lock(uid, chat_id):
            return await asyncio.to_thread(Tools._read_plan_from_codex, uid, chat_id, plan_id)

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

    @echo_tool
    async def build_plan(
        self,
        goal: str,
        context: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __tools__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> Any:
        """Conçoit un plan stratégique formel et sa liste de tâches dans le Codex (statut `proposed`) et retourne son `plan_id`."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "❌ Erreur: Aucun chat_id détecté."

        await events.status("🗂️ Préparation du plan stratégique...")

        # Génération des identifiants
        plan_id = f"plan-{int(time.time())}-{uuid.uuid4().hex[:4]}"
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
            "generationConfig": get_generation_config(PLANNER_MODEL_BUILD, override_thinking="high"),
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
            return "❌ Échec : aucun modèle disponible pour la planification."

        # Extraction du texte généré
        plan_content = self._extract_llm_text(res_json)
        if not plan_content:
            await events.status("❌ Réponse vide du planificateur.", done=True)
            return "❌ Erreur : le planificateur n'a produit aucun contenu."

        # Remplacement du placeholder author_model dans le frontmatter
        plan_content = plan_content.replace("{author_model}", model_key_used)

        # Séparation du contenu généré
        parts = plan_content.split("=== TASKS ===")
        plan_part = parts[0].replace("=== PLAN ===", "").strip()
        tasks_part = parts[1].strip() if len(parts) > 1 else "- [ ] Aucune tâche."

        plan_filename = filename
        tasks_filename = f"tasks_{plan_id}_{slug}.md"

        # Persistance dans le Codex (Git, workspace plans)
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            plan_commit_sha = await asyncio.to_thread(repo.commit_file, plan_filename, plan_part, f"Strategy {plan_id}: {goal[:60]}")
            await asyncio.to_thread(repo.commit_file, tasks_filename, tasks_part, f"Tasks {plan_id}: {goal[:60]}")

        # Enregistrement dans le registre unifié
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.save_resource(
            id=plan_id, name=goal[:80], resource_type='plan', status='proposed',
            mime='text/markdown', plan_goal=goal[:200], author_model=model_key_used,
            git_tracked=True, last_commit=(plan_commit_sha or "")[:12],
            commit_msg=f"Strategy {plan_id}: {goal[:60]}",
            storage_path=format_codex_storage_path(filename, CODEX_WORKSPACE_PLANS),
        )

        if __event_emitter__:
            await events.status(f"✅ Plan {plan_id} créé.", done=True)
        
        user_decision = (
            "Le plan a été créé au statut 'proposed'. L'Utilisateur a été informé implicitement. "
            "Le Modèle DOIT lui indiquer qu'il peut proposer des ajustements via le chat (`update_plan`) "
            "ou demander formellement son exécution via `process_plan`."
        )
        final_status = "proposed"

        return wrap_tool_output(
            text=f"### Plan stratégique créé — `{plan_id}`\n\n"
                 f"**Décision Utilisateur :** {user_decision}\n"
                 f"**Statut :** {final_status}\n"
                 f"**Modèle :** {model_key_used}\n"
                 f"**Fichier :** [{filename}](codex:current:{CODEX_WORKSPACE_PLANS}/{filename})\n"
                 f"**Tâches :** [{tasks_filename}](codex:current:{CODEX_WORKSPACE_PLANS}/{tasks_filename})\n\n"
                 f"---\n\n{plan_content}",
            model_requested=PLANNER_MODEL_BUILD,
            model_used=model_key_used,
            reason=reason,
        )

    @echo_tool
    async def read_plan(
        self,
        plan_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> Any:
        """Lit le document de stratégie et la liste des tâches associés à un `plan_id`."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "❌ Erreur: Aucun chat_id détecté."

        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            return f"❌ Plan `{plan_id}` introuvable dans le Codex."

        plan_content = result["content"]
        tasks_filename = "tasks_" + result["filename"]
        
        # Lecture silencieuse des tâches
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            tasks_result = await asyncio.to_thread(repo.read_file, tasks_filename)
        tasks_text = tasks_result["content"] if tasks_result else "- [ ] Fichier de tâches introuvable."
        
        full_content = (
            f"=== STRATÉGIE (Fichier: [{result['filename']}](codex:current:{CODEX_WORKSPACE_PLANS}/{result['filename']})) ===\n{plan_content}\n\n"
            f"=== TÂCHES (Fichier: [{tasks_filename}](codex:current:{CODEX_WORKSPACE_PLANS}/{tasks_filename})) ===\n{tasks_text}"
        )

        await events.status(f"📖 Plan `{plan_id}` et ses tâches lus.", done=True)
        return full_content

    @echo_tool
    async def update_plan(
        self,
        plan_id: str,
        instructions: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> Any:
        """Modifie la stratégie d'un plan d'action existant (autorisé uniquement au statut `proposed`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "❌ Erreur: Aucun chat_id détecté."

        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            return f"❌ Plan `{plan_id}` introuvable dans le Codex."

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
            await events.status("❌ Échec — tous les modèles sont indisponibles.", done=True)
            return "Échec : aucun modèle disponible pour la modification."

        new_content = self._extract_llm_text(res_json)
        if not new_content:
            await events.status("❌ Réponse vide du planificateur.", done=True)
            return "Erreur : le planificateur n'a produit aucun contenu."

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        current_status = self._extract_frontmatter_status(current_content)
        new_status = self._extract_frontmatter_status(new_content)

        # Gatekeeper 1 : Verrouillage strict dès que le plan n'est plus "proposed"
        if current_status != 'proposed':
            await events.status("⚠️ Action refusée : Le plan est verrouillé.", done=True)
            return f"Action refusée : Un plan au statut '{current_status}' est verrouillé stratégiquement. Le Modèle ne PEUT modifier qu'un plan au statut 'proposed'."

        # Gatekeeper 2 : Interdiction absolue de bypass process_plan
        if new_status == 'executing':
            await events.status("⛔ ERREUR : Tentative de démarrage illicite bloquée.", done=True)
            return "ACTION INTERDITE : Le passage au statut 'executing' est verrouillé pour des raisons de sécurité. Le Modèle n'a pas l'autorisation d'utiliser `update_plan` pour cela. Le Modèle DOIT obligatoirement invoquer l'outil `process_plan` pour démarrer l'exécution d'un plan."

        # Application silencieuse de la modification
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            upd_commit_sha = await asyncio.to_thread(repo.commit_file, plan_filename, new_content, f"Update plan {plan_id}")
        if new_status and new_status in PLAN_STATUS:
            state.update_resource_status(plan_id, new_status)
        existing_res = state.get_resource(plan_id) or {}
        state.save_resource(
            id=plan_id,
            name=existing_res.get("name") or plan_filename,
            resource_type="plan",
            status=new_status if (new_status and new_status in PLAN_STATUS) else (existing_res.get("status") or "proposed"),
            mime="text/markdown",
            plan_goal=existing_res.get("plan_goal") or "",
            author_model=model_key_used,
            git_tracked=True,
            last_commit=(upd_commit_sha or "")[:12],
            commit_msg=f"Update plan {plan_id}",
            storage_path=format_codex_storage_path(plan_filename, CODEX_WORKSPACE_PLANS),
        )
        
        user_decision = (
            "Mise à jour effectuée silencieusement au statut 'proposed'. L'Utilisateur a été informé implicitement. "
            "Le Modèle DOIT lui indiquer qu'il peut proposer des ajustements via le chat (`update_plan`) "
            "ou demander formellement son exécution via `process_plan`."
        )
        if __event_emitter__:
            await events.status(f"✅ Plan {plan_id} mis à jour.", done=True)

        return wrap_tool_output(
            text=f"### Tentative de mise à jour du plan `{plan_id}`\n\n"
                 f"**Décision Utilisateur :** {user_decision}\n"
                 f"**Modèle :** {model_key_used}\n"
                 f"**Fichier :** [{plan_filename}](codex:current:{CODEX_WORKSPACE_PLANS}/{plan_filename})\n\n"
                 f"---\n\n{new_content}",
            model_requested=PLANNER_MODEL_UPDATE,
            model_used=model_key_used,
            reason=reason,
        )

    @echo_tool
    async def update_tasks(
        self,
        plan_id: str,
        instructions: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> Any:
        """Met à jour le statut des tâches (`[ ]`, `[/]`, `[x]`, `[!]`, `[-]`) dans le fichier de tâches d'un plan (`plan_id`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "❌ Erreur: Aucun chat_id détecté."

        # 1. Vérifier que le plan existe pour obtenir le nom de fichier
        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            return f"Plan `{plan_id}` introuvable dans le Codex."
        
        plan_filename = result["filename"]
        tasks_filename = "tasks_" + plan_filename

        # 2. Lecture des tâches
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            tasks_result = await asyncio.to_thread(repo.read_file, tasks_filename)
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
            await events.status("❌ Échec de la mise à jour des tâches.", done=True)
            return "Échec : aucun modèle disponible."

        new_tasks_content = self._extract_llm_text(res_json)
        if not new_tasks_content:
            await events.status("❌ Réponse vide pour les tâches.", done=True)
            return "Erreur : aucune tâche générée."

        # 4. Commit silencieux
        async with get_codex_lock(user_id, chat_id):
            tasks_commit_sha = await asyncio.to_thread(repo.commit_file, tasks_filename, new_tasks_content, f"Update tasks {plan_id}")

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        existing_res = state.get_resource(plan_id) or {}
        if existing_res:
            state.save_resource(
                id=plan_id,
                name=existing_res.get("name") or plan_filename,
                resource_type="plan",
                status=existing_res.get("status") or "executing",
                mime="text/markdown",
                plan_goal=existing_res.get("plan_goal") or "",
                author_model=existing_res.get("author_model") or model_key_used,
                git_tracked=True,
                last_commit=(tasks_commit_sha or "")[:12],
                commit_msg=f"Update tasks {plan_id}",
                storage_path=format_codex_storage_path(plan_filename, CODEX_WORKSPACE_PLANS),
            )

        # 5. Évènement de notification UI
        short_inst = instructions.replace('\n', ' ')
        if len(short_inst) > 80:
            short_inst = short_inst[:77] + "..."
        await events.status(f"📋 Tâches (Plan {plan_id}) : {short_inst}", done=True)

        return wrap_tool_output(
            text=f"### Tâches du plan `{plan_id}` mises à jour\n\n"
                 f"**Modèle :** {model_key_used}\n"
                 f"**Fichier :** [{tasks_filename}](codex:current:{CODEX_WORKSPACE_PLANS}/{tasks_filename})\n\n"
                 f"---\n\n{new_tasks_content}",
            model_requested=PLANNER_MODEL_UPDATE,
            model_used=model_key_used,
            reason=reason,
        )

    @echo_tool
    async def process_plan(
        self, plan_id: str, user_already_validated: bool = False,
        __user__: Optional[dict] = None, __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None, __event_call__: Optional[Any] = None,
    ) -> Any:
        """Amorçage obligatoire déclenchant la validation et le passage d'un plan `proposed` au statut `executing` avant l'exécution de ses tâches."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            await events.status("❌ Erreur : Aucun chat_id détecté.", done=True)
            return "Erreur : Aucun chat_id détecté."

        # 1. Vérification d'existence
        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            await events.status(f"❌ Erreur : Plan {plan_id} introuvable.", done=True)
            return f"Erreur : Plan `{plan_id}` introuvable."

        plan_filename = result["filename"]
        tasks_filename = "tasks_" + plan_filename
        plan_content = result["content"]

        current_status = self._extract_frontmatter_status(plan_content)
        if current_status != 'proposed':
            await events.status(f"⚠️ Erreur : Le statut doit être 'proposed' (actuel: {current_status}).", done=True)
            return f"Erreur : Le plan doit être au statut 'proposed' pour être exécuté. Statut actuel: {current_status}"

        # 2. Modale de confirmation (Si non validé précédemment)
        from echo_constants import ECHO_SUBAGENT_CONTEXT
        is_subagent = ECHO_SUBAGENT_CONTEXT.get().get("is_subagent", False)
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
                await events.status(f"⏳ Attente de confirmation pour lancer le plan {plan_id}...", done=False)
                
            user_confirmed = await events.call_execute(js_code)
            if not user_confirmed:
                await events.status(f"🛑 Lancement du plan {plan_id} refusé par l'utilisateur.", done=True)
                return "Refus : L'Utilisateur a refusé de lancer l'exécution du plan. Attendez ses consignes."

        # 3. Remplacement du statut dans le frontmatter (proposed -> executing)
        new_plan_content = re.sub(r"^status:\s*(\w+)", "status: executing", plan_content, flags=re.MULTILINE)
        
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            exec_commit_sha = await asyncio.to_thread(repo.commit_file, plan_filename, new_plan_content, f"Start execution {plan_id}")
            # 5. Lecture silencieuse des tâches
            tasks_result = await asyncio.to_thread(repo.read_file, tasks_filename)

        # 4. Mise à jour de l'état SQLite
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.update_resource_status(plan_id, 'executing')
        existing_res = state.get_resource(plan_id) or {}
        if existing_res:
            state.save_resource(
                id=plan_id,
                name=existing_res.get("name") or plan_filename,
                resource_type="plan",
                status="executing",
                mime="text/markdown",
                plan_goal=existing_res.get("plan_goal") or "",
                author_model=existing_res.get("author_model") or "",
                git_tracked=True,
                last_commit=(exec_commit_sha or "")[:12],
                commit_msg=f"Start execution {plan_id}",
                storage_path=format_codex_storage_path(plan_filename, CODEX_WORKSPACE_PLANS),
            )

        tasks_text = tasks_result["content"] if tasks_result else "- [ ] Fichier de tâches introuvable."

        # 6. Évènement UI
        await events.status(f"🚀 Exécution du plan {plan_id} amorcée.", done=True)

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
        return directive

    @echo_tool
    async def delete_plan(
        self,
        plan_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> Any:
        """Supprime définitivement un plan stratégique (`plan_id`) du Codex et du Registre Unifié."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "Erreur: Aucun chat_id détecté."

        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            return f"Plan `{plan_id}` introuvable dans le Codex."

        plan_filename = result["filename"]
        tasks_filename = "tasks_" + plan_filename

        # 1. Suppression dans le Codex (Git)
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            await asyncio.to_thread(repo.delete_file, plan_filename, f"Delete plan {plan_id}")
            await asyncio.to_thread(repo.delete_file, tasks_filename, f"Delete tasks {plan_id}")

        # 2. Nettoyage du registre unifié
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.delete_resource(plan_id)

        await events.status(f"🗑️ Plan `{plan_id}` supprimé.", done=True)

        return f"Plan `{plan_id}` (`{plan_filename}`) supprimé définitivement."

    @echo_tool
    async def analyze_plan(
        self,
        plan_id: str,
        focus: str = "Applicabilité, logique, dépendances et bonnes pratiques",
        context_info: str = "",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> Any:
        """Exécute un audit critique non modificateur sur la robustesse et la cohérence d'un plan (`plan_id`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "Erreur: Aucun chat_id détecté."

        # 1. Vérification d'existence
        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            return f"Plan `{plan_id}` introuvable."

        # 2. Lecture des tâches associées
        plan_content = result["content"]
        tasks_filename = "tasks_" + result["filename"]
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            tasks_result = await asyncio.to_thread(repo.read_file, tasks_filename)
        tasks_content = tasks_result["content"] if tasks_result else "- [ ] Aucune tâche."

        await events.status(f"🔍 Analyse du plan {plan_id} en cours...")

        # 3. Appel du LLM auditeur
        user_prompt = f"## Stratégie actuelle\n{plan_content}\n\n"
        if context_info:
            user_prompt += f"## Contexte d'exécution et contraintes métier\n{context_info}\n\n"
        user_prompt += f"## Tâches actuelles\n{tasks_content}\n\n"
        user_prompt += f"## Focus d'analyse demandé\n{focus}"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": get_generation_config(PLANNER_MODEL_BUILD, override_thinking="high"),
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
            await events.status("❌ Échec : aucun modèle disponible pour l'analyse.", done=True)
            return "Échec : aucun modèle disponible."

        analysis_content = self._extract_llm_text(res_json)
        await events.status(f"✅ Analyse du plan {plan_id} terminée.", done=True)

        return wrap_tool_output(
            text=f"### Audit du plan `{plan_id}`\n\n"
                 f"**Modèle Auditeur :** {model_key_used}\n"
                 f"**Focus :** {focus}\n"
                 f"**Contexte additionnel :** {'Oui' if context_info else 'Non'}\n\n"
                 f"---\n\n{analysis_content}",
            model_requested=PLANNER_MODEL_BUILD,
            model_used=model_key_used,
            reason=reason,
        )
