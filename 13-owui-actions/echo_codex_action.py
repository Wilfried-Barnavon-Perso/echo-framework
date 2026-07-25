"""
title: ECHO Codex
author: Wilfried BARNAVON
version: 2.7
description: Éditeur de code natif (HUD) avec intégration Git locale et diffusion en direct des modifications.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xNiA0aDJhMiAyIDAgMCAxIDIgMnYxNGEyIDIgMCAwIDEtMiAySDZhMiAyIDAgMCAxLTItMlY2YTIgMiAwIDAgMSAyLTJoMiIvPjxyZWN0IHg9IjgiIHk9IjIiIHdpZHRoPSI4IiBoZWlnaHQ9IjQiIHJ4PSIxIiByeT0iMSIvPjxwYXRoIGQ9Ik0xMCAxMmw0LTRtLTQgNGw0IDQiLz48L3N2Zz4=
"""
# Historique des versions :
# 2.7: Remplacement du prompt natif par une interface in-line pour la création, résolution du bug de scoping state (currentFile).
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
import time
import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_constants import (
    get_generation_config,
    CODEX_EDIT_SYSTEM_PROMPT, CODEX_QUICK_ACTIONS,
    FILE_INGESTION_STATUS
)
from echo_utils import EchoEvents, EchoGeminiClient, EchoStateManager
from echo_codex_git import CodexRepo
from echo_ui import EchoUI

logger = logging.getLogger(__name__)


