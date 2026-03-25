"""
title: ECHO Shared Utils
author: ECHO Framework
version: 2.26
description: 2.26: Added message_id to processed_files for branch-aware registry filtering.
"""

import os
import sqlite3
import json
import requests
import time
import asyncio
import glob
import hashlib
import re
import httpx
import shutil
from typing import Optional, Tuple, List, Set, Any, Union, Dict

# Alias pour json standard si besoin
import json as std_json

# Importation directe (Strict)
from echo_constants import (
    ECHO_UPLOADS_DIR, ECHO_USER_DBS_DIR, ECHO_VERSION_PATH,
    GOOGLE_TOKEN_URI, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    ECHO_USER_AGENT, ECHO_USERS_ROOT
)

# ==============================================================================
# SECTION 1 : STANDARDS DE COMMUNICATION (MULTI-PARTS)
# ==============================================================================

def split_thought_process(text: str) -> Tuple[str, Optional[str]]:
    if not isinstance(text, str): return text, None
    for tag in ["think", "thought"]:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            thoughts = match.group(1).strip()
            clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
            return clean_text, thoughts
    return text, None

def wrap_tool_output(text: str, status: dict = None, echo_tool_multiparts: List[dict] = None, nouveaux_fichiers: List[dict] = None) -> dict:
    if nouveaux_fichiers:
        json_str = std_json.dumps(nouveaux_fichiers, ensure_ascii=False, indent=2)
        text += f"\n\n```json:nouveaux_artefacts\n{json_str}\n```"
    return {"text": text, "status": status or {"status": "success"}, "echo_tool_multiparts": echo_tool_multiparts or []}

# ==============================================================================
# SECTION 2 : RÉSOLUTION DE FICHIERS & VERSIONS
# ==============================================================================

def generate_echo_file_id(user_id: str, chat_id: str) -> str:
    ts = int(time.time() * 1000)
    return f"U_{user_id}_C_{chat_id}_T_{ts}"

