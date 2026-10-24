"""
title: ECHO Codex
author: Wilfried BARNAVON
version: 5.229.7.2
description: HUD Monaco et Explorateur (Data Island) couplé à une isolation Workspace
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xNiA0aDJhMiAyIDAgMCAxIDIgMnYxNGEyIDIgMCAwIDEtMiAySDZhMiAyIDAgMCAxLTItMlY2YTIgMiAwIDAgMSAyLTJoMiIvPjxyZWN0IHg9IjgiIHk9IjIiIHdpZHRoPSI4IiBoZWlnaHQ9IjQiIHJ4PSIxIiByeT0iMSIvPjxwYXRoIGQ9Ik0xMCAxMmw0LTRtLTQgNGw0IDQiLz48L3N2Zz4=
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.229.7.2: Résolution dual-key (target_chat_id / chat_id) dans switch_chat et switch_workspace.
# 5.229.7.1: Support Multi-Chat sélectif (découverte SSoT, UI hiérarchique, Read-Only strict et tooling transverse).
# 5.228.12.1: Délégation de la résolution Socket.IO et de l'attente RPC vers EchoEvents (resolve_live_session, wait_hud_action).
# 5.228.10.1: Factorisation de la boucle de retry Socket.IO dans _resolve_live_events (mutualisée entre action reconnect_only et background_loop).
# 5.228.9.1: Inversion de l'ordre d'appel echoCodexSetCurrentFile avant echoCodexSetContent dans switch_workspace.

import sys
import uuid
import asyncio
import orjson as json
import logging
from typing import Optional
from pydantic import BaseModel, Field

# Ajout dynamique du chemin pour les libs ECHO avant l'import
sys.path.append("/app/backend/echo_libs")

from echo_codex_ui import EchoCodexUI
from echo_codex_git import CodexRepo
from echo_state_manager import EchoStateManager
from echo_gemini_client import EchoGeminiClient
from echo_events import EchoEvents
from echo_prompts import SYS_CODEX_EDIT
from echo_constants import (
    get_generation_config,
    CODEX_QUICK_ACTIONS,
    FILE_INGESTION_STATUS,
    ECHO_CODEX_WORKSPACES
)

logger = logging.getLogger(__name__)

# Actions mutatives strictement interdites sur les sessions Codex distantes (Read-Only invariant)
MUTATIVE_CODEX_ACTIONS = frozenset({
    "save", "new_file", "delete_file", "rename_file",
    "ai_edit", "accept_diff", "reset", "upload", "history_restore"
})

# Registre des boucles RPC actives par (user_id, chat_id) pour évincer proprement les anciennes boucles
_ACTIVE_CODEX_LOOPS: dict[tuple[str, str], str] = {}


class Action:
    class Valves(BaseModel):
        priority: int = Field(
            default=70,
            description="Priorité d'affichage (70 = Septième).")
        CODEX_EDIT_TIMEOUT: int = Field(
            default=300, description="Timeout sub-chat édition (secondes).")

    def __init__(self):
        self.valves = self.Valves()

    async def _resolve_live_events(
        self, uid: str, cid: str, mid: str, retries: int = 3, delay: float = 1.5
    ) -> Optional[tuple[EchoEvents, str]]:
        """Résout dynamiquement la session Socket.IO active de l'utilisateur possédant le HUD Codex ouvert."""
        return await EchoEvents.resolve_live_session(
            user_id=uid,
            chat_id=cid,
            message_id=mid,
            hud_id="echo-codex-hud",
            probe_expr="document.getElementById('echo-codex-hud-workspace')?.value || 'main'",
            retries=retries,
            delay=delay,
        )

    async def action(
        self,
        body: dict,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_call__=None,
        __event_emitter__=None,
    ) -> Optional[dict]:
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __event_call__:
            return None

        uid = __user__.get("id", "anonymous")
        cid = body.get("chat_id") or __metadata__.get("chat_id")
        if not cid:
            return None

        mid = body.get("id") or __metadata__.get("message_id") or ""
        model_id = body.get("model") or __metadata__.get("model_id") or "echo_orchestrator"
        reconnect_only = bool(body.get("reconnect_only"))

        # Initialisation du repo et du state manager
        current_workspace = body.get("workspace") or "main"
        if current_workspace not in ECHO_CODEX_WORKSPACES:
            current_workspace = "main"

        if reconnect_only or not body.get("session_id"):
            resolved = await self._resolve_live_events(uid, cid, mid)
            if resolved:
                events, detected_ws = resolved
                if not body.get("workspace") and detected_ws in ECHO_CODEX_WORKSPACES:
                    current_workspace = detected_ws
            elif reconnect_only:
                return {"status": "no_active_hud"}

        repo = CodexRepo(uid, cid, workspace=current_workspace)
        state = EchoStateManager(user_id=uid, chat_id=cid)
        files = await asyncio.to_thread(repo.list_files)

        # Index de navigation historique par fichier : {filename:
        # [commit_list], idx}
        history_nav = {}

        files_json = json.dumps(files).decode("utf-8")

        # Résolution SSoT des conversations de l'utilisateur pour le sélecteur multi-chat
        chats_data = await asyncio.to_thread(CodexRepo.get_user_codex_chats, uid, cid)
        chats_json = json.dumps(chats_data).decode("utf-8")
        active_chat_title = chats_data.get("current", {}).get("title", "Conversation Actuelle")
        active_chat_id = cid

        # 1. Injection du HUD Monaco (sauf en cas de reconnexion transparente post-standby)
        if not reconnect_only:
            quick_actions_json = json.dumps(CODEX_QUICK_ACTIONS).decode("utf-8")
            workspaces_json = json.dumps(ECHO_CODEX_WORKSPACES).decode("utf-8")
            hud_js = EchoCodexUI._generate_codex_js(
                files_json,
                quick_actions_json,
                workspaces_json,
                current_workspace,
                cid,
                mid,
                model_id,
                chats_json=chats_json,
                active_chat_id=active_chat_id,
                active_chat_title=active_chat_title,
            )
            await events.call_execute(hud_js)
            await events.status("HUD Codex injecté.", done=True, hidden=True)

        loop_id = uuid.uuid4().hex
        _ACTIVE_CODEX_LOOPS[(uid, cid)] = loop_id

        # 2. Définition de la boucle événementielle bidirectionnelle (Détachée)
        async def background_loop():
            nonlocal files_json, current_workspace, repo, events
            active_chat_id = cid

            async def _refresh_tree(is_read_only: Optional[bool] = None, target_cid: Optional[str] = None):
                updated_files = await asyncio.to_thread(repo.list_files)
                f_json = json.dumps(updated_files).decode("utf-8")
                is_ro = (active_chat_id != cid) if is_read_only is None else is_read_only
                effective_cid = target_cid or active_chat_id
                r_code = (
                    f"if(window.echoCodexRefreshTree) "
                    f"window.echoCodexRefreshTree({f_json}, '{current_workspace}', '{effective_cid}', {'true' if is_ro else 'false'});"
                )
                await events.call_execute(r_code)

            async def _notify_error(e: Exception):
                err_msg = json.dumps(str(e)).decode("utf-8")
                err_code = f"if(window.echoCodexNotify) window.echoCodexNotify('error', {err_msg});"
                await events.call_execute(err_code)

            def _sync_registry(filename, commit_hash, msg, line_count=0, lang=None):
                if current_workspace != "sandbox":
                    l = lang or CodexRepo.detect_language(filename)
                    state.save_resource(
                        id=filename,
                        name=filename,
                        resource_type='codex',
                        status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                        git_tracked=True,
                        language=l,
                        lines=line_count,
                        last_commit=commit_hash[:12],
                        commit_msg=msg,
                        storage_path=f"codex/{current_workspace}/{filename}"
                    )

            try:
                current_state = await asyncio.to_thread(repo.get_latest_state)
                while _ACTIVE_CODEX_LOOPS.get((uid, cid)) == loop_id:
                    response = await events.wait_hud_action(
                        "echoCodexResolve", "_echoCodexOnReady"
                    )

                    if _ACTIVE_CODEX_LOOPS.get((uid, cid)) != loop_id:
                        break

                    if not response or not isinstance(response, dict) or response.get("error"):
                        # Tentative de ré-attachement à la nouvelle session Socket.IO après standby
                        if _ACTIVE_CODEX_LOOPS.get((uid, cid)) == loop_id:
                            await asyncio.sleep(1.5)
                            resolved = await self._resolve_live_events(uid, cid, mid, retries=3, delay=2.0)
                            if resolved and _ACTIVE_CODEX_LOOPS.get((uid, cid)) == loop_id:
                                events, detected_ws = resolved
                                if detected_ws in ECHO_CODEX_WORKSPACES and detected_ws != current_workspace:
                                    current_workspace = detected_ws
                                    repo = CodexRepo(uid, active_chat_id, workspace=current_workspace)
                                continue
                        break

                    action_type = response.get("action")

                    # ---- FERMETURE ----
                    if action_type == "close":
                        break

                    # ---- GARDE SÉCURITÉ FAIL-CLOSED : INTERDICTION DES ACTIONS MUTATIVES SUR CHAT DISTANT ----
                    if action_type in MUTATIVE_CODEX_ACTIONS and active_chat_id != cid:
                        await _notify_error(PermissionError("Action interdite : la conversation distante est en lecture seule."))
                        continue

                    # ---- SWITCH CHAT ----
                    elif action_type == "switch_chat":
                        try:
                            target_chat_id = response.get("target_chat_id") or response.get("chat_id")
                            if not target_chat_id or not CodexRepo.validate_user_chat(uid, target_chat_id):
                                await _notify_error(ValueError("Identifiant de conversation invalide ou inaccessible."))
                                continue
                            active_chat_id = target_chat_id
                            repo = CodexRepo(uid, active_chat_id, workspace=current_workspace)
                            is_ro = (active_chat_id != cid)
                            current_state = await asyncio.to_thread(repo.get_latest_state)
                            updated_files = await asyncio.to_thread(repo.list_files)
                            files_json = json.dumps(updated_files).decode("utf-8")
                            await _refresh_tree(is_read_only=is_ro, target_cid=active_chat_id)

                            if updated_files:
                                first_file_entry = next((f for f in updated_files if f.get("type") != "directory"), None)
                                if first_file_entry:
                                    first_file = first_file_entry["filename"]
                                    result = await asyncio.to_thread(repo.read_file, first_file)
                                    if result:
                                        escaped_content = json.dumps(result["content"]).decode("utf-8")
                                        escaped_name = json.dumps(first_file).decode("utf-8")
                                        is_bin_js = "true" if result.get("is_binary") else "false"
                                        load_code = (
                                            f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                            f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name}, {is_bin_js});"
                                        )
                                        await events.call_execute(load_code)
                                        continue

                            clear_code = (
                                "if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile('');"
                                "if(window.echoCodexSetContent) window.echoCodexSetContent('', '', false);"
                            )
                            await events.call_execute(clear_code)
                        except Exception as e:
                            logger.error(f"[ECHO Codex] Switch chat failed: {e}")
                            await _notify_error(e)
                        continue

                    # ---- SWITCH WORKSPACE ----
                    elif action_type == "switch_workspace":
                        try:
                            current_workspace = response.get("workspace", "main")
                            requested_file = response.get("current_file")
                            req_cid = response.get("target_chat_id") or response.get("chat_id")
                            if req_cid and CodexRepo.validate_user_chat(uid, req_cid):
                                active_chat_id = req_cid
                            repo = CodexRepo(uid, active_chat_id, workspace=current_workspace)
                            is_ro = (active_chat_id != cid)
                            current_state = await asyncio.to_thread(repo.get_latest_state)
                            updated_files = await asyncio.to_thread(repo.list_files)
                            files_json = json.dumps(updated_files).decode("utf-8")
                            await _refresh_tree(is_read_only=is_ro, target_cid=active_chat_id)
                            
                            if updated_files:
                                latest_file = requested_file
                                if not latest_file:
                                    latest_file_entry = next((f for f in updated_files if f.get("type") != "directory"), None)
                                    latest_file = latest_file_entry["filename"] if latest_file_entry else None
                                if latest_file:
                                    result = await asyncio.to_thread(repo.read_file, latest_file)
                                    if result:
                                        escaped_content = json.dumps(result["content"]).decode("utf-8")
                                        escaped_name = json.dumps(latest_file).decode("utf-8")
                                        is_bin_js = "true" if result.get("is_binary") else "false"
                                        load_code = (
                                            f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                            f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name}, {is_bin_js});"
                                        )
                                        await events.call_execute(load_code)
                                        continue
                            
                            clear_code = "if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile(''); if(window.echoCodexSetContent) window.echoCodexSetContent('', '', false);"
                            await events.call_execute(clear_code)
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).error(f"[ECHO Codex] Switch workspace failed: {e}")
                            await _notify_error(e)
                        continue

                    # ---- PING HEARTBEAT (Auto-refresh) ----
                    elif action_type == "ping":
                        new_state = await asyncio.to_thread(repo.get_latest_state)
                        if new_state != current_state:
                            current_state = new_state
                            updated_files = await asyncio.to_thread(repo.list_files)
                            files_json = json.dumps(
                                updated_files).decode("utf-8")
                            current_file = response.get("current_file", "")
                            if current_file:
                                result = await asyncio.to_thread(repo.read_file, current_file)
                                if result:
                                    escaped_content = json.dumps(
                                        result["content"]).decode("utf-8")
                                    escaped_name = json.dumps(
                                        current_file).decode("utf-8")
                                    is_bin_js = "true" if result.get("is_binary") else "false"
                                    sync_code = (
                                        f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name}, {is_bin_js});"
                                    )
                                    await _refresh_tree()
                                    await events.call_execute(sync_code)
                                    continue
                            await _refresh_tree()

                    # ---- SAUVEGARDE (Ctrl+S dans Monaco / Hex Editor) ----
                    elif action_type == "save":
                        filename = response.get("filename", "")
                        content = response.get("content", "")
                        is_bin = response.get("is_binary", None)
                        lang = response.get(
                            "language") or CodexRepo.detect_language(filename)

                        if not filename:
                            continue

                        msg = f"Edit {filename}"
                        try:
                            commit_hash = await asyncio.to_thread(
                                repo.commit_file, filename, content, msg, "ECHO Codex", is_bin
                            )
                        except Exception as e:
                            await _notify_error(e)
                            continue
                        line_count = ((len(content) + 15) // 16) if is_bin else (content.count("\n") + 1)

                        _sync_registry(filename, commit_hash, msg, line_count, lang)

                        # Notification dans le HUD
                        notify_code = f"if(window.echoCodexNotify) window.echoCodexNotify('saved', '{commit_hash[:7]}');"
                        await events.call_execute(notify_code)

                    # ---- ÉDITION AI (sub-chat) ----
                    elif action_type == "ai_edit":
                        instruction = response.get("instruction", "")
                        content = response.get("content", "")
                        selection = response.get("selection")
                        filename = response.get("filename", "")
                        lang = response.get("language", "plaintext")

                        if not instruction or not filename:
                            continue

                        target_model = response.get("model", "MODEL_APOLLO")
                        await events.status(f"🧠 Codex AI ({target_model.split('_')[-1]}) : édition de {filename}...", done=False)

                        result = await self._codex_ai_edit(
                            instruction, content, selection, filename, lang,
                            uid, cid, events, __metadata__, target_model
                        )

                        if result:
                            modified_text, actual_model = result
                            escaped = json.dumps(modified_text).decode("utf-8")
                            # Appel combiné : repositionner le modèle +
                            # afficher le diff
                            combined = (
                                f"if(window.echoCodexSetModel) window.echoCodexSetModel('{actual_model}');"
                                f"if(window.echoCodexShowDiff) window.echoCodexShowDiff({escaped});"
                            )
                            await events.call_execute(combined)
                            await events.status(f"✅ Proposition prête ({actual_model.split('_')[-1]}) — Accepter ou Rejeter.", done=True)
                        else:
                            hide_code = "if(window.echoCodexNotify) window.echoCodexNotify('error', 'Aucun r\u00e9sultat');"
                            await events.call_execute(hide_code)
                            await events.status("❌ L'éÉditeur AI n'a pas produit de résultat.", done=True)

                    # ---- ACCEPTER DIFF ----
                    elif action_type == "accept_diff":
                        filename = response.get("filename", "")
                        content = response.get("content", "")
                        instruction = response.get("instruction", "AI edit")
                        lang = CodexRepo.detect_language(filename)

                        if not filename or not content:
                            continue

                        try:
                            msg = f"AI: {instruction[:60]}"
                            commit_hash = await asyncio.to_thread(repo.commit_file, filename, content, msg)
                            current_state = await asyncio.to_thread(repo.get_latest_state)
                            line_count = content.count("\n") + 1

                            _sync_registry(filename, commit_hash, msg, line_count, lang)

                            notify_code = f"if(window.echoCodexNotify) window.echoCodexNotify('committed', '{commit_hash[:7]}');"
                            await events.call_execute(notify_code)

                            # Recharger le fichier dans l'éditeur
                            result = await asyncio.to_thread(repo.read_file, filename)
                            file_content = result["content"] if result else ""
                            is_bin_js = "true" if (result and result.get("is_binary")) else "false"
                            escaped = json.dumps(file_content).decode("utf-8")
                            escaped_name = json.dumps(filename).decode("utf-8")
                            load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name}, {is_bin_js});"
                            await events.call_execute(load_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- REJETER DIFF ----
                    elif action_type == "reject_diff":
                        try:
                            revert_code = "if(window.echoCodexRevertDiff) window.echoCodexRevertDiff();"
                            await events.call_execute(revert_code)

                            # Recharger le fichier original dans l'éditeur
                            filename = response.get("filename", "")
                            if filename:
                                result = await asyncio.to_thread(repo.read_file, filename)
                                file_content = result["content"] if result else ""
                                is_bin_js = "true" if (result and result.get("is_binary")) else "false"
                                escaped = json.dumps(file_content).decode("utf-8")
                                escaped_name = json.dumps(filename).decode("utf-8")
                                load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name}, {is_bin_js});"
                                await events.call_execute(load_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- REFRESH (🔄 dans le header) ----
                    elif action_type == "refresh":
                        try:
                            await _refresh_tree()
                            # Recharger le fichier courant si spécifié
                            filename = response.get("filename", "")
                            if filename:
                                result = await asyncio.to_thread(repo.read_file, filename)
                                if result:
                                    escaped = json.dumps(
                                        result["content"]).decode("utf-8")
                                    escaped_name = json.dumps(
                                        filename).decode("utf-8")
                                    is_bin_js = "true" if result.get("is_binary") else "false"
                                    load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name}, {is_bin_js});"
                                    await events.call_execute(load_code)
                            await events.status("🔄 Actualisé.", done=True)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- UPLOAD (PC → Codex) ----
                    elif action_type == "upload":
                        files_list = response.get("files", [])
                        if not files_list:
                            # Fallback de compatibilité ascendante
                            filename = response.get("filename", "")
                            content = response.get("content", "")
                            if filename:
                                files_list = [
                                    {"filename": filename, "content": content}]

                        if not files_list:
                            continue

                        try:
                            last_uploaded_name = None
                            last_uploaded_content = ""
                            last_uploaded_bin = False
                            commit_hash = ""
                            for f in files_list:
                                filename = f.get("filename", "")
                                content = f.get("content", "")
                                is_bin = f.get("is_binary", None)
                                if not filename:
                                    continue

                                lang = CodexRepo.detect_language(filename)
                                commit_hash = await asyncio.to_thread(
                                    repo.commit_file, filename, content, f"Import {filename}", "ECHO Codex", is_bin
                                )
                                read_back = await asyncio.to_thread(repo.read_file, filename)
                                actual_bin = bool(read_back.get("is_binary")) if read_back else bool(is_bin)
                                line_count = ((len(content) + 15) // 16) if actual_bin else (content.count("\n") + 1)
                                last_uploaded_name = filename
                                last_uploaded_content = content
                                last_uploaded_bin = actual_bin

                                if current_workspace != "sandbox":
                                    _sync_registry(filename, commit_hash, f"Import {filename}", line_count, lang)

                            current_state = await asyncio.to_thread(repo.get_latest_state)
                            updated_files = await asyncio.to_thread(repo.list_files)
                            files_json = json.dumps(updated_files).decode("utf-8")

                            if last_uploaded_name:
                                escaped_name = json.dumps(last_uploaded_name).decode("utf-8")
                                escaped_content = json.dumps(last_uploaded_content).decode("utf-8")
                                is_bin_js = "true" if last_uploaded_bin else "false"
                                status_msg = json.dumps(
                                    f"📂 {last_uploaded_name} importé ({commit_hash[:7]})"
                                    if len(files_list) == 1
                                    else f"📂 {len(files_list)} fichiers importés ({commit_hash[:7]})"
                                ).decode("utf-8")
                                upload_js = (
                                    f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                    f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                                    f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name}, {is_bin_js});"
                                    f"if(window.echoCodexNotify) window.echoCodexNotify('info', {status_msg});"
                                )
                                await events.call_execute(upload_js)
                            else:
                                await _refresh_tree()

                            if len(files_list) == 1 and last_uploaded_name:
                                await events.status(f"📂 {last_uploaded_name} importé (commit {commit_hash[:7]}).", done=True)
                            else:
                                await events.status(f"📂 {len(files_list)} fichiers importés.", done=True)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- DOWNLOAD (Codex → PC) ----
                    elif action_type == "download":
                        try:
                            filename = response.get("filename", "")
                            result = await asyncio.to_thread(repo.read_file, filename)
                            if result:
                                escaped = json.dumps(
                                    result["content"]).decode("utf-8")
                                escaped_name = json.dumps(filename).decode("utf-8")
                                is_bin_js = "true" if result.get("is_binary") else "false"
                                dl_code = f"if(window.echoCodexDownload) window.echoCodexDownload({escaped_name}, {escaped}, {is_bin_js});"
                                await events.call_execute(dl_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- NAVIGATION HISTORIQUE ◀ ----
                    elif action_type == "history_prev":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        try:
                            # Initialiser l'index de navigation si nécessaire
                            if filename not in history_nav:
                                commits = await asyncio.to_thread(repo.get_file_history_index, filename)
                                if not commits:
                                    continue
                                history_nav[filename] = {
                                    "commits": commits, "idx": len(commits) - 1}

                            nav = history_nav[filename]
                            if nav["idx"] > 0:
                                nav["idx"] -= 1

                            commit_entry = nav["commits"][nav["idx"]]
                            content = await asyncio.to_thread(
                                repo.get_file_at_commit, filename, commit_entry["hash_full"]
                            )
                            if content is not None:
                                info_json = json.dumps({
                                    "hash": commit_entry["hash"],
                                    "message": commit_entry["message"],
                                    "timestamp": commit_entry["timestamp"],
                                }).decode("utf-8")
                                escaped = json.dumps(content).decode("utf-8")
                                load_code = (
                                    f"if(window.echoCodexLoadVersion) "
                                    f"window.echoCodexLoadVersion({escaped}, {info_json}, {nav['idx']}, {len(nav['commits'])});"
                                )
                                await events.call_execute(load_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- NAVIGATION HISTORIQUE ▶ ----
                    elif action_type == "history_next":
                        filename = response.get("filename", "")
                        if not filename or filename not in history_nav:
                            continue

                        try:
                            nav = history_nav[filename]
                            if nav["idx"] < len(nav["commits"]) - 1:
                                nav["idx"] += 1

                            commit_entry = nav["commits"][nav["idx"]]
                            content = await asyncio.to_thread(
                                repo.get_file_at_commit, filename, commit_entry["hash_full"]
                            )
                            if content is not None:
                                info_json = json.dumps({
                                    "hash": commit_entry["hash"],
                                    "message": commit_entry["message"],
                                    "timestamp": commit_entry["timestamp"],
                                }).decode("utf-8")
                                escaped = json.dumps(content).decode("utf-8")
                                load_code = (
                                    f"if(window.echoCodexLoadVersion) "
                                    f"window.echoCodexLoadVersion({escaped}, {info_json}, {nav['idx']}, {len(nav['commits'])});"
                                )
                                await events.call_execute(load_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- RESTAURER VERSION HISTORIQUE ----
                    elif action_type == "history_restore":
                        filename = response.get("filename", "")
                        content = response.get("content", "")
                        source_hash = response.get("source_hash", "???")
                        if not filename or not content:
                            continue

                        try:
                            lang = CodexRepo.detect_language(filename)
                            msg = f"Restore from {source_hash}"
                            commit_hash = await asyncio.to_thread(repo.commit_file, filename, content, msg)
                            current_state = await asyncio.to_thread(repo.get_latest_state)
                            line_count = content.count("\n") + 1

                            _sync_registry(filename, commit_hash, msg, line_count, lang)

                            # Purge navigation historique
                            history_nav.pop(filename, None)

                            notify_code = f"if(window.echoCodexNotify) window.echoCodexNotify('restored', '{commit_hash[:7]}');"
                            await events.call_execute(notify_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- SORTIR DE L'HISTORIQUE ----
                    elif action_type == "history_exit":
                        filename = response.get("filename", "")
                        history_nav.pop(filename, None)

                        exit_code = "if(window.echoCodexExitHistory) window.echoCodexExitHistory();"
                        await events.call_execute(exit_code)

                    # ---- RESET ALL ----
                    elif action_type == "reset":
                        try:
                            file_count = len(await asyncio.to_thread(repo.list_files))
                            # La confirmation est gérée côté JS (confirm dialog)
                            await asyncio.to_thread(repo.reset_all)
                            if current_workspace != "sandbox":
                                state.clear_resources_by_type('codex')
                            history_nav.clear()

                            reset_code = "if(window.echoCodexReset) window.echoCodexReset();"
                            await events.call_execute(reset_code)
                            await events.toast(f"🗑️ Workspace '{current_workspace}' réinitialisé ({file_count} éléments supprimés).", "success")
                        except Exception as e:
                            await _notify_error(e)
                        break

                    # ---- NOUVEAU FICHIER / DOSSIER ----
                    elif action_type == "new_file":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        is_dir = filename.endswith("/")
                        clean_name = filename.strip("/")
                        if not clean_name:
                            continue

                        try:
                            if is_dir:
                                await asyncio.to_thread(repo.create_directory, clean_name)
                                commit_hash = "Dossier"
                            else:
                                commit_hash = await asyncio.to_thread(
                                    repo.commit_file, clean_name, "", f"Create {clean_name}"
                                )
                                lang = CodexRepo.detect_language(clean_name)
                                _sync_registry(clean_name, commit_hash, f"Create {clean_name}", 0, lang)

                            current_state = await asyncio.to_thread(repo.get_latest_state)
                            updated_files = await asyncio.to_thread(repo.list_files)
                            files_json = json.dumps(
                                updated_files).decode("utf-8")
                            escaped_content = json.dumps("").decode("utf-8")
                            escaped_name = json.dumps(clean_name).decode("utf-8")

                            refresh_code = ""
                            if not is_dir:
                                refresh_code += f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"

                            refresh_code += (
                                f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                            )

                            if not is_dir:
                                refresh_code += f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"
                            else:
                                status_msg = json.dumps(f"📁 Dossier {clean_name} créé").decode("utf-8")
                                refresh_code += f"if(window.echoCodexNotify) window.echoCodexNotify('info', {status_msg});"

                            await events.call_execute(refresh_code)

                        except Exception as e:
                            await _notify_error(e)
                            continue
                    # ---- CHARGEMENT DYNAMIQUE DOSSIER (Lazy Loading) ----
                    elif action_type == "load_directory":
                        try:
                            target_dir = response.get("path", "")
                            
                            # Récupère uniquement la profondeur 1 via le moteur Git
                            dir_content = await asyncio.to_thread(repo.list_directory, target_dir)
                            dir_json = json.dumps(dir_content).decode("utf-8") if isinstance(json.dumps(dir_content), bytes) else json.dumps(dir_content)
                            
                            # Demande au frontend de greffer ces nœuds sur le parent
                            append_code = f"if(window.echoCodexAppendNodes) window.echoCodexAppendNodes('{target_dir}', {dir_json});"
                            await events.call_execute(append_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- CHARGEMENT CONTENU FICHIER ----
                    elif action_type == "load_file":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        try:
                            result = await asyncio.to_thread(repo.read_file, filename)
                            if result is None:
                                continue
                            content = result["content"]
                            is_bin_js = "true" if result.get("is_binary") else "false"
                            
                            escaped = json.dumps(content).decode("utf-8")
                            escaped_name = json.dumps(filename).decode("utf-8")
                            load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name}, {is_bin_js});"
                            
                            await events.call_execute(load_code)
                        except Exception as e:
                            await _notify_error(e)

                    # ---- SUPPRESSION FICHIER ----
                    elif action_type == "delete_file":
                        filename = response.get("filename", "")
                        current_file = response.get("current_file", "")
                        if not filename:
                            continue

                        try:
                            delete_result = await asyncio.to_thread(
                                repo.delete_file, filename, f"Delete {filename}"
                            )
                        except Exception as e:
                            await _notify_error(e)
                            continue
                        if delete_result:
                            commit_hash, paths_to_rm = delete_result
                            if current_workspace != "sandbox":
                                for path in paths_to_rm:
                                    state.delete_resource(path)

                        current_state = await asyncio.to_thread(repo.get_latest_state)
                        updated_files = await asyncio.to_thread(repo.list_files)
                        files_json = json.dumps(updated_files).decode("utf-8")
                        refresh_code = f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                        await events.call_execute(refresh_code)

                        # Si le fichier supprimé était ouvert, charger le
                        # premier fichier restant
                        if filename == current_file or current_file.startswith(filename + "/"):
                            if updated_files:
                                first_entry = next((f for f in updated_files if f.get("type") != "directory"), None)
                                if first_entry:
                                    first = first_entry["filename"]
                                    first_escaped = json.dumps(first).decode("utf-8")
                                    result = await asyncio.to_thread(repo.read_file, first)
                                    content = result["content"] if result else ""
                                    is_bin_js = "true" if (result and result.get("is_binary")) else "false"
                                    escaped_content = json.dumps(content).decode("utf-8")
                                    switch_code = f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({first_escaped}); if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {first_escaped}, {is_bin_js});"
                                    await events.call_execute(switch_code)
                                else:
                                    empty_escaped = json.dumps("").decode("utf-8")
                                    switch_code = f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile(null); if(window.echoCodexSetContent) window.echoCodexSetContent({empty_escaped}, null, false);"
                                    await events.call_execute(switch_code)
                            else:
                                empty_escaped = json.dumps("").decode("utf-8")
                                switch_code = f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile(null); if(window.echoCodexSetContent) window.echoCodexSetContent({empty_escaped}, null, false);"
                                await events.call_execute(switch_code)

                        await events.status(f"🗑️ {filename} supprimé.", done=True)

                    # ---- RENOMMAGE FICHIER (changement de langage) ----
                    elif action_type == "rename_file":
                        old_name = response.get("old_name", "")
                        new_name = response.get("new_name", "")
                        if not old_name or not new_name:
                            continue

                        try:
                            commit_hash = await asyncio.to_thread(
                                repo.rename_file, old_name, new_name, f"Rename {old_name} → {new_name}"
                            )
                        except Exception as e:
                            await _notify_error(e)
                            continue
                        if commit_hash:
                            current_state = await asyncio.to_thread(repo.get_latest_state)
                            updated_files = await asyncio.to_thread(repo.list_files)
                            is_dir = any(f["filename"] == new_name and f.get("type") == "directory" for f in updated_files)
                            result = None if is_dir else await asyncio.to_thread(repo.read_file, new_name)

                            # Mettre à jour le registre codex (supprimer
                            # ancien, créer nouveau)
                            if current_workspace != "sandbox" and not is_dir:
                                state.delete_resource(old_name)
                                new_lang = CodexRepo.detect_language(new_name)
                                line_count = result["total_lines"] if result else 0
                                _sync_registry(new_name, commit_hash, f"Rename {old_name} → {new_name}", line_count, new_lang)

                            # Refresh tree + charger le fichier renommé
                            files_json = json.dumps(updated_files).decode("utf-8")
                            current_file = response.get("current_file", "")
                            
                            if is_dir:
                                if current_file.startswith(old_name + "/"):
                                    new_current_file = new_name + current_file[len(old_name):]
                                    result = await asyncio.to_thread(repo.read_file, new_current_file)
                                    content = result["content"] if result else ""
                                    is_bin_js = "true" if (result and result.get("is_binary")) else "false"
                                    escaped_name = json.dumps(new_current_file).decode("utf-8")
                                    escaped_content = json.dumps(content).decode("utf-8")
                                    combined = (
                                        f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                        f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                                        f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name}, {is_bin_js});"
                                    )
                                else:
                                    combined = (
                                        f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                                    )
                            else:
                                escaped_name = json.dumps(new_name).decode("utf-8")
                                content = result["content"] if result else ""
                                is_bin_js = "true" if (result and result.get("is_binary")) else "false"
                                escaped_content = json.dumps(content).decode("utf-8")
                                combined = (
                                    f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                    f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                                    f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name}, {is_bin_js});"
                                )
                            await events.call_execute(combined)
                            await events.status(f"✏️ Renommé : {old_name} → {new_name} ({commit_hash[:7]})", done=True)
                        else:
                            notify_code = "if(window.echoCodexNotify) window.echoCodexNotify('error', 'Renommage \u00e9chou\u00e9');"
                            await events.call_execute(notify_code)

            except Exception as e:
                logger.error(f"[ECHO Codex] Erreur background_loop: {e}")
            finally:
                if _ACTIVE_CODEX_LOOPS.get((uid, cid)) == loop_id:
                    _ACTIVE_CODEX_LOOPS.pop((uid, cid), None)

        # 3. Lancement de la tâche de fond
        asyncio.create_task(background_loop())

        # 4. Libération immédiate de la requête HTTP
        return {"status": "success"}

    async def _codex_ai_edit(
        self,
        instruction: str,
        content: str,
        selection: Optional[str],
        filename: str,
        lang: str,
        uid: str,
        cid: str,
        events: EchoEvents,
        metadata: dict,
        target_model: str = "MODEL_APOLLO",
    ) -> Optional[tuple]:
        """Sub-chat : instruction + contenu → cascade → code modifié.
        Retourne (texte_modifié, model_key_effectif) ou None."""
        user_prompt = f"## Document complet\n```{lang}\n{content}\n```\n"
        if selection:
            user_prompt += f"\n## Sélection ciblée\n```{lang}\n{selection}\n```\n"
        user_prompt += f"\n## Instruction\n{instruction}"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium"),
            "systemInstruction": {
                "parts": [{"text": SYS_CODEX_EDIT.format(filename=filename, language=lang)}]
            },
        }

        data, model_key, reason = await EchoGeminiClient.call_cascade(
            target_model_key=target_model,
            payload=payload,
            user_id=uid,
            metadata=metadata,
            events=events,
            timeout=self.valves.CODEX_EDIT_TIMEOUT,
            chat_id=cid,
            include_thoughts=False,
        )

        if not data:
            return None

        # Extraction du texte (filtrage thought parts)
        candidates = data.get("candidates", [])
        if candidates and candidates[0].get("content"):
            parts = candidates[0]["content"].get("parts", [])
            text_parts = [p.get("text", "")
                          for p in parts if not p.get("thought")]
            raw = "".join(text_parts).strip()
            # Nettoyage blocs markdown enveloppants
            if raw.startswith("```"):
                lines = raw.split("\n")
                if len(lines) >= 3 and lines[-1].strip() == "```":
                    return ("\n".join(lines[1:-1]), model_key or target_model)
            return (raw, model_key or target_model)
        return None