class Action:
    class Valves(BaseModel):
        priority: int = Field(default=70, description="Priorité d'affichage (70 = Septième).")
        CODEX_EDIT_TIMEOUT: int = Field(default=300, description="Timeout sub-chat édition (secondes).")

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
        repo = CodexRepo(uid, cid)
        state = EchoStateManager(user_id=uid, chat_id=cid)
        files = repo.list_files()

        # Index de navigation historique par fichier : {filename: [commit_list], idx}
        history_nav = {}

        # 1. Injection du HUD Monaco
        files_json = json.dumps(files).decode("utf-8")
        quick_actions_json = json.dumps(CODEX_QUICK_ACTIONS).decode("utf-8")
        hud_js = EchoUI._generate_codex_js(files_json, quick_actions_json, cid)
        await __event_call__({"type": "execute", "data": {"code": hud_js}})
        await events.status("HUD Codex injecté.", done=True, hidden=True)

        # 2. Définition de la boucle événementielle bidirectionnelle (Détachée)
        async def background_loop():
            nonlocal files_json
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

                    # ---- PING HEARTBEAT (Auto-refresh) ----
                    elif action_type == "ping":
                        stats = repo.get_repo_stats()
                        new_commit = stats.get("last_commit_hash")
                        if new_commit != current_commit:
                            current_commit = new_commit
                            updated_files = repo.list_files()
                            files_json = json.dumps(updated_files).decode("utf-8")
                            current_file = response.get("current_file", "")
                            if current_file:
                                result = repo.read_file(current_file)
                                if result:
                                    escaped_content = json.dumps(result["content"]).decode("utf-8")
                                    escaped_name = json.dumps(current_file).decode("utf-8")
                                    sync_code = (
                                        f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json});" 
                                        f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});" 
                                    )
                                    await __event_call__({"type": "execute", "data": {"code": sync_code}})
                                    continue
                            refresh_code = f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json});"
                            await __event_call__({"type": "execute", "data": {"code": refresh_code}})

                    # ---- SAUVEGARDE (Ctrl+S dans Monaco) ----
                    elif action_type == "save":
                        filename = response.get("filename", "")
                        content = response.get("content", "")
                        lang = response.get("language") or CodexRepo.detect_language(filename)

                        if not filename:
                            continue

                        msg = f"Edit {filename}"
                        commit_hash = repo.commit_file(filename, content, msg)
                        line_count = content.count("\n") + 1
                        state.save_resource(
                            id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                            git_tracked=True, language=lang, lines=line_count,
                            last_commit=commit_hash[:12], commit_msg=msg, storage_path=f"codex/{filename}",
                        )

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
                            # Appel combiné : repositionner le modèle + afficher le diff
                            combined = (
                                f"if(window.echoCodexSetModel) window.echoCodexSetModel('{actual_model}');"
                                f"if(window.echoCodexShowDiff) window.echoCodexShowDiff({escaped});"
                            )
                            await __event_call__({"type": "execute", "data": {"code": combined}})
                            await events.status(f"✅ Proposition prête ({actual_model.split('_')[-1]}) — Accepter ou Rejeter.", done=True)
                        else:
                            hide_code = "if(window.echoCodexNotify) window.echoCodexNotify('error', 'Aucun r\u00e9sultat');"
                            await __event_call__({"type": "execute", "data": {"code": hide_code}})
                            await events.status("❌ L'éditeur AI n'a pas produit de résultat.", done=True)

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
                        state.save_resource(
                            id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                            git_tracked=True, language=lang, lines=line_count,
                            last_commit=commit_hash[:12], commit_msg=msg, storage_path=f"codex/{filename}",
                        )

                        notify_code = f"if(window.echoCodexNotify) window.echoCodexNotify('committed', '{commit_hash[:7]}');"
                        await __event_call__({"type": "execute", "data": {"code": notify_code}})

                        # Recharger le fichier dans l'éditeur
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

                        # Recharger le fichier original dans l'éditeur
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
                        updated_files = repo.list_files()
                        files_json = json.dumps(updated_files).decode("utf-8")
                        refresh_code = f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json});"
                        await __event_call__({"type": "execute", "data": {"code": refresh_code}})
                        # Recharger le fichier courant si spécifié
                        filename = response.get("filename", "")
                        if filename:
                            result = repo.read_file(filename)
                            if result:
                                escaped = json.dumps(result["content"]).decode("utf-8")
                                escaped_name = json.dumps(filename).decode("utf-8")
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
                                files_list = [{"filename": filename, "content": content}]

                        if not files_list:
                            continue

                        for f in files_list:
                            filename = f.get("filename", "")
                            content = f.get("content", "")
                            if not filename:
                                continue

                            lang = CodexRepo.detect_language(filename)
                            commit_hash = repo.commit_file(filename, content, f"Import {filename}")
                            line_count = content.count("\n") + 1
                            state.save_resource(
                                id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                                git_tracked=True, language=lang, lines=line_count,
                                last_commit=commit_hash[:12], commit_msg=f"Import {filename}", storage_path=f"codex/{filename}",
                            )

                        # Refresh file tree
                        updated_files = repo.list_files()
                        files_json = json.dumps(updated_files).decode("utf-8")
                        refresh_code = f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json});"
                        await __event_call__({"type": "execute", "data": {"code": refresh_code}})
                        if len(files_list) == 1:
                            await events.status(f"📂 {files_list[0]['filename']} importé (commit {commit_hash[:7]}).", done=True)
                        else:
                            await events.status(f"📂 {len(files_list)} fichiers importés.", done=True)

                    # ---- DOWNLOAD (Codex → PC) ----
                    elif action_type == "download":
                        filename = response.get("filename", "")
                        result = repo.read_file(filename)
                        if result:
                            escaped = json.dumps(result["content"]).decode("utf-8")
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
                            history_nav[filename] = {"commits": commits, "idx": len(commits) - 1}

                        nav = history_nav[filename]
                        if nav["idx"] > 0:
                            nav["idx"] -= 1

                        commit_entry = nav["commits"][nav["idx"]]
                        content = repo.get_file_at_commit(filename, commit_entry["hash_full"])
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
                        content = repo.get_file_at_commit(filename, commit_entry["hash_full"])
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
                        state.save_resource(
                            id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                            git_tracked=True, language=lang, lines=line_count,
                            last_commit=commit_hash[:12], commit_msg=msg, storage_path=f"codex/{filename}",
                        )

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
                        state.clear_resources_by_type('codex')
                        history_nav.clear()

                        reset_code = "if(window.echoCodexReset) window.echoCodexReset();"
                        await __event_call__({"type": "execute", "data": {"code": reset_code}})
                        await events.toast(f"🗑️ Codex réinitialisé ({file_count} fichiers supprimés).", "success")
                        break

                    # ---- NOUVEAU FICHIER ----
                    elif action_type == "new_file":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        lang = CodexRepo.detect_language(filename)
                        commit_hash = repo.commit_file(filename, "", f"Create {filename}")
                        state.save_resource(
                            id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                            git_tracked=True, language=lang, lines=0,
                            last_commit=commit_hash[:12], commit_msg=f"Create {filename}", storage_path=f"codex/{filename}",
                        )

                        updated_files = repo.list_files()
                        files_json = json.dumps(updated_files).decode("utf-8")
                        escaped_content = json.dumps("").decode("utf-8")
                        escaped_name = json.dumps(filename).decode("utf-8")
                
                        refresh_code = (
                            f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                            f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json});"
                            f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"
                        )
                        await __event_call__({"type": "execute", "data": {"code": refresh_code}})

                    # ---- CHARGEMENT CONTENU FICHIER ----
                    elif action_type == "load_file":
                        filename = response.get("filename", "")
                        if not filename:
                            continue

                        result = repo.read_file(filename)
                        content = result["content"] if result else ""
                        escaped = json.dumps(content).decode("utf-8")
                        escaped_name = json.dumps(filename).decode("utf-8")
                        load_code = f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped}, {escaped_name});"
                        await __event_call__({"type": "execute", "data": {"code": load_code}})

                    # ---- SUPPRESSION FICHIER ----
                    elif action_type == "delete_file":
                        filename = response.get("filename", "")
                        current_file = response.get("current_file", "")
                        if not filename:
                            continue

                        commit_hash = repo.delete_file(filename, f"Delete {filename}")
                        if commit_hash:
                            state.delete_resource(filename)

                        updated_files = repo.list_files()
                        files_json = json.dumps(updated_files).decode("utf-8")
                        refresh_code = f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json});"
                        await __event_call__({"type": "execute", "data": {"code": refresh_code}})

                        # Si le fichier supprimé était ouvert, charger le premier fichier restant
                        if filename == current_file:
                            if updated_files:
                                first = updated_files[0]["filename"]
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

                        await events.status(f"🗑️ {filename} supprimé.", done=True)

                    # ---- RENOMMAGE FICHIER (changement de langage) ----
                    elif action_type == "rename_file":
                        old_name = response.get("old_name", "")
                        new_name = response.get("new_name", "")
                        if not old_name or not new_name:
                            continue

                        commit_hash = repo.rename_file(old_name, new_name, f"Rename {old_name} → {new_name}")
                        if commit_hash:
                            # Mettre à jour le registre codex (supprimer ancien, créer nouveau)
                            state.delete_resource(old_name)
                            new_lang = CodexRepo.detect_language(new_name)
                            result = repo.read_file(new_name)
                            line_count = result["total_lines"] if result else 0
                            state.save_resource(
                                id=new_name, name=new_name, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                                git_tracked=True, language=new_lang, lines=line_count,
                                last_commit=commit_hash[:12], commit_msg=f"Rename {old_name} → {new_name}",
                                storage_path=f"codex/{new_name}",
                            )

                            # Refresh tree + charger le fichier renommé
                            updated_files = repo.list_files()
                            files_json = json.dumps(updated_files).decode("utf-8")
                            escaped_name = json.dumps(new_name).decode("utf-8")
                            content = result["content"] if result else ""
                            escaped_content = json.dumps(content).decode("utf-8")
                            combined = (
                                f"if(window.echoCodexSetCurrentFile) window.echoCodexSetCurrentFile({escaped_name});"
                                f"if(window.echoCodexRefreshTree) window.echoCodexRefreshTree({files_json});"
                                f"if(window.echoCodexSetContent) window.echoCodexSetContent({escaped_content}, {escaped_name});"
                            )
                            await __event_call__({"type": "execute", "data": {"code": combined}})
                            await events.status(f"✏️ Renommé : {old_name} → {new_name} ({commit_hash[:7]})", done=True)
                        else:
                            notify_code = "if(window.echoCodexNotify) window.echoCodexNotify('error', 'Renommage \u00e9chou\u00e9');"
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
                "parts": [{"text": CODEX_EDIT_SYSTEM_PROMPT.format(filename=filename, language=lang)}]
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
            text_parts = [p.get("text", "") for p in parts if not p.get("thought")]
            raw = "".join(text_parts).strip()
            # Nettoyage blocs markdown enveloppants
            if raw.startswith("```"):
                lines = raw.split("\n")
                if len(lines) >= 3 and lines[-1].strip() == "```":
                    return ("\n".join(lines[1:-1]), model_key or target_model)
            return (raw, model_key or target_model)
        return None
