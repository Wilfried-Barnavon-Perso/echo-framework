"""
title: ECHO Codex
author: Wilfried BARNAVON
version: 3.4
description: Éditeur de code natif (HUD) avec intégration Git locale et diffusion en direct des modifications.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xNiA0aDJhMiAyIDAgMCAxIDIgMnYxNGEyIDIgMCAwIDEtMiAySDZhMiAyIDAgMCAxLTItMlY2YTIgMiAwIDAgMSAyLTJoMiIvPjxyZWN0IHg9IjgiIHk9IjIiIHdpZHRoPSI4IiBoZWlnaHQ9IjQiIHJ4PSIxIiByeT0iMSIvPjxwYXRoIGQ9Ik0xMCAxMmw0LTRtLTQgNGw0IDQiLz48L3N2Zz4=
"""
# Historique des versions :
# 3.4: Support de la création de dossiers vides dans l'espace 'main' sans notification/pollution du Registre (SQLite).
# 3.2: Chargement automatique du dernier fichier lors du changement de workspace.
# 3.1: Résolution du crash silencieux de la boucle asynchrone (UnboundLocalError sur repo et current_workspace empêchant l'exécution de la boucle et gelant l'UI).
# 3.0: Asymétrie Main/Sandbox et correction des chemins `storage_path` isolés par workspace.
# 2.9: Remplacement du prompt natif par une interface in-line pour la création, résolution du bug de scoping state (currentFile).
# 2.6: Mise à jour de la priorité d'affichage à 70.
# 2.5: Fix timeout (augmentation du CODEX_EDIT_TIMEOUT à 600s pour permettre la réflexion prolongée du MODEL_PRO sur des contextes massifs sans échec HTTPX).
# 2.4: Fix du crash silencieux (UnboundLocalError sur files_json), support de l'upload multiple (batch), et correction de la synchronisation UI après une suppression.
# 2.3: Fix du crash silencieux de la boucle asynchrone (get_latest_commit n'existait pas).
# Remplacement par get_repo_stats().get('last_commit_hash').
# 2.2: Refonte de la boucle événementielle en tâche de fond (asyncio) pour
# upload, download, historique ◀ ▶, reset). Sub-chat MODEL_FLASH via call_cascade.
# 1.1: Ajout load_file (chargement contenu via echoCodexSetContent), delete_file
# (suppression individuelle avec refresh tree). Bouton × par fichier dans le tree.
# 1.2: Support sélecteur modèle (Flash/Pro/Lite). Spinner masqué sur erreur AI.
# 1.3: Feedback modèle effectif — repositionnement dropdown après cascade.
# Bouton copier. _codex_ai_edit retourne (texte, model_key).
# 1.6: Preview Panel WYSIWYG — Panneau latéral droit déployable (toggle 🤖).
# Rendu Markdown/HTML/CSS/SVG temps réel. Splitter draggable.
# 1.7: Bouton Sauver explicite. Rename fichier via changement de langage.
# 1.8: Rename fichier via handler dédié.
# 2.0: Registre Unifié V2 — save_codex_record → save_resource,
# delete_codex_record → delete_resource, clear_codex_records →
# clear_resources_by_type.
# 2.1: Fix Race Condition au chargement initial (Pull au lieu de Push).
# Affichage direct du contenu vide lors de la création manuelle (new_file).

import sys
import orjson as json
import logging
from typing import Optional
from pydantic import BaseModel, Field

# Ajout dynamique du chemin pour les libs ECHO avant l'import
sys.path.append("/app/backend/echo_libs")

from echo_ui import EchoUI
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


