# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Strategic Planner
author: ECHO Framework
version: 6.23.10.1
description: Composant système interne : ECHO Strategic Planner.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.23.10.1: Adoption de extract_llm_response_text et rejet strict des sorties tronquées MAX_TOKENS avant écriture Git.
# 6.20.3.1: Migration de la confirmation process_plan vers events.confirm (Markdown).
# 6.15.0.1: Ajout de close_plan (PAS, zéro LLM, états terminaux success/partial/failed/abandoned + ## Synthèse d'exécution), Gatekeeper 1 pré-LLM dans update_plan, garde terminal dans update_tasks et parsing/remplacement borné du frontmatter YAML.
# 6.10.0.1: Mutualisation du blocage headless/automatisation via get_headless_block_reason dans process_plan.
# 6.7.1.1: Mise en conformité PAS des prompts et directives.

import sys
import uuid
import asyncio
import re
import time
import unicodedata
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional, Any, Literal

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output, echo_tool, extract_llm_response_text
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_prompts import (
    SYS_PLANNER_BUILD, SYS_PLANNER_UPDATE,
    SYS_PLANNER_UPDATE_TASKS, SYS_PLANNER_ANALYZE
)
from echo_codex_git import CodexRepo
from echo_paths import get_codex_lock, format_codex_storage_path
from echo_constants import (
    CODEX_WORKSPACE_PLANS,
    PLANNER_MODEL_BUILD,
    PLANNER_MODEL_UPDATE,
    get_generation_config,
    get_headless_block_reason,
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

    _PLAN_TERMINAL_STATUSES = frozenset({"success", "partial", "failed", "abandoned"})
    _FRONTMATTER_RE = re.compile(r"^(---\r?\n)(.*?)(\r?\n---(?:\r?\n|$))", re.DOTALL)
    _SYNTHESIS_SECTION_RE = re.compile(r"(?:\r?\n){1,}##\s+Synthèse d'exécution\b.*", re.DOTALL)

    @classmethod
    def _extract_frontmatter_status(cls, content: str) -> Optional[str]:
        """Extrait le champ status: du frontmatter YAML d'un plan."""
        fm_match = cls._FRONTMATTER_RE.match(content or "")
        search_block = fm_match.group(2) if fm_match else (content or "")
        match = re.search(r"^status:\s*['\"]?([a-zA-Z0-9_-]+)['\"]?\s*$", search_block, re.MULTILINE)
        return match.group(1).strip().lower() if match else None

    @classmethod
    def _replace_frontmatter_status(cls, content: str, new_status: str) -> str:
        """Remplace ou insère le champ status: dans le frontmatter YAML d'un plan."""
        fm_match = cls._FRONTMATTER_RE.match(content or "")
        if not fm_match:
            return re.sub(
                r"^status:\s*['\"]?[a-zA-Z0-9_-]+['\"]?\s*$",
                f"status: {new_status}",
                content or "",
                count=1,
                flags=re.MULTILINE,
            )
        prefix, body, suffix = fm_match.group(1), fm_match.group(2), fm_match.group(3)
        if re.search(r"^status:\s*.*$", body, re.MULTILINE):
            new_body = re.sub(
                r"^status:\s*.*$",
                f"status: {new_status}",
                body,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            new_body = f"{body.rstrip()}\nstatus: {new_status}"
        return f"{prefix}{new_body}{suffix}{(content or '')[fm_match.end():]}"

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
        plan_content, is_truncated, finish_reason = extract_llm_response_text(res_json)
        if is_truncated:
            await events.status(f"❌ Génération interrompue ({finish_reason}) — opération annulée.", done=True)
            return wrap_tool_output(
                text=f"❌ Opération annulée : La sortie du planificateur a été tronquée (`finishReason={finish_reason}`). Aucune écriture n'a été effectuée.",
                status={"status": "error", "error_code": "output_truncated_max_tokens", "finish_reason": finish_reason},
            )
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
        """Révise exclusivement la stratégie d'un plan d'action au statut `proposed` (utiliser `process_plan` pour démarrer et `close_plan` pour clôturer)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "❌ Erreur: Aucun chat_id détecté."

        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            return f"❌ Plan `{plan_id}` introuvable dans le Codex."

        # 1. Lecture du plan actuel et Gatekeeper 1 AVANT appel LLM
        current_content = result["content"]
        plan_filename = result["filename"]
        current_status = self._extract_frontmatter_status(current_content)
        if current_status != "proposed":
            await events.status("⚠️ Action refusée : Le plan est verrouillé.", done=True)
            if current_status == "executing":
                return (
                    f"Action refusée : Le plan `{plan_id}` est verrouillé stratégiquement en cours d'exécution "
                    f"(statut `'executing'`). Le Modèle DOIT utiliser `update_tasks` pour pointer l'avancement "
                    f"et `close_plan` pour clôturer le plan."
                )
            return (
                f"Action refusée : Le plan `{plan_id}` au statut `'{current_status}'` est verrouillé définitivement. "
                f"Le Modèle NE PEUT réviser que les plans au statut `'proposed'`."
            )

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

        new_content, is_truncated, finish_reason = extract_llm_response_text(res_json)
        if is_truncated:
            await events.status(f"❌ Génération interrompue ({finish_reason}) — opération annulée.", done=True)
            return wrap_tool_output(
                text=f"❌ Opération annulée : La sortie du planificateur a été tronquée (`finishReason={finish_reason}`). Aucune écriture n'a été effectuée.",
                status={"status": "error", "error_code": "output_truncated_max_tokens", "finish_reason": finish_reason},
            )
        if not new_content:
            await events.status("❌ Réponse vide du planificateur.", done=True)
            return "Erreur : le planificateur n'a produit aucun contenu."

        new_status = self._extract_frontmatter_status(new_content)

        # Gatekeeper 2 : Interdiction absolue de bypass process_plan ou close_plan
        if new_status == "executing":
            await events.status("⛔ ERREUR : Tentative de démarrage illicite bloquée.", done=True)
            return "ACTION INTERDITE : Le passage au statut 'executing' est verrouillé pour des raisons de sécurité. Le Modèle n'a pas l'autorisation d'utiliser `update_plan` pour cela. Le Modèle DOIT obligatoirement invoquer l'outil `process_plan` pour démarrer l'exécution d'un plan."

        if new_status != "proposed":
            await events.status("⛔ ERREUR : Changement de statut non autorisé bloqué.", done=True)
            return (
                f"ACTION INTERDITE : La modification du statut vers '{new_status}' via `update_plan` est "
                f"ABSOLUMENT INTERDITE. Le Modèle DOIT conserver le statut 'proposed' lors d'une révision "
                f"et utiliser `close_plan` pour clôturer ou abandonner un plan."
            )

        # Application silencieuse de la modification
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            upd_commit_sha = await asyncio.to_thread(repo.commit_file, plan_filename, new_content, f"Update plan {plan_id}")
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        existing_res = state.get_resource(plan_id) or {}
        state.save_resource(
            id=plan_id,
            name=existing_res.get("name") or plan_filename,
            resource_type="plan",
            status="proposed",
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
            text=f"### Plan `{plan_id}` mis à jour\n\n"
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

        current_status = self._extract_frontmatter_status(result["content"])
        if current_status in self._PLAN_TERMINAL_STATUSES:
            await events.status("⚠️ Action refusée : Le plan est clôturé.", done=True)
            return (
                f"ACTION REFUSÉE : Le plan `{plan_id}` est clôturé au statut '{current_status}'. "
                f"La modification des tâches est ABSOLUMENT INTERDITE."
            )

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

        new_tasks_content, is_truncated, finish_reason = extract_llm_response_text(res_json)
        if is_truncated:
            await events.status(f"❌ Génération interrompue ({finish_reason}) — opération annulée.", done=True)
            return wrap_tool_output(
                text=f"❌ Opération annulée : La sortie du planificateur a été tronquée (`finishReason={finish_reason}`). Aucune écriture n'a été effectuée.",
                status={"status": "error", "error_code": "output_truncated_max_tokens", "finish_reason": finish_reason},
            )
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
                status=current_status or existing_res.get("status") or "proposed",
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
        block_reason = get_headless_block_reason(__metadata__)
        if not user_already_validated and block_reason is None:
            if __event_emitter__:
                await events.status(f"⏳ Attente de confirmation pour lancer le plan {plan_id}...", done=False)

            user_confirmed = await events.confirm(
                f"Lancer le plan {plan_id}",
                f"Confirmez-vous l'exécution du plan **{plan_id}** (`proposed` → `executing`) ?",
            )
            if not user_confirmed:
                await events.status(f"🛑 Lancement du plan {plan_id} refusé par l'utilisateur.", done=True)
                return "Refus : L'Utilisateur a refusé de lancer l'exécution du plan. Le Modèle DOIT attendre les consignes de L'Utilisateur."

        # 3. Remplacement du statut dans le frontmatter (proposed -> executing)
        new_plan_content = self._replace_frontmatter_status(plan_content, "executing")
        
        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            exec_commit_sha = await asyncio.to_thread(repo.commit_file, plan_filename, new_plan_content, f"Start execution {plan_id}")
            # 5. Lecture silencieuse des tâches
            tasks_result = await asyncio.to_thread(repo.read_file, tasks_filename)

        # 4. Mise à jour de l'état SQLite
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        existing_res = state.get_resource(plan_id) or {}
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
4. CLÔTURE : Une fois la dernière tâche terminée, Le Modèle DOIT invoquer `close_plan(plan_id, final_status, execution_summary)` pour basculer le statut du plan en `success`, `partial`, `failed` ou `abandoned` ET consigner la section `## Synthèse d'exécution`.
"""
        return directive

    @echo_tool
    async def close_plan(
        self,
        plan_id: str,
        final_status: Literal["success", "partial", "failed", "abandoned"],
        execution_summary: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> Any:
        """Clôture formellement un plan stratégique existant (`plan_id`) vers un statut terminal (`success`, `partial`, `failed`, `abandoned`) et consigne `## Synthèse d'exécution`."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return "Erreur : Aucun chat_id détecté."

        final_status_norm = str(final_status or "").strip().lower()
        if final_status_norm not in self._PLAN_TERMINAL_STATUSES:
            return (
                f"ACTION REFUSÉE : Le statut final `{final_status}` est invalide. "
                f"Le Modèle DOIT fournir un statut parmi : `success`, `partial`, `failed`, `abandoned`."
            )

        summary_clean = str(execution_summary or "").strip()
        if not summary_clean:
            return (
                "ACTION REFUSÉE : Le paramètre `execution_summary` DOIT être une synthèse non vide "
                "des résultats et actions menées."
            )

        result = await self._read_plan_from_codex_async(user_id, chat_id, plan_id)
        if not result:
            return f"Erreur : Plan `{plan_id}` introuvable dans le Codex."

        plan_content = result["content"]
        plan_filename = result["filename"]
        current_status = self._extract_frontmatter_status(plan_content)

        if current_status in self._PLAN_TERMINAL_STATUSES:
            await events.status("⚠️ Action refusée : Le plan est déjà clôturé.", done=True)
            return (
                f"ACTION REFUSÉE : Le plan `{plan_id}` est déjà clôturé au statut '{current_status}'. "
                f"Toute nouvelle clôture est ABSOLUMENT INTERDITE."
            )

        if current_status == "proposed" and final_status_norm != "abandoned":
            await events.status("⚠️ Action interdite : Exécution non amorcée.", done=True)
            return (
                f"ACTION INTERDITE : Un plan au statut 'proposed' NE PEUT PAS être clôturé au statut "
                f"'{final_status_norm}' sans exécution préalable. Le Modèle DOIT invoquer `process_plan` "
                f"pour démarrer l'exécution, ou `close_plan` avec `final_status='abandoned'` pour l'abandonner."
            )

        if current_status not in ("executing", "proposed"):
            await events.status("⚠️ Action refusée : Statut source invalide.", done=True)
            return f"ACTION REFUSÉE : Transition impossible depuis le statut '{current_status}'."

        updated_content = self._replace_frontmatter_status(plan_content, final_status_norm)
        base_body = self._SYNTHESIS_SECTION_RE.sub("", updated_content).rstrip()
        closed_content = f"{base_body}\n\n## Synthèse d'exécution\n\n{summary_clean}\n"

        async with get_codex_lock(user_id, chat_id):
            repo = CodexRepo(user_id, chat_id, CODEX_WORKSPACE_PLANS)
            close_commit_sha = await asyncio.to_thread(
                repo.commit_file,
                plan_filename,
                closed_content,
                f"Close plan {plan_id} ({final_status_norm})",
            )

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        existing_res = state.get_resource(plan_id) or {}
        state.save_resource(
            id=plan_id,
            name=existing_res.get("name") or plan_filename,
            resource_type="plan",
            status=final_status_norm,
            mime="text/markdown",
            plan_goal=existing_res.get("plan_goal") or "",
            author_model=existing_res.get("author_model") or "",
            git_tracked=True,
            last_commit=(close_commit_sha or "")[:12],
            commit_msg=f"Close plan {plan_id} ({final_status_norm})",
            storage_path=format_codex_storage_path(plan_filename, CODEX_WORKSPACE_PLANS),
        )

        await events.status(f"🏁 Plan {plan_id} clôturé ({final_status_norm}).", done=True)

        return wrap_tool_output(
            text=(
                f"Le plan `{plan_id}` est clôturé au statut `{final_status_norm}`.\n\n"
                f"**Fichier :** [{plan_filename}](codex:current:{CODEX_WORKSPACE_PLANS}/{plan_filename})\n"
                f"**Commit :** `{(close_commit_sha or '')[:12]}`"
            ),
            status={
                "status": "success",
                "plan_id": plan_id,
                "final_status": final_status_norm,
                "commit": (close_commit_sha or "")[:12],
            },
        )

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

        analysis_content = extract_llm_response_text(res_json)[0]
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
