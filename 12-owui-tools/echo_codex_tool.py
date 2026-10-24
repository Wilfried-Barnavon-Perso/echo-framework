"""
title: ECHO Codex Editor
author: Wilfried BARNAVON
version: 5.230.0.1
description: Permet au modèle de manipuler un espace de travail virtuel et asynchrone (Codex) avec versioning Git invisible, et accès natif à Python/Bash sécurisés.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.230.0.1: Intégration de _emit_urn_directive (Haut SNR, 35 tokens) et rappel URN canonique dans les contrats wrap_tool_output.
# 5.229.7.3: Harmonisation des signatures -> dict et enrichissement systématique des sorties LLM via wrap_tool_output.
# 5.229.7.2: Introduction de la fonction-outil dédiée list_other_chats_ids et épuration de list_codex.
# 5.229.7.1: Support Multi-Chat sélectif (découverte SSoT, UI hiérarchique, Read-Only strict et tooling transverse).
# 5.229.0.0: Compression des docstrings OpenAPI, purge des directives IMPLIQUE et externalisation vers 14-owui-libs/tool_guides/codex_management.md.

# ECHO CONFIG NAME : ECHO Codex

import os
import sys
import asyncio
from collections import defaultdict
from pydantic import BaseModel, Field
from typing import Optional, Any, Literal

sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output, wrap_cascade_output
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_aec import EchoAEC
import echo_constants
from echo_constants import (
    ECHO_API_MAX_RETRIES, get_generation_config, FILE_INGESTION_STATUS,
    ECHO_USERS_ROOT, ECHO_SYNC_EXCLUDE_LIST, ECHO_CODEX_WORKSPACES
)
from echo_prompts import SYS_CODEX_EDIT, SYS_CODEX_SUMMARIZE
from echo_codex_git import CodexRepo

# Gestionnaire de verrous pour la concurrence intra-chat
_codex_locks = defaultdict(asyncio.Lock)


class Tools:
    class Valves(BaseModel):
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES)
        CODEX_EDIT_TIMEOUT: int = Field(default=600, description="Timeout sub-chat édition (secondes).")

    class UserValves(BaseModel):
        CODEX_EDIT_MODEL: str = Field(
            default="MODEL_APOLLO",
            description="Modèle pour l'édition assistée Codex (sub-chat).",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_context(self, __user__: dict, __metadata__: dict, workspace: str = "main", target_chat_id: Optional[str] = None):
        """Extrait user_id, chat_id et initialise CodexRepo + StateManager."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")
        if not cid:
            return None, None, None, None, False
        effective_cid = target_chat_id if target_chat_id else cid
        is_remote = bool(target_chat_id and target_chat_id != cid)
        repo = CodexRepo(uid, effective_cid, workspace)
        state = EchoStateManager(user_id=uid, chat_id=effective_cid)
        return uid, effective_cid, repo, state, is_remote

    def _extract_llm_text(self, data: dict) -> str:
        """Extrait le texte des candidates (filtrage thought parts)."""
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [p.get("text", "") for p in parts if not p.get("thought")]
        raw = "".join(text_parts).strip()
        # Nettoyage des blocs markdown enveloppants (```lang ... ```)
        if raw.startswith("```"):
            lines = raw.split("\n")
            if len(lines) >= 3 and lines[-1].strip() == "```":
                return "\n".join(lines[1:-1])
        return raw

    def _emit_urn_directive(self, state: EchoStateManager):
        """Enregistre une directive AEC ultra-concise (Haut SNR, ~35 tokens) pour le prochain tour du Modèle."""
        if not state:
            return
        ws_list = " | ".join(f"'{k}'" for k in ECHO_CODEX_WORKSPACES.keys())
        directive_content = (
            "Le Modèle DOIT citer tout fichier Codex sous la forme stricte "
            "[libellé](codex:CHAT_SCOPE:WORKSPACE/CHEMIN_RELATIF) avec CHAT_SCOPE ∈ {'current', chat_id} "
            f"et WORKSPACE ∈ {{{ws_list}}} (consulter tool_advisor('codex_management') pour le protocole complet)."
        )
        EchoAEC.record_event(
            state_manager=state,
            event_name="codex_urn_directive",
            summary=directive_content,
            resource_type="aec_directive"
        )

    async def _update_registry(self, state, filename: str, lang: str, line_count: int, commit_hash: str, msg: str, workspace: str):
        """Met à jour le registre unifié (SQLite) en isolant la Sandbox."""
        if workspace != "sandbox":
            await asyncio.to_thread(
                state.save_resource,
                id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                git_tracked=True, language=lang, lines=line_count,
                last_commit=commit_hash[:12], commit_msg=msg, storage_path=f"codex/{filename}"
            )

    # =========================================================================
    # ÉCRITURE
    # =========================================================================

    async def create_codex(
        self,
        filename: str,
        content: str,
        language: str = None,
        commit_message: str = None,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Crée un nouveau fichier versionné dans le Codex (`workspace='main'` ou `'sandbox'`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        if repo.read_file(filename):
            return wrap_tool_output(text=f"❌ Le fichier `{filename}` existe déjà dans l'espace `{workspace}`.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        await events.status(f"📝 Création de {filename}...", done=False)

        lang = language or CodexRepo.detect_language(filename)
        msg = commit_message or f"Create {filename}"
        line_count = content.count("\n") + 1

        async with _codex_locks[f"{uid}:{cid}"]:
            commit_hash = await asyncio.to_thread(repo.commit_file, filename, content, msg)
            await self._update_registry(state, filename, lang, line_count, commit_hash, msg, workspace)

        self._emit_urn_directive(state)
        urn_ref = f"[{filename}](codex:current:{workspace}/{filename})"
        await events.status(f"✅ {filename} créé ({line_count} lignes, commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(
            text=f"Fichier `{filename}` créé avec succès dans l'espace `{workspace}`.\n- Langage : {lang}\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`\n- URN : {urn_ref}",
            status={
                "status": "success",
                "filename": filename,
                "workspace": workspace,
                "language": lang,
                "lines": line_count,
                "commit": commit_hash[:12]
            },
            user_id=__user__.get("id", "system") if __user__ else "system",
            chat_id=__metadata__.get("chat_id") if __metadata__ else None,
            metadata=__metadata__
        )

    async def edit_codex(
        self,
        filename: str,
        new_content: str = None,
        instructions: str = None,
        commit_message: str = None,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Modifie un fichier existant du Codex par remplacement direct (`new_content`) OU par édition assistée (`instructions`, mutuellement exclusifs)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Vérification existence
        existing = repo.read_file(filename)
        if not existing:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable dans l'espace `{workspace}`.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        lang = CodexRepo.detect_language(filename)

        # Mode 1 : Remplacement direct
        if new_content is not None:
            msg = commit_message or f"Edit {filename}"
            line_count = new_content.count("\n") + 1
            async with _codex_locks[f"{uid}:{cid}"]:
                commit_hash = await asyncio.to_thread(repo.commit_file, filename, new_content, msg)
                await self._update_registry(state, filename, lang, line_count, commit_hash, msg, workspace)

            self._emit_urn_directive(state)
            urn_ref = f"[{filename}](codex:current:{workspace}/{filename})"
            await events.status(f"✅ {filename} modifié (commit {commit_hash[:7]}).", done=True)
            return wrap_tool_output(
                text=f"Fichier `{filename}` modifié avec succès dans l'espace `{workspace}`.\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`\n- URN : {urn_ref}",
                status={
                    "status": "success",
                    "mode": "direct_replacement",
                    "filename": filename,
                    "workspace": workspace,
                    "language": lang,
                    "lines": line_count,
                    "commit": commit_hash[:12]
                },
                user_id=__user__.get("id", "system") if __user__ else "system",
                chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                metadata=__metadata__
            )

        # Mode 2 : Édition assistée via sub-chat
        if instructions:
            await events.status(f"🧠 Édition assistée de {filename}...", done=False)

            content = existing["content"]
            user_prompt = f"## Document complet\n```{lang}\n{content}\n```\n\n## Instructions\n{instructions}"

            payload = {
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": get_generation_config("MODEL_APOLLO"),
                "systemInstruction": {
                    "parts": [{"text": SYS_CODEX_EDIT.format(filename=filename, language=lang)}]
                },
            }

            data, model_key, reason = await EchoGeminiClient.call_cascade(
                target_model_key=self.user_valves.CODEX_EDIT_MODEL,
                payload=payload,
                user_id=uid,
                metadata=__metadata__,
                events=events,
                timeout=self.valves.CODEX_EDIT_TIMEOUT,
                chat_id=cid,
                include_thoughts=False,
                max_retries=0,
            )

            if not data:
                await events.status("❌ Édition échouée — aucun modèle disponible.", done=True)
                return wrap_tool_output(text="❌ Échec : aucun modèle disponible pour l'édition.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            modified = self._extract_llm_text(data)
            if not modified:
                await events.status("❌ L'agent éditeur n'a produit aucun contenu.", done=True)
                return wrap_tool_output(text="❌ L'agent éditeur n'a pas retourné de contenu exploitable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            msg = commit_message or f"AI edit: {instructions[:60]}"
            line_count = modified.count("\n") + 1
            async with _codex_locks[f"{uid}:{cid}"]:
                commit_hash = await asyncio.to_thread(repo.commit_file, filename, modified, msg)
                await self._update_registry(state, filename, lang, line_count, commit_hash, msg, workspace)

            self._emit_urn_directive(state)
            urn_ref = f"[{filename}](codex:current:{workspace}/{filename})"
            await events.status(f"✅ Édition assistée terminée (commit {commit_hash[:7]}).", done=True)
            return wrap_cascade_output(
                text=f"Fichier `{filename}` modifié par agent éditeur dans l'espace `{workspace}`.\n- Instructions : {instructions}\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`\n- URN : {urn_ref}",
                model_requested=self.user_valves.CODEX_EDIT_MODEL,
                model_used=model_key or "unknown",
                status={
                    "status": "success",
                    "mode": "assisted_edition",
                    "filename": filename,
                    "workspace": workspace,
                    "language": lang,
                    "lines": line_count,
                    "commit": commit_hash[:12],
                    "model_used": model_key or "unknown"
                },
                reason=reason,
                user_id=__user__.get("id", "system") if __user__ else "system",
                chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                metadata=__metadata__
            )

        return wrap_tool_output(text="❌ Ni `new_content` ni `instructions` fournis.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def delete_codex(
        self,
        filename: str,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Supprime un fichier du Codex (`main` ou `sandbox`) et met à jour le Registre Unifié."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        async with _codex_locks[f"{uid}:{cid}"]:
            try:
                delete_result = await asyncio.to_thread(repo.delete_file, filename, f"Delete {filename}")
            except ValueError as e:
                return wrap_tool_output(text=f"❌ {e}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            
            if not delete_result:
                return wrap_tool_output(text=f"❌ Fichier/Dossier `{filename}` introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            commit_hash, paths_to_rm = delete_result
            if workspace != "sandbox":
                for path in paths_to_rm:
                    await asyncio.to_thread(state.delete_resource, path)
        await events.status(f"🗑️ {filename} supprimé (commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(
            text=f"Fichier `{filename}` supprimé avec succès de l'espace `{workspace}`.\n- Commit : `{commit_hash[:12]}`",
            status={
                "status": "success",
                "filename": filename,
                "workspace": workspace,
                "commit": commit_hash[:12],
                "deleted_paths": paths_to_rm
            },
            user_id=__user__.get("id", "system") if __user__ else "system",
            chat_id=__metadata__.get("chat_id") if __metadata__ else None,
            metadata=__metadata__
        )

    # =========================================================================
    # LECTURE
    # =========================================================================

    async def read_codex(
        self,
        filename: str,
        start_line: int = None,
        end_line: int = None,
        commit_hash: str = None,
        workspace: Literal["main", "sandbox"] = "main",
        target_chat_id: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Lit le contenu d'un fichier du Codex (optionnel: plage de lignes, commit_hash, ou target_chat_id en lecture seule)."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")
        if not cid:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error", "error_code": "missing_context"}, user_id=uid, chat_id=None, metadata=__metadata__)

        if target_chat_id and not CodexRepo.validate_user_chat(uid, target_chat_id):
            return wrap_tool_output(text=f"❌ Identifiant de conversation `{target_chat_id}` invalide ou inaccessible.", status={"status": "error", "error_code": "invalid_or_forbidden_chat", "target_chat_id": target_chat_id}, user_id=uid, chat_id=cid, metadata=__metadata__)

        uid, cid, repo, state, is_remote = self._get_context(__user__, __metadata__, workspace, target_chat_id)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error", "error_code": "missing_repo"}, user_id=uid, chat_id=cid, metadata=__metadata__)

        lang = CodexRepo.detect_language(filename)
        remote_badge = f" 🔒 [Lecture Seule - Chat `{cid}`]" if is_remote else ""

        if commit_hash:
            # Lecture historique
            content = await asyncio.to_thread(repo.get_file_at_commit, filename, commit_hash)
            if content is None:
                return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable au commit `{commit_hash}`.", status={"status": "error", "error_code": "not_found", "filename": filename, "commit_hash": commit_hash}, user_id=uid, chat_id=cid, metadata=__metadata__)
            
            lines = content.split('\n')
            total_lines = len(lines)
            s = max(1, start_line) if start_line else 1
            e = min(total_lines, end_line) if end_line else total_lines
            content_subset = "\n".join(lines[s-1:e])
            range_info = f"lignes {s}-{e} (commit {commit_hash[:7]})"
            
            self._emit_urn_directive(state)
            urn_scope = cid if is_remote else "current"
            urn_ref = f"[{filename}](codex:{urn_scope}:{workspace}/{filename})"
            return wrap_tool_output(
                text=f"**{filename}**{remote_badge} ({lang}, {total_lines} lignes total, {range_info})\n- URN : {urn_ref}\n\n```{lang}\n{content_subset}\n```",
                status={
                    "status": "success",
                    "filename": filename,
                    "workspace": workspace,
                    "language": lang,
                    "total_lines": total_lines,
                    "lines_returned": e - s + 1,
                    "start_line": s,
                    "end_line": e,
                    "is_remote": is_remote,
                    "target_chat_id": cid if is_remote else None,
                    "commit_hash": commit_hash[:12]
                },
                user_id=uid,
                chat_id=cid,
                metadata=__metadata__
            )
        else:
            # Lecture classique (HEAD)
            result = await asyncio.to_thread(repo.read_file, filename, start_line, end_line)
            if not result:
                return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error", "error_code": "not_found", "filename": filename}, user_id=uid, chat_id=cid, metadata=__metadata__)

            range_info = f"lignes {result['range'][0]}-{result['range'][1]}" if result["range"] else "complet"
            s_line = result["range"][0] if result["range"] else 1
            e_line = result["range"][1] if result["range"] else result['total_lines']
            lines_returned = len(result['content'].split('\n')) if result.get('content') else 0
            self._emit_urn_directive(state)
            urn_scope = cid if is_remote else "current"
            urn_ref = f"[{filename}](codex:{urn_scope}:{workspace}/{filename})"
            return wrap_tool_output(
                text=f"**{filename}**{remote_badge} ({lang}, {result['total_lines']} lignes total, {range_info})\n- URN : {urn_ref}\n\n```{lang}\n{result['content']}\n```",
                status={
                    "status": "success",
                    "filename": filename,
                    "workspace": workspace,
                    "language": lang,
                    "total_lines": result['total_lines'],
                    "lines_returned": lines_returned,
                    "start_line": s_line,
                    "end_line": e_line,
                    "is_remote": is_remote,
                    "target_chat_id": cid if is_remote else None,
                    "commit_hash": "HEAD"
                },
                user_id=uid,
                chat_id=cid,
                metadata=__metadata__
            )

    async def restore_codex(
        self,
        filename: str,
        commit_hash: str,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Restaure un fichier du Codex à une version antérieure à partir de son `commit_hash`."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        async with _codex_locks[f"{uid}:{cid}"]:
            old_content = await asyncio.to_thread(repo.get_file_at_commit, filename, commit_hash)
            if old_content is None:
                return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable au commit `{commit_hash}`.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            
            lang = CodexRepo.detect_language(filename)
            msg = f"Restore {filename} from {commit_hash[:7]}"
            new_commit_hash = await asyncio.to_thread(repo.commit_file, filename, old_content, msg)
            line_count = old_content.count("\n") + 1
            
            await self._update_registry(state, filename, lang, line_count, new_commit_hash, msg, workspace)
            
        await events.status(f"🔄 {filename} restauré depuis {commit_hash[:7]}.", done=True)
        return wrap_tool_output(
            text=f"Fichier `{filename}` restauré avec succès depuis `{commit_hash[:7]}` dans l'espace `{workspace}`.\n- Nouveau Commit : `{new_commit_hash[:12]}`",
            status={
                "status": "success",
                "filename": filename,
                "workspace": workspace,
                "restored_from": commit_hash[:12],
                "new_commit": new_commit_hash[:12]
            },
            user_id=__user__.get("id", "system") if __user__ else "system",
            chat_id=__metadata__.get("chat_id") if __metadata__ else None,
            metadata=__metadata__
        )

    async def search_codex(
        self,
        query: str,
        target_paths: list[str] = None,
        is_regex: bool = False,
        commit_hash: str = None,
        trace_history: bool = False,
        history_depth: int = 50,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Recherche un motif texte ou regex dans les fichiers actuels du Codex ou à travers l'historique des modifications (`trace_history=True`)."""
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        if trace_history:
            # Mode Temporel (Delta / Pickaxe) dans le Codex
            results = await asyncio.to_thread(repo.trace_history, query, target_paths, history_depth)
            if not results:
                scope = f"les chemins `{target_paths}`" if target_paths else "l'historique global du Codex"
                return wrap_tool_output(
                    text=f"Aucune évolution trouvée pour la chaîne `{query}` dans {scope}.",
                    status={"status": "success", "mode": "trace_history", "query": query, "commits_found": 0, "target_paths": target_paths},
                    user_id=__user__.get("id", "system") if __user__ else "system",
                    chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                    metadata=__metadata__
                )
                
            lines = [f"## Trace d'évolution de `{query}` dans le Codex"]
            for entry in results:
                lines.append(f"\n### Commit `{entry['hash']}` : _{entry['message']}_")
                for diff in entry['diffs']:
                    lines.append(f"**Fichier :** `{diff['file']}`")
                    if diff['removed']:
                        lines.append("```diff\n" + "\n".join(f"- {r}" for r in diff['removed']) + "\n```")
                    if diff['added']:
                        lines.append("```diff\n" + "\n".join(f"+ {a}" for a in diff['added']) + "\n```")
            return wrap_tool_output(
                text="\n".join(lines),
                status={"status": "success", "mode": "trace_history", "query": query, "commits_found": len(results), "target_paths": target_paths},
                user_id=__user__.get("id", "system") if __user__ else "system",
                chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                metadata=__metadata__
            )
            
        else:
            # Mode Spatial (Grep) dans le Codex
            all_files = [f["filename"] for f in repo.list_files() if f["type"] == "file"]
            if target_paths:
                files_to_check = [f for f in all_files if any(f.startswith(p) for p in target_paths)]
            else:
                files_to_check = all_files

            all_results = []
            total_matches_count = 0
            for f in files_to_check:
                matches = await asyncio.to_thread(repo.search_in_file, f, query, is_regex, commit_hash)
                if matches:
                    total_matches_count += len(matches)
                    lines_text = "\n".join(f"L{m['line_number']:>4}: {m['line_content']}" for m in matches)
                    all_results.append(f"### `{f}` ({len(matches)} match)\n```\n{lines_text}\n```")

            scope = f"dans les chemins `{target_paths}`" if target_paths else "dans l'intégralité du Codex"
            epoch = f"au commit `{commit_hash}`" if commit_hash else "actuellement"

            if not all_results:
                return wrap_tool_output(
                    text=f"Aucun résultat pour `{query}` {scope} {epoch}.",
                    status={"status": "success", "mode": "spatial_grep", "query": query, "files_matched_count": 0, "total_matches": 0, "commit": commit_hash or "HEAD"},
                    user_id=__user__.get("id", "system") if __user__ else "system",
                    chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                    metadata=__metadata__
                )

            return wrap_tool_output(
                text=f"**Résultats de la recherche Codex pour `{query}` :**\n\n" + "\n\n".join(all_results),
                status={"status": "success", "mode": "spatial_grep", "query": query, "files_matched_count": len(all_results), "total_matches": total_matches_count, "commit": commit_hash or "HEAD"},
                user_id=__user__.get("id", "system") if __user__ else "system",
                chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                metadata=__metadata__
            )

    async def summarize_codex(
        self,
        filename: str,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Génère un résumé technique structuré d'un fichier volumineux du Codex."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        result = repo.read_file(filename)
        if not result:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        lang = CodexRepo.detect_language(filename)
        await events.status(f"🔍 Distillation de {filename} ({result['total_lines']} lignes)...", done=False)

        payload = {
            "contents": [{"role": "user", "parts": [{"text": result["content"]}]}],
            "systemInstruction": {"parts": [{"text": SYS_CODEX_SUMMARIZE.format(filename=filename, language=lang)}]},
            "generationConfig": {**get_generation_config("MODEL_APOLLO", override_thinking="medium"), "maxOutputTokens": 8192}
        }

        data, _, err = await EchoGeminiClient.call_cascade(
            target_model_key="MODEL_APOLLO",
            payload=payload,
            user_id=uid,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.CODEX_EDIT_TIMEOUT,
            chat_id=cid,
            include_thoughts=False
        )
        summary = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "") if data else f"❌ Échec synthèse : {err}"

        await events.status(f"✅ Synthèse de {filename} terminée.", done=True)
        return wrap_tool_output(
            text=f"## Synthèse — {filename} ({lang}, {result['total_lines']} lignes)\n\n{summary}",
            status={
                "status": "success",
                "filename": filename,
                "workspace": workspace,
                "language": lang,
                "lines": result['total_lines']
            },
            user_id=__user__.get("id", "system") if __user__ else "system",
            chat_id=__metadata__.get("chat_id") if __metadata__ else None,
            metadata=__metadata__
        )

    async def list_other_chats_ids(
        self,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Liste les identifiants et titres des autres conversations de l'utilisateur disposant d'un espace Codex."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")
        if not cid:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=uid, chat_id=None, metadata=__metadata__)

        chats_data = await asyncio.to_thread(CodexRepo.get_user_codex_chats, uid, cid)
        others = chats_data.get("others", [])
        if not others:
            return wrap_tool_output(
                text="Aucune autre conversation de l'utilisateur ne dispose d'un espace Codex.",
                status={"status": "success", "count": 0},
                user_id=uid, chat_id=cid, metadata=__metadata__
            )

        users_root = getattr(echo_constants, "ECHO_USERS_ROOT", ECHO_USERS_ROOT)
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")

        def _inspect_chat_codex(target_cid: str) -> dict:
            info = {"main_count": 0, "sandbox_count": 0, "extensions": set()}
            codex_base = os.path.join(users_root, safe_uid, "chats", target_cid, "codex")
            for ws in ("main", "sandbox"):
                ws_path = os.path.join(codex_base, ws)
                if os.path.exists(ws_path):
                    try:
                        for entry in os.scandir(ws_path):
                            if not entry.name.startswith(".") and entry.name not in ECHO_SYNC_EXCLUDE_LIST:
                                if ws == "main":
                                    info["main_count"] += 1
                                else:
                                    info["sandbox_count"] += 1
                                _, ext = os.path.splitext(entry.name)
                                if ext:
                                    info["extensions"].add(ext.lower())
                    except Exception:
                        pass
            return info

        lines = [
            "## 📚 Conversations Distantes Disponibles avec Espace Codex\n",
            "| Chat ID | Titre | Fichiers (main) | Fichiers (sandbox) | Types de fichiers |",
            "|---|---|---|---|---|"
        ]

        chats_summary = []
        for c in others:
            target_id = c["id"]
            meta_info = await asyncio.to_thread(_inspect_chat_codex, target_id)
            exts_str = ", ".join(sorted(meta_info["extensions"])) if meta_info["extensions"] else "aucun"
            lines.append(f"| `{target_id}` | {c['title']} | {meta_info['main_count']} | {meta_info['sandbox_count']} | `{exts_str}` |")
            chats_summary.append({
                "chat_id": target_id,
                "title": c["title"],
                "main_files_count": meta_info["main_count"],
                "sandbox_files_count": meta_info["sandbox_count"],
                "extensions": list(meta_info["extensions"])
            })

        footer = (
            f"\n\n**Total : {len(others)} autre(s) conversation(s) accessible(s) en lecture seule.**\n\n"
            "> **Rappels d'utilisation pour le Modèle** :\n"
            "> - Pour lister l'arborescence complète d'un chat distant : `list_codex(target_chat_id=\"<Chat ID>\", workspace=\"main\")`\n"
            "> - Pour lire un fichier d'un chat distant en lecture seule : `read_codex(filename=\"...\", target_chat_id=\"<Chat ID>\")`"
        )

        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=cid))
        return wrap_tool_output(
            text="\n".join(lines) + footer,
            status={"status": "success", "count": len(others), "chats": chats_summary},
            user_id=uid,
            chat_id=cid,
            metadata=__metadata__
        )

    async def list_codex(
        self,
        workspace: Literal["main", "sandbox"] = "main",
        target_chat_id: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Liste l'arborescence des fichiers du Codex (courante ou distante via target_chat_id)."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")
        if not cid:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=uid, chat_id=None, metadata=__metadata__)

        if target_chat_id and not CodexRepo.validate_user_chat(uid, target_chat_id):
            return wrap_tool_output(text=f"❌ Identifiant de conversation `{target_chat_id}` invalide ou inaccessible.", status={"status": "error"}, user_id=uid, chat_id=cid, metadata=__metadata__)

        uid, cid, repo, state, is_remote = self._get_context(__user__, __metadata__, workspace, target_chat_id)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=uid, chat_id=cid, metadata=__metadata__)

        files = await asyncio.to_thread(repo.list_files)
        remote_badge = " 🔒 [Lecture Seule]" if is_remote else ""
        if not files:
            msg = f"Le Codex de la conversation `{cid}` ({workspace}){remote_badge} est vide." if is_remote else f"Le Codex de cette conversation ({workspace}) est vide."
            return wrap_tool_output(text=msg, status={"status": "success", "files_count": 0, "workspace": workspace, "is_remote": is_remote}, user_id=uid, chat_id=cid, metadata=__metadata__)

        lines = [f"| Fichier | Type | Langage | Lignes | Taille |{remote_badge}"]
        lines.append("|---|---|---|---|---|")
        for f in files:
            size_kb = f["size_bytes"] / 1024
            is_dir = f.get("type") == "directory"
            type_icon = "📁 Dossier" if is_dir else "📄 Fichier"
            size_str = "-" if is_dir else f"{size_kb:.1f} Ko"
            lines_str = "-" if is_dir else str(f.get("lines", 0))
            lines.append(f"| `{f['filename']}` | {type_icon} | {f['lang']} | {lines_str} | {size_str} |")

        stats = await asyncio.to_thread(repo.get_repo_stats)
        footer = f"\n**{stats['total_files']} éléments au total**, {stats['total_commits']} commits"
        if stats["last_commit_message"]:
            footer += f", dernier commit : _{stats['last_commit_message']}_"
        if is_remote:
            footer += (
                f"\n\n*(Session source distante : `{cid}` - Consultation en lecture seule)*\n"
                f"> Pour lire un fichier : `read_codex(filename=\"<fichier>\", target_chat_id=\"{cid}\", workspace=\"{workspace}\")`"
            )

        files_meta = [{"name": f["filename"], "type": f.get("type", "file"), "size": f["size_bytes"], "lang": f["lang"]} for f in files]
        self._emit_urn_directive(state)
        return wrap_tool_output(
            text="\n".join(lines) + footer,
            status={"status": "success", "files_count": len(files), "workspace": workspace, "is_remote": is_remote, "files": files_meta},
            user_id=uid,
            chat_id=cid,
            metadata=__metadata__
        )

    async def history_codex(
        self,
        filename: Optional[str] = None,
        limit: int = 20,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Affiche l'historique des versions (`commit_hash`, message, date) du Codex global ou d'un fichier `filename`."""
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        log = repo.get_log(filename=filename, limit=limit)
        if not log:
            scope = f"de `{filename}`" if filename else "du Codex"
            return wrap_tool_output(
                text=f"Aucun historique {scope}.",
                status={"status": "success", "filename": filename, "workspace": workspace, "commits_count": 0, "limit": limit},
                user_id=__user__.get("id", "system") if __user__ else "system",
                chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                metadata=__metadata__
            )

        import time as time_mod
        lines = []
        for entry in log:
            dt = time_mod.strftime("%Y-%m-%d %H:%M", time_mod.localtime(entry["timestamp"]))
            lines.append(f"- `{entry['hash']}` — {entry['message']} ({dt})")

        scope = f"de `{filename}`" if filename else "global"
        return wrap_tool_output(
            text=f"## Historique {scope} ({len(log)} commits)\n\n" + "\n".join(lines),
            status={"status": "success", "filename": filename, "workspace": workspace, "commits_count": len(log), "limit": limit},
            user_id=__user__.get("id", "system") if __user__ else "system",
            chat_id=__metadata__.get("chat_id") if __metadata__ else None,
            metadata=__metadata__
        )

    async def open_codex_ui(
        self,
        filename: str,
        workspace: Literal["main", "sandbox"] = "main",
        __user__: dict = None,
        __metadata__: dict = None,
        __messages__: list = [],
        __model__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Ouvre le HUD Codex dans l'interface de l'Utilisateur pour afficher `filename` (à utiliser uniquement sur demande expresse ou relecture indispensable)."""
        events = EchoEvents(__event_emitter__, __event_call__)

        chat_id = (__metadata__ or {}).get("chat_id", "")
        # CRITIQUE (Chat.svelte:1513-1724) : Ancrer l'Action sur le message UTILISATEUR immuable
        # du tour courant (_echo_user_msg_id / parent_id) et NON sur le message assistant en cours
        # de streaming (message_id), afin que la réécriture différée de Chat.svelte:1724 après le
        # long-polling wait_code n'écrase jamais history.messages[assistant_message_id] (done: false).
        meta = __metadata__ or {}
        user_msg_id = (
            meta.get("_echo_user_msg_id")
            or meta.get("parent_id")
            or (meta.get("user_message") or {}).get("id")
            or meta.get("message_id", "")
        )
        model_id = (__model__ or {}).get("id", "echo_orchestrator")
        session_id = meta.get("session_id", "")

        # 1. Injection de la cible
        target_payload = f"window._echoCodexTarget = {{ file: '{filename}', workspace: '{workspace}' }};\nreturn true;"
        await events.call_execute(target_payload, timeout=5.0)

        # 2. Déclenchement de l'action native via la méthode factorisée ancrée sur user_msg_id
        await events.trigger_action("echo_codex_action", chat_id, user_msg_id, model_id, session_id)
        await events.status(f"Ouverture du Codex demandée pour {filename}...", done=True)

        return wrap_tool_output(
            text=f"✅ Requête d'ouverture visuelle transmise pour `{filename}` dans l'espace `{workspace}`.",
            status={"status": "success", "action": "open_codex_ui", "filename": filename, "workspace": workspace},
            user_id=__user__.get("id", "system") if __user__ else "system",
            chat_id=__metadata__.get("chat_id") if __metadata__ else None,
            metadata=__metadata__
        )
