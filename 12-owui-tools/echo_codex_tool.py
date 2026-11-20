"""
title: ECHO Codex Editor
author: Wilfried BARNAVON
version: 6.15.0.1
description: Permet au modèle de manipuler un espace virtuel (Codex) avec versioning Git invisible et accès natif à Python/Bash sécurisés.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.15.0.1: Ajout de process_plan et close_plan dans l'indication _check_generic_writable du workspace 'plans'.
# 6.12.0.1: Ajout create_codex_directory, move_codex, transfer_codex (Option A), list_codex(path, recursive), delete_codex(recursive), garde restore_codex anti-sandbox, recherche récursive et délégation sync_codex_registry_* vers echo_codex_git.
# 6.7.11.1: Délégation du fenêtrage read_codex à paginate_lines_window et suppression des skip_truncation=True redondants.
# 6.7.4.1: Alignement de get_generation_config sur CODEX_EDIT_MODEL dans edit_codex.
# 6.7.1.1: Mise en conformité PAS des prompts et directives.

# ECHO CONFIG NAME : ECHO Codex

import os
import sys
import hashlib
import asyncio
from pydantic import BaseModel, Field
from typing import Optional, Any, Literal

sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool, paginate_lines_window, wrap_tool_output
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_aec import EchoAEC
import echo_constants
from echo_constants import (
    CODEX_DEFAULT_WORKSPACE,
    CODEX_DIR_NAME,
    CODEX_LIST_MAX_ENTRIES,
    CODEX_TRANSFER_SOURCE_LITERAL,
    CODEX_WORKSPACE_GENERATED,
    CODEX_WORKSPACE_LITERAL,
    CODEX_WORKSPACE_MAIN,
    CODEX_WORKSPACE_PLANS,
    CODEX_WRITABLE_WORKSPACE_LITERAL,
    ECHO_API_MAX_RETRIES,
    ECHO_CODEX_MAX_READ_BYTES,
    ECHO_CODEX_MAX_READ_LINES,
    ECHO_CODEX_WORKSPACES,
    ECHO_SYNC_EXCLUDE_LIST,
    ECHO_USERS_ROOT,
    get_generation_config,
    is_codex_workspace_generic_writable,
    is_codex_workspace_indexed,
    is_codex_workspace_versioned,
)
from echo_prompts import SYS_CODEX_EDIT, SYS_CODEX_SUMMARIZE
from echo_window_manager import get_window_manager
from echo_codex_git import (
    CodexDirectoryNotEmptyError,
    CodexPathConflictError,
    CodexQuotaExceededError,
    CodexRepo,
    sync_codex_registry_delete,
    sync_codex_registry_relocate,
    sync_codex_registry_save,
    transfer_codex_entries,
)
from echo_paths import get_codex_lock


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

    def _get_context(
        self,
        __user__: dict,
        __metadata__: dict,
        workspace: str = CODEX_DEFAULT_WORKSPACE,
        target_chat_id: Optional[str] = None,
    ):
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

    async def _resolve_and_verify_file(
        self,
        filename: str,
        workspace: str,
        target_chat_id: Optional[str],
        commit_hash: Optional[str],
        __user__: dict,
        __metadata__: dict,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> tuple[Optional[dict], Optional[dict]]:
        """Valide le contexte, sécurise le chemin et vérifie l'existence physique du fichier (HEAD ou commit historique)."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        session_cid = (__metadata__ or {}).get("chat_id")
        if not session_cid:
            err = wrap_tool_output(
                text="❌ Contexte manquant (chat_id).",
                status={"status": "error", "error_code": "missing_context"},
            )
            return None, err

        # Inscription déterministe de la directive URN sur la session active dès l'entrée
        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=session_cid))

        # Contrôle de sécurité anti-IDOR sur les conversations distantes
        if target_chat_id and not CodexRepo.validate_user_chat(uid, target_chat_id):
            err = wrap_tool_output(
                text=f"❌ Identifiant de conversation `{target_chat_id}` invalide ou inaccessible.",
                status={"status": "error", "error_code": "invalid_or_forbidden_chat", "target_chat_id": target_chat_id},
            )
            return None, err

        # Validation de l'espace de travail contre le SSOT ECHO_CODEX_WORKSPACES
        if workspace not in ECHO_CODEX_WORKSPACES:
            err = wrap_tool_output(
                text=f"❌ Espace de travail `{workspace}` invalide (valeurs autorisées : {list(ECHO_CODEX_WORKSPACES.keys())}).",
                status={"status": "error", "error_code": "invalid_workspace", "workspace": workspace},
            )
            return None, err

        uid, effective_cid, repo, state, is_remote = self._get_context(__user__, __metadata__, workspace, target_chat_id)
        if not repo:
            err = wrap_tool_output(
                text="❌ Contexte manquant (chat_id).",
                status={"status": "error", "error_code": "missing_repo"},
            )
            return None, err

        safe_file = repo._secure_path(filename)
        if not safe_file:
            err = wrap_tool_output(
                text="❌ Chemin de fichier invalide.",
                status={"status": "error", "error_code": "invalid_path", "filename": filename},
            )
            return None, err

        # Vérification d'existence physique
        if commit_hash:
            commit_result = await asyncio.to_thread(repo.read_file_at_commit, safe_file, commit_hash)
            if commit_result is None:
                err = wrap_tool_output(
                    text=f"❌ Fichier `{safe_file}` introuvable au commit `{commit_hash}`.",
                    status={"status": "error", "error_code": "not_found", "filename": safe_file, "commit_hash": commit_hash},
                )
                return None, err
            return {
                "uid": uid,
                "session_cid": session_cid,
                "effective_cid": effective_cid,
                "repo": repo,
                "state": state,
                "is_remote": is_remote,
                "safe_file": safe_file,
                "commit_content": commit_result["content"],
                "head_result": None,
                "file_info": commit_result,
            }, None
        else:
            result = await asyncio.to_thread(repo.read_file, safe_file)
            if not result:
                err = wrap_tool_output(
                    text=f"❌ Fichier `{safe_file}` introuvable.",
                    status={"status": "error", "error_code": "not_found", "filename": safe_file},
                )
                return None, err
            return {
                "uid": uid,
                "session_cid": session_cid,
                "effective_cid": effective_cid,
                "repo": repo,
                "state": state,
                "is_remote": is_remote,
                "safe_file": safe_file,
                "commit_content": None,
                "head_result": result,
                "file_info": result,
            }, None

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
        """Enregistre une directive AEC ultra-concise (Haut SNR, ~35 tokens) pour le prochain tour du Modèle avec protection anti-doublon intra-tour."""
        if not state:
            return
        # Protection anti-doublon : ne pas réinscrire si une directive URN non encore drainée est déjà présente
        if state.has_pending_directive("codex_urn_directive"):
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
        """Synchronise la ressource dans la table SQLite echo_resources uniquement pour les espaces indexés (exclut la sandbox)."""
        await sync_codex_registry_save(
            state.user_id,
            state.chat_id,
            workspace,
            filename,
            commit_hash,
            msg,
            language=lang,
            lines=line_count,
        )

    def _check_generic_writable(self, workspace: str, user_id: str, chat_id: Optional[str], metadata: Optional[dict]) -> Optional[dict]:
        """Vérifie si l'espace de travail accepte les écritures via les outils génériques Codex."""
        if not is_codex_workspace_generic_writable(workspace):
            hint = (
                "Le Modèle DOIT utiliser les outils dédiés `build_plan`, `update_plan`, `update_tasks`, `process_plan`, `close_plan` ou `delete_plan`."
                if workspace == CODEX_WORKSPACE_PLANS
                else "Le Modèle DOIT utiliser l'outil dédié `generate_image`."
            )
            return wrap_tool_output(
                text=f"❌ Action refusée : L'espace `{workspace}` est verrouillé en écriture directe. {hint}",
                status={"status": "error", "error_code": "workspace_read_only", "workspace": workspace},
            )
        return None

    # =========================================================================
    # ÉCRITURE
    # =========================================================================

    @echo_tool
    async def create_codex(
        self,
        filename: str,
        content: str,
        language: str = None,
        commit_message: str = None,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Crée un nouveau fichier dans le Codex (`workspace='main'` ou `'sandbox'`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        ro_err = self._check_generic_writable(workspace, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        # Inscription déterministe de la directive URN sur la session active dès l'entrée
        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=cid))

        try:
            safe_file = repo._secure_path(filename)
        except ValueError as e:
            return wrap_tool_output(
                text=f"❌ {e}",
                status={"status": "error", "error_code": "invalid_path", "filename": filename},
            )

        async with get_codex_lock(uid, cid):
            try:
                if await asyncio.to_thread(repo.read_file, safe_file):
                    return f"❌ Le fichier `{safe_file}` existe déjà dans l'espace `{workspace}`."
            except ValueError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "invalid_path", "filename": safe_file},
                )

            await events.status(f"📝 Création de {safe_file}...", done=False)

            lang = language or CodexRepo.detect_language(safe_file)
            msg = commit_message or f"Create {safe_file}"
            line_count = content.count("\n") + 1

            try:
                commit_hash = await asyncio.to_thread(repo.commit_file, safe_file, content, msg)
            except CodexPathConflictError as e:
                await events.status(f"❌ Conflit de chemin sur {safe_file}.", done=True)
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "path_conflict", "filename": safe_file, "workspace": workspace},
                )
            await self._update_registry(state, safe_file, lang, line_count, commit_hash, msg, workspace)

        urn_ref = f"[{safe_file}](codex:current:{workspace}/{safe_file})"
        await events.status(f"✅ {safe_file} créé ({line_count} lignes, commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(
            text=f"Fichier `{safe_file}` créé avec succès dans l'espace `{workspace}`.\n- Langage : {lang}\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`\n- URN : {urn_ref}",
            status={
                "status": "success",
                "filename": safe_file,
                "workspace": workspace,
                "language": lang,
                "lines": line_count,
                "commit": commit_hash[:12]
            }
        )

    @echo_tool
    async def edit_codex(
        self,
        filename: str,
        new_content: str = None,
        instructions: str = None,
        commit_message: str = None,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Modifie un fichier existant du Codex (`main` ou `sandbox`) par remplacement direct (`new_content`) OU par édition assistée (`instructions`, mutuellement exclusifs)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        ro_err = self._check_generic_writable(workspace, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        # Inscription déterministe de la directive URN sur la session active dès l'entrée
        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=cid))

        try:
            safe_file = repo._secure_path(filename)
        except ValueError as e:
            return wrap_tool_output(
                text=f"❌ {e}",
                status={"status": "error", "error_code": "invalid_path", "filename": filename},
            )

        # Vérification existence
        async with get_codex_lock(uid, cid):
            try:
                existing = await asyncio.to_thread(repo.read_file, safe_file)
            except ValueError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "invalid_path", "filename": safe_file},
                )
        if not existing:
            return f"❌ Fichier `{safe_file}` introuvable dans l'espace `{workspace}`."

        lang = CodexRepo.detect_language(safe_file)

        # Mode 1 : Remplacement direct
        if new_content is not None:
            msg = commit_message or f"Edit {safe_file}"
            line_count = new_content.count("\n") + 1
            async with get_codex_lock(uid, cid):
                try:
                    commit_hash = await asyncio.to_thread(repo.commit_file, safe_file, new_content, msg)
                except CodexPathConflictError as e:
                    await events.status(f"❌ Conflit de chemin sur {safe_file}.", done=True)
                    return wrap_tool_output(
                        text=f"❌ {e}",
                        status={"status": "error", "error_code": "path_conflict", "filename": safe_file, "workspace": workspace},
                    )
                await self._update_registry(state, safe_file, lang, line_count, commit_hash, msg, workspace)

            urn_ref = f"[{safe_file}](codex:current:{workspace}/{safe_file})"
            await events.status(f"✅ {safe_file} modifié (commit {commit_hash[:7]}).", done=True)
            return wrap_tool_output(
                text=f"Fichier `{safe_file}` modifié avec succès dans l'espace `{workspace}`.\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`\n- URN : {urn_ref}",
                status={
                    "status": "success",
                    "mode": "direct_replacement",
                    "filename": safe_file,
                    "workspace": workspace,
                    "language": lang,
                    "lines": line_count,
                    "commit": commit_hash[:12]
                }
            )

        # Mode 2 : Édition assistée via sub-chat
        if instructions:
            if existing.get("is_binary"):
                return wrap_tool_output(
                    text=f"❌ Action refusée : Le fichier `{safe_file}` est un binaire. L'édition assistée par instructions n'est applicable qu'aux fichiers texte.",
                    status={"status": "error", "error_code": "binary_edit_not_supported", "filename": safe_file}
                )

            await events.status(f"🧠 Édition assistée de {safe_file}...", done=False)

            content = existing["content"]
            original_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            user_prompt = f"## Document complet\n```{lang}\n{content}\n```\n\n## Instructions\n{instructions}"

            edit_model = self.user_valves.CODEX_EDIT_MODEL or "MODEL_APOLLO"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": get_generation_config(edit_model),
                "systemInstruction": {
                    "parts": [{"text": SYS_CODEX_EDIT.format(filename=safe_file, language=lang)}]
                },
            }

            data, model_key, reason = await EchoGeminiClient.call_cascade(
                target_model_key=edit_model,
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
                return "❌ Échec : aucun modèle disponible pour l'édition."

            modified = self._extract_llm_text(data)
            if not modified:
                await events.status("❌ L'agent éditeur n'a produit aucun contenu.", done=True)
                return "❌ L'agent éditeur n'a pas retourné de contenu exploitable."

            msg = commit_message or f"AI edit: {instructions[:60]}"
            line_count = modified.count("\n") + 1
            async with get_codex_lock(uid, cid):
                current_doc = await asyncio.to_thread(repo.read_file, safe_file)
                if current_doc and hashlib.sha256(current_doc.get("content", "").encode("utf-8")).hexdigest() != original_sha:
                    await events.status("❌ Conflit d'édition concurrente détecté.", done=True)
                    return wrap_tool_output(
                        text=f"❌ Conflit d'édition concurrente (OCC) : Le fichier `{safe_file}` a été modifié pendant l'édition assistée. Le Modèle DOIT relire le fichier avant de réappliquer les modifications.",
                        status={"status": "error", "error_code": "concurrent_modification_conflict", "filename": safe_file}
                    )
                try:
                    commit_hash = await asyncio.to_thread(repo.commit_file, safe_file, modified, msg)
                except CodexPathConflictError as e:
                    await events.status(f"❌ Conflit de chemin sur {safe_file}.", done=True)
                    return wrap_tool_output(
                        text=f"❌ {e}",
                        status={"status": "error", "error_code": "path_conflict", "filename": safe_file, "workspace": workspace},
                    )
                await self._update_registry(state, safe_file, lang, line_count, commit_hash, msg, workspace)

            urn_ref = f"[{safe_file}](codex:current:{workspace}/{safe_file})"
            await events.status(f"✅ Édition assistée terminée (commit {commit_hash[:7]}).", done=True)
            return wrap_tool_output(
                text=f"Fichier `{safe_file}` modifié par agent éditeur dans l'espace `{workspace}`.\n- Instructions : {instructions}\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`\n- URN : {urn_ref}",
                model_requested=self.user_valves.CODEX_EDIT_MODEL,
                model_used=model_key or "unknown",
                status={
                    "status": "success",
                    "mode": "assisted_edition",
                    "filename": safe_file,
                    "workspace": workspace,
                    "language": lang,
                    "lines": line_count,
                    "commit": commit_hash[:12],
                    "model_used": model_key or "unknown"
                },
                reason=reason
            )

        return "❌ Ni `new_content` ni `instructions` fournis."

    @echo_tool
    async def delete_codex(
        self,
        filename: str,
        workspace: CODEX_WRITABLE_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        recursive: bool = False,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Supprime un fichier ou un dossier du Codex (`main` ou `sandbox`, avec `recursive=True` requis si dossier non vide)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        ro_err = self._check_generic_writable(workspace, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        async with get_codex_lock(uid, cid):
            try:
                safe_target = repo._secure_path(filename)
            except ValueError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "invalid_path", "filename": filename, "workspace": workspace},
                )

            is_dir = os.path.isdir(os.path.join(repo.repo_path, *safe_target.split("/")))
            try:
                delete_result = await asyncio.to_thread(repo.delete_file, safe_target, f"Delete {safe_target}", recursive)
            except CodexDirectoryNotEmptyError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={
                        "status": "error",
                        "error_code": "directory_not_empty",
                        "filename": safe_target,
                        "workspace": workspace,
                        "entries_count": e.entries_count,
                    },
                )
            except ValueError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "invalid_path", "filename": safe_target, "workspace": workspace},
                )

            if not delete_result:
                return wrap_tool_output(
                    text=f"❌ Cible `{safe_target}` introuvable dans `{workspace}`.",
                    status={"status": "error", "error_code": "not_found", "filename": safe_target, "workspace": workspace},
                )

            commit_hash, paths_to_rm = delete_result
            await sync_codex_registry_delete(
                uid,
                cid,
                workspace,
                paths_to_rm,
                dir_rel_path=safe_target if is_dir else None,
            )

        target_type = "directory" if is_dir else "file"
        await events.status(f"🗑️ {safe_target} supprimé (commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(
            text=f"Cible `{safe_target}` ({target_type}) supprimée avec succès de l'espace `{workspace}` ({len(paths_to_rm)} fichier(s)).\n- Commit : `{commit_hash[:12]}`",
            status={
                "status": "success",
                "filename": safe_target,
                "workspace": workspace,
                "target_type": target_type,
                "deleted_count": len(paths_to_rm),
                "deleted_paths": paths_to_rm[:50],
                "commit": commit_hash[:12],
            }
        )

    # =========================================================================
    # LECTURE
    # =========================================================================

    @echo_tool
    async def read_codex(
        self,
        filename: str,
        start_line: int = None,
        end_line: int = None,
        commit_hash: str = None,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        target_chat_id: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Lit le contenu d'un fichier du Codex (optionnel: plage de lignes, commit_hash, ou target_chat_id en lecture seule). Les flux binaires sont interceptés sans transmission de données brutes."""
        ctx, err = await self._resolve_and_verify_file(
            filename, workspace, target_chat_id, commit_hash, __user__, __metadata__, start_line, end_line
        )
        if err:
            return err

        cid = ctx["effective_cid"]
        is_remote = ctx["is_remote"]
        safe_file = ctx["safe_file"]
        file_info = ctx["file_info"]
        lang = CodexRepo.detect_language(safe_file)
        remote_badge = f" 🔒 [Lecture Seule - Chat `{cid}`]" if is_remote else ""

        if file_info.get("is_binary"):
            mime = file_info.get("mime", "application/octet-stream")
            size_bytes = file_info.get("size_bytes", 0)
            urn_scope = cid if is_remote else "current"
            urn_ref = f"[{safe_file}](codex:{urn_scope}:{workspace}/{safe_file})"
            return wrap_tool_output(
                text=f"⚠️ **{safe_file}**{remote_badge} : Fichier binaire ({mime}, {size_bytes} octets). Le Modèle DOIT utiliser un outil dédié pour lire ou manipuler ce fichier.\n- URN : {urn_ref}",
                status={
                    "status": "warning",
                    "error_code": "binary_content_omitted",
                    "filename": safe_file,
                    "workspace": workspace,
                    "is_binary": True,
                    "mime": mime,
                    "size_bytes": size_bytes,
                    "is_remote": is_remote,
                    "target_chat_id": cid if is_remote else None,
                    "commit_hash": commit_hash[:12] if commit_hash else "HEAD",
                }
            )

        raw_content = ctx["commit_content"] if commit_hash else (ctx["head_result"].get("content") or "")
        lines = raw_content.splitlines()
        page = paginate_lines_window(
            lines,
            start_line=start_line,
            end_line=end_line,
            with_line_numbers=False,
        )
        total_lines = page["total_lines"]
        s = page["start_line"]

        if page["out_of_range"]:
            urn_scope = cid if is_remote else "current"
            urn_ref = f"[{safe_file}](codex:{urn_scope}:{workspace}/{safe_file})"
            return wrap_tool_output(
                text=f"⚠️ **{safe_file}**{remote_badge} : `start_line={s}` dépasse le nombre total de lignes ({total_lines}).\n- URN : {urn_ref}",
                status={
                    "status": "warning",
                    "filename": safe_file,
                    "workspace": workspace,
                    "language": lang,
                    "total_lines": total_lines,
                    "lines_returned": 0,
                    "has_more": False,
                    "is_remote": is_remote,
                    "target_chat_id": cid if is_remote else None,
                    "commit_hash": commit_hash[:12] if commit_hash else "HEAD",
                },
            )

        actual_e = page["end_line"]
        content_subset = "\n".join(page["selected_lines"])
        lines_returned = page["lines_returned"]
        has_more = page["has_more"]
        next_s = page["next_start_line"]
        next_e = page["next_end_line"]
        commit_label = f" (commit {commit_hash[:7]})" if commit_hash else ""
        range_info = (
            f"lignes {s}-{actual_e}{commit_label}"
            if (start_line is not None or end_line is not None or has_more)
            else f"complet{commit_label}"
        )
        pagination_notice = (
            f"\n\n⚠️ [LECTURE PAGINÉE : lignes {s} à {actual_e} sur {total_lines} affichées (plafond {ECHO_CODEX_MAX_READ_LINES} lignes / {ECHO_CODEX_MAX_READ_BYTES} octets). "
            f"Pour lire la suite : `read_codex(filename=\"{safe_file}\", workspace=\"{workspace}\", start_line={next_s}, end_line={next_e})`]"
            if has_more
            else ""
        )
        urn_scope = cid if is_remote else "current"
        urn_ref = f"[{safe_file}](codex:{urn_scope}:{workspace}/{safe_file})"
        return wrap_tool_output(
            text=f"**{safe_file}**{remote_badge} ({lang}, {total_lines} lignes total, {range_info})\n- URN : {urn_ref}\n\n```{lang}\n{content_subset}\n```{pagination_notice}",
            status={
                "status": "success",
                "filename": safe_file,
                "workspace": workspace,
                "language": lang,
                "total_lines": total_lines,
                "lines_returned": lines_returned,
                "start_line": s,
                "end_line": actual_e,
                "has_more": has_more,
                "next_start_line": next_s,
                "is_remote": is_remote,
                "target_chat_id": cid if is_remote else None,
                "commit_hash": commit_hash[:12] if commit_hash else "HEAD",
            },
        )

    @echo_tool
    async def restore_codex(
        self,
        filename: str,
        commit_hash: str,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Restaure un fichier du Codex à une version antérieure à partir de son `commit_hash` (`workspace='main'`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        ro_err = self._check_generic_writable(workspace, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        if not is_codex_workspace_versioned(workspace):
            return wrap_tool_output(
                text=f"❌ Action refusée : L'espace `{workspace}` n'est pas versionné par Git.",
                status={"status": "error", "error_code": "workspace_not_versioned", "workspace": workspace},
            )

        try:
            safe_file = repo._secure_path(filename)
        except ValueError as e:
            return wrap_tool_output(
                text=f"❌ {e}",
                status={"status": "error", "error_code": "invalid_path", "filename": filename, "workspace": workspace},
            )

        self._emit_urn_directive(state)

        async with get_codex_lock(uid, cid):
            old_res = await asyncio.to_thread(repo.read_file_at_commit, safe_file, commit_hash)
            if old_res is None:
                return f"❌ Fichier `{safe_file}` introuvable au commit `{commit_hash}`."
            old_content = old_res["content"]

            lang = CodexRepo.detect_language(safe_file)
            msg = f"Restore {safe_file} from {commit_hash[:7]}"
            try:
                new_commit_hash = await asyncio.to_thread(
                    repo.commit_file,
                    safe_file,
                    old_content,
                    msg,
                    is_binary=old_res.get("is_binary"),
                )
            except CodexPathConflictError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "path_conflict", "filename": safe_file, "workspace": workspace},
                )
            line_count = old_res.get("total_lines", old_content.count("\n") + 1)

            await self._update_registry(state, safe_file, lang, line_count, new_commit_hash, msg, workspace)

        urn_ref = f"[{safe_file}](codex:current:{workspace}/{safe_file})"
        await events.status(f"🔄 {safe_file} restauré depuis {commit_hash[:7]}.", done=True)
        return wrap_tool_output(
            text=f"Fichier `{safe_file}` restauré avec succès depuis `{commit_hash[:7]}` dans l'espace `{workspace}`.\n- Nouveau Commit : `{new_commit_hash[:12]}`\n- URN : {urn_ref}",
            status={
                "status": "success",
                "filename": safe_file,
                "workspace": workspace,
                "restored_from": commit_hash[:12],
                "new_commit": new_commit_hash[:12]
            }
        )

    @echo_tool
    async def search_codex(
        self,
        query: str,
        target_paths: list[str] = None,
        is_regex: bool = False,
        commit_hash: str = None,
        trace_history: bool = False,
        history_depth: int = 50,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Recherche un motif texte ou regex dans les fichiers actuels du Codex ou à travers l'historique des modifications (`trace_history=True`)."""
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant."

        if trace_history:
            # Mode Temporel (Delta / Pickaxe) dans le Codex
            results = await asyncio.to_thread(repo.trace_history, query, target_paths, history_depth)
            if not results:
                scope = f"les chemins `{target_paths}`" if target_paths else "l'historique global du Codex"
                return wrap_tool_output(
                    text=f"Aucune évolution trouvée pour la chaîne `{query}` dans {scope}.",
                    status={"status": "success", "mode": "trace_history", "query": query, "commits_found": 0, "target_paths": target_paths}
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
                status={"status": "success", "mode": "trace_history", "query": query, "commits_found": len(results), "target_paths": target_paths}
            )
            
        else:
            # Mode Spatial (Grep) dans le Codex
            raw_files = await asyncio.to_thread(repo.list_all_files)
            all_files = [f["filename"] for f in raw_files if f["type"] == "file"]
            if target_paths:
                norm_paths = [p.replace("\\", "/").strip("/") for p in target_paths if p]
                files_to_check = [f for f in all_files if any(f.startswith(p) for p in norm_paths)]
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
                    status={"status": "success", "mode": "spatial_grep", "query": query, "files_matched_count": 0, "total_matches": 0, "commit": commit_hash or "HEAD"}
                )

            return wrap_tool_output(
                text=f"**Résultats de la recherche Codex pour `{query}` :**\n\n" + "\n\n".join(all_results),
                status={"status": "success", "mode": "spatial_grep", "query": query, "files_matched_count": len(all_results), "total_matches": total_matches_count, "commit": commit_hash or "HEAD"}
            )

    @echo_tool
    async def summarize_codex(
        self,
        filename: str,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Génère un résumé technique structuré d'un fichier volumineux du Codex."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        result = await asyncio.to_thread(repo.read_file, filename)
        if not result:
            return f"❌ Fichier `{filename}` introuvable."

        if result.get("is_binary"):
            mime = result.get("mime", "application/octet-stream")
            size_bytes = result.get("size_bytes", 0)
            return wrap_tool_output(
                text=f"⚠️ **{filename}** : Fichier binaire ({mime}, {size_bytes} octets). La synthèse textuelle via `summarize_codex` est non applicable. Le Modèle DOIT utiliser `read_multimedia_file` (si image/audio/vidéo) ou un script dédié via `code_executor`.",
                status={
                    "status": "warning",
                    "error_code": "binary_summary_not_supported",
                    "filename": filename,
                    "workspace": workspace,
                    "is_binary": True,
                    "mime": mime,
                    "size_bytes": size_bytes
                }
            )

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
            }
        )

    @echo_tool
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
            return "❌ Contexte manquant (chat_id)."

        # Inscription déterministe de la directive URN sur la session active dès l'entrée
        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=cid))

        chats_data = await asyncio.to_thread(CodexRepo.get_user_codex_chats, uid, cid)
        others = chats_data.get("others", [])
        if not others:
            return wrap_tool_output(
                text="Aucune autre conversation de l'utilisateur ne dispose d'un espace Codex.",
                status={"status": "success", "count": 0}
            )

        users_root = getattr(echo_constants, "ECHO_USERS_ROOT", ECHO_USERS_ROOT)
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")

        def _inspect_chat_codex(target_cid: str) -> dict:
            info = {"main_count": 0, "plans_count": 0, "generated_count": 0, "sandbox_count": 0, "extensions": set()}
            codex_base = os.path.join(users_root, safe_uid, "chats", target_cid, CODEX_DIR_NAME)
            for ws in ECHO_CODEX_WORKSPACES:
                ws_path = os.path.join(codex_base, ws)
                if os.path.exists(ws_path) and not os.path.islink(ws_path):
                    try:
                        for root, dirs, files in os.walk(ws_path, followlinks=False):
                            dirs[:] = [
                                d for d in dirs
                                if not d.startswith(".")
                                and d not in ECHO_SYNC_EXCLUDE_LIST
                                and not os.path.islink(os.path.join(root, d))
                            ]
                            for fname in files:
                                full_f = os.path.join(root, fname)
                                if (
                                    not fname.startswith(".")
                                    and fname not in ECHO_SYNC_EXCLUDE_LIST
                                    and not os.path.islink(full_f)
                                    and os.path.isfile(full_f)
                                ):
                                    if ws == CODEX_WORKSPACE_MAIN:
                                        info["main_count"] += 1
                                    elif ws == CODEX_WORKSPACE_PLANS:
                                        info["plans_count"] += 1
                                    elif ws == CODEX_WORKSPACE_GENERATED:
                                        info["generated_count"] += 1
                                    else:
                                        info["sandbox_count"] += 1
                                    _, ext = os.path.splitext(fname)
                                    if ext:
                                        info["extensions"].add(ext.lower())
                    except Exception:
                        pass
            return info

        lines = [
            "## 📚 Conversations Distantes Disponibles avec Espace Codex\n",
            "| Chat ID | Titre | Fichiers (main) | Plans | Generated | Sandbox | Types de fichiers |",
            "|---|---|---|---|---|---|---|"
        ]

        chats_summary = []
        for c in others:
            target_id = c["id"]
            meta_info = await asyncio.to_thread(_inspect_chat_codex, target_id)
            exts_str = ", ".join(sorted(meta_info["extensions"])) if meta_info["extensions"] else "aucun"
            lines.append(
                f"| `{target_id}` | {c['title']} | {meta_info['main_count']} | {meta_info['plans_count']} "
                f"| {meta_info['generated_count']} | {meta_info['sandbox_count']} | `{exts_str}` |"
            )
            chats_summary.append({
                "chat_id": target_id,
                "title": c["title"],
                "main_files_count": meta_info["main_count"],
                "plans_files_count": meta_info["plans_count"],
                "generated_files_count": meta_info["generated_count"],
                "sandbox_files_count": meta_info["sandbox_count"],
                "extensions": list(meta_info["extensions"])
            })

        footer = (
            f"\n\n**Total : {len(others)} autre(s) conversation(s) accessible(s) en lecture seule.**\n\n"
            "> **Rappels d'utilisation pour le Modèle** :\n"
            "> - Pour lister l'arborescence complète d'un chat distant : `list_codex(target_chat_id=\"<Chat ID>\", workspace=\"main\")`\n"
            "> - Pour lire un fichier d'un chat distant en lecture seule : `read_codex(filename=\"...\", target_chat_id=\"<Chat ID>\")`"
        )

        return wrap_tool_output(
            text="\n".join(lines) + footer,
            status={"status": "success", "count": len(others), "chats": chats_summary}
        )

    @echo_tool
    async def list_codex(
        self,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        path: str = "",
        recursive: bool = False,
        target_chat_id: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Liste les fichiers et dossiers du Codex (racine ou sous-dossier `path`, mode `recursive` optionnel, local ou `target_chat_id`)."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")
        if not cid:
            return "❌ Contexte manquant (chat_id)."

        # Inscription déterministe de la directive URN sur la session active dès l'entrée
        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=cid))

        if target_chat_id and not CodexRepo.validate_user_chat(uid, target_chat_id):
            return f"❌ Identifiant de conversation `{target_chat_id}` invalide ou inaccessible."

        uid, cid, repo, state, is_remote = self._get_context(__user__, __metadata__, workspace, target_chat_id)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        try:
            clean_sub = repo._secure_path(path, allow_empty=True)
        except ValueError as e:
            return wrap_tool_output(
                text=f"❌ {e}",
                status={"status": "error", "error_code": "invalid_path", "path": path, "workspace": workspace},
            )

        raw_entries = await asyncio.to_thread(
            repo.list_directory,
            clean_sub,
            recursive,
            CODEX_LIST_MAX_ENTRIES + 1,
        )
        truncated = len(raw_entries) > CODEX_LIST_MAX_ENTRIES
        files = raw_entries[:CODEX_LIST_MAX_ENTRIES] if truncated else raw_entries

        remote_badge = " 🔒 [Lecture Seule]" if is_remote else ""
        if not files:
            scope_desc = f"sous-dossier `{clean_sub}` de l'espace `{workspace}`" if clean_sub else f"Codex ({workspace})"
            msg = (
                f"Le {scope_desc} de la conversation `{cid}`{remote_badge} est vide."
                if is_remote
                else f"Le {scope_desc} de cette conversation est vide."
            )
            return wrap_tool_output(
                text=msg,
                status={
                    "status": "success",
                    "files_count": 0,
                    "workspace": workspace,
                    "path": clean_sub,
                    "recursive": recursive,
                    "truncated": False,
                    "is_remote": is_remote,
                },
            )

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
        if truncated:
            footer += f"\n\n⚠️ Résultat tronqué à {CODEX_LIST_MAX_ENTRIES} entrées (ciblez un sous-dossier via `path`)."
        if is_remote:
            footer += (
                f"\n\n*(Session source distante : `{cid}` - Consultation en lecture seule)*\n"
                f"> Pour lire un fichier : `read_codex(filename=\"<fichier>\", target_chat_id=\"{cid}\", workspace=\"{workspace}\")`"
            )

        files_meta = [{"name": f["filename"], "type": f.get("type", "file"), "size": f["size_bytes"], "lang": f["lang"]} for f in files]
        return wrap_tool_output(
            text="\n".join(lines) + footer,
            status={
                "status": "success",
                "files_count": len(files),
                "workspace": workspace,
                "path": clean_sub,
                "recursive": recursive,
                "truncated": truncated,
                "is_remote": is_remote,
                "files": files_meta,
            },
        )

    @echo_tool
    async def history_codex(
        self,
        filename: Optional[str] = None,
        limit: int = 20,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Affiche l'historique des versions (`commit_hash`, message, date) du Codex global ou d'un fichier `filename`."""
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        log = await asyncio.to_thread(repo.get_log, filename, limit)
        if not log:
            scope = f"de `{filename}`" if filename else "du Codex"
            return wrap_tool_output(
                text=f"Aucun historique {scope}.",
                status={"status": "success", "filename": filename, "workspace": workspace, "commits_count": 0, "limit": limit}
            )

        import time as time_mod
        lines = []
        for entry in log:
            dt = time_mod.strftime("%Y-%m-%d %H:%M", time_mod.localtime(entry["timestamp"]))
            lines.append(f"- `{entry['hash']}` — {entry['message']} ({dt})")

        scope = f"de `{filename}`" if filename else "global"
        return wrap_tool_output(
            text=f"## Historique {scope} ({len(log)} commits)\n\n" + "\n".join(lines),
            status={"status": "success", "filename": filename, "workspace": workspace, "commits_count": len(log), "limit": limit}
        )

    @echo_tool
    async def open_codex_ui(
        self,
        filename: str,
        commit_hash: str = None,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        target_chat_id: Optional[str] = None,
        __user__: dict = None,
        __metadata__: dict = None,
        __messages__: list = [],
        __model__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Ouvre le HUD Codex dans l'interface de l'Utilisateur pour inspecter, éditer ou prévisualiser statiquement un fichier (Monaco Editor / aperçu passif) du chat courant ou d'un chat distant (`target_chat_id`)."""
        events = EchoEvents(__event_emitter__, __event_call__)

        # Support défensif : inversion workspace / commit_hash si appelé positionnellement ("file.py", "sandbox")
        eff_ws = workspace
        eff_commit = commit_hash
        if eff_commit in ECHO_CODEX_WORKSPACES and eff_ws == CODEX_DEFAULT_WORKSPACE:
            eff_ws = eff_commit
            eff_commit = None

        ctx, err = await self._resolve_and_verify_file(
            filename, eff_ws, target_chat_id, eff_commit, __user__, __metadata__
        )
        if err:
            return err

        session_cid = ctx["session_cid"]
        effective_cid = ctx["effective_cid"]
        is_remote = ctx["is_remote"]
        safe_file = ctx["safe_file"]
        uid = (__user__ or {}).get("id", "anonymous")

        import orjson as json
        tab_payload = {
            "file": safe_file,
            "workspace": eff_ws,
            "targetChatId": effective_cid,
            "commitHash": eff_commit or "",
        }
        wm = get_window_manager()
        await wm.open_tab(uid, session_cid, "echo-codex", tab_payload)

        detail_json = json.dumps(
            {"chatId": session_cid, "kind": "echo-codex", "payload": tab_payload}
        ).decode("utf-8")
        await events.emit_execute(
            f"window.dispatchEvent(new CustomEvent('echo:open-tab', {{ detail: {detail_json} }}));"
        )

        remote_badge = f" 🔒 [Lecture Seule - Chat `{effective_cid}`]" if is_remote else ""
        urn_scope = effective_cid if is_remote else "current"
        urn_ref = f"[{safe_file}](codex:{urn_scope}:{eff_ws}/{safe_file})"
        await events.status(f"Ouverture du Codex demandée pour {safe_file}...", done=True)

        return wrap_tool_output(
            text=f"✅ Requête d'ouverture visuelle transmise pour `{safe_file}` dans l'espace `{eff_ws}`{remote_badge}.\n- URN : {urn_ref}",
            status={
                "status": "success",
                "action": "open_codex_ui",
                "filename": safe_file,
                "workspace": eff_ws,
                "commit_hash": eff_commit,
                "is_remote": is_remote,
                "target_chat_id": effective_cid if is_remote else None,
            }
        )

    @echo_tool
    async def create_codex_directory(
        self,
        path: str,
        workspace: CODEX_WRITABLE_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Crée un dossier (ou une arborescence de dossiers) dans le Codex (`main` ou `sandbox`)."""
        uid, cid, repo, _, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(
                text="❌ Contexte manquant (chat_id).",
                status={"status": "error", "error_code": "missing_context"},
            )

        ro_err = self._check_generic_writable(workspace, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        async with get_codex_lock(uid, cid):
            try:
                safe_rel, created = await asyncio.to_thread(repo.create_directory, path)
            except CodexPathConflictError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "path_conflict", "path": path, "workspace": workspace},
                )
            except ValueError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={"status": "error", "error_code": "invalid_path", "path": path, "workspace": workspace},
                )

        msg = (
            f"Dossier `{safe_rel}` créé avec succès dans l'espace `{workspace}`."
            if created
            else f"Le dossier `{safe_rel}` existe déjà dans l'espace `{workspace}`."
        )
        return wrap_tool_output(
            text=msg,
            status={
                "status": "success",
                "path": safe_rel,
                "workspace": workspace,
                "created": created,
                "already_existed": not created,
            },
        )

    @echo_tool
    async def move_codex(
        self,
        source_path: str,
        destination_path: str,
        workspace: CODEX_WRITABLE_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        overwrite: bool = False,
        commit_message: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Déplace ou renomme un fichier ou un dossier à l'intérieur d'un même espace Codex (`main` ou `sandbox`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return wrap_tool_output(
                text="❌ Contexte manquant (chat_id).",
                status={"status": "error", "error_code": "missing_context"},
            )

        ro_err = self._check_generic_writable(workspace, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        self._emit_urn_directive(state)
        msg = commit_message or f"Move {source_path} -> {destination_path}"

        async with get_codex_lock(uid, cid):
            try:
                mv_res = await asyncio.to_thread(
                    repo.move_entry,
                    source_path,
                    destination_path,
                    msg,
                    "ECHO Codex",
                    overwrite,
                )
            except CodexPathConflictError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={
                        "status": "error",
                        "error_code": "path_conflict",
                        "source_path": source_path,
                        "destination_path": destination_path,
                        "workspace": workspace,
                    },
                )
            except ValueError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={
                        "status": "error",
                        "error_code": "invalid_move",
                        "source_path": source_path,
                        "destination_path": destination_path,
                        "workspace": workspace,
                    },
                )

            if mv_res is None:
                return wrap_tool_output(
                    text=f"❌ Déplacement impossible de `{source_path}` vers `{destination_path}` dans `{workspace}`.",
                    status={
                        "status": "error",
                        "error_code": "move_failed",
                        "source_path": source_path,
                        "destination_path": destination_path,
                        "workspace": workspace,
                    },
                )

            await sync_codex_registry_relocate(
                uid,
                cid,
                workspace,
                mv_res["moved_pairs"],
                mv_res["commit_hash"],
                msg,
                overwrite=overwrite,
                repo_base_dir=repo.repo_path,
            )

        old_p = mv_res["old_path"]
        new_p = mv_res["new_path"]
        target_type = mv_res["target_type"]
        commit_hash = mv_res["commit_hash"]
        moved_count = len(mv_res["moved_pairs"])
        await events.status(f"📦 {old_p} déplacé vers {new_p}.", done=True)

        text_lines = [
            f"{'Fichier' if target_type == 'file' else 'Dossier'} déplacé avec succès dans l'espace `{workspace}`.",
            f"- Source : `{old_p}`",
            f"- Destination : `{new_p}`",
            f"- Fichiers déplacés : {moved_count}",
            f"- Commit : `{commit_hash[:12]}`",
        ]
        if target_type == "file":
            text_lines.append(f"- URN : [{new_p}](codex:current:{workspace}/{new_p})")

        return wrap_tool_output(
            text="\n".join(text_lines),
            status={
                "status": "success",
                "target_type": target_type,
                "old_path": old_p,
                "new_path": new_p,
                "workspace": workspace,
                "moved_files_count": moved_count,
                "commit": commit_hash[:12],
            },
        )

    @echo_tool
    async def transfer_codex(
        self,
        source_path: str,
        source_workspace: CODEX_TRANSFER_SOURCE_LITERAL,
        destination_workspace: CODEX_WRITABLE_WORKSPACE_LITERAL,
        destination_path: Optional[str] = None,
        mode: Literal["copy", "move"] = "copy",
        overwrite: bool = False,
        commit_message: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Copie ou déplace un fichier ou dossier entre espaces autorisés (ex: `files`/`browserviews`/`main`/`plans`/`generated` vers `sandbox`, ou `sandbox` vers `main`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")
        if not cid:
            return wrap_tool_output(
                text="❌ Contexte manquant (chat_id).",
                status={"status": "error", "error_code": "missing_context"},
            )

        src_ws = str(source_workspace or "").strip().lower()
        dst_ws = str(destination_workspace or "").strip().lower()

        ro_err = self._check_generic_writable(dst_ws, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        if src_ws == dst_ws:
            return wrap_tool_output(
                text=f"❌ Transfert intra-espace (`{src_ws}` -> `{dst_ws}`) interdit ; utilisez `move_codex`.",
                status={
                    "status": "error",
                    "error_code": "intra_workspace_forbidden",
                    "source_workspace": src_ws,
                    "destination_workspace": dst_ws,
                },
            )

        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=cid))

        async with get_codex_lock(uid, cid):
            try:
                tr = await asyncio.to_thread(
                    transfer_codex_entries,
                    uid,
                    cid,
                    source_path,
                    src_ws,
                    dst_ws,
                    destination_path,
                    mode,
                    overwrite,
                    commit_message,
                )
            except FileNotFoundError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={
                        "status": "error",
                        "error_code": "source_not_found",
                        "source_path": source_path,
                        "source_workspace": src_ws,
                    },
                )
            except CodexPathConflictError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={
                        "status": "error",
                        "error_code": "destination_conflict",
                        "destination_path": destination_path or source_path,
                        "destination_workspace": dst_ws,
                    },
                )
            except CodexQuotaExceededError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={
                        "status": "error",
                        "error_code": "quota_exceeded",
                        "source_workspace": src_ws,
                        "destination_workspace": dst_ws,
                    },
                )
            except ValueError as e:
                return wrap_tool_output(
                    text=f"❌ {e}",
                    status={
                        "status": "error",
                        "error_code": "invalid_transfer",
                        "source_workspace": src_ws,
                        "destination_workspace": dst_ws,
                    },
                )

            if is_codex_workspace_indexed(dst_ws) and tr.get("transferred_files"):
                dst_repo = CodexRepo(uid, cid, workspace=dst_ws)
                reg_msg = commit_message or (
                    f"Transfer ({tr['mode']}) {src_ws}:{tr['source_path']} -> {dst_ws}:{tr['destination_path']}"
                )
                for rel_p in tr["transferred_files"]:
                    await sync_codex_registry_save(
                        uid,
                        cid,
                        dst_ws,
                        rel_p,
                        tr["commit_hash"],
                        reg_msg,
                        repo_base_dir=dst_repo.repo_path,
                    )

        transferred_files = tr.get("transferred_files") or []
        skipped_entries = tr.get("skipped_entries") or []
        dst_p = tr["destination_path"]
        commit_hash = tr["commit_hash"]
        await events.status(
            f"🔁 Transfert {src_ws} -> {dst_ws} terminé ({len(transferred_files)} fichier(s)).",
            done=True,
        )

        text_lines = [
            f"Transfert (`{tr['mode']}`) effectué avec succès : `{src_ws}:{tr['source_path']}` -> `{dst_ws}:{dst_p}`.",
            f"- Type : `{tr['target_type']}`",
            f"- Fichiers transférés : {len(transferred_files)} ({tr['total_bytes']} octets)",
            f"- Commit : `{commit_hash[:12]}`",
        ]
        if tr["target_type"] == "file":
            text_lines.append(f"- URN : [{dst_p}](codex:current:{dst_ws}/{dst_p})")
        if skipped_entries:
            text_lines.append(f"- Entrées ignorées : {len(skipped_entries)}")

        return wrap_tool_output(
            text="\n".join(text_lines),
            status={
                "status": "success",
                "target_type": tr["target_type"],
                "source_workspace": tr["source_workspace"],
                "source_path": tr["source_path"],
                "destination_workspace": tr["destination_workspace"],
                "destination_path": dst_p,
                "mode": tr["mode"],
                "transferred_count": len(transferred_files),
                "transferred_files": transferred_files[:50],
                "skipped_entries": skipped_entries,
                "total_bytes": tr["total_bytes"],
                "commit": commit_hash[:12],
            },
        )