def resolve_upload_file_path(user_id: str, file_id: str, uploads_dir: str = ECHO_UPLOADS_DIR) -> Optional[str]:
    if not file_id: return None
    
    # 1. Recherche PRIORITAIRE dans le Coffre-Fort (Vault) de l'utilisateur
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        user_vault = os.path.join(ECHO_USERS_ROOT, safe_uid, "files")
        pattern = os.path.join(user_vault, f"{file_id}_*")
        matches = glob.glob(pattern)
        if matches: return matches[0]
        
    # 2. Recherche de SECOURS dans le dossier de transit (Uploads OWUI)
    pattern = os.path.join(uploads_dir, f"{file_id}_*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None

def get_echo_version() -> str:
    try:
        if os.path.exists(ECHO_VERSION_PATH):
            with open(ECHO_VERSION_PATH, "r") as f: return f.read().strip()
    except: pass
    return ""

# ==============================================================================
# SECTION 3 : GESTION DES ÉVÉNEMENTS (OWUI COMPAT)
# ==============================================================================

class EchoEvents:
    def __init__(self, emitter: Any = None, caller: Any = None):
        self.emitter = emitter; self.caller = caller
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

# ==============================================================================
# SECTION 4 : SERVICE D'AUTHENTIFICATION (DAL)
# ==============================================================================

class EchoAuth:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR, user_id: str = "system"):
        # Flexibilité pour les appels sans user_id
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        self.user_db_dir = db_dir

    def _get_db_path(self, user_id: str = None) -> str:
        uid = user_id or self.user_id
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")
        path = os.path.join(ECHO_USERS_ROOT, safe_uid, "identity.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def get_credentials(self, user_id: str = None) -> Tuple[Optional[str], Optional[str]]:
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return None, None
        try:
            conn = sqlite3.connect(f"file://{db_path}?mode=ro", uri=True, timeout=5.0)
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
        except: return None, None

    def get_project_id_from_cache(self) -> Optional[str]:
        _, pid = self.get_credentials(); return pid

    async def refresh_google_token(self, user_id: str = None) -> Optional[str]:
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return None
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_token'")
            row = cursor.fetchone()
            if not row: conn.close(); return None
            token_data = json.loads(row[0]); refresh_token = token_data.get("refresh_token")
            if not refresh_token: conn.close(); return None
            async with httpx.AsyncClient() as client:
                resp = await client.post(GOOGLE_TOKEN_URI, data={"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "refresh_token": refresh_token, "grant_type": "refresh_token"})
                if resp.status_code == 200:
                    token_data.update(resp.json())
                    cursor.execute("UPDATE auth_data SET value = ?, updated_at = ? WHERE key = 'google_token'", (json.dumps(token_data), int(time.time())))
                    conn.commit(); conn.close(); return token_data.get("access_token") or token_data.get("token")
            conn.close()
        except: pass
        return None

# ==============================================================================
# SECTION 5 : GESTIONNAIRE D'ÉTAT (SQLite)
# ==============================================================================

class EchoStateManager:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR, user_id: str = "system", chat_id: Optional[str] = None):
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        self.db_dir = db_dir; self.chat_id = chat_id
        
        safe_uid = "".join(x for x in str(self.user_id) if x.isalnum() or x in "-_")
        self.user_dir = os.path.join(ECHO_USERS_ROOT, safe_uid)
        os.makedirs(os.path.join(self.user_dir, "files"), exist_ok=True)
        os.makedirs(os.path.join(self.user_dir, "chats"), exist_ok=True)

        if chat_id:
            safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
            self.db_path = os.path.join(self.user_dir, "chats", f"{safe_cid}.db")
        else: self.db_path = os.path.join(self.user_dir, "identity.db")
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;"); return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                # Tables existantes (Suture & Payloads)
                conn.execute("CREATE TABLE IF NOT EXISTS suture_index (cumulative_hash TEXT PRIMARY KEY, chat_id TEXT NOT NULL, invariant_hash TEXT NOT NULL, parent_hash TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS rich_payloads (invariant_hash TEXT PRIMARY KEY, rich_parts_json TEXT NOT NULL, created_at INTEGER)")
                
                # --- NOUVELLE TABLE DES OMBRES (Suture par ID) ---
                conn.execute("CREATE TABLE IF NOT EXISTS message_shadows (message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, full_parts_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_chat_id ON message_shadows (chat_id)")

                # Migration du schéma (Ajout de message_id aux anciennes tables)
                try: conn.execute("ALTER TABLE suture_index ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE rich_payloads ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE cognitive_signatures ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE tool_journal ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE processed_files ADD COLUMN file_content TEXT")
                except: pass
                try: conn.execute("ALTER TABLE processed_files ADD COLUMN message_id TEXT")
                except: pass

                # Autres tables de l'infrastructure
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON suture_index (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_hash ON suture_index (invariant_hash)")
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_signatures (cumulative_hash TEXT PRIMARY KEY, thought_signature TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS tool_journal (cumulative_hash TEXT PRIMARY KEY, io_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS thought_archive (cumulative_hash TEXT PRIMARY KEY, raw_thought TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS processed_files (chat_id TEXT, file_id TEXT, filename TEXT, mime TEXT, mode TEXT, timestamp INTEGER, file_content TEXT, PRIMARY KEY (chat_id, file_id))")
                conn.execute("CREATE TABLE IF NOT EXISTS call_bridge (call_id TEXT PRIMARY KEY, signature TEXT NOT NULL, function_name TEXT NOT NULL, args_json TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] Init DB Error: {e}")

    # --- MÉTHODES DE SHADOWING (SUTURE PAR ID) ---
    
    def save_message_shadow(self, message_id: str, chat_id: str, role: str, parts: List[dict]):
        """Scelle l'état complet (parts) d'un message pour une restauration Bit-Perfect."""
        if not message_id: return
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (message_id, chat_id, role, std_json.dumps(parts), int(time.time()))
                )
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] Save Shadow Error: {e}")

    def get_message_shadow(self, message_id: str, updated_at: int) -> Optional[List[dict]]:
        """Récupère le moulage original d'un message SEULEMENT s'il correspond au timestamp physique."""
        if not message_id: return None
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT full_parts_json FROM message_shadows WHERE message_id = ? AND updated_at = ?", 
                    (message_id, int(updated_at))
                ).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def get_last_assistant_shadow(self, chat_id: str) -> Optional[List[dict]]:
        """Récupère la dernière ombre de l'assistant pour le chaînage CoT."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT full_parts_json FROM message_shadows WHERE chat_id = ? AND role IN ('assistant', 'model') ORDER BY updated_at DESC LIMIT 1",
                    (chat_id,)
                ).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    # --- MÉTHODES DE HACHAGE (LEGACY & SUTURE) ---

    def calculate_invariant_hash(self, role: str, content: Any, tool_io: dict = None) -> str:
        norm_c = content.strip() if isinstance(content, str) else std_json.dumps(content, sort_keys=True)
        norm_t = std_json.dumps(tool_io, sort_keys=True) if tool_io else ""
        return hashlib.sha256(f"{role.lower()}|{norm_c}|{norm_t}".encode("utf-8")).hexdigest()

    def calculate_cumulative_hash(self, inv: str, parent: str = None) -> str:
        return hashlib.sha256(f"{inv}|{parent or ''}".encode("utf-8")).hexdigest()

    def get_session_registry(self, chat_id: str, active_message_ids: Optional[List[str]] = None) -> dict:
        reg = {}
        try:
            with self._get_connection() as conn:
                if active_message_ids:
                    placeholders = ','.join('?' for _ in active_message_ids)
                    query = f"SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ? AND message_id IN ({placeholders})"
                    params = [chat_id] + active_message_ids
                    rows = conn.execute(query, params).fetchall()
                else:
                    rows = conn.execute("SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ?", (chat_id,)).fetchall()
                
                for row in rows:
                    reg[row[0]] = {
                        "id": row[1],
                        "mime": row[2] or "application/octet-stream",
                        "statut": row[3] or "unknown"
                    }
        except: pass
        return reg
    def mark_processed(self, chat_id: str, file_id: str, filename: str, mime: str, mode: str, content: Optional[str] = None, message_id: Optional[str] = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp, file_content, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chat_id, file_id, filename, mime, mode, int(time.time()), content, message_id))
                conn.commit()
        except:
            try:
                with self._get_connection() as conn:
                    conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, file_id, filename, mime, mode, int(time.time())))
                    conn.commit()
            except: pass

    def save_call_bridge(self, call_id: str, signature: str, function_name: str, args: dict = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO call_bridge (call_id, signature, function_name, args_json, timestamp) VALUES (?, ?, ?, ?, ?)", (call_id, signature, function_name, std_json.dumps(args), int(time.time())))
                conn.commit()
        except: pass

    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT signature, function_name, args_json FROM call_bridge WHERE call_id = ?", (call_id,)).fetchone()
                if row: return {"signature": row[0], "name": row[1], "args": std_json.loads(row[2]) if row[2] else {}}
        except: pass
        return None

    def get_rich_payload(self, inv: str) -> Optional[List[dict]]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT rich_parts_json FROM rich_payloads WHERE invariant_hash = ?", (inv,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_rich_payload(self, inv: str, rich: List[dict], message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO rich_payloads (invariant_hash, rich_parts_json, message_id, created_at) VALUES (?, ?, ?, ?)",
                    (inv, std_json.dumps(rich), message_id, int(time.time()))
                )
                conn.commit()
        except: pass

    def index_suture(self, cumul: str, chat_id: str, inv: str, parent: str = None, message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO suture_index (cumulative_hash, chat_id, invariant_hash, parent_hash, message_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (cumul, chat_id, inv, parent, message_id, int(time.time()))
                )
                conn.commit()
        except: pass

    def save_cognitive_data(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None):
        try:
            with self._get_connection() as conn:
                if sig: conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, updated_at) VALUES (?, ?, ?, ?)", (cumul, sig, message_id, int(time.time())))
                if thought: conn.execute("INSERT OR REPLACE INTO thought_archive (cumulative_hash, raw_thought, updated_at) VALUES (?, ?, ?)", (cumul, thought, int(time.time())))
                if tool_io: conn.execute("INSERT OR REPLACE INTO tool_journal (cumulative_hash, io_json, updated_at) VALUES (?, ?, ?)", (cumul, std_json.dumps(tool_io), int(time.time())))
                conn.commit()
        except: pass

    def get_thought_signature(self, cumul: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT thought_signature FROM cognitive_signatures WHERE cumulative_hash = ?", (cumul,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_tool_io(self, cumul: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT io_json FROM tool_journal WHERE cumulative_hash = ?", (cumul,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_auth_data(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
                conn.commit()
        except: pass

    def get_auth_data(self, key: str) -> Optional[Tuple[str, int]]:
        try:
            with self._get_connection() as conn:
                return conn.execute("SELECT value, updated_at FROM auth_data WHERE key = ?", (key,)).fetchone()
        except: pass
        return None

    def delete_auth_data(self, key: str):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM auth_data WHERE key = ?", (key,)); conn.commit()
        except: pass

    def save_context_stats(self, stats: dict):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO context_stats (id, data, updated_at) VALUES (1, ?, ?)", (std_json.dumps(stats), int(time.time())))
                conn.commit()
        except: pass

    def move_to_vault(self, file_id: str, filename: str) -> bool:
        old_path = resolve_upload_file_path(self.user_id, file_id)
        if not old_path: return False
        new_path = os.path.join(self.user_dir, "files", os.path.basename(old_path))
        try:
            if not os.path.exists(new_path): shutil.move(old_path, new_path)
            return True
        except: return False
