"""
title: ECHO Shared Utils (Core)
author: Wilfried BARNAVON
version: 3.2
description: 3.2: Harmonisation de l'Exponential Backoff (Multiplier 2.0) et standardisation des retries (5) via les constantes globales.
"""

import os
import sqlite3
import orjson as json
import pybase64 as base64
import requests
import time
import asyncio
import glob
import hashlib
import re
import httpx
import random
import shutil
from typing import Optional, Tuple, List, Set, Any, Union, Dict, AsyncGenerator

# Alias pour json standard si besoin
import orjson as std_json

# Importation directe (Strict)
from echo_constants import (
    ECHO_UPLOADS_DIR, ECHO_USER_DBS_DIR, ECHO_VERSION_PATH,
    GOOGLE_API_BASE_URL, ECHO_USER_AGENT, ECHO_USERS_ROOT,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    ECHO_RETRY_BASE_DELAY, ECHO_RETRY_MULTIPLIER,
    ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX
)

# ==============================================================================
# SECTION 0 : CLIENT HTTP GLOBAL (HTTP/2)
# ==============================================================================

_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(
    timeout: int = 600, 
    max_connections: int = 100,
    max_keepalive: int = 20,
    keepalive_expiry: int = 300
) -> httpx.AsyncClient:
    """Gestionnaire de client HTTP/2 STRICT (Mutualisé)."""
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
    now = time.time()
    
    if _SHARED_ASYNC_CLIENT and (now - _LAST_CLIENT_ACCESS > timeout):
        old_client = _SHARED_ASYNC_CLIENT; _SHARED_ASYNC_CLIENT = None 
        try: await old_client.aclose()
        except: pass

    if _SHARED_ASYNC_CLIENT is None or _SHARED_ASYNC_CLIENT.is_closed:
        limits = httpx.Limits(
            max_keepalive_connections=max_keepalive, 
            max_connections=max_connections, 
            keepalive_expiry=keepalive_expiry
        )
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=True)
    
    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

