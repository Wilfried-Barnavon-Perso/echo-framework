"""
title: ECHO Shared Utils
author: ECHO Framework
version: 2.40
description: 2.40: Ajout de la persistance de l'identité du modèle et de la table session_state.
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
    GOOGLE_API_BASE_URL, ECHO_USER_AGENT, ECHO_USERS_ROOT
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
        # HTTP/2 STRICT : Pas de fallback possible si h2 est installé
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=True)
    
    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

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
# SECTION 4 : SERVICE D'AUTHENTIFICATION (DAL) & CLIENT GEMINI
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

    def get_api_keys(self, user_id: str = None) -> List[str]:
        """Récupère la liste des clés API Google (primaire et optionnellement secondaire)."""
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return []
        keys = []
        try:
            conn = sqlite3.connect(f"file://{db_path}?mode=ro", uri=True, timeout=5.0)
            cursor = conn.cursor()
            # On récupère les deux clés potentielles
            for key_name in ['google_api_key', 'google_api_key_secondary']:
                cursor.execute("SELECT value FROM auth_data WHERE key = ?", (key_name,))
                row = cursor.fetchone()
                if row and row[0]: keys.append(row[0])
            conn.close()
        except: pass
        return keys

    def save_api_key(self, key_name: str, value: str, user_id: str = None):
        """Enregistre ou met à jour une clé API."""
        db_path = self._get_db_path(user_id)
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key_name, value, int(time.time())))
                conn.commit()
        except Exception as e:
            print(f"[EchoAuth] Erreur sauvegarde clé {key_name}: {e}")

    def delete_api_key(self, key_name: str, user_id: str = None):
        """Supprime une clé API."""
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
        threshold: int = 2,
        max_retries: int = 3,
        events: Optional[EchoEvents] = None,
        timeout: int = 120
    ) -> dict:
        """Appel JSON classique (pour Filtres et Outils)."""
        if not keys: raise ValueError("Aucune clé API fournie.")
        
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = 2 # RETRY_TIMEBASE par défaut

        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}

            try:
                resp = await client.post(api_url, json=payload, headers=headers, timeout=timeout)
                
                if resp.status_code == 200:
                    return resp.json()
                
                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    # Condition de bascule sur la clé de secours
                    if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                        active_key_idx += 1
                        consecutive_errors = 0
                        if events: await events.status(f"🔄 Surcharge API ({resp.status_code}). Bascule sur la clé de secours...", done=False)
                        continue

                    if attempt < max_retries:
                        wait_time = current_delay * random.uniform(0.7, 1.3)
                        if events: await events.status(f"⚠️ Surcharge API Google ({resp.status_code}). Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                        await asyncio.sleep(wait_time)
                        current_delay *= 2
                        continue
                
                resp.raise_for_status()

            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(0.7, 1.3)
                    if events: await events.status(f"⚠️ Instabilité réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= 2
                    continue
                raise e
        
        raise Exception(f"Échec après {max_retries} tentatives.")

    @staticmethod
    async def stream(
        keys: List[str],
        target_model: str,
        payload: dict,
        threshold: int = 2,
        max_retries: int = 5,
        events: Optional[EchoEvents] = None,
        process_callback: Optional[Any] = None,
        timeout: int = 300
    ) -> AsyncGenerator[Union[str, Dict], None]:
        """Appel SSE avec streaming (pour le Pipe)."""
        if not keys: yield "❌ Aucune clé API configurée."; return
        
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = 2 # RETRY_TIMEBASE par défaut

        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            # alt=sse est crucial pour le streaming Gemini
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:streamGenerateContent?key={api_key}&alt=sse"
            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
                "User-Agent": ECHO_USER_AGENT
            }

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
                            wait_time = current_delay * random.uniform(0.7, 1.3)
                            if events: await events.status(f"⚠️ Surcharge API Google ({r.status_code}). Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                            await asyncio.sleep(wait_time)
                            current_delay *= 3
                            continue
                        else: yield f"❌ Erreur API Google ({r.status_code})."; return
                    
                    r.raise_for_status()
                    
                    if r.http_version != "HTTP/2":
                        yield "❌ Erreur de protocole : HTTP/2 obligatoire pour Gemini AI Studio."; return
                    
                    if process_callback:
                        async for chunk in process_callback(r): yield chunk
                break

            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(0.7, 1.3)
                    if events: await events.status(f"⚠️ Instabilité réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= 2
                    continue
                else: yield f"❌ Erreur système : {str(e)}"; return

    @staticmethod
    async def embed(
        keys: List[str],
        model: str,
        content: dict,
        threshold: int = 2,
        max_retries: int = 3,
        events: Optional[EchoEvents] = None,
        timeout: int = 30
    ) -> dict:
        """Appel Embedding (pour les Outils de mémoire)."""
        if not keys: raise ValueError("Aucune clé API fournie.")
        
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = 2

        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            api_url = f"{GOOGLE_API_BASE_URL}/models/{model}:embedContent?key={api_key}"
            headers = {"Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
            payload = {"model": f"models/{model}", "content": content}

            try:
                resp = await client.post(api_url, json=payload, headers=headers, timeout=timeout)
                
                if resp.status_code == 200:
                    return resp.json()
                
                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                        active_key_idx += 1
                        consecutive_errors = 0
                        if events: await events.status(f"🔄 Surcharge API Embedding ({resp.status_code}). Bascule sur la clé de secours...", done=False)
                        continue

                    if attempt < max_retries:
                        wait_time = current_delay * random.uniform(0.7, 1.3)
                        await asyncio.sleep(wait_time)
                        current_delay *= 2
                        continue
                
                resp.raise_for_status()

            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(0.7, 1.3)
                    await asyncio.sleep(wait_time)
                    current_delay *= 2
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
                conn.execute("INSERT OR REPLACE INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (message_id, chat_id, role, std_json.dumps(parts).decode('utf-8'), int(time.time()))
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

    # --- MÉTHODES DE HACHAGE (LEGACY & SUTURE) ---

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
                conn.execute(
                    "INSERT OR REPLACE INTO rich_payloads (invariant_hash, rich_parts_json, message_id, created_at) VALUES (?, ?, ?, ?)",
                    (inv, json.dumps(rich).decode('utf-8'), message_id, int(time.time()))
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

    def save_cognitive_data(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None, model_id: str = None):
        try:
            with self._get_connection() as conn:
                if sig: 
                    try:
                        conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, model_id, updated_at) VALUES (?, ?, ?, ?, ?)", (cumul, sig, message_id, model_id, int(time.time())))
                    except:
                        # Fallback pour l'ancien schéma de BDD sans model_id
                        conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, updated_at) VALUES (?, ?, ?, ?)", (cumul, sig, message_id, int(time.time())))
                
                if thought: conn.execute("INSERT OR REPLACE INTO thought_archive (cumulative_hash, raw_thought, updated_at) VALUES (?, ?, ?)", (cumul, thought, int(time.time())))
                if tool_io: conn.execute("INSERT OR REPLACE INTO tool_journal (cumulative_hash, io_json, updated_at) VALUES (?, ?, ?)", (cumul, json.dumps(tool_io).decode('utf-8'), int(time.time())))
                
                # Table dédiée pour la persistance du modèle actuel
                if model_id:
                    conn.execute("CREATE TABLE IF NOT EXISTS session_state (id INTEGER PRIMARY KEY, last_model_id TEXT, updated_at INTEGER)")
                    conn.execute("INSERT OR REPLACE INTO session_state (id, last_model_id, updated_at) VALUES (1, ?, ?)", (model_id, int(time.time())))
                conn.commit()
        except: pass

    def get_last_active_model(self) -> Optional[str]:
        """Récupère l'ID du dernier modèle ayant répondu dans cette session."""
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT last_model_id FROM session_state WHERE id = 1").fetchone()
                if row: return row[0]
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

