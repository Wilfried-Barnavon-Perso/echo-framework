"""
title: ECHO Shared Utils
author: ECHO Framework
version: 2.3
description: v2.3 : Persistent Registry support (filename storage in DB).
"""

import os
import sqlite3
import json
import requests
import time
import asyncio
import glob
from typing import Optional, Tuple, List, Set, Any

# Importation directe (Strict)
from echo_constants import (
    ECHO_UPLOADS_DIR, ECHO_USER_DBS_DIR,
    GOOGLE_TOKEN_URI, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
)

def resolve_upload_file_path(file_id: str, uploads_dir: str = ECHO_UPLOADS_DIR) -> Optional[str]:
    """Résout le chemin physique d'un fichier uploadé via son UUID (Globbing)."""
    if not file_id: return None
    pattern = os.path.join(uploads_dir, f"{file_id}_*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None

class EchoEvents:
    """Gestionnaire centralisé pour les événements Open WebUI."""
    def __init__(self, emitter: Any = None, caller: Any = None):
        self.emitter = emitter
        self.caller = caller

    async def emit(self, event_type: str, data: dict):
        if self.emitter:
            try: await self.emitter({"type": event_type, "data": data})
            except Exception as e: print(f"[EchoEvents] Emit Error: {e}")

    async def status(self, description: str, done: bool = False, hidden: bool = False):
        await self.emit("status", {"description": description, "done": done, "hidden": hidden})

    async def toast(self, content: str, level: str = "info"):
        await self.emit("notification", {"type": level, "content": content})

    async def call(self, event_type: str, data: dict) -> Any:
        if self.caller:
            try: return await self.caller({"type": event_type, "data": data})
            except Exception as e: print(f"[EchoEvents] Call Error: {e}")
        return None

    async def input(self, title: str, message: str, placeholder: str = "", type: str = "text") -> Optional[str]:
        return await self.call("input", {"title": title, "message": message, "placeholder": placeholder, "type": type})

    async def confirm(self, title: str, message: str) -> bool:
        res = await self.call("confirmation", {"title": title, "message": message})
        return bool(res)

def wrap_multimodal_response(logic_data: dict, b64_image: str = None, html_ui: str = None, context_policy: str = "strip") -> dict:
    response = {"logic_response": logic_data, "context_policy": context_policy}
    if b64_image: response["__echo_multimodal__"] = {"mime_type": "image/png", "base64_data": b64_image}
    if html_ui:
        policy_meta = f'<meta name="echo-context-policy" content="{context_policy}">'
        response["html_ui"] = html_ui.replace("<head>", f"<head>{policy_meta}") if "<head>" in html_ui else f"{policy_meta}{html_ui}"
    return response

class EchoAuth:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR):
        self.user_db_dir = db_dir

    def _get_db_path(self, user_id: str) -> str:
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        return os.path.join(self.user_db_dir, f"user-{safe_uid}.db")

    def get_credentials(self, user_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Retourne (AccessToken, ProjectID). Mode RO strict."""
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return None, None
        uri = f"file://{db_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_token'")
            row_token = cursor.fetchone()
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_project_id'")
            row_pid = cursor.fetchone()
            conn.close()
            if not row_token: return None, None
            token_data = json.loads(row_token[0])
            access_token = token_data.get("token") or token_data.get("access_token")
            project_id = row_pid[0] if row_pid else None
            if project_id: project_id = project_id.replace("projects/", "")
            return access_token, project_id
        except Exception as e:
            print(f"[EchoAuth] Error: {str(e)}")
            return None, None

    def get_google_token(self, user_id: str) -> Optional[str]:
        token, _ = self.get_credentials(user_id)
        return token

class EchoStateManager:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR, user_id: str = "system"):
        self.db_dir = db_dir
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        self.db_path = os.path.join(self.db_dir, f"user-{safe_uid}.db")
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute("CREATE TABLE IF NOT EXISTS processed_files (chat_id TEXT, file_id TEXT, filename TEXT, mode TEXT, timestamp INTEGER, PRIMARY KEY (chat_id, file_id))")
            try: conn.execute("ALTER TABLE processed_files ADD COLUMN filename TEXT")
            except: pass
            conn.commit()
            conn.close()
        except: pass

    def get_session_registry(self, chat_id: str) -> dict:
        """Mapping Nom -> ID pour tous les fichiers de la session."""
        if not chat_id: return {}
        registry = {}
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT filename, file_id FROM processed_files WHERE chat_id = ?", (chat_id,))
            for row in cursor.fetchall():
                if row[0]: registry[row[0]] = row[1]
            conn.close()
        except: pass
        return registry

    def sync_state(self, chat_id: str, current_file_ids: List[str]) -> Set[str]:
        if not chat_id: return set()
        known_files = set()
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT file_id FROM processed_files WHERE chat_id = ?", (chat_id,))
            db_files = {row[0] for row in cursor.fetchall()}
            current_set = set(current_file_ids)
            to_delete = list(db_files - current_set)
            if to_delete:
                cursor.executemany("DELETE FROM processed_files WHERE chat_id = ? AND file_id = ?", [(chat_id, fid) for fid in to_delete])
            known_files = db_files.intersection(current_set)
            conn.commit()
            conn.close()
        except: pass
        return known_files

    def mark_processed(self, chat_id: str, file_id: str, filename: str, mode: str):
        if not chat_id or not file_id: return
        try:
            conn = sqlite3.connect(self.db_path, timeout=2.0)
            conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mode, timestamp) VALUES (?, ?, ?, ?, ?)", (chat_id, file_id, filename, mode, int(time.time())))
            conn.commit()
            conn.close()
        except: pass