def get_stealth_headers(url: Optional[str] = None) -> Dict[str, str]:
    """Génère des en-têtes HTTP de haute fidélité pour simuler un navigateur réel (Stealth)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="123", "Not:A-Brand";v="8", "Google Chrome";v="123"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "image",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "cross-site",
        "sec-fetch-user": "?1",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1"
    }
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        headers["Host"] = parsed.netloc
        if any(x in parsed.netloc for x in ["wikimedia", "wikipedia"]):
             headers["sec-fetch-dest"] = "document"
             headers["sec-fetch-mode"] = "navigate"
             headers["sec-fetch-site"] = "none"
             headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    return headers

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
        json_str = json.dumps(nouveaux_fichiers, option=json.OPT_INDENT_2).decode('utf-8')
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
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        user_vault = os.path.join(ECHO_USERS_ROOT, safe_uid, "files")
        pattern = os.path.join(user_vault, f"{file_id}_*")
        matches = glob.glob(pattern)
        if matches: return matches[0]
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
# SECTION 3b : [RÉFÉRÉ DANS echo_ui.py]
# ==============================================================================

# ==============================================================================
# SECTION 4 : SERVICE D'AUTHENTIFICATION (DAL) & CLIENT GEMINI
# ==============================================================================

class EchoAuth:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR, user_id: str = "system"):
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        self.user_db_dir = db_dir

    def _get_db_path(self, user_id: str = None) -> str:
        uid = user_id or self.user_id
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")
        path = os.path.join(ECHO_USERS_ROOT, safe_uid, "identity.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def get_api_keys(self, user_id: str = None) -> List[str]:
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return []
        keys = []
        try:
            conn = sqlite3.connect(f"file://{db_path}?mode=ro", uri=True, timeout=5.0)
            cursor = conn.cursor()
            for key_name in ['google_api_key', 'google_api_key_secondary']:
                cursor.execute("SELECT value FROM auth_data WHERE key = ?", (key_name,))
                row = cursor.fetchone()
                if row and row[0]: keys.append(row[0])
            conn.close()
        except: pass
        return keys

    def save_api_key(self, key_name: str, value: str, user_id: str = None):
        db_path = self._get_db_path(user_id)
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key_name, value, int(time.time())))
                conn.commit()
        except Exception as e:
            print(f"[EchoAuth] Erreur sauvegarde clé {key_name}: {e}")

    def delete_api_key(self, key_name: str, user_id: str = None):
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("DELETE FROM auth_data WHERE key = ?", (key_name,))
                conn.commit()
        except Exception as e:
            print(f"[EchoAuth] Erreur suppression clé {key_name}: {e}")

class EchoGeminiClient:
    """Moteur factorisé pour les appels API Gemini avec Fallback et Résilience."""

    @staticmethod
    async def call(
        keys: List[str],
        target_model: str,
        payload: dict,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        timeout: int = 120
    ) -> dict:
        if not keys: raise ValueError("Aucune clé API fournie.")
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = ECHO_RETRY_BASE_DELAY
        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
            try:
                resp = await client.post(api_url, json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200: return resp.json()
                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                        active_key_idx += 1
                        consecutive_errors = 0
                        if events: await events.status(f"🔄 Surcharge API ({resp.status_code}). Bascule sur la clé de secours...", done=False)
                        continue
                    if attempt < max_retries:
                        wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                        if events: await events.status(f"⚠️ Surcharge API Google ({resp.status_code}). Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                        await asyncio.sleep(wait_time)
                        current_delay *= ECHO_RETRY_MULTIPLIER
                        continue
                resp.raise_for_status()
            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                    if events: await events.status(f"⚠️ Instabilité réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= ECHO_RETRY_MULTIPLIER
                    continue
                raise e
        raise Exception(f"Échec après {max_retries} tentatives.")

    @staticmethod
    async def stream(
        keys: List[str],
        target_model: str,
        payload: dict,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        process_callback: Optional[Any] = None,
        timeout: int = 300
    ) -> AsyncGenerator[Union[str, Dict], None]:
        if not keys: yield "🚫 Aucune clé API configurée."; return
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = ECHO_RETRY_BASE_DELAY
        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:streamGenerateContent?key={api_key}&alt=sse"
            headers = {"x-goog-api-key": api_key, "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
            try:
                async with client.stream("POST", api_url, content=json.dumps(payload), headers=headers, timeout=timeout) as r:
                    if r.status_code in [429, 500, 503]:
                        consecutive_errors += 1
                        if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                            active_key_idx += 1
                            consecutive_errors = 0
                            if events: await events.status(f"🔄 Surcharge API ({r.status_code}). Bascule sur la clé de secours...", done=False)
                            continue
                        if attempt < max_retries:
                            wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                            if events: await events.status(f"⚠️ Surcharge API Google ({r.status_code}). Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                            await asyncio.sleep(wait_time)
                            current_delay *= ECHO_RETRY_MULTIPLIER
                            continue
                        else: yield f"🚫 Erreur API Google ({r.status_code})."; return
                    r.raise_for_status()
                    if r.http_version != "HTTP/2": yield "🚫 Erreur de protocole : HTTP/2 obligatoire pour Gemini AI Studio."; return
                    if process_callback:
                        async for chunk in process_callback(r): yield chunk
                break
            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                    if events: await events.status(f"⚠️ Instabilité réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= ECHO_RETRY_MULTIPLIER
                    continue
                else: yield f"🚫 Erreur système : {str(e)}"; return

    @staticmethod
    async def embed(
        keys: List[str],
        model: str,
        content: dict,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        timeout: int = 30
    ) -> dict:
        if not keys: raise ValueError("Aucune clé API fournie.")
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = ECHO_RETRY_BASE_DELAY
        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            api_url = f"{GOOGLE_API_BASE_URL}/models/{model}:embedContent?key={api_key}"
            headers = {"Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
            payload = {"model": f"models/{model}", "content": content}
            try:
                resp = await client.post(api_url, json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200: return resp.json()
                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                        active_key_idx += 1
                        consecutive_errors = 0
                        if events: await events.status(f"🔄 Surcharge API Embedding ({resp.status_code}). Bascule sur la clé de secours...", done=False)
                        continue
                    if attempt < max_retries:
                        wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                        await asyncio.sleep(wait_time)
                        current_delay *= ECHO_RETRY_MULTIPLIER
                        continue
                resp.raise_for_status()
            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                    await asyncio.sleep(wait_time)
                    current_delay *= ECHO_RETRY_MULTIPLIER
                    continue
                raise e
        raise Exception(f"Échec Embedding après {max_retries} tentatives.")

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
                conn.execute("CREATE TABLE IF NOT EXISTS suture_index (cumulative_hash TEXT PRIMARY KEY, chat_id TEXT NOT NULL, invariant_hash TEXT NOT NULL, parent_hash TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS rich_payloads (invariant_hash TEXT PRIMARY KEY, rich_parts_json TEXT NOT NULL, created_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS message_shadows (message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, full_parts_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_chat_id ON message_shadows (chat_id)")
                try: conn.execute("ALTER TABLE suture_index ADD COLUMN message_id TEXT")
                except: pass
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON suture_index (chat_id)")
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_signatures (cumulative_hash TEXT PRIMARY KEY, thought_signature TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS tool_journal (cumulative_hash TEXT PRIMARY KEY, io_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS thought_archive (cumulative_hash TEXT PRIMARY KEY, raw_thought TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS processed_files (chat_id TEXT, file_id TEXT, filename TEXT, mime TEXT, mode TEXT, timestamp INTEGER, file_content TEXT, PRIMARY KEY (chat_id, file_id))")
                conn.execute("CREATE TABLE IF NOT EXISTS call_bridge (call_id TEXT PRIMARY KEY, signature TEXT NOT NULL, function_name TEXT NOT NULL, args_json TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.commit()
        except Exception as e: print(f"[EchoStateManager] Init DB Error: {e}")

    def save_message_shadow(self, message_id: str, chat_id: str, role: str, parts: List[dict]):
        if not message_id: return
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (message_id, chat_id, role, std_json.dumps(parts).decode('utf-8'), int(time.time()))
                )
                conn.commit()
        except: pass

    def get_message_shadow(self, message_id: str, updated_at: int) -> Optional[List[dict]]:
        if not message_id: return None
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT full_parts_json FROM message_shadows WHERE message_id = ? AND updated_at = ?", (message_id, int(updated_at))).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def calculate_invariant_hash(self, role: str, content: Any, tool_io: dict = None) -> str:
        norm_c = content.strip() if isinstance(content, str) else json.dumps(content, option=json.OPT_SORT_KEYS).decode('utf-8')
        norm_t = json.dumps(tool_io, option=json.OPT_SORT_KEYS).decode('utf-8') if tool_io else ""
        return hashlib.sha256(f"{role.lower()}|{norm_c}|{norm_t}".encode("utf-8")).hexdigest()

    def calculate_cumulative_hash(self, inv: str, parent: str = None) -> str:
        return hashlib.sha256(f"{inv}|{parent or ''}".encode("utf-8")).hexdigest()

    def get_session_registry(self, chat_id: str, active_message_ids: Optional[List[str]] = None) -> dict:
        reg = {}
        try:
            with self._get_connection() as conn:
                if active_message_ids:
                    placeholders = ','.join('?' for _ in active_message_ids)
                    query = f"SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ? AND file_id IN ({placeholders})"
                    rows = conn.execute(query, [chat_id] + active_message_ids).fetchall()
                else:
                    rows = conn.execute("SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ?", (chat_id,)).fetchall()
                for row in rows: reg[row[0]] = {"id": row[1], "mime": row[2] or "application/octet-stream", "statut": row[3] or "unknown"}
        except: pass
        return reg

    def mark_processed(self, chat_id: str, file_id: str, filename: str, mime: str, mode: str, content: Optional[str] = None, message_id: Optional[str] = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp, file_content, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chat_id, file_id, filename, mime, mode, int(time.time()), content, message_id))
                conn.commit()
        except: pass

    def save_call_bridge(self, call_id: str, signature: str, function_name: str, args: dict = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO call_bridge (call_id, signature, function_name, args_json, timestamp) VALUES (?, ?, ?, ?, ?)", (call_id, signature, function_name, json.dumps(args).decode('utf-8'), int(time.time())))
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
                conn.execute("INSERT OR REPLACE INTO rich_payloads (invariant_hash, rich_parts_json, message_id, created_at) VALUES (?, ?, ?, ?)", (inv, json.dumps(rich).decode('utf-8'), message_id, int(time.time())))
                conn.commit()
        except: pass

    def index_suture(self, cumul: str, chat_id: str, inv: str, parent: str = None, message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO suture_index (cumulative_hash, chat_id, invariant_hash, parent_hash, message_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (cumul, chat_id, inv, parent, message_id, int(time.time())))
                conn.commit()
        except: pass

    def save_cognitive_data(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None, model_id: str = None):
        try:
            with self._get_connection() as conn:
                if sig:
                    try: conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, model_id, updated_at) VALUES (?, ?, ?, ?, ?)", (cumul, sig, message_id, model_id, int(time.time())))
                    except: conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, updated_at) VALUES (?, ?, ?, ?)", (cumul, sig, message_id, int(time.time())))
                if thought: conn.execute("INSERT OR REPLACE INTO thought_archive (cumulative_hash, raw_thought, updated_at) VALUES (?, ?, ?)", (cumul, thought, int(time.time())))
                if tool_io: conn.execute("INSERT OR REPLACE INTO tool_journal (cumulative_hash, io_json, updated_at) VALUES (?, ?, ?)", (cumul, json.dumps(tool_io).decode('utf-8'), int(time.time())))
                if model_id:
                    conn.execute("CREATE TABLE IF NOT EXISTS session_state (id INTEGER PRIMARY KEY, last_model_id TEXT, updated_at INTEGER)")
                    conn.execute("INSERT OR REPLACE INTO session_state (id, last_model_id, updated_at) VALUES (1, ?, ?)", (model_id, int(time.time())))
                conn.commit()
        except: pass

    def get_last_active_model(self) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT last_model_id FROM session_state WHERE id = 1").fetchone()
                return row[0] if row else None
        except: pass
        return None

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

    def save_context_stats(self, stats: dict):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO context_stats (id, data, updated_at) VALUES (1, ?, ?)", (std_json.dumps(stats).decode('utf-8'), int(time.time())))
                conn.commit()
        except: pass

    def get_last_context_stats(self) -> dict:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT data FROM context_stats WHERE id = 1").fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return {}

    def move_to_vault(self, file_id: str, filename: str) -> bool:
        old_path = resolve_upload_file_path(self.user_id, file_id)
        if not old_path: return False
        new_path = os.path.join(self.user_dir, "files", os.path.basename(old_path))
        try:
            if not os.path.exists(new_path): shutil.move(old_path, new_path)
            return True
        except: return False
