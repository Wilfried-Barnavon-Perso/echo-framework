"""
title: ECHO Shared Utils
author: ECHO Framework
version: 2.15
description: v2.15 : Added Google OAuth token refresh logic for Filter and Pipe.
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
from typing import Optional, Tuple, List, Set, Any, Union, Dict

# Alias pour json standard si besoin
import json as std_json

# Importation directe (Strict)
from echo_constants import (
    ECHO_UPLOADS_DIR, ECHO_USER_DBS_DIR, ECHO_VERSION_PATH,
    GOOGLE_TOKEN_URI, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    ECHO_USER_AGENT
)

# ==============================================================================
# SECTION 1 : STANDARDS DE COMMUNICATION (MULTI-PARTS)
# ==============================================================================

def split_thought_process(text: str) -> Tuple[str, Optional[str]]:
    """
    Extrait le contenu entre balises <think> ou <thought> et retourne (texte_propre, pensées).
    Standard ECHO pour le traitement des modèles de raisonnement.
    """
    if not isinstance(text, str): return text, None
    
    # Support <think> (Gemini Pro/Flash Official) et <thought> (Legacy/Internal)
    for tag in ["think", "thought"]:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            thoughts = match.group(1).strip()
            clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
            return clean_text, thoughts
            
    return text, None

def wrap_tool_output(text: str, status: dict = None, echo_tool_multiparts: List[dict] = None) -> dict:
    """
    ============================================================================
    STRICT ECHO TOOL OUTPUT STANDARD (v5.48.5)
    ============================================================================
    Structure de retour unique pour le Pipe. L'outil est le seul responsable de ce 
    qu'il envoie à l'IA.

    - 'text' (str) : Obligatoire. Contenu métier principal que le modèle doit lire.
    - 'status' (dict) : Optionnel. État technique (ex: {"status": "success"}).
    - 'echo_tool_multiparts' (list) : Optionnel. Uniquement pour l'IA (pensées internes, 
      ou médias EXPLICITEMENT destinés à être analysés par le modèle).
    ============================================================================
    """
    return {
        "text": text,
        "status": status or {"status": "success"},
        "echo_tool_multiparts": echo_tool_multiparts or []
    }

# ==============================================================================
# SECTION 2 : RÉSOLUTION DE FICHIERS & VERSIONS
# ==============================================================================

def resolve_upload_file_path(file_id: str, uploads_dir: str = ECHO_UPLOADS_DIR) -> Optional[str]:
    """Résout le chemin physique d'un fichier uploadé via son UUID (Globbing)."""
    if not file_id: return None
    pattern = os.path.join(uploads_dir, f"{file_id}_*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None

def get_echo_version() -> str:
    """Lit la version ECHO depuis le fichier source de vérité."""
    try:
        if os.path.exists(ECHO_VERSION_PATH):
            with open(ECHO_VERSION_PATH, "r") as f: return f.read().strip()
    except: pass
    return ""

# ==============================================================================
# SECTION 3 : GESTION DES ÉVÉNEMENTS (OWUI COMPAT)
# ==============================================================================

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

# ==============================================================================
# SECTION 4 : SERVICE D'AUTHENTIFICATION (DAL)
# ==============================================================================

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

    async def refresh_google_token(self, user_id: str) -> Optional[str]:
        """Rafraîchit le token Google OAuth et met à jour la BDD."""
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return None
        
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_token'")
            row = cursor.fetchone()
            if not row: conn.close(); return None
            
            token_data = json.loads(row[0])
            refresh_token = token_data.get("refresh_token")
            if not refresh_token: conn.close(); return None

            payload = {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }
            
            async with httpx.AsyncClient() as client:
                resp = await client.post(GOOGLE_TOKEN_URI, data=payload, headers={"User-Agent": ECHO_USER_AGENT})
                if resp.status_code == 200:
                    new_data = resp.json()
                    token_data.update(new_data)
                    cursor.execute("UPDATE auth_data SET value = ?, updated_at = ? WHERE key = 'google_token'",
                                 (json.dumps(token_data), int(time.time())))
                    conn.commit()
                    conn.close()
                    print(f"[EchoAuth] Token rafraîchi pour {user_id}.", flush=True)
                    return token_data.get("access_token") or token_data.get("token")
                else:
                    print(f"[EchoAuth] Erreur refresh ({resp.status_code}): {resp.text}", flush=True)
            conn.close()
        except Exception as e:
            print(f"[EchoAuth] Exception refresh: {e}", flush=True)
        return None

# ==============================================================================
# SECTION 5 : GESTIONNAIRE D'ÉTAT (SQLite)
# ==============================================================================

class EchoStateManager:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR, user_id: str = "system"):
        self.db_dir = db_dir
        os.makedirs(self.db_dir, exist_ok=True)
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        self.db_path = os.path.join(self.db_dir, f"user-{safe_uid}.db")
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self):
        """Initialise le schéma ECHO (v2.13)."""
        try:
            with self._get_connection() as conn:
                # 1. TABLE: SUTURE_INDEX
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS suture_index (
                        cumulative_hash TEXT PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        invariant_hash TEXT NOT NULL,
                        parent_hash TEXT,
                        timestamp INTEGER
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON suture_index (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_hash ON suture_index (invariant_hash)")

                # 2. TABLE: RICH_PAYLOADS
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rich_payloads (
                        invariant_hash TEXT PRIMARY KEY,
                        rich_parts_json TEXT NOT NULL,
                        created_at INTEGER
                    )
                """)

                # 3. TABLE: COGNITIVE_SIGNATURES
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cognitive_signatures (
                        cumulative_hash TEXT PRIMARY KEY,
                        thought_signature TEXT NOT NULL,
                        updated_at INTEGER
                    )
                """)

                # 4. TABLE: TOOL_JOURNAL
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tool_journal (
                        cumulative_hash TEXT PRIMARY KEY,
                        io_json TEXT NOT NULL,
                        updated_at INTEGER
                    )
                """)

                # 5. TABLE: THOUGHT_ARCHIVE
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS thought_archive (
                        cumulative_hash TEXT PRIMARY KEY,
                        raw_thought TEXT NOT NULL,
                        updated_at INTEGER
                    )
                """)

                # 6. TABLE: PROCESSED_FILES
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS processed_files (
                        chat_id TEXT, 
                        file_id TEXT, 
                        filename TEXT, 
                        mime TEXT, 
                        mode TEXT, 
                        timestamp INTEGER, 
                        PRIMARY KEY (chat_id, file_id)
                    )
                """)
                
                # 7. TABLE: CALL_BRIDGE
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS call_bridge (
                        call_id TEXT PRIMARY KEY,
                        signature TEXT NOT NULL,
                        function_name TEXT NOT NULL,
                        args_json TEXT,
                        timestamp INTEGER
                    )
                """)

                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] Init Error: {e}")

    # --- HASHAGE ---

    def calculate_invariant_hash(self, role: str, content: Any, files: List[dict] = None, tool_io: dict = None) -> str:
        norm_content = ""
        if isinstance(content, str): norm_content = content.strip()
        elif isinstance(content, list): norm_content = std_json.dumps(content, sort_keys=True)

        file_ids = ""
        if files:
            ids = sorted([f.get("id") or f.get("file", {}).get("id") for f in files if f])
            file_ids = "|".join([str(i) for i in ids if i])

        norm_tool = std_json.dumps(tool_io, sort_keys=True) if tool_io else ""
        data = f"{role.lower()}|{norm_content}|{file_ids}|{norm_tool}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def calculate_cumulative_hash(self, invariant_hash: str, parent_hash: str = None) -> str:
        data = f"{invariant_hash}|{parent_hash or ''}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    # --- FICHIERS ---

    def get_session_registry(self, chat_id: str) -> dict:
        if not chat_id: return {}
        registry = {}
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT filename, file_id, mime FROM processed_files WHERE chat_id = ?", (chat_id,))
                for row in cursor.fetchall():
                    if row[0]: registry[row[0]] = {"id": row[1], "mime": row[2] or "application/octet-stream"}
        except: pass
        return registry

    def mark_processed(self, chat_id: str, file_id: str, filename: str, mime: str, mode: str):
        if not chat_id or not file_id: return
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp) 
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (chat_id, file_id, filename, mime, mode, int(time.time())))
                conn.commit()
        except: pass

    def sync_state(self, chat_id: str, current_file_ids: List[str]) -> Set[str]:
        if not chat_id: return set()
        known_files = set()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT file_id FROM processed_files WHERE chat_id = ?", (chat_id,))
                db_files = {row[0] for row in cursor.fetchall()}
                current_set = set(current_file_ids)
                to_delete = list(db_files - current_set)
                if to_delete:
                    cursor.executemany("DELETE FROM processed_files WHERE chat_id = ? AND file_id = ?", [(chat_id, fid) for fid in to_delete])
                known_files = db_files.intersection(current_set)
                conn.commit()
        except: pass
        return known_files

    # --- CALL BRIDGE ---

    def save_call_bridge(self, call_id: str, signature: str, function_name: str, args: dict = None):
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO call_bridge (call_id, signature, function_name, args_json, timestamp) 
                    VALUES (?, ?, ?, ?, ?)
                """, (call_id, signature, function_name, std_json.dumps(args), int(time.time())))
                conn.commit()
        except: pass

    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT signature, function_name, args_json FROM call_bridge WHERE call_id = ?", (call_id,))
                row = cursor.fetchone()
                if row: return {"signature": row[0], "name": row[1], "args": std_json.loads(row[2]) if row[2] else {}}
        except: pass
        return None

    # --- RESTAURATION ---

    def get_rich_payload(self, invariant_hash: str) -> Optional[List[dict]]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT rich_parts_json FROM rich_payloads WHERE invariant_hash = ?", (invariant_hash,))
                row = cursor.fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_rich_payload(self, invariant_hash: str, rich_parts: List[dict]):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO rich_payloads (invariant_hash, rich_parts_json, created_at) VALUES (?, ?, ?)",
                             (invariant_hash, std_json.dumps(rich_parts), int(time.time())))
                conn.commit()
        except: pass

    def index_suture(self, cumulative_hash: str, chat_id: str, invariant_hash: str, parent_hash: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO suture_index (cumulative_hash, chat_id, invariant_hash, parent_hash, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (cumulative_hash, chat_id, invariant_hash, parent_hash, int(time.time())))
                conn.commit()
        except: pass

    def save_cognitive_data(self, cumulative_hash: str, signature: str = None, raw_thought: str = None, tool_io: dict = None):
        try:
            with self._get_connection() as conn:
                if signature:
                    conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, updated_at) VALUES (?, ?, ?)",
                                 (cumulative_hash, signature, int(time.time())))
                if raw_thought:
                    conn.execute("INSERT OR REPLACE INTO thought_archive (cumulative_hash, raw_thought, updated_at) VALUES (?, ?, ?)",
                                 (cumulative_hash, raw_thought, int(time.time())))
                if tool_io:
                    conn.execute("INSERT OR REPLACE INTO tool_journal (cumulative_hash, io_json, updated_at) VALUES (?, ?, ?)",
                                 (cumulative_hash, std_json.dumps(tool_io), int(time.time())))
                conn.commit()
        except: pass

    def get_thought_signature(self, cumulative_hash: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT thought_signature FROM cognitive_signatures WHERE cumulative_hash = ?", (cumulative_hash,))
                row = cursor.fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_tool_io(self, cumulative_hash: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT io_json FROM tool_journal WHERE cumulative_hash = ?", (cumulative_hash,))
                row = cursor.fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    # --- AUTH ---
    def save_auth_data(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
                conn.commit()
        except: pass

    def get_auth_data(self, key: str) -> Optional[Tuple[str, int]]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value, updated_at FROM auth_data WHERE key = ?", (key,))
                row = cursor.fetchone()
                return row if row else None
        except: pass
        return None

    def delete_auth_data(self, key: str):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM auth_data WHERE key = ?", (key,))
                conn.commit()
        except: pass

    def save_context_stats(self, stats: dict):
        if not stats: return
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO context_stats (id, data, updated_at) VALUES (1, ?, ?)",
                             (std_json.dumps(stats), int(time.time())))
                conn.commit()
        except: pass