# ==============================================================================
# SECTION 6 : HUD & UI COMPONENTS (ECHO UI)
# ==============================================================================

class EchoUI:
    @staticmethod
    def _generate_universal_hud_js(b64: str, mime: str, hud_id: str, title: str, state_key: str, timeout: int) -> str:
        return f"""
    (function() {{
        const HUD_ID = '{hud_id}';
        const STATE_KEY = '{state_key}';
        const payload = {{ b64: "{b64}", mime: "{mime}", timeout: {timeout} }};
        const ENGINE_KEY = 'echoEngine_' + HUD_ID.replace(/[^a-zA-Z0-9]/g, '_');

        if (!window[ENGINE_KEY]) {{
            window[ENGINE_KEY] = {{
                hud: null, isCropping: false, zoomActive: false, ratio: 1.0, posX: 0, posY: 0,
                timeLeft: 0, timerInt: null,

                getBestSize: function(ratio, percent = 0.25) {{
                    const vw = window.innerWidth, vh = window.innerHeight;
                    let w = Math.sqrt(percent * vw * vh / ratio);
                    let h = w * ratio;
                    if (w > vw * 0.97) {{ w = vw * 0.97; h = w * ratio; }}
                    if (h > vh * 0.97) {{ h = vh * 0.97; w = h / ratio; }}
                    return {{ w, h }};
                }},

                clampHud: function() {{
                    if (!this.hud) return;
                    const vw = window.innerWidth, vh = window.innerHeight;
                    const rect = this.hud.getBoundingClientRect();
                    const marginW = 0.015 * vw, marginH = 0.015 * vh;
                    if (this.posX < marginW) this.posX = marginW;
                    if (this.posY < marginH) this.posY = marginH;
                    if (this.posX + rect.width > vw - marginW) this.posX = vw - marginW - rect.width;
                    if (this.posY + rect.height > vh - marginH) this.posY = vh - marginH - rect.height;
                    this.hud.style.transform = "translate3d(" + this.posX + "px, " + this.posY + "px, 0px)";
                }},

                saveState: function(isFS = null) {{
                    if (!this.hud) return;
                    const area = document.getElementById(HUD_ID + "-area");
                    const isM = area && area.style.display === 'none';
                    const saved = JSON.parse(localStorage.getItem(STATE_KEY) || '{{}}');
                    localStorage.setItem(STATE_KEY, JSON.stringify({{
                        w: this.hud.offsetWidth, x: this.posX, y: this.posY, m: isM, f: isFS !== null ? isFS : (saved.f || false)
                    }}));
                }},

                applyTransition: function(enabled) {{
                    if (!this.hud) return;
                    this.hud.style.transition = enabled ? 'opacity 0.3s, transform 0.3s ease-out, width 0.3s ease-out, height 0.3s ease-out' : 'opacity 0.3s';
                }},

                exportMedia: async function(mode) {{
                    const img = document.getElementById(HUD_ID + "-img");
                    const cropBox = document.getElementById(HUD_ID + "-crop-box");
                    const area = document.getElementById(HUD_ID + "-area");
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    const natW = img.naturalWidth, natH = img.naturalHeight;

                    if (this.isCropping && cropBox.style.display !== 'none') {{
                        const rect = cropBox.getBoundingClientRect(), aRect = area.getBoundingClientRect();
                        const scaleX = natW / aRect.width, scaleY = natH / aRect.height;
                        const sx = (rect.left - aRect.left) * scaleX, sy = (rect.top - aRect.top) * scaleY;
                        const sw = rect.width * scaleX, sh = rect.height * scaleY;
                        canvas.width = sw; canvas.height = sh;
                        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
                    }} else {{
                        canvas.width = natW; canvas.height = natH;
                        ctx.drawImage(img, 0, 0);
                    }}

                    if (mode === 'copy') {{
                        if (!navigator.clipboard || !navigator.clipboard.write) {{
                            const win = window.open();
                            win.document.write('<p>Mode non-sécurisé (HTTP). <br>Faites <b>Clic droit -> Copier</b> :</p><img src="' + canvas.toDataURL('image/png') + '" style="max-width:100%; border:1px solid #ccc;" />');
                            return;
                        }}
                        canvas.toBlob(async (blob) => {{
                            try {{
                                await navigator.clipboard.write([new ClipboardItem({{ 'image/png': blob }})]);
                                const btn = document.getElementById(HUD_ID + "-btn-copy");
                                const old = btn.innerText; btn.innerText = '✓'; btn.style.color = '#4ade80';
                                setTimeout(() => {{ btn.innerText = old; btn.style.color = '#aaa'; }}, 1000);
                            }} catch (err) {{ alert("Erreur copie : " + err); }}
                        }}, 'image/png');
                    }} else {{
                        const link = document.createElement('a');
                        const label = this.isCropping ? 'Crop' : 'Full';
                        link.download = "ECHO_MEDIA_" + label + "_" + Date.now() + ".png";
                        link.href = canvas.toDataURL('image/png');
                        link.click();
                    }}
                }},

                attachEvents: function() {{
                    if (!this.hud) return;
                    const area = document.getElementById(HUD_ID + "-area");
                    const lens = document.getElementById(HUD_ID + "-lens");
                    const img = document.getElementById(HUD_ID + "-img");

                    this.hud.ondblclick = (e) => {{
                        if (e.target.tagName === 'BUTTON') return;
                        this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.25);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        this.clampHud();
                        if(area) area.style.display = 'flex';
                        setTimeout(() => this.saveState(false), 350);
                    }};

                    const header = document.getElementById(HUD_ID + "-header");
                    header.onmousedown = (e) => {{
                        if (e.target.tagName === 'BUTTON') return;
                        e.preventDefault(); this.applyTransition(false);
                        let ox = e.clientX, oy = e.clientY;
                        document.onmousemove = (me) => {{
                            this.posX += (me.clientX - ox); this.posY += (me.clientY - oy);
                            ox = me.clientX; oy = me.clientY;
                            this.clampHud();
                        }};
                        document.onmouseup = () => {{ document.onmousemove = null; this.saveState(false); }};
                    }};

                    this.hud.querySelectorAll('.hdl').forEach(hdl => {{
                        hdl.onmousedown = (e) => {{
                            e.preventDefault(); e.stopPropagation(); this.applyTransition(false);
                            const isR = hdl.classList.contains('tr') || hdl.classList.contains('br'), isT = hdl.classList.contains('tl') || hdl.classList.contains('tr');
                            const isL = hdl.classList.contains('tl') || hdl.classList.contains('bl'), isB = hdl.classList.contains('bl') || hdl.classList.contains('br');
                            const startW = this.hud.offsetWidth, startH = this.hud.offsetHeight, startX = this.posX, startY = this.posY;
                            const ox = e.clientX, oy = e.clientY;
                            document.onmousemove = (me) => {{
                                let nw = isR ? (startW + (me.clientX - ox)) : (startW - (me.clientX - ox));
                                if (nw < 200) nw = 200; if (nw > window.innerWidth * 0.97) nw = window.innerWidth * 0.97;
                                let nh = nw * this.ratio;
                                if (nh > window.innerHeight * 0.97) {{ nh = window.innerHeight * 0.97; nw = nh / this.ratio; }}
                                if (isL && !isR) this.posX = startX + (startW - nw);
                                if (isT && !isB) this.posY = startY + (startH - nh);
                                this.hud.style.width = nw + 'px'; this.hud.style.height = nh + 'px';
                                this.clampHud();
                            }};
                            document.onmouseup = () => {{ document.onmousemove = null; this.saveState(false); }};
                        }};
                    }});

                    area.onmousemove = (e) => {{
                        if (!this.zoomActive) return;
                        const aRect = area.getBoundingClientRect(), hRect = this.hud.getBoundingClientRect();
                        const lx = e.clientX - hRect.left - 75, ly = e.clientY - hRect.top - 75;
                        lens.style.transform = "translate3d(" + lx + "px, " + ly + "px, 0px)";
                        
                        const natW = img.naturalWidth, natH = img.naturalHeight;
                        if (!natW || !natH) return;
                        
                        let renderW = aRect.width, renderH = renderW * (natH / natW);
                        if (renderH > aRect.height) {{ renderH = aRect.height; renderW = renderH * (natW / natH); }}
                        
                        const offsetX = (aRect.width - renderW) / 2, offsetY = (aRect.height - renderH) / 2;
                        const mouseX_on_image = e.clientX - aRect.left - offsetX, mouseY_on_image = e.clientY - aRect.top - offsetY;
                        
                        const zoomFactor = 2.5;
                        lens.style.backgroundSize = (renderW * zoomFactor) + "px " + (renderH * zoomFactor) + "px";
                        lens.style.backgroundPosition = (75 - (mouseX_on_image * zoomFactor)) + "px " + (75 - (mouseY_on_image * zoomFactor)) + "px";
                    }};
                    area.onmouseenter = () => {{ if(this.zoomActive) lens.style.display = 'block'; }};
                    area.onmouseleave = () => {{ lens.style.display = 'none'; }};

                    document.getElementById(HUD_ID + "-btn-crop").onclick = (e) => {{
                        e.stopPropagation(); this.isCropping = !this.isCropping;
                        const cropBox = document.getElementById(HUD_ID + "-crop-box");
                        cropBox.style.display = this.isCropping ? 'block' : 'none';
                        e.target.style.color = this.isCropping ? '#fff' : '#aaa';
                        if (this.isCropping) {{
                            cropBox.style.width = area.offsetWidth + 'px'; cropBox.style.height = area.offsetHeight + 'px';
                            cropBox.style.transform = 'translate3d(0px, 0px, 0px)';
                        }}
                    }};

                    const cropBox = document.getElementById(HUD_ID + "-crop-box");
                    cropBox.onmousedown = (e) => {{
                        if (e.target.classList.contains('cp')) return;
                        e.preventDefault(); e.stopPropagation();
                        let ox = e.clientX, oy = e.clientY, tx = 0, ty = 0;
                        const match = cropBox.style.transform.match(/translate3d\\(([-0-9.]+)px,\\s*([-0-9.]+)px/);
                        if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                        document.onmousemove = (me) => {{
                            tx += (me.clientX - ox); ty += (me.clientY - oy);
                            ox = me.clientX; oy = me.clientY;
                            cropBox.style.transform = "translate3d(" + tx + "px, " + ty + "px, 0px)";
                        }};
                        document.onmouseup = () => document.onmousemove = null;
                    }};

                    cropBox.querySelectorAll('.cp').forEach(cp => {{
                        cp.onmousedown = (e) => {{
                            e.preventDefault(); e.stopPropagation();
                            const isL = cp.classList.contains('tl') || cp.classList.contains('bl') || cp.classList.contains('lc');
                            const isR = cp.classList.contains('tr') || cp.classList.contains('br') || cp.classList.contains('rc');
                            const isT = cp.classList.contains('tl') || cp.classList.contains('tr') || cp.classList.contains('tc');
                            const isB = cp.classList.contains('bl') || cp.classList.contains('br') || cp.classList.contains('bc');
                            let startW = cropBox.offsetWidth, startH = cropBox.offsetHeight, ox = e.clientX, oy = e.clientY;
                            let tx = 0, ty = 0;
                            const match = cropBox.style.transform.match(/translate3d\\(([-0-9.]+)px,\\s*([-0-9.]+)px/);
                            if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                            const startX = tx, startY = ty;
                            document.onmousemove = (me) => {{
                                if (isR) cropBox.style.width = (startW + (me.clientX - ox)) + "px";
                                else if (isL) {{
                                    const nw = startW - (me.clientX - ox);
                                    cropBox.style.width = nw + "px"; 
                                    cropBox.style.transform = "translate3d(" + (startX + (startW - nw)) + "px, " + ty + "px, 0px)";
                                }}
                                if (isB) cropBox.style.height = (startH + (me.clientY - oy)) + "px";
                                else if (isT) {{
                                    const nh = startH - (me.clientY - oy);
                                    cropBox.style.height = nh + "px";
                                    const curX = isL ? (startX + (startW - cropBox.offsetWidth)) : tx;
                                    cropBox.style.transform = "translate3d(" + curX + "px, " + (startY + (startH - nh)) + "px, 0px)";
                                }}
                            }};
                            document.onmouseup = () => document.onmousemove = null;
                        }};
                    }});

                    document.getElementById(HUD_ID + "-btn-copy").onclick = (e) => {{ e.stopPropagation(); this.exportMedia('copy'); }};
                    document.getElementById(HUD_ID + "-btn-save").onclick = (e) => {{ e.stopPropagation(); this.exportMedia('save'); }};
                    document.getElementById(HUD_ID + "-btn-zoom").onclick = (e) => {{
                        e.stopPropagation(); this.zoomActive = !this.zoomActive;
                        e.target.style.color = this.zoomActive ? '#4ade80' : '#aaa';
                        if(!this.zoomActive) lens.style.display = 'none';
                    }};
                    document.getElementById(HUD_ID + "-btn-min").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const a = document.getElementById(HUD_ID + "-area");
                        a.style.display = a.style.display === 'none' ? 'flex' : 'none';
                        this.hud.style.height = a.style.display === 'none' ? 'auto' : (this.hud.offsetWidth * this.ratio) + 'px';
                        this.saveState(false);
                    }};
                    document.getElementById(HUD_ID + "-btn-def").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.25);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        if(area) area.style.display = 'flex'; this.clampHud();
                        setTimeout(() => this.saveState(false), 350);
                    }};
                    document.getElementById(HUD_ID + "-btn-full").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.97);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        if(area) area.style.display = 'flex'; this.clampHud();
                        setTimeout(() => this.saveState(true), 350);
                    }};
                    document.getElementById(HUD_ID + "-btn-close").onclick = (e) => {{ e.stopPropagation(); this.hud.remove(); }};
                }},

                create: function(data) {{
                    const old = document.getElementById(HUD_ID); if(old) old.remove();
                    this.hud = document.createElement('div');
                    this.hud.id = HUD_ID;
                    this.hud.style.cssText = 'position:fixed; top:0; left:0; z-index:10000; background:rgba(30,30,30,0.95); backdrop-filter:blur(12px); border:1px solid #444; border-radius:8px; box-shadow:0 10px 50px rgba(0,0,0,0.7); color:white; font-family:sans-serif; display:flex; flex-direction:column; opacity:0; min-width:200px; transition:opacity 0.3s; will-change:transform, opacity; transform: translate3d(20px, 50px, 0px);';
                    
                    this.hud.innerHTML = `
                        <div id="${{HUD_ID}}-header" style="padding:6px 12px; background:rgba(0,0,0,0.4); display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #444; cursor:move; user-select:none; border-radius: 8px 8px 0 0; min-height: 32px;">
                            <div style="display:flex; align-items:center; gap:8px;">
                                <span style="font-size:11px; font-weight:bold; color:#4ade80;">{title}</span>
                                <span id="${{HUD_ID}}-timer" style="font-size:10px; color:#888;"></span>
                            </div>
                            <div style="display:flex; gap:10px;">
                                <button id="${{HUD_ID}}-btn-crop" title="Sélection" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">⛶</button>
                                <button id="${{HUD_ID}}-btn-copy" title="Copier" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">❐</button>
                                <button id="${{HUD_ID}}-btn-save" title="Télécharger" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">📥</button>
                                <button id="${{HUD_ID}}-btn-zoom" title="Loupe" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">🔍</button>
                                <button id="${{HUD_ID}}-btn-min" title="Réduire" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">_</button>
                                <button id="${{HUD_ID}}-btn-def" title="Taille Défaut" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">↺</button>
                                <button id="${{HUD_ID}}-btn-full" title="Plein Écran" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">□</button>
                                <button id="${{HUD_ID}}-btn-close" title="Fermer" style="background:none; border:none; color:#ff4444; cursor:pointer; font-size:18px; font-weight:bold; line-height:1; padding:2px;">×</button>
                            </div>
                        </div>
                        <div id="${{HUD_ID}}-area" style="flex:1; width:100%; height:100%; background:black; display:flex; justify-content:center; overflow:hidden; border-radius: 0 0 8px 8px; position:relative; cursor:crosshair;">
                            <img id="${{HUD_ID}}-img" style="width:100%; height:100%; object-fit:contain; pointer-events:none;" />
                            <div id="${{HUD_ID}}-crop-box" style="position:absolute; border:2px dashed #fff; display:none; box-sizing:border-box; z-index:10002; cursor:move; box-shadow: 0 0 0 1px #000; will-change: transform;">
                                <div class="cp tl" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; top:-5px; cursor:nwse-resize;"></div>
                                <div class="cp tr" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; top:-5px; cursor:nesw-resize;"></div>
                                <div class="cp bl" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; bottom:-5px; cursor:nesw-resize;"></div>
                                <div class="cp br" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; bottom:-5px; cursor:nwse-resize;"></div>
                                <div class="cp tc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:50%; top:-5px; margin-left:-5px; cursor:ns-resize;"></div>
                                <div class="cp bc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:50%; bottom:-5px; margin-left:-5px; cursor:ns-resize;"></div>
                                <div class="cp lc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; top:50%; margin-top:-5px; cursor:ew-resize;"></div>
                                <div class="cp rc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; top:50%; margin-top:-5px; cursor:ew-resize;"></div>
                            </div>
                        </div>
                        <div id="${{HUD_ID}}-lens" style="position:absolute; width:150px; height:150px; border:2px solid #4ade80; border-radius:50%; pointer-events:none; display:none; box-shadow:0 0 30px rgba(0,0,0,0.8); z-index:10005; background-repeat:no-repeat; will-change: transform;"></div>
                        <div class="hdl tl" style="position:absolute; width:20px; height:20px; left:-10px; top:-10px; cursor:nwse-resize; z-index:100;"></div>
                        <div class="hdl tr" style="position:absolute; width:20px; height:20px; right:-10px; top:-10px; cursor:nesw-resize; z-index:100;"></div>
                        <div class="hdl bl" style="position:absolute; width:20px; height:20px; left:-10px; bottom:-10px; cursor:nesw-resize; z-index:100;"></div>
                        <div class="hdl br" style="position:absolute; width:20px; height:20px; right:-10px; bottom:-10px; cursor:nwse-resize; z-index:100;"></div>
                    `;
                    document.body.appendChild(this.hud);
                    this.attachEvents();
                    setTimeout(() => {{ if(this.hud) this.hud.style.opacity = '1'; }}, 50);

                    const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                    if (saved && saved.w) {{
                        this.applyTransition(false);
                        this.posX = saved.x; this.posY = saved.y;
                        this.hud.style.width = saved.w + 'px';
                        if (!saved.f && saved.m) {{
                            const a = document.getElementById(HUD_ID + "-area"); if(a) a.style.display = 'none';
                            this.hud.style.height = 'auto';
                        }}
                        this.clampHud();
                    }}
                }},

                update: function(data) {{
                    if (!this.hud || !document.getElementById(HUD_ID)) {{ this.create(data); }}
                    const img = document.getElementById(HUD_ID + "-img");
                    img.onload = () => {{
                        this.ratio = img.naturalHeight / img.naturalWidth;
                        const lens = document.getElementById(HUD_ID + "-lens");
                        if (lens) lens.style.backgroundImage = 'url("' + img.src + '")';
                        const area = document.getElementById(HUD_ID + "-area");
                        const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                        if (saved && saved.w) {{
                            if (saved.f) {{
                                const size = this.getBestSize(this.ratio, 0.97);
                                this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                                this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                            }} else if (area && area.style.display !== 'none') {{
                                this.hud.style.height = (this.hud.offsetWidth * this.ratio) + 'px';
                            }}
                        }} else {{
                            const size = this.getBestSize(this.ratio, 0.25);
                            this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                            this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        }}
                        this.clampHud();
                    }};
                    img.src = "data:" + data.mime + ";base64," + data.b64;

                    if (this.timerInt) clearInterval(this.timerInt);
                    if (data.timeout > 0) {{
                        this.timeLeft = data.timeout;
                        const tSpan = document.getElementById(HUD_ID + "-timer");
                        this.timerInt = setInterval(() => {{
                            window[ENGINE_KEY].timeLeft--; 
                            if(tSpan) tSpan.innerText = "[" + window[ENGINE_KEY].timeLeft + "s]";
                            if (window[ENGINE_KEY].timeLeft <= 5 && this.hud) this.hud.style.opacity = (window[ENGINE_KEY].timeLeft / 5);
                            if (window[ENGINE_KEY].timeLeft <= 0) {{ 
                                clearInterval(this.timerInt); 
                                const h = document.getElementById(HUD_ID); if(h) h.remove();
                            }}
                        }}, 1000);
                    }} else {{
                        const tSpan = document.getElementById(HUD_ID + "-timer");
                        if (tSpan) tSpan.innerText = "";
                    }}
                }}
            }};
        }}
        window[ENGINE_KEY].update(payload);
    }})();
"""

    @staticmethod
    async def monitor_ECHO(events: EchoEvents, b64: str, mime: str, hud_id: str, title: str, state_key: str, timeout: int = 0):
        js_code = EchoUI._generate_universal_hud_js(b64, mime, hud_id, title, state_key, timeout)
        try:
            import asyncio
            await asyncio.wait_for(events.call("execute", {"code": js_code}), timeout=5.0)
        except Exception as e:
            print(f"[EchoUI] HUD Injection timeout/error: {e}")

    @staticmethod
    async def deploy_context_gauge(events: EchoEvents, plan_name: str, credits_val: str, quota_str: str, c_t: int, active_p_t: int, g_t: int, max_t: int, cache_pct: float, prompt_pct: float, gen_pct: float):
        js_code = f"""
        (function() {{
            var navContainer = document.querySelector('nav div.flex.items-center.w-full.max-w-full');
            if (!navContainer) return;
            var rightControls = navContainer.querySelector('div.self-start.flex.flex-none.items-center');
            var oldHud = document.getElementById('echo-nav-context-hud');
            if (oldHud) oldHud.remove();
            var hud = document.createElement('div');
            hud.id = 'echo-nav-context-hud';
            hud.style.cssText = 'display:flex;align-items:center;margin:0 12px;flex-grow:8;width:66%;min-width:350px;opacity:0.9;transition:opacity 0.2s;';
            hud.onmouseover = function() {{ this.style.opacity = '1'; }};
            hud.onmouseout = function() {{ this.style.opacity = '0.9'; }};
            var billingInfo = "";
            if ("{plan_name}") billingInfo += `💳 {plan_name} | {quota_str}`;
            if ("{credits_val}" !== "0") billingInfo += `🔋 {credits_val} crédits IA | `;
            hud.title = billingInfo + `🟪 Cache: {c_t} | 🟩 User/Prompt: {active_p_t} | 🟧 Generated: {g_t} | ⬜ Max: {max_t}`;
            var label = document.createElement('span');
            label.innerText = 'CTX'; label.style.cssText = 'font-size:10px;font-weight:bold;color:var(--color-gray-500, #6b7280);margin-right:6px;white-space:nowrap;';
            if (window.innerWidth < 640) label.style.display = 'none';
            hud.appendChild(label);
            var barContainer = document.createElement('div');
            barContainer.style.cssText = 'display:flex;width:100%;height:8px;background-color:rgba(128,128,128,0.2);border-radius:4px;overflow:hidden;';
            var bars = [['#8b5cf6', {cache_pct}], ['#10b981', {prompt_pct}], ['#f59e0b', {gen_pct}]];
            bars.forEach(b => {{
                var div = document.createElement('div');
                div.style.width = b[1] + '%'; div.style.backgroundColor = b[0];
                barContainer.appendChild(div);
            }});
            hud.appendChild(barContainer);
            if (rightControls) navContainer.insertBefore(hud, rightControls); else navContainer.appendChild(hud);
        }})();
        """
        await events.emit("execute", {"code": js_code})
