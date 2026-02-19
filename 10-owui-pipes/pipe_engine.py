"""
title: ECHO Engine
author: Wilfried BARNAVON
version: 142.0
description: 142.0: Identity Awareness. Placeholder resolution (__PIPE_MODEL_ID__) in context.
"""

# ==============================================================================
# SECTION 0 : IMPORTATIONS & CONSTANTES GLOBALES
# ==============================================================================
import os
import sys
import secrets
import hashlib
import re
import time
import base64
import codecs
import asyncio
import json as std_json 
import sqlite3
import zlib
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# --- IMPORTATIONS TIERCES CRITIQUES ---
try:
    import httpx
    import orjson
    import pybase64
    import mgzip as gzip
    from pydantic import BaseModel, Field
except ImportError as e:
    missing_module = e.name or "inconnu"
    raise ImportError(
        f"❌ Module critique manquant : '{missing_module}'. "
        f"Ce module est requis pour le fonctionnement du script Gemini Pro Unified v142+. "
        f"Veuillez l'installer dans l'environnement Python."
    ) from e

# --- CONSTANTES DE CONFIGURATION GOOGLE ---
GOOGLE_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REDIRECT_URI = "https://codeassist.google.com/authcode"
GOOGLE_API_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# --- LOGGER DEBUG (DOCKER CONSOLE ONLY) ---
class DebugLogger:
    def __init__(self, data_dir: str, chat_id: str):
        self.log_dir = os.path.join(data_dir, "debug_logs")
        os.makedirs(self.log_dir, exist_ok=True)
        safe_id = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_") if chat_id else "unknown_chat"
        self.log_path = os.path.join(self.log_dir, f"debug_{safe_id}.json")

    def log(self, event_type: str, payload: Any, metadata: Dict = None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "metadata": metadata or {},
            "data": payload
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(std_json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception: pass

    def console(self, msg: str):
        print(f"[PIPE DEBUG] {msg}", flush=True)

# --- GESTIONNAIRE DECONNEXION PARTAGÉ ---
_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(idle_timeout: int = 300, enable_http2: bool = True) -> httpx.AsyncClient:
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
    now = time.time()
    
    try:
        if _SHARED_ASYNC_CLIENT and not _SHARED_ASYNC_CLIENT.is_closed:
            if hasattr(_SHARED_ASYNC_CLIENT, "_transport") and hasattr(_SHARED_ASYNC_CLIENT._transport, "_pool"):
                 client_loop = getattr(_SHARED_ASYNC_CLIENT._transport._pool, "_loop", None)
                 if client_loop and client_loop != asyncio.get_running_loop():
                     await _SHARED_ASYNC_CLIENT.aclose()
                     _SHARED_ASYNC_CLIENT = None
    except:
        _SHARED_ASYNC_CLIENT = None

    if _SHARED_ASYNC_CLIENT and (now - _LAST_CLIENT_ACCESS > idle_timeout):
        old_client = _SHARED_ASYNC_CLIENT
        _SHARED_ASYNC_CLIENT = None 
        try:
            await old_client.aclose()
        except: pass

    if _SHARED_ASYNC_CLIENT is None or _SHARED_ASYNC_CLIENT.is_closed:
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=300)
        try:
            _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=enable_http2)
        except ImportError:
            _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=False)
        except Exception:
            _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=False)
    
    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

# Chaîne magique définie par Google pour le bypass de validation stricte
MAGIC_KEY_SKIP_VALIDATION = "context_engineering_is_the_way_to_go"

# ==============================================================================
# SECTION 1 : DÉPENDANCES OPTIONNELLES
# ==============================================================================
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from google.auth.transport.requests import Request as GoogleAuthRequest
    HAS_GOOGLE_LIBS = True
except ImportError:
    HAS_GOOGLE_LIBS = False