class Action:
    class Valves(BaseModel):
        priority: int = Field(
            default=70,
            description="Priorité d'affichage (70 = Septième).")
        CODEX_EDIT_TIMEOUT: int = Field(
            default=300, description="Timeout sub-chat édition (secondes).")

    def __init__(self):
        self.valves = self.Valves()

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

        # Initialisation du repo et du state manager
        current_workspace = "main"
        repo = CodexRepo(uid, cid, workspace=current_workspace)
        state = EchoStateManager(user_id=uid, chat_id=cid)
        files = repo.list_files()

        # Index de navigation historique par fichier : {filename:
        # [commit_list], idx}
        history_nav = {}

        # 1. Injection du HUD Monaco
        files_json = json.dumps(files).decode("utf-8")
        quick_actions_json = json.dumps(CODEX_QUICK_ACTIONS).decode("utf-8")
        workspaces_json = json.dumps(ECHO_CODEX_WORKSPACES).decode("utf-8")
        hud_js = EchoUI._generate_codex_js(
            files_json,
            quick_actions_json,
            workspaces_json,
            current_workspace,
            cid)
        await __event_call__({"type": "execute", "data": {"code": hud_js}})
        await events.status("HUD Codex injecté.", done=True, hidden=True)

        # 2. Définition de la boucle événementielle bidirectionnelle (Détachée)
        async def background_loop():
            nonlocal files_json, current_workspace, repo

            async def _refresh_tree():
                updated_files = repo.list_files()
                f_json = json.dumps(updated_files).decode("utf-8")
                r_code = f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({f_json}, '{current_workspace}');"
                await __event_call__({"type": "execute", "data": {"code": r_code}})

            async def _notify_error(e: Exception):
                err_msg = json.dumps(str(e)).decode("utf-8")
                err_code = f"if(window.echoCodexNotify) window.echoCodexNotify('error', {err_msg});"
                await __event_call__({"type": "execute", "data": {"code": err_code}})

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
                stats = repo.get_repo_stats()
                current_commit = stats.get("last_commit_hash")
                while True:
                    wait_code = "return new Promise(r => window.echoCodexResolve = r);"
                    response = await __event_call__({"type": "execute", "data": {"code": wait_code}})

                    if not response or not isinstance(response, dict):
                        break

                    action_type = response.get("action")

                    # ---- FERMETURE ----
                    if action_type == "close":
                        break

                    # ---- SWITCH WORKSPACE ----
                    elif action_type == "switch_workspace":
                        current_workspace = response.get("workspace", "main")
                        repo = CodexRepo(uid, cid, workspace=current_workspace)
                        stats = repo.get_repo_stats()
                        current_commit = stats.get("last_commit_hash")
                        updated_files = repo.list_files()
                        files_json = json.dumps(updated_files).decode("utf-8")
                        await _refresh_tree()
                        
                        if updated_files:
                            latest_file_entry = next((f for f in updated_files if f.get("type") != "directory"), None)
                            if latest_file_entry:
                                latest_file = latest_file_entry["filename"]
                                result = repo.read_file(latest_file)
                                if result:
                                    escaped_content = json.dumps(result["content"]).decode("utf-8")
                                    escaped_name = json.dumps(latest_file).decode("utf-8")
                                load_code = (
                                    f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"
                                    f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                )
                                await __event_call__({"type": "execute", "data": {"code": load_code}})
                                continue
                        
                        clear_code = "if(window.echoCodexSetContent) window.echoCodexSetContent('', ''); if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile('');"
                        await __event_call__({"type": "execute", "data": {"code": clear_code}})
                        continue

                    # ---- PING HEARTBEAT (Auto-refresh) ----
                    elif action_type == "ping":
                        stats = repo.get_repo_stats()
                        new_commit = stats.get("last_commit_hash")
                        if new_commit != current_commit:
                            current_commit = new_commit
                            updated_files = repo.list_files()
                            files_json = json.dumps(
                                updated_files).decode("utf-8")
                            current_file = response.get("current_file", "")
                            if current_file:
                                result = repo.read_file(current_file)
                                if result:
                                    escaped_content = json.dumps(
                                        result["content"]).decode("utf-8")
                                    escaped_name = json.dumps(
                                        current_file).decode("utf-8")
                                    sync_code = (
                                        f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"
                                    )
                                    await _refresh_tree()
                                    await __event_call__({"type": "execute", "data": {"code": sync_code}})
                                    continue
                            await _refresh_tree()

                    # ---- SAUVEGARDE (Ctrl+S dans Monaco) ----
                    elif action_type == "save":
                        filename = response.get("filename", "")
                        content = response.get("content", "")
                        lang = response.get(
                            "language") or CodexRepo.detect_language(filename)

                        if not filename:
                            continue

                        msg = f"Edit {filename}"
                        try:
                            commit_hash = repo.commit_file(filename, content, msg)
                        except Exception as e:
                            await _notify_error(e)
                            continue
                        line_count = content.count("\n") + 1

                        _sync_registry(filename, commit_hash, msg, line_count, lang)

                        # Notification dans le HUD
                        notify_code = f"if(window.echoCodexNotify) window.echoCodexNotify('saved', '{commit_hash[:7]}');"
                        await __event_call__({"type": "execute", "data": {"code": notify_code}})

                    # ---- ÉDITION AI (sub-chat) ----
                    elif action_type == "ai_edit":
                        instruction = response.get("instruction", "")
                        content = response.get("content", "")
                        selection = response.get("selection")
                        filename = response.get("filename", "")
                        lang = response.get("language", "plaintext")

                        if not instruction or not filename:
                            continue

                        target_model = response.get("model", "MODEL_FLASH")
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
                            await __event_call__({"type": "execute", "data": {"code": combined}})
                            await events.status(f"✅ Proposition prête ({actual_model.split('_')[-1]}) — Accepter ou Rejeter.", done=True)
                        else:
                            hide_code = "if(window.echoCodexNotify) window.echoCodexNotify('error', 'Aucun r\u00e9sultat');"
                            await __event_call__({"type": "execute", "data": {"code": hide_code}})
                            await events.status("❌ L'éÉditeur AI n'a pas produit de résultat.", done=True)

                    # ---- ACCEPTER DIFF ----
                    elif action_type == "accept_diff":
                        filename = response.get("filename", "")
                        content = response.get("content", "")
                        instruction = response.get("instruction", "AI edit")
                        lang = CodexRepo.detect_language(filename)

                        if not filename or not content:
                            continue

                        msg = f"AI: {instruction[:60]}"
                        commit_hash = repo.commit_file(filename, content, msg)
                        line_count = content.count("\n") + 1

                        _sync_registry(filename, commit_hash, msg, line_count, lang)

                        notify_code = f"if(window.echoCodexNotify) window.echoCodexNotify('committed', '{commit_hash[:7]}');"
                        await __event_call__({"type": "execute", "data": {"code": notify_code}})

                        # Recharger le fichier dans l'éÉditeur
                        result = repo.read_file(filename)
                        file_content = result["content"] if result else ""
                        escaped = json.dumps(file_content).decode("utf-8")
                        escaped_name = json.dumps(filename).decode("utf-8")
                        load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name});"
                        await __event_call__({"type": "execute", "data": {"code": load_code}})

                    # ---- REJETER DIFF ----
                    elif action_type == "reject_diff":
                        revert_code = "if(window.echoCodexRevertDiff) window.echoCodexRevertDiff();"
                        await __event_call__({"type": "execute", "data": {"code": revert_code}})

                        # Recharger le fichier original dans l'éÉditeur
                        filename = response.get("filename", "")
                        if filename:
                            result = repo.read_file(filename)
                            file_content = result["content"] if result else ""
                            escaped = json.dumps(file_content).decode("utf-8")
                            escaped_name = json.dumps(filename).decode("utf-8")
                            load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name});"
                            await __event_call__({"type": "execute", "data": {"code": load_code}})

                    # ---- REFRESH (🔄 dans le header) ----
                    elif action_type == "refresh":
                        await _refresh_tree()
                        # Recharger le fichier courant si spécifié
                        filename = response.get("filename", "")
                        if filename:
                            result = repo.read_file(filename)
                            if result:
                                escaped = json.dumps(
                                    result["content"]).decode("utf-8")
                                escaped_name = json.dumps(
                                    filename).decode("utf-8")
                                load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name});"
                                await __event_call__({"type": "execute", "data": {"code": load_code}})
                        await events.status("🔄 Actualisé.", done=True)

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

                        for f in files_list:
                            filename = f.get("filename", "")
                            content = f.get("content", "")
                            if not filename:
                                continue

                            lang = CodexRepo.detect_language(filename)
                            commit_hash = repo.commit_file(
                                filename, content, f"Import {filename}")
                            line_count = content.count("\n") + 1

                            if current_workspace != "sandbox":
                                _sync_registry(filename, commit_hash, f"Import {filename}", line_count, lang)

                        # Refresh file tree
                        await _refresh_tree()
                        if len(files_list) == 1:
                            await events.status(f"📂 {files_list[0]['filename']} importé (commit {commit_hash[:7]}).", done=True)
                        else:
                            await events.status(f"📂 {len(files_list)} fichiers importés.", done=True)

                    # ---- DOWNLOAD (Codex → PC) ----
                    elif action_type == "download":
                        filename = response.get("filename", "")
                        result = repo.read_file(filename)
                        if result:
                            escaped = json.dumps(
                                result["content"]).decode("utf-8")
                            dl_code = f"if(window.echoCodexDownload) window.echoCodexDownload('{filename}', {escaped});"
                            await __event_call__({"type": "execute", "data": {"code": dl_code}})

                    # ---- NAVIGATION HISTORIQUE ◀ ----
                    elif action_type == "history_prev":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        # Initialiser l'index de navigation si nécessaire
                        if filename not in history_nav:
                            commits = repo.get_file_history_index(filename)
                            if not commits:
                                continue
                            history_nav[filename] = {
                                "commits": commits, "idx": len(commits) - 1}

                        nav = history_nav[filename]
                        if nav["idx"] > 0:
                            nav["idx"] -= 1

                        commit_entry = nav["commits"][nav["idx"]]
                        content = repo.get_file_at_commit(
                            filename, commit_entry["hash_full"])
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
                            await __event_call__({"type": "execute", "data": {"code": load_code}})

                    # ---- NAVIGATION HISTORIQUE ▶ ----
                    elif action_type == "history_next":
                        filename = response.get("filename", "")
                        if not filename or filename not in history_nav:
                            continue

                        nav = history_nav[filename]
                        if nav["idx"] < len(nav["commits"]) - 1:
                            nav["idx"] += 1

                        commit_entry = nav["commits"][nav["idx"]]
                        content = repo.get_file_at_commit(
                            filename, commit_entry["hash_full"])
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
                            await __event_call__({"type": "execute", "data": {"code": load_code}})

                    # ---- RESTAURER VERSION HISTORIQUE ----
                    elif action_type == "history_restore":
                        filename = response.get("filename", "")
                        content = response.get("content", "")
                        source_hash = response.get("source_hash", "???")
                        if not filename or not content:
                            continue

                        lang = CodexRepo.detect_language(filename)
                        msg = f"Restore from {source_hash}"
                        commit_hash = repo.commit_file(filename, content, msg)
                        line_count = content.count("\n") + 1

                        _sync_registry(filename, commit_hash, msg, line_count, lang)

                        # Purge navigation historique
                        history_nav.pop(filename, None)

                        notify_code = f"if(window.echoCodexNotify) window.echoCodexNotify('restored', '{commit_hash[:7]}');"
                        await __event_call__({"type": "execute", "data": {"code": notify_code}})

                    # ---- SORTIR DE L'HISTORIQUE ----
                    elif action_type == "history_exit":
                        filename = response.get("filename", "")
                        history_nav.pop(filename, None)

                        exit_code = "if(window.echoCodexExitHistory) window.echoCodexExitHistory();"
                        await __event_call__({"type": "execute", "data": {"code": exit_code}})

                    # ---- RESET ALL ----
                    elif action_type == "reset":
                        file_count = len(repo.list_files())
                        # La confirmation est gérée côté JS (confirm dialog)
                        repo.reset_all()
                        if current_workspace != "sandbox":
                            state.clear_resources_by_type('codex')
                        history_nav.clear()

                        reset_code = "if(window.echoCodexReset) window.echoCodexReset();"
                        await __event_call__({"type": "execute", "data": {"code": reset_code}})
                        await events.toast(f"🗑️ Codex réinitialisé ({file_count} fichiers supprimés).", "success")
                        break

                    # ---- NOUVEAU FICHIER / DOSSIER ----
                    elif action_type == "new_file":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        is_dir = filename.endswith("/")

                        try:
                            if is_dir:
                                repo.create_directory(filename)
                                commit_hash = "Dossier"
                            else:
                                commit_hash = repo.commit_file(
                                    filename, "", f"Create {filename}")
                                lang = CodexRepo.detect_language(filename)
                                _sync_registry(filename, commit_hash, f"Create {filename}", 0, lang)

                            updated_files = repo.list_files()
                            files_json = json.dumps(
                                updated_files).decode("utf-8")
                            escaped_content = json.dumps("").decode("utf-8")
                            escaped_name = json.dumps(
                                filename.strip("/")).decode("utf-8")

                            refresh_code = (
                                f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                            )
                            # On ne charge pas de contenu vide dans l'éÉditeur
                            # si on vient de créer un dossier
                            if not is_dir:
                                refresh_code += f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"

                            await __event_call__({"type": "execute", "data": {"code": refresh_code}})

                        except Exception as e:
                            await _notify_error(e)
                            continue

                    # ---- CHARGEMENT CONTENU FICHIER ----
                    elif action_type == "load_file":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        result = repo.read_file(filename)
                        content = result["content"] if result else ""
                        
                        logger.error(f"ECHO CODEX DEBUG: load_file '{filename}', result is None? {result is None}, content len: {len(content)}")
                        
                        escaped = json.dumps(content).decode("utf-8")
                        escaped_name = json.dumps(filename).decode("utf-8")
                        load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name});"
                        
                        logger.error(f"ECHO CODEX DEBUG: load_code generated, length: {len(load_code)}")
                        
                        await __event_call__({"type": "execute", "data": {"code": load_code}})

                    # ---- SUPPRESSION FICHIER ----
                    elif action_type == "delete_file":
                        filename = response.get("filename", "")
                        current_file = response.get("current_file", "")
                        if not filename:
                            continue

                        try:
                            commit_hash = repo.delete_file(
                                filename, f"Delete {filename}")
                        except Exception as e:
                            await _notify_error(e)
                            continue
                        if commit_hash:
                            if current_workspace != "sandbox":
                                state.delete_resource(filename)

                        updated_files = repo.list_files()
                        files_json = json.dumps(updated_files).decode("utf-8")
                        refresh_code = f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                        await __event_call__({"type": "execute", "data": {"code": refresh_code}})

                        # Si le fichier supprimé était ouvert, charger le
                        # premier fichier restant
                        if filename == current_file:
                            if updated_files:
                                first_entry = next((f for f in updated_files if f.get("type") != "directory"), None)
                                if first_entry:
                                    first = first_entry["filename"]
                                    first_escaped = json.dumps(first).decode("utf-8")
                                    result = repo.read_file(first)
                                    content = result["content"] if result else ""
                                    escaped_content = json.dumps(content).decode("utf-8")
                                    switch_code = f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({first_escaped}); if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {first_escaped});"
                                    await __event_call__({"type": "execute", "data": {"code": switch_code}})
                                else:
                                    empty_escaped = json.dumps("").decode("utf-8")
                                    switch_code = f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile(null); if(window.echoCodexSetContent) window.echoCodexSetContent({empty_escaped}, null);"
                                    await __event_call__({"type": "execute", "data": {"code": switch_code}})
                            else:
                                empty_escaped = json.dumps("").decode("utf-8")
                                switch_code = f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile(null); if(window.echoCodexSetContent) window.echoCodexSetContent({empty_escaped}, null);"
                                await __event_call__({"type": "execute", "data": {"code": switch_code}})

                        await events.status(f"🗑️ {filename} supprimé.", done=True)

                    # ---- RENOMMAGE FICHIER (changement de langage) ----
                    elif action_type == "rename_file":
                        old_name = response.get("old_name", "")
                        new_name = response.get("new_name", "")
                        if not old_name or not new_name:
                            continue

                        try:
                            commit_hash = repo.rename_file(
                                old_name, new_name, f"Rename {old_name} → {new_name}")
                        except Exception as e:
                            await _notify_error(e)
                            continue
                        if commit_hash:
                            updated_files = repo.list_files()
                            is_dir = any(f["filename"] == new_name and f.get("type") == "directory" for f in updated_files)
                            result = None if is_dir else repo.read_file(new_name)

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
                                    result = repo.read_file(new_current_file)
                                    content = result["content"] if result else ""
                                    escaped_name = json.dumps(new_current_file).decode("utf-8")
                                    escaped_content = json.dumps(content).decode("utf-8")
                                    combined = (
                                        f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                        f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                                        f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"
                                    )
                                else:
                                    combined = (
                                        f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                                    )
                            else:
                                escaped_name = json.dumps(new_name).decode("utf-8")
                                content = result["content"] if result else ""
                                escaped_content = json.dumps(content).decode("utf-8")
                                combined = (
                                    f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                    f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json}, '{current_workspace}');"
                                    f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"
                                )
                            await __event_call__({"type": "execute", "data": {"code": combined}})
                            await events.status(f"✏️ Renommé : {old_name} → {new_name} ({commit_hash[:7]})", done=True)
                        else:
                            notify_code = "if(window.echoCodexNotify) window.echoCodexNotify('error', 'Renommage \u00e9échoué\u00e9');"
                            await __event_call__({"type": "execute", "data": {"code": notify_code}})

            except Exception as e:
                logger.error(f"[ECHO Codex] Erreur background_loop: {e}")

        # 3. Lancement de la tâche de fond
        import asyncio
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
        target_model: str = "MODEL_FLASH",
    ) -> Optional[tuple]:
        """Sub-chat : instruction + contenu → cascade → code modifié.
        Retourne (texte_modifié, model_key_effectif) ou None."""
        user_prompt = f"## Document complet\n```{lang}\n{content}\n```\n"
        if selection:
            user_prompt += f"\n## Sélection ciblée\n```{lang}\n{selection}\n```\n"
        user_prompt += f"\n## Instruction\n{instruction}"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": get_generation_config("MODEL_FLASH"),
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
