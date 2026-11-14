"""
title: ECHO Codex Editor
author: Wilfried BARNAVON
version: 5.239.17.1
description: Permet au modèle de manipuler un espace virtuel (Codex) avec versioning Git invisible et accès natif à Python/Bash sécurisés.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.17.1: Pagination bornée unifiée (HEAD et commits) de read_codex à ECHO_CODEX_MAX_READ_LINES (800 lignes) et ECHO_CODEX_MAX_READ_BYTES (46080 octets), indicateurs has_more/next_start_line et skip_truncation=True.
# 5.235.5.1: Remplacement de l'accès direct SQLite par la méthode abstraite state.has_pending_directive.
# 5.235.4.1: Sécurisation binaire exhaustive : refus d'édition assistée, propagation des métadonnées dans restore_codex et guard clause dans summarize_codex.
# 5.235.2.2: Précision de la docstring OpenAPI de read_codex pour l'introspection dynamique tool_advisor.
# 5.235.2.1: Détection et omission sécurisée des fichiers binaires dans read_codex (HEAD et commits) avec statut warning explicite et directive PAS.

# ECHO CONFIG NAME : ECHO Codex

import os
import sys
import hashlib
import asyncio
from pydantic import BaseModel, Field
from typing import Optional, Any

sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output, echo_tool
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_aec import EchoAEC
import echo_constants
from echo_constants import (
    CODEX_DEFAULT_WORKSPACE,
    CODEX_DIR_NAME,
    CODEX_WORKSPACE_GENERATED,
    CODEX_WORKSPACE_LITERAL,
    CODEX_WORKSPACE_MAIN,
    CODEX_WORKSPACE_PLANS,
    ECHO_API_MAX_RETRIES,
    ECHO_CODEX_MAX_READ_BYTES,
    ECHO_CODEX_MAX_READ_LINES,
    ECHO_CODEX_WORKSPACES,
    ECHO_SYNC_EXCLUDE_LIST,
    ECHO_USERS_ROOT,
    FILE_INGESTION_STATUS,
    get_codex_workspace_resource_type,
    get_generation_config,
    is_codex_workspace_generic_writable,
    is_codex_workspace_indexed,
)
from echo_prompts import SYS_CODEX_EDIT, SYS_CODEX_SUMMARIZE
from echo_codex_git import CodexRepo
from echo_paths import get_codex_lock, format_codex_storage_path


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
        if is_codex_workspace_indexed(workspace):
            res_type = get_codex_workspace_resource_type(workspace) or "codex"
            await asyncio.to_thread(
                state.save_resource,
                id=filename,
                name=filename,
                resource_type=res_type,
                status=FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
                git_tracked=True,
                language=lang,
                lines=line_count,
                last_commit=commit_hash[:12],
                commit_msg=msg,
                storage_path=format_codex_storage_path(filename, workspace),
            )

    def _check_generic_writable(self, workspace: str, user_id: str, chat_id: Optional[str], metadata: Optional[dict]) -> Optional[dict]:
        """Vérifie si l'espace de travail accepte les écritures via les outils génériques Codex."""
        if not is_codex_workspace_generic_writable(workspace):
            hint = (
                "Utilisez les outils dédiés `build_plan`, `update_plan`, `update_tasks` ou `delete_plan`."
                if workspace == CODEX_WORKSPACE_PLANS
                else "Utilisez l'outil dédié `generate_image`."
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

        async with get_codex_lock(uid, cid):
            if await asyncio.to_thread(repo.read_file, filename):
                return f"❌ Le fichier `{filename}` existe déjà dans l'espace `{workspace}`."

            await events.status(f"📝 Création de {filename}...", done=False)

            lang = language or CodexRepo.detect_language(filename)
            msg = commit_message or f"Create {filename}"
            line_count = content.count("\n") + 1

            commit_hash = await asyncio.to_thread(repo.commit_file, filename, content, msg)
            await self._update_registry(state, filename, lang, line_count, commit_hash, msg, workspace)

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

        # Vérification existence
        async with get_codex_lock(uid, cid):
            existing = await asyncio.to_thread(repo.read_file, filename)
        if not existing:
            return f"❌ Fichier `{filename}` introuvable dans l'espace `{workspace}`."

        lang = CodexRepo.detect_language(filename)

        # Mode 1 : Remplacement direct
        if new_content is not None:
            msg = commit_message or f"Edit {filename}"
            line_count = new_content.count("\n") + 1
            async with get_codex_lock(uid, cid):
                commit_hash = await asyncio.to_thread(repo.commit_file, filename, new_content, msg)
                await self._update_registry(state, filename, lang, line_count, commit_hash, msg, workspace)

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
                }
            )

        # Mode 2 : Édition assistée via sub-chat
        if instructions:
            if existing.get("is_binary"):
                return wrap_tool_output(
                    text=f"❌ Action refusée : Le fichier `{filename}` est un binaire. L'édition assistée par instructions n'est applicable qu'aux fichiers texte.",
                    status={"status": "error", "error_code": "binary_edit_not_supported", "filename": filename}
                )

            await events.status(f"🧠 Édition assistée de {filename}...", done=False)

            content = existing["content"]
            original_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
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
                return "❌ Échec : aucun modèle disponible pour l'édition."

            modified = self._extract_llm_text(data)
            if not modified:
                await events.status("❌ L'agent éditeur n'a produit aucun contenu.", done=True)
                return "❌ L'agent éditeur n'a pas retourné de contenu exploitable."

            msg = commit_message or f"AI edit: {instructions[:60]}"
            line_count = modified.count("\n") + 1
            async with get_codex_lock(uid, cid):
                current_doc = await asyncio.to_thread(repo.read_file, filename)
                if current_doc and hashlib.sha256(current_doc.get("content", "").encode("utf-8")).hexdigest() != original_sha:
                    await events.status("❌ Conflit d'édition concurrente détecté.", done=True)
                    return wrap_tool_output(
                        text=f"❌ Conflit d'édition concurrente (OCC) : Le fichier `{filename}` a été modifié pendant l'édition assistée. Relisez le fichier avant de réappliquer vos modifications.",
                        status={"status": "error", "error_code": "concurrent_modification_conflict", "filename": filename}
                    )
                commit_hash = await asyncio.to_thread(repo.commit_file, filename, modified, msg)
                await self._update_registry(state, filename, lang, line_count, commit_hash, msg, workspace)

            urn_ref = f"[{filename}](codex:current:{workspace}/{filename})"
            await events.status(f"✅ Édition assistée terminée (commit {commit_hash[:7]}).", done=True)
            return wrap_tool_output(
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
                reason=reason
            )

        return "❌ Ni `new_content` ni `instructions` fournis."

    @echo_tool
    async def delete_codex(
        self,
        filename: str,
        workspace: CODEX_WORKSPACE_LITERAL = CODEX_DEFAULT_WORKSPACE,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Supprime un fichier du Codex (`main` ou `sandbox`) et met à jour le Registre Unifié."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state, _ = self._get_context(__user__, __metadata__, workspace)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        ro_err = self._check_generic_writable(workspace, uid, cid, __metadata__)
        if ro_err:
            return ro_err

        async with get_codex_lock(uid, cid):
            try:
                delete_result = await asyncio.to_thread(repo.delete_file, filename, f"Delete {filename}")
            except ValueError as e:
                return f"❌ {e}"
            
            if not delete_result:
                return f"❌ Fichier/Dossier `{filename}` introuvable."

            commit_hash, paths_to_rm = delete_result
            if is_codex_workspace_indexed(workspace):
                for path in paths_to_rm:
                    await asyncio.to_thread(state.delete_codex_resource, workspace, path)
        await events.status(f"🗑️ {filename} supprimé (commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(
            text=f"Fichier `{filename}` supprimé avec succès de l'espace `{workspace}`.\n- Commit : `{commit_hash[:12]}`",
            status={
                "status": "success",
                "filename": filename,
                "workspace": workspace,
                "commit": commit_hash[:12],
                "deleted_paths": paths_to_rm
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
        total_lines = len(lines)
        s = max(1, int(start_line)) if start_line is not None else 1

        if total_lines > 0 and s > total_lines:
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
                skip_truncation=True,
            )

        req_e = int(end_line) if end_line is not None else (s + ECHO_CODEX_MAX_READ_LINES - 1)
        max_e = min(total_lines, req_e, s + ECHO_CODEX_MAX_READ_LINES - 1) if total_lines > 0 else 0

        selected_lines = []
        acc_bytes = 0
        actual_e = s - 1
        for ln_no in range(s, max_e + 1):
            line_str = lines[ln_no - 1]
            line_bytes = len(line_str.encode("utf-8")) + 1
            if acc_bytes + line_bytes > ECHO_CODEX_MAX_READ_BYTES and selected_lines:
                break
            selected_lines.append(line_str)
            acc_bytes += line_bytes
            actual_e = ln_no

        content_subset = "\n".join(selected_lines)
        lines_returned = len(selected_lines)
        has_more = actual_e < total_lines
        next_s = actual_e + 1 if has_more else None
        next_e = min(total_lines, next_s + ECHO_CODEX_MAX_READ_LINES - 1) if next_s else None
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
                "start_line": s if total_lines > 0 else 0,
                "end_line": actual_e if total_lines > 0 else 0,
                "has_more": has_more,
                "next_start_line": next_s,
                "is_remote": is_remote,
                "target_chat_id": cid if is_remote else None,
                "commit_hash": commit_hash[:12] if commit_hash else "HEAD",
            },
            skip_truncation=True,
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

        async with get_codex_lock(uid, cid):
            old_res = await asyncio.to_thread(repo.read_file_at_commit, filename, commit_hash)
            if old_res is None:
                return f"❌ Fichier `{filename}` introuvable au commit `{commit_hash}`."
            old_content = old_res["content"]
            
            lang = CodexRepo.detect_language(filename)
            msg = f"Restore {filename} from {commit_hash[:7]}"
            new_commit_hash = await asyncio.to_thread(
                repo.commit_file,
                filename,
                old_content,
                msg,
                is_binary=old_res.get("is_binary"),
            )
            line_count = old_res.get("total_lines", old_content.count("\n") + 1)
            
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
            raw_files = await asyncio.to_thread(repo.list_files)
            all_files = [f["filename"] for f in raw_files if f["type"] == "file"]
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
                if os.path.exists(ws_path):
                    try:
                        for entry in os.scandir(ws_path):
                            if not entry.name.startswith(".") and entry.name not in ECHO_SYNC_EXCLUDE_LIST:
                                if ws == CODEX_WORKSPACE_MAIN:
                                    info["main_count"] += 1
                                elif ws == CODEX_WORKSPACE_PLANS:
                                    info["plans_count"] += 1
                                elif ws == CODEX_WORKSPACE_GENERATED:
                                    info["generated_count"] += 1
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
            return "❌ Contexte manquant (chat_id)."

        # Inscription déterministe de la directive URN sur la session active dès l'entrée
        self._emit_urn_directive(EchoStateManager(user_id=uid, chat_id=cid))

        if target_chat_id and not CodexRepo.validate_user_chat(uid, target_chat_id):
            return f"❌ Identifiant de conversation `{target_chat_id}` invalide ou inaccessible."

        uid, cid, repo, state, is_remote = self._get_context(__user__, __metadata__, workspace, target_chat_id)
        if not repo:
            return "❌ Contexte manquant (chat_id)."

        files = await asyncio.to_thread(repo.list_files)
        remote_badge = " 🔒 [Lecture Seule]" if is_remote else ""
        if not files:
            msg = f"Le Codex de la conversation `{cid}` ({workspace}){remote_badge} est vide." if is_remote else f"Le Codex de cette conversation ({workspace}) est vide."
            return wrap_tool_output(text=msg, status={"status": "success", "files_count": 0, "workspace": workspace, "is_remote": is_remote})

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
        return wrap_tool_output(
            text="\n".join(lines) + footer,
            status={"status": "success", "files_count": len(files), "workspace": workspace, "is_remote": is_remote, "files": files_meta}
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

        # 1. Injection de la cible client
        target_payload = (
            f"window._echoCodexTarget = {{ "
            f"file: '{safe_file}', "
            f"workspace: '{eff_ws}', "
            f"target_chat_id: '{effective_cid}', "
            f"commit_hash: '{eff_commit or ''}' "
            f"}};\nreturn true;"
        )
        await events.call_execute(target_payload, timeout=5.0)

        # 2. Déclenchement de l'action native avec transmission structurée du payload
        action_payload = {
            "target_chat_id": effective_cid,
            "target_file": safe_file,
            "workspace": eff_ws,
            "commit_hash": eff_commit,
        }
        await events.trigger_action(
            "echo_codex_action", session_cid, user_msg_id, model_id, session_id, payload=action_payload
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