# ==============================================================================
# SECTION 2 : CLIENT CONFIG
# ==============================================================================
OFFICIAL_CLIENT_CONFIG = {
    "installed": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": GOOGLE_AUTH_URI,
        "token_uri": GOOGLE_TOKEN_URI,
        "redirect_uris": [GOOGLE_REDIRECT_URI],
    }
}

# ==============================================================================
# SECTION 4 : UNIFIED USER DATA MANAGER (OPTIMIZED CACHE - NO ZLIB)
# ==============================================================================
class UserDataManager:
    def __init__(self, data_dir: str, user_id: str, debug_mode: bool = False):
        self.db_dir = os.path.join(data_dir, "user_dbs")
        self.debug_mode = debug_mode
        os.makedirs(self.db_dir, exist_ok=True)
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        self.db_path = os.path.join(self.db_dir, f"user-{safe_uid}.db")
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS messages (hash TEXT PRIMARY KEY, data BLOB NOT NULL, created_at INTEGER NOT NULL)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS signatures (
                        chat_id TEXT NOT NULL,
                        tool_call_id TEXT NOT NULL,
                        signature TEXT NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (chat_id, tool_call_id)
                    )
                """)
                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
        except Exception as e:
            if self.debug_mode: print(f"[PIPE ERROR] DB Init {self.db_path}: {e}")

    def compute_message_hash(self, block_messages: List[Dict], chat_id: Optional[str]) -> str:
        hasher = hashlib.sha256()
        if chat_id:
            hasher.update(str(chat_id).encode("utf-8"))

        for msg in block_messages:
            hasher.update((msg.get("role", "")).encode("utf-8"))
            content = msg.get("content", "")
            if isinstance(content, str):
                hasher.update(content.strip().encode("utf-8"))
            elif isinstance(content, list):
                hasher.update(orjson.dumps(content, option=orjson.OPT_SORT_KEYS))
            
            tcs = msg.get("tool_calls", [])
            if tcs:
                hasher.update("TOOLS:".encode("utf-8")),
                for tc in tcs:
                    hasher.update(f"{tc.get('id', '')}:{tc.get('function', {}).get('name', '')}:{tc.get('function', {}).get('arguments', '')}".encode("utf-8"))
        return hasher.hexdigest()

    # --- Auth Data Methods ---
    def save_auth_data(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)",
                             (key, value, int(time.time())))
        except Exception: pass

    def get_auth_data(self, key: str) -> Optional[Tuple[str, int]]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value, updated_at FROM auth_data WHERE key = ?", (key,))
                row = cursor.fetchone()
                return row if row else None
        except Exception: pass
        return None

    def delete_auth_data(self, key: str):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM auth_data WHERE key = ?", (key,))
        except Exception: pass

    # --- Message Cache Methods (OPTIMIZED - NO ZLIB) ---
    # Le cache stocke le résultat JSON prêt à l'emploi pour éviter le re-traitement.
    # Format : Hash(Block) -> JSON(Parts List + Deferred Text)
    def get_message_cache(self, msg_hash: str) -> Optional[List[Dict]]:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM messages WHERE hash = ?", (msg_hash,))
                row = cursor.fetchone()
                if row:
                    try: return orjson.loads(row[0])
                    except: return None
        except Exception: pass
        return None

    def set_message_cache(self, msg_hash: str, data: List[Dict]):
        try:
            raw_data = orjson.dumps(data)
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO messages (hash, data, created_at) VALUES (?, ?, ?)", 
                             (msg_hash, raw_data, int(time.time())))
        except Exception: pass

    # --- Signature Methods ---
    def save_signature(self, chat_id: str, signature: str, tool_call_id: Optional[str] = None):
        if not chat_id or not signature: return
        effective_tool_call_id = tool_call_id or '__latest__'
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO signatures (chat_id, tool_call_id, signature, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (chat_id, effective_tool_call_id, signature, int(time.time())))
        except Exception: pass

    def get_signature(self, chat_id: str, tool_call_id: Optional[str] = None) -> Optional[str]:
        if not chat_id: return None
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if tool_call_id:
                    cursor.execute("SELECT signature FROM signatures WHERE chat_id = ? AND tool_call_id = ?", (chat_id, tool_call_id))
                    row = cursor.fetchone()
                    if row: return row[0]
                
                cursor.execute("SELECT signature FROM signatures WHERE chat_id = ? AND tool_call_id = '__latest__'", (chat_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception: pass
        return None

    # --- Context Stats Methods ---
    def save_context_stats(self, stats: Dict):
        if not stats: return
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO context_stats (id, data, updated_at) VALUES (1, ?, ?)",
                             (std_json.dumps(stats), int(time.time())))
        except Exception: pass
        
    # --- Async Wrappers ---
    async def get_message_cache_async(self, msg_hash: str) -> Optional[List[Dict]]:
        return await asyncio.to_thread(self.get_message_cache, msg_hash)

    async def set_message_cache_async(self, msg_hash: str, data: List[Dict]):
        await asyncio.to_thread(self.set_message_cache, msg_hash, data)

# ==============================================================================
# SECTION 3 : SERVICE D'AUTHENTIFICATION
# ==============================================================================
class AuthService:
    def __init__(self, user_data_manager: UserDataManager):
        self.user_data_manager = user_data_manager
        self.base_url = GOOGLE_API_BASE_URL

    def _generate_pkce(self):
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        if not HAS_GOOGLE_LIBS: return "❌ **Erreur** : Librairies `google-auth` manquantes."
        
        pkce_data = self.user_data_manager.get_auth_data('pkce_verifier')
        if pkce_data and time.time() - pkce_data[1] < 300:
            verifier = pkce_data[0]
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        else:
            verifier, challenge = self._generate_pkce()
            self.user_data_manager.save_auth_data('pkce_verifier', verifier)

        flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False)
        flow.redirect_uri = GOOGLE_REDIRECT_URI
        url, _ = flow.authorization_url(prompt="consent", access_type="offline", code_challenge=challenge, code_challenge_method="S256")
        return f"### 🔐 Authentification Requise\n\n1. **[Cliquez ici]({url})**\n2. Connectez-vous.\n3. Copiez le code `4/...`.\n4. **Collez-le ici**."

    def exchange_code(self, code: str) -> Tuple[bool, str]:
        if not HAS_GOOGLE_LIBS: return False, "Libs manquantes."
        pkce_data = self.user_data_manager.get_auth_data('pkce_verifier')
        if not pkce_data or time.time() - pkce_data[1] > 600: return False, "Session expirée."

        verifier = pkce_data[0]
        try:
            flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False)
            flow.redirect_uri = GOOGLE_REDIRECT_URI
            flow.fetch_token(code=code.strip(), code_verifier=verifier)
            self.user_data_manager.save_auth_data('google_token', flow.credentials.to_json())
            return True, "Succès."
        except Exception as e: return False, str(e)
        finally:
            self.user_data_manager.delete_auth_data('pkce_verifier')

    def get_valid_credentials(self):
        token_data = self.user_data_manager.get_auth_data('google_token')
        if not token_data: return None

        creds = None
        try: creds = Credentials.from_authorized_user_info(std_json.loads(token_data[0]), GOOGLE_SCOPES)
        except: return None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                self.user_data_manager.save_auth_data('google_token', creds.to_json())
            except: return None
        return creds if (creds and creds.valid) else None

    def get_project_id(self, creds, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        cached_pid_data = self.user_data_manager.get_auth_data('google_project_id')
        if cached_pid_data and not debug_mode: return cached_pid_data[0], "Cache."

        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json", "User-Agent": "GeminiCLI/0.24.0"}
        payload = {"metadata": {"ideType": "IDE_UNSPECIFIED", "pluginType": "GEMINI"}}
        try:
            resp = httpx.post(f"{self.base_url}:loadCodeAssist", headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("cloudaicompanionProject")
                pid = raw.get("id") if isinstance(raw, dict) else raw
                if pid:
                    pid = pid.replace("projects/", "")
                    self.user_data_manager.save_auth_data('google_project_id', pid)
                    return pid, "API OK."
                else:
                    if cached_pid_data: return cached_pid_data[0], f"API Fail, Fallback to Cache."
                    return None, f"**JSON inattendu** : {str(data)[:200]}"
            else: return None, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e: return None, str(e)

# ==============================================================================
# SECTION 5 : ORCHESTRATEUR (LOGIQUE MÉTIER SIMPLIFIÉE)
# ==============================================================================
class Orchestrator:
    def __init__(self, valves, user_valves, data_dir: str, user_id: str):
        self.valves = valves
        self.user_valves = user_valves
        self.data_dir = data_dir
        self.user_id = user_id
        self.tool_map = {}
        self.user_data_manager = UserDataManager(data_dir, user_id, valves.DEBUG_MODE)
        self.logger = DebugLogger(data_dir, "orchestrator") if valves.DEBUG_MODE else None

    def check_for_auth_code(self, messages: List[Dict]) -> Optional[str]:
        if not messages: return None
        last_msg = messages[-1].get("content", "")
        if isinstance(last_msg, list): return None
        match = re.search(r"(4/[a-zA-Z0-9_-]+)", str(last_msg).strip())
        return match.group(1) if match and len(match.group(1)) > 30 else None

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        if not tools: return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                funcs.append({"name": f.get("name"), "description": f.get("description", ""), "parameters": f.get("parameters", {"type": "object", "properties": {}})})
        return [{"functionDeclarations": funcs}] if funcs else None

    async def _process_tool_turn(self, messages: List[Dict], start_idx: int) -> Tuple[List[Dict], int]:
        parts = []
        i = start_idx
        while i < len(messages) and messages[i]["role"] == "tool":
            tm = messages[i]
            tool_name = self.tool_map.get(tm.get("tool_call_id"), "unknown_tool")
            try: val = orjson.loads(tm.get("content", "{{}}"))
            except: val = {"result": str(tm.get("content", ""))}
            parts.append({"functionResponse": {"name": tool_name, "response": val}})
            i += 1
        return parts, i

    async def _process_model_turn(self, messages: List[Dict], start_idx: int, chat_id: str) -> Tuple[List[Dict], str, int]:
        parts = []
        deferred_text = ""
        i = start_idx
        
        while i < len(messages) and messages[i]["role"] in ["assistant", "model"]:
            sub_m = messages[i]
            txt = sub_m.get("content", "")
            if isinstance(txt, list): txt = "".join([x.get("text","") for x in txt if "text" in x])
            
            txt = re.sub(r'<think>.*?</think>', '', str(txt), flags=re.DOTALL)
            txt = re.sub(r'<details>.*?</details>', '', txt, flags=re.DOTALL)
            txt = txt.strip()
            
            if sub_m.get("tool_calls"):
                if txt:
                    if deferred_text: deferred_text += "\n\n"
                    deferred_text += txt
            else:
                if deferred_text:
                    if txt: txt = deferred_text + "\n\n" + txt
                    else: txt = deferred_text
                    deferred_text = ""
                if txt: parts.append({"text": txt})

            if sub_m.get("tool_calls"):
                for idx, tc in enumerate(sub_m["tool_calls"]) :
                    try:
                        args = orjson.loads(tc["function"]["arguments"])
                        part_data = {"functionCall": {"name": tc["function"]["name"], "args": args}}
                        if idx == 0:
                            sig = args.pop("_thought_signature", None)
                            call_id = tc.get("id")
                            if not sig and chat_id: sig = self.user_data_manager.get_signature(chat_id, call_id)
                            if not sig: sig = MAGIC_KEY_SKIP_VALIDATION
                            part_data["thoughtSignature"] = sig
                        parts.append(part_data)
                    except: pass
            i += 1
        
        if parts and "text" in parts[-1] and not any("functionCall" in p for p in parts) and chat_id:
              latest_sig = self.user_data_manager.get_signature(chat_id)
              if latest_sig: parts[-1]["thoughtSignature"] = latest_sig
        
        if not parts: parts.append({"text": " "})
        return parts, deferred_text, i

    async def _process_user_turn(self, message: Dict) -> List[Dict]:
        """
        Traite un message USER.
        N'utilise QUE le JSON déjà préparé par le Filtre (Texte + Inline Data).
        Plus aucune lecture disque ici (délégué au Filtre v4.13+).
        """
        parts = []
        content = message.get("content", "")

        if isinstance(content, str) and content.strip():
            parts.append({"text": content})
        
        elif isinstance(content, list):
            for item in content:
                 if item.get("type") == "text": 
                     parts.append({"text": item.get("text", "")})
                 elif item.get("type") == "image_url":
                     url = item.get("image_url", {}).get("url", "")
                     if url.startswith("data:"):
                         try:
                             header, b64_data = url.split(",", 1)
                             mime_type = header.split(":")[1].split(";")[0]
                             parts.append({"inlineData": {"mimeType": mime_type, "data": b64_data}})
                         except: pass
                 elif "inline_data" in item:
                     parts.append({"inlineData": {"mimeType": item["inline_data"]["mime_type"], "data": item["inline_data"]["data"]}})

        return parts

    async def prepare_context(self, body: Dict, chat_id: str) -> List[Dict]:
        messages = body.get("messages", [])
        
        # --- RESOLUTION PLACEHOLDER IDENTITY AWARENESS ---
        # Le Filtre v4.17 injecte "__PIPE_MODEL_ID__" dans le contexte.
        # Le Pipe v142.0 le remplace par la vraie valeur active.
        active_model = self.user_valves.MODEL_SELECTION
        
        for m in messages:
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str):
                    if "__PIPE_MODEL_ID__" in content:
                        m["content"] = content.replace("__PIPE_MODEL_ID__", active_model)
                elif isinstance(content, list):
                    for part in content:
                        if part.get("type") == "text":
                            txt = part.get("text", "")
                            if "__PIPE_MODEL_ID__" in txt:
                                part["text"] = txt.replace("__PIPE_MODEL_ID__", active_model)
        # ------------------------------------------------
        
        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if "id" in tc and "function" in tc:
                        self.tool_map[tc["id"]] = tc["function"].get("name")

        final_contents = []
        deferred_text = ""
        cache_valid = True
        i = 0
        
        while i < len(messages):
            if messages[i]["role"] == "system": 
                i+=1; continue
            
            block_msgs = []
            block_type = messages[i]["role"]
            
            if block_type == "tool":
                while i < len(messages) and messages[i]["role"] == "tool":
                    block_msgs.append(messages[i]); i += 1
            elif block_type in ["assistant", "model"]:
                while i < len(messages) and messages[i]["role"] in ["assistant", "model"]:
                    block_msgs.append(messages[i]); i += 1
            else:
                block_msgs.append(messages[i]); i += 1

            current_hash_input = block_msgs
            if deferred_text:
                current_hash_input = block_msgs + [{"role": "virtual_state", "content": deferred_text}]
            
            block_hash = self.user_data_manager.compute_message_hash(current_hash_input, chat_id)
            
            if self.logger: self.logger.console(f"Hash Computation ({len(block_msgs)} msgs): {block_hash[:8]}...")

            cached_data = None
            if cache_valid:
                cached_data = await self.user_data_manager.get_message_cache_async(block_hash)
            
            if cached_data:
                if self.logger: self.logger.console(f"CACHE HIT: {block_hash[:8]}")
                parts = cached_data.get("parts", [])
                new_deferred = cached_data.get("deferred_text", "")
                
                if block_type == "tool":
                    if final_contents and final_contents[-1]["role"] == "user": final_contents[-1]["parts"].extend(parts)
                    else: final_contents.append({"role": "user", "parts": parts})
                elif block_type in ["assistant", "model"]:
                    if deferred_text and new_deferred: deferred_text += "\n\n" + new_deferred
                    elif new_deferred: deferred_text = new_deferred
                    final_contents.append({"role": "model", "parts": parts})
                else: # User
                    if deferred_text:
                        if final_contents:
                            last_msg = final_contents[-1]
                            if last_msg["role"] == "model": last_msg["parts"].append({"text": deferred_text})
                            else: final_contents.append({"role": "model", "parts": [{"text": deferred_text}]})
                        deferred_text = ""
                    if parts: final_contents.append({"role": "user", "parts": parts})

            else:
                if self.logger: self.logger.console(f"CACHE MISS: {block_hash[:8]}")
                cache_valid = False
                processed_parts = []
                new_deferred = ""
                
                if block_type == "tool":
                    processed_parts, _ = await self._process_tool_turn(block_msgs, 0)
                    if final_contents and final_contents[-1]["role"] == "user": final_contents[-1]["parts"].extend(processed_parts)
                    else: final_contents.append({"role": "user", "parts": processed_parts})

                elif block_type in ["assistant", "model"]:
                    processed_parts, new_deferred, _ = await self._process_model_turn(block_msgs, 0, chat_id)
                    if deferred_text and new_deferred: deferred_text += "\n\n" + new_deferred
                    elif new_deferred: deferred_text = new_deferred
                    final_contents.append({"role": "model", "parts": processed_parts})

                else: # User
                    if deferred_text:
                        if final_contents:
                            last_msg = final_contents[-1]
                            if last_msg["role"] == "model": last_msg["parts"].append({"text": deferred_text})
                            else: final_contents.append({"role": "model", "parts": [{"text": deferred_text}]})
                        deferred_text = ""
                    
                    m = block_msgs[0]
                    processed_parts = await self._process_user_turn(m)
                    if processed_parts: final_contents.append({"role": "user", "parts": processed_parts})

                if block_hash:
                    data_to_cache = {"parts": processed_parts, "deferred_text": new_deferred}
                    await self.user_data_manager.set_message_cache_async(block_hash, data_to_cache)
        
        return final_contents

# ==============================================================================
# SECTION 6 : ADAPTER STANDARD
# ==============================================================================
class GeminiAdapter:
    def __init__(self, base_url):
        self.base_url = base_url

    def build(self, project_id, contents, system_instr, temp, max_tok, think_level, model_id, tools=None):
        gen_config = {"temperature": temp, "maxOutputTokens": max_tok}
        gen_config["thinkingConfig"] = {"includeThoughts": True, "thinkingLevel": think_level.lower()}
        payload = {
            "model": model_id, "project": project_id,
            "request": {"systemInstruction": system_instr, "contents": contents, "generationConfig": gen_config}
        }
        if tools: payload["request"]["tools"] = tools; payload["request"]["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        return {"url": f"{self.base_url}:streamGenerateContent?alt=sse", "headers": {"Content-Type": "application/json", "User-Agent": "GeminiCLI"}, "json": payload}

# ==============================================================================
# SECTION 7 : STREAM PROCESSOR
# ==============================================================================
class StreamProcessor:
    def __init__(self, context_window: int, user_data_manager: UserDataManager, debug=False, chat_id=None, initial_label="Réponse", logger=None):
        self.debug = debug
        self.chat_id = chat_id
        self.user_data_manager = user_data_manager
        self.context_window = context_window
        self.initial_label = initial_label
        self.usage_stats = None
        self.current_sig = None
        self.logger = logger
        self.pending_tool_calls = {}
        self.full_response_accumulator = []
        self.response_id = None

    def _update_stats(self, data):
        if "response" in data and "usageMetadata" in data["response"]:
            self.usage_stats = data["response"]["usageMetadata"]
        elif "usageMetadata" in data:
            self.usage_stats = data["usageMetadata"]
        if not self.response_id:
            if "responseId" in data: self.response_id = data["responseId"]
            elif "response" in data and "id" in data["response"]: self.response_id = data["response"]["id"]
        if self.usage_stats:
            self.user_data_manager.save_context_stats(self.usage_stats)

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False
        tool_index = 0
        ct = response.headers.get("content-type", "")
        if "application/json" in ct:
            yield f"⚠️ API Error: {await response.aread()}"
            return

        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        async for chunk in response.aiter_bytes():
            try:
                buffer = decoder.decode(chunk, final=False)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line: continue
                    if line.startswith("data:"):
                        data = orjson.loads(line[6:])
                        self._update_stats(data)
                        self.full_response_accumulator.append(data)
                        cand = data.get("candidates", []) or data.get("response", {}).get("candidates", [])
                        if cand and cand[0].get("content"):
                            for part in cand[0]["content"]["parts"]:
                                txt = part.get("text", "")
                                func_call = part.get("functionCall")
                                if "thoughtSignature" in part:
                                    self.current_sig = part["thoughtSignature"]
                                    if self.chat_id: self.user_data_manager.save_signature(self.chat_id, self.current_sig)
                                if part.get("thought"):
                                    if not in_think: yield "<think>\n"; in_think = True
                                    yield txt
                                elif func_call:
                                    if in_think: yield "\n</think>\n"; in_think = False
                                    tc_id = f"call_{secrets.token_hex(8)}"
                                    if self.current_sig and self.chat_id: self.user_data_manager.save_signature(self.chat_id, self.current_sig, tc_id)
                                    args = func_call.get("args", {})
                                    if self.current_sig: args["_thought_signature"] = self.current_sig
                                    yield {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": tool_index, "id": tc_id, "type": "function", "function": {"name": func_call["name"], "arguments": orjson.dumps(args).decode()}}]}}]}
                                    tool_index += 1
                                elif "text" in part:
                                    if in_think: yield "\n</think>\n"; in_think = False
                                    yield txt
            except: pass
        
        remaining = decoder.decode(b"", final=True)
        if remaining.strip().startswith("data:"):
             try: self._update_stats(orjson.loads(remaining.strip()[6:]))
             except: pass

        if in_think: yield "\n</think>\n"
        if self.logger: self.logger.log("api_response", self.full_response_accumulator, metadata={"response_id": self.response_id})
        if self.usage_stats:
            yield {"usage": {"prompt_tokens": self.usage_stats.get("promptTokenCount", 0), "completion_tokens": self.usage_stats.get("candidatesTokenCount", 0), "total_tokens": self.usage_stats.get("totalTokenCount", 0)}}

# ==============================================================================
# SECTION 8 : LE PIPE
# ==============================================================================
class Pipe:
    class Valves(BaseModel):
        API_RETRY_COUNT: int = Field(default=3, description="🔄 Nombre d'essais API")
        HTTP_CLIENT_TIMEOUT: int = Field(default=300, description="⏱️ Autokill Client HTTP (sec)")
        ENABLE_HTTP2: bool = Field(default=True, description="🚀 Activer HTTP/2")
        ENABLE_UPSTREAM_GZIP: bool = Field(default=True, description="📦 Activer Compression GZIP")
        GZIP_LEVEL: int = Field(default=1, description="🎚️ Niveau GZIP (1-9)")
        GZIP_THRESHOLD_KB: int = Field(default=10240, description="🚫 Désactiver GZIP si > Ko")
        DEBUG_MODE: bool = Field(default=False, description="🐞 DEBUG MODE (Docker Logs Only)")
        MAX_CONTEXT_SIZE: int = Field(default=1048576, description="📚 Taille Contexte Max")

    class UserValves(BaseModel):
        MODEL_SELECTION: Literal["gemini-3-pro-preview", "gemini-3-flash-preview"] = Field(default="gemini-3-pro-preview", description="Modèle")
        PRO_THINKING_LEVEL: Literal["LOW", "HIGH"] = Field(default="HIGH", description="Niveau de réflexion (Pro)")
        FLASH_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH", description="Niveau de réflexion (Flash)")
        TEMPERATURE: float = Field(default=1.0, description="Température")
        MAX_TOKENS: int = Field(default=65536, description="Max Tokens")

    def __init__(self):
        self.valves = self.Valves()
        self.data_dir = "/app/backend/data"

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __request__: Optional[any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        if not __user__ or "id" not in __user__:
             yield "❌ **Erreur Critique** : Impossible d'identifier l'utilisateur."; return

        user_id = __user__["id"]
        user_valves = __user__.get("valves") or self.UserValves()

        try:
            orch = Orchestrator(self.valves, user_valves, self.data_dir, user_id)
            auth = AuthService(orch.user_data_manager)
        except Exception as e:
            print(f"[PIPE CRITICAL] Init Error: {e}", flush=True)
            yield f"❌ **Erreur Critique Initialisation** : {e}"; return

        chat_id = body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        
        ac = orch.check_for_auth_code(body.get("messages", []))
        if ac:
            success, msg = auth.exchange_code(ac)
            yield f"✅ **{msg}**" if success else f"❌ **Échec** : `{msg}`"; return
        
        creds = auth.get_valid_credentials()
        if not creds: yield auth.get_auth_url(); return
        pid, debug_log = auth.get_project_id(creds, self.valves.DEBUG_MODE)
        
        if not pid: yield f"❌ **Erreur Projet**\n{debug_log}"; return

        tools = orch.convert_owui_tools(body.get("tools"))
        
        system_messages = [m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"]
        client_context = "\n".join(system_messages) if system_messages else None
        
        context = await orch.prepare_context(body, chat_id)
        system_instruction = {"parts": [{"text": client_context or "Tu es un assistant IA expert."}]}

        selected_thinking_level = user_valves.PRO_THINKING_LEVEL if user_valves.MODEL_SELECTION == "gemini-3-pro-preview" else user_valves.FLASH_THINKING_LEVEL

        adapter = GeminiAdapter(auth.base_url)
        req = adapter.build(
            pid, context, system_instruction,
            user_valves.TEMPERATURE, user_valves.MAX_TOKENS, 
            selected_thinking_level, user_valves.MODEL_SELECTION, tools
        )
        req["headers"]["Authorization"] = f"Bearer {creds.token}"

        proc = StreamProcessor(
            self.valves.MAX_CONTEXT_SIZE,
            orch.user_data_manager,
            self.valves.DEBUG_MODE, 
            chat_id,
            logger=orch.logger
        )

        try:
            client = await _get_global_client(self.valves.HTTP_CLIENT_TIMEOUT, self.valves.ENABLE_HTTP2)
            req_content = orjson.dumps(req["json"])
            
            if self.valves.ENABLE_UPSTREAM_GZIP and len(req_content) < (self.valves.GZIP_THRESHOLD_KB * 1024):
                req_content = gzip.compress(req_content, compresslevel=self.valves.GZIP_LEVEL)
                req["headers"]["Content-Encoding"] = "gzip"
            
            for attempt in range(self.valves.API_RETRY_COUNT):
                try:
                    async with client.stream("POST", req["url"], content=req_content, headers=req["headers"]) as r:
                        if r.status_code == 200:
                            async for token in proc.process(r): yield token
                            break
                        if r.status_code in [429, 500, 502, 503, 504]:
                             await asyncio.sleep(1 * (attempt + 1)); continue
                        err_text = (await r.aread()).decode(errors='ignore')
                        yield f"⚠️ **API ERROR {r.status_code}**\n`{err_text}`"; return
                except Exception as e:
                    if attempt == self.valves.API_RETRY_COUNT - 1: raise e 
                    await asyncio.sleep(1)
        except Exception as e: yield f"🔥 **CRASH** : `{str(e)}`"