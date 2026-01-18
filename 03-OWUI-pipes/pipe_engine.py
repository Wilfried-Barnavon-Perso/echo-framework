"""
title: Gemini Pro Unified System (Platinum Agentic 136.26 - Turbo No Upload)
author: Wilfried BARNAVON
version: 136.26
description: 136.26: Fix "Stuttering" (Repetition) by merging consecutive model messages (Text + ToolCall) into single API turns.
"""

# ==============================================================================
# SECTION 0 : IMPORTATIONS & CONSTANTES GLOBALES
# ==============================================================================
import os
import sys
import secrets
import hashlib
import random
import re
import time
import uuid
import base64
import mimetypes
import glob
import codecs
import asyncio
import json as std_json 
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# --- IMPORTATIONS TIERCES CRITIQUES ---
# Vérification stricte des modules requis pour le fonctionnement du Pipe
try:
    import httpx
    import orjson
    import pybase64
    from pydantic import BaseModel, Field
except ImportError as e:
    missing_module = e.name or "inconnu"
    raise ImportError(
        f"❌ Module critique manquant : '{missing_module}'. "
        f"Ce module est requis pour le fonctionnement du script Gemini Pro Unified v136.21+. "
        f"Veuillez l'installer dans l'environnement Python."
    ) from e

# --- OPTIMISATION COMPRESSION (DROP-IN REPLACEMENT) ---
# Tente d'utiliser mgzip (Multithread) sinon fallback sur gzip standard
try:
    import mgzip as gzip
except ImportError:
    import gzip

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

# --- GESTIONNAIRE DE CONNEXION PARTAGÉ (CONNECTION POOLING) ---
_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(idle_timeout: int = 300, enable_http2: bool = True) -> httpx.AsyncClient:
    """Récupère ou crée un client HTTP asynchrone partagé pour le Connection Pooling."""
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
    
    now = time.time()
    
    # Sécurisation Event Loop (v136.23)
    # Si la loop qui a créé le client est fermée (cas du Hot Reload), on doit recréer le client.
    try:
        if _SHARED_ASYNC_CLIENT and not _SHARED_ASYNC_CLIENT.is_closed:
            # Vérification bas niveau de la loop attachée au transport
            if hasattr(_SHARED_ASYNC_CLIENT, "_transport") and hasattr(_SHARED_ASYNC_CLIENT._transport, "_pool"):
                 client_loop = getattr(_SHARED_ASYNC_CLIENT._transport._pool, "_loop", None)
                 if client_loop and client_loop != asyncio.get_running_loop():
                     await _SHARED_ASYNC_CLIENT.aclose()
                     _SHARED_ASYNC_CLIENT = None
    except:
        _SHARED_ASYNC_CLIENT = None

    # Autokill (Nettoyage) si inactif depuis trop longtemps
    if _SHARED_ASYNC_CLIENT and (now - _LAST_CLIENT_ACCESS > idle_timeout):
        old_client = _SHARED_ASYNC_CLIENT
        _SHARED_ASYNC_CLIENT = None # Détachement immédiat pour éviter les race conditions
        try:
            await old_client.aclose()
        except: pass

    if _SHARED_ASYNC_CLIENT is None or _SHARED_ASYNC_CLIENT.is_closed:
        # Configuration optimisée pour le streaming et la réutilisation
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=300)
        
        try:
            # Tentative d'initialisation avec HTTP/2 si demandé
            _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=enable_http2)
        except ImportError:
            # Fallback de sécurité si la librairie 'h2' est absente malgré la config
            # Cela garantit la non-régression.
            print("⚠️ Module 'h2' manquant pour HTTP/2. Fallback sur HTTP/1.1.")
            _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=False)
        except Exception:
            # Autre erreur imprévue lors de l'init
            _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=False)
    
    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

# --- FONCTION UTILITAIRE BASE64 RAPIDE ---
def fast_b64encode(data: bytes) -> str:
    """Encode des bytes en base64 string via pybase64 (SIMD)."""
    return pybase64.b64encode(data).decode("utf-8")

# --- CONSTANTES MAGIQUES ---
MAGIC_KEY_SKIP_VALIDATION = "skip_thought_signature_validator"

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

try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False

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
# SECTION 3 : SERVICE D'AUTHENTIFICATION
# ==============================================================================
class AuthService:
    def __init__(self, data_dir: str):
        self.token_path = f"{data_dir}/gemini_official_token.json"
        self.pkce_path = f"{data_dir}/gemini_pkce_verifier.txt"
        self.internal_project_cache = f"{data_dir}/gemini_internal_project.txt"
        self.base_url = GOOGLE_API_BASE_URL

    def _generate_pkce(self):
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        import base64
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        if not HAS_GOOGLE_LIBS: return "❌ **Erreur** : Librairies `google-auth` manquantes."
        should_generate_new = True
        if os.path.exists(self.pkce_path):
            try:
                if time.time() - os.path.getmtime(self.pkce_path) < 300:
                    with open(self.pkce_path, "r") as f:
                        if len(f.read().strip()) > 10: should_generate_new = False
            except: pass

        if should_generate_new:
            verifier, challenge = self._generate_pkce()
            try:
                with open(self.pkce_path, "w") as f: f.write(verifier)
            except Exception as e: return f"❌ Erreur IO: {str(e)}"
        else:
            with open(self.pkce_path, "r") as f: verifier = f.read().strip()
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

        flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False)
        flow.redirect_uri = GOOGLE_REDIRECT_URI
        url, _ = flow.authorization_url(prompt="consent", access_type="offline", code_challenge=challenge, code_challenge_method="S256")
        return f"### 🔐 Authentification Requise\n\n1. **[Cliquez ici]({url})**\n2. Connectez-vous.\n3. Copiez le code `4/...`.\n4. **Collez-le ici**."

    def exchange_code(self, code: str) -> Tuple[bool, str]:
        if not HAS_GOOGLE_LIBS: return False, "Libs manquantes."
        
        if not os.path.exists(self.pkce_path):
             for _ in range(3):
                if self.get_valid_credentials(): return True, "Succès (Récupéré via cache)."
                time.sleep(0.5)
             return False, "Session expirée (PKCE introuvable)."

        try:
            with open(self.pkce_path, "r") as f: verifier = f.read().strip()
            flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False)
            flow.redirect_uri = GOOGLE_REDIRECT_URI
            flow.fetch_token(code=code.strip(), code_verifier=verifier)
            with open(self.token_path, "w") as f: f.write(flow.credentials.to_json())
            if os.path.exists(self.pkce_path): os.remove(self.pkce_path)
            return True, "Succès."
        except Exception as e: return False, str(e)

    def get_valid_credentials(self):
        creds = None
        if os.path.exists(self.token_path):
            try: creds = Credentials.from_authorized_user_file(self.token_path, GOOGLE_SCOPES)
            except: pass
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                with open(self.token_path, "w") as f: f.write(creds.to_json())
            except: return None
        return creds if (creds and creds.valid) else None

    def get_project_id(self, creds, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        cached_pid = None
        if os.path.exists(self.internal_project_cache):
             with open(self.internal_project_cache, "r") as f: cached_pid = f.read().strip()

        if cached_pid and not debug_mode:
            return cached_pid, "Cache."
            
        headers = {
            "Authorization": f"Bearer {creds.token}", 
            "Content-Type": "application/json",
            "User-Agent": "GeminiCLI/0.24.0" 
        }
        payload = {"metadata": {"ideType": "IDE_UNSPECIFIED", "pluginType": "GEMINI"}}
        
        try:
            resp = httpx.post(f"{self.base_url}:loadCodeAssist", headers=headers, json=payload, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("cloudaicompanionProject")
                pid = raw.get("id") if isinstance(raw, dict) else raw
                
                if pid:
                    pid = pid.replace("projects/", "")
                    with open(self.internal_project_cache, "w") as f: f.write(pid)
                    return pid, "API OK."
                else:
                    if cached_pid:
                         return cached_pid, f"API Fail (Partial Response), Fallback to Cache. JSON: {str(data)[:50]}"
                    try: error_dump = std_json.dumps(data, indent=2)
                    except: error_dump = str(data)
                    return None, f"**JSON inattendu** (Project ID introuvable) :\n```json\n{error_dump}\n```"
            else:
                return None, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e: return None, str(e)

    def reset_storage(self):
        for p in [self.token_path, self.pkce_path, self.internal_project_cache]:
            if os.path.exists(p): os.remove(p)

# ==============================================================================
# SECTION 4 : REGISTRE CAS & SIGNATURES
# ==============================================================================
class SignatureManager:
    def __init__(self, data_dir: str):
        self.sig_dir = os.path.join(data_dir, "signatures")
        os.makedirs(self.sig_dir, exist_ok=True)

    def save_signature(self, chat_id: str, signature: str):
        if not chat_id or not signature: return
        try:
            path = os.path.join(self.sig_dir, f"{chat_id}.txt")
            with open(path, "w") as f: f.write(signature)
        except Exception as e: pass

    def get_signature(self, chat_id: str) -> Optional[str]:
        if not chat_id: return None
        try:
            path = os.path.join(self.sig_dir, f"{chat_id}.txt")
            if os.path.exists(path):
                os.utime(path, None)
                with open(path, "r") as f: return f.read().strip()
        except: pass
        return None

# ==============================================================================
# SECTION 5 : ORCHESTRATEUR (LOGIQUE MÉTIER)
# ==============================================================================
class Orchestrator:
    def __init__(self, valves, data_dir):
        self.valves = valves
        self.data_dir = data_dir
        self.uploads_dir = "/app/backend/data/uploads" 
        self.tool_map = {}
        self.sig_manager = SignatureManager(data_dir)
        self.debug_log = []
        self.files_processed_info = []

    def check_for_auth_code(self, messages: List[Dict]) -> Optional[str]:
        if not messages: return None
        last_msg = messages[-1].get("content", "")
        if isinstance(last_msg, list): return None
        match = re.search(r"(4/[a-zA-Z0-9_-]+)", str(last_msg).strip())
        return match.group(1) if match and len(match.group(1)) > 30 else None

    def _get_geo_info(self) -> Tuple[str, str]:
        loc, tz = "Paris, France", "Europe/Paris"
        return loc, tz

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        if not tools: return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                funcs.append({"name": f.get("name"), "description": f.get("description", ""), "parameters": f.get("parameters", {"type": "object", "properties": {}})})
        return [{"functionDeclarations": funcs}] if funcs else None

    def get_system_instruction(self) -> Dict:
        sys_prompt_text = self.valves.SYSTEM_PROMPT
        if getattr(self.valves, "ENABLE_DATE_TIME", True):
            loc, tz = self._get_geo_info()
            try: now = datetime.now(ZoneInfo(tz)) if HAS_ZONEINFO else datetime.now()
            except: now = datetime.now()
            sys_prompt_text += f"\n\n[CONTEXT]\nDate: {now.strftime('%A %d %B %Y')}\nTime: {now.strftime('%H:%M')}\nLocation: {loc}\n"
        return {"parts": [{"text": sys_prompt_text}]}
    
    def _probe_disk(self) -> str:
        try:
            files = os.listdir(self.uploads_dir)
            return f"✅ Dir exists. {len(files)} files."
        except Exception as e: return f"❌ Error: {str(e)}"

    def _resolve_local_path(self, provided_path: str, f_id: str, f_name: str) -> Optional[str]:
        if provided_path and os.path.exists(provided_path):
            self.debug_log.append(f"✅ Direct: {provided_path}")
            return provided_path
        
        candidates = []
        if f_name:
            clean_name = f_name.replace("/", "_").replace("\\", "_")
            candidates.append(os.path.join(self.uploads_dir, f"{f_id}_{clean_name}"))
            candidates.append(os.path.join(self.uploads_dir, clean_name))
            
        matches = glob.glob(os.path.join(self.uploads_dir, f"{f_id}_*"))
        candidates.extend(matches)

        for cand in candidates:
            if os.path.exists(cand):
                return cand

        try:
            files_in_dir = os.listdir(self.uploads_dir)
            self.debug_log.append(f"❌ File Not Found. Looking for ID: {f_id}, Name: {f_name}")
            self.debug_log.append(f"📂 Content of {self.uploads_dir} ({len(files_in_dir)} files): {str(files_in_dir)[:300]}...")
        except Exception as e:
            self.debug_log.append(f"❌ Cannot list {self.uploads_dir}: {str(e)}")
            
        return None

    def _parse_mime_valves(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        txt_map = {}
        bin_map = {}
        try:
            raw_txt = std_json.loads(self.valves.GEMINI_MIME_MAPPING_TXT)
            for mime, exts in raw_txt.items():
                for ext in exts:
                    txt_map[ext.lower().strip()] = mime
        except Exception as e:
            if self.valves.DEBUG_MODE: self.debug_log.append(f"⚠️ Erreur JSON TXT: {e}")

        try:
            raw_bin = std_json.loads(self.valves.GEMINI_MIME_MAPPING_BIN)
            for mime, exts in raw_bin.items():
                for ext in exts:
                    bin_map[ext.lower().strip()] = mime
        except Exception as e:
            if self.valves.DEBUG_MODE: self.debug_log.append(f"⚠️ Erreur JSON BIN: {e}")
            
        return txt_map, bin_map

    def _get_file_info(self, f_id: str, f_name: str, owui_path: str, txt_map: Dict, bin_map: Dict) -> Tuple[str, bool, str, Optional[str]]:
        if not f_id: return "", False, "No ID", None
        real_path = self._resolve_local_path(owui_path, f_id, f_name)
        if not real_path: return "", False, f"Not Found: {f_id}", None

        ext = os.path.splitext(real_path)[1].lower()
        
        if ext in txt_map:
            return txt_map[ext], True, "", real_path
            
        if ext in bin_map:
            return bin_map[ext], False, "", real_path

        mime_type, _ = mimetypes.guess_type(real_path)
        is_text = False
        if mime_type:
            if mime_type.startswith("text/") or mime_type in ["application/json", "application/javascript", "application/xml"]:
                is_text = True
        else:
            mime_type = "application/octet-stream"

        return mime_type, is_text, "", real_path

    def _process_single_file_sync(self, f_obj: Dict, txt_map: Dict, bin_map: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Worker synchrone pour traiter un seul fichier : I/O Disque + Encodage CPU."""
        f_real = f_obj.get("file", f_obj)
        f_id = f_real.get("id")
        f_name = f_real.get("filename") or f_real.get("meta", {}).get("name")
        f_path = f_real.get("path")

        mime, is_text, err, real_path = self._get_file_info(f_id, f_name, f_path, txt_map, bin_map)

        if err:
            if self.valves.DEBUG_MODE: self.debug_log.append(f"⚠️ {f_name}: {err}")
            return None, None
        
        file_size = os.path.getsize(real_path)
        info_entry = None
        part = None

        if is_text:
            # v136.23: Fallback Encoding (UTF-8 -> Latin-1)
            content_str = None
            try:
                with open(real_path, "r", encoding="utf-8") as f:
                    content_str = f.read()
            except UnicodeDecodeError:
                # Fallback de sécurité (ne plante jamais sur les bytes)
                try:
                    with open(real_path, "r", encoding="latin-1") as f:
                        content_str = f.read()
                except: pass
            except Exception: pass
            
            if content_str is not None:
                part = {"text": f"--- FILE: {f_name} ---\n{content_str}\n--- END FILE ---\n"}
                info_entry = {"name": f_name, "type": "Text (Inline)", "size": file_size, "status": "Embedded 📄"}
        else:
            try:
                with open(real_path, "rb") as f:
                    raw_data = f.read()
                    # Utilisation native pybase64
                    b64_data = fast_b64encode(raw_data)
                
                part = {"inlineData": {"mimeType": mime, "data": b64_data}}
                info_entry = {"name": f_name, "type": f"{mime} (Base64)", "size": file_size, "status": "Embedded 🖼️"}
                
                if file_size > (self.valves.MAX_INLINE_SIZE_KB * 1024):
                    if self.valves.DEBUG_MODE: self.debug_log.append(f"⚠️ File {f_name} large ({file_size/1024:.0f}KB) but sent as Base64 (No Upload Mode).")

            except Exception as e:
                if self.valves.DEBUG_MODE: self.debug_log.append(f"⚠️ Base64 Error {f_name}: {str(e)}")
                part = {"text": f"[Error processing binary file {f_name}: {str(e)}]"}

        return part, info_entry

    async def _process_files_for_message(self, files_raw: List[Dict]) -> List[Dict]:
        parts = []
        files_to_process = []
        seen_ids = set()

        txt_map, bin_map = self._parse_mime_valves()
        
        # 1. Deduplication et préparation
        for f in files_raw:
            fid = f.get("id") or f.get("file", {}).get("id")
            if fid and fid not in seen_ids:
                files_to_process.append(f); seen_ids.add(fid)

        # 2. Lancement parallèle (Threads pour I/O + CPU)
        tasks = []
        for f_obj in files_to_process:
            # On déporte le travail lourd dans un thread séparé
            tasks.append(asyncio.to_thread(self._process_single_file_sync, f_obj, txt_map, bin_map))
        
        # 3. Attente non-bloquante de tous les fichiers
        if tasks:
            results = await asyncio.gather(*tasks)
            
            # 4. Assemblage ordonné des résultats
            for part, info in results:
                if part:
                    parts.append(part)
                if info:
                    self.files_processed_info.append(info)
        
        return parts

    async def prepare_context(self, body: Dict, chat_id: str, auth_token: str, extra_files: Any = None) -> List[Dict]:
        self.files_processed_info = []
        messages = body.get("messages", [])
        contents = []

        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if "id" in tc and "function" in tc:
                        self.tool_map[tc["id"]] = tc["function"].get("name")

        last_user_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx]["role"] == "user": last_user_idx = idx; break

        i = 0
        while i < len(messages):
            m = messages[i]
            role = m["role"]
            if role == "system": i+=1; continue

            if role == "tool":
                parts = []
                while i < len(messages) and messages[i]["role"] == "tool":
                    tm = messages[i]
                    tool_name = self.tool_map.get(tm.get("tool_call_id"), "unknown_tool")
                    try: val = std_json.loads(tm.get("content", "{}"))
                    except: val = {"result": str(tm.get("content", ""))}
                    parts.append({"functionResponse": {"name": tool_name, "response": val}})
                    i += 1
                
                if contents and contents[-1]["role"] == "user":
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": "user", "parts": parts})
                continue

            elif role in ["assistant", "model"]:
                # v136.26: FIX STUTTERING/REPETITION
                # Merge consecutive model messages (Text + FunctionCall) into a single API turn.
                parts = []
                found_in_band_sig = None
                
                # Consolidate consecutive model messages
                while i < len(messages) and messages[i]["role"] in ["assistant", "model"]:
                    sub_m = messages[i]
                    
                    # 1. Text Content
                    txt = sub_m.get("content", "")
                    if isinstance(txt, list): txt = "".join([x.get("text","") for x in txt if "text" in x])
                    
                    # Clean tags
                    txt = re.sub(r'<think>.*?</think>', '', str(txt), flags=re.DOTALL).strip()
                    txt = re.sub(r'<details>.*?</details>', '', txt, flags=re.DOTALL).strip()
                    if txt: parts.append({"text": txt})

                    # 2. Tool Calls
                    if sub_m.get("tool_calls"):
                        for tc in sub_m["tool_calls"]:
                            try:
                                args = std_json.loads(tc["function"]["arguments"])
                                # Extract signature if present
                                if "_thought_signature" in args: 
                                    found_in_band_sig = args.pop("_thought_signature")
                                parts.append({"functionCall": {"name": tc["function"]["name"], "args": args}})
                            except: pass
                    
                    i += 1
                
                # Fallback text if empty
                if not parts: parts.append({"text": " "})

                # Signature Logic (Applied to the merged block)
                tool_calls_in_msg = any("functionCall" in p for p in parts)
                
                # Check if this merged block is the LAST model turn in the history
                # (i is already at the next message or end)
                is_last_model_msg = True
                for j in range(i, len(messages)):
                    if messages[j]["role"] in ["assistant", "model"]: 
                        is_last_model_msg = False
                        break

                if tool_calls_in_msg or is_last_model_msg:
                    sig_to_use = found_in_band_sig
                    if not sig_to_use and chat_id: sig_to_use = self.sig_manager.get_signature(chat_id)
                    if not sig_to_use and tool_calls_in_msg: sig_to_use = MAGIC_KEY_SKIP_VALIDATION
                    
                    if sig_to_use and parts:
                         # v136.24 CRITICAL FIX: Signature Pollution Prevention.
                         if tool_calls_in_msg:
                             found_first_fc = False
                             for part in parts:
                                 if "functionCall" in part:
                                     if not found_first_fc:
                                         part["thoughtSignature"] = sig_to_use
                                         found_first_fc = True
                                     # Do NOT add signature to subsequent parallel function calls
                         else:
                             # Text only response: Add to the last part (usually text)
                             parts[-1]["thoughtSignature"] = sig_to_use
                
                contents.append({"role": "model", "parts": parts})
                continue # Continue outer loop (i was incremented in inner loop)
            
            else: # USER
                parts = []
                raw_list = []
                if "files" in m and isinstance(m["files"], list): raw_list.extend(m["files"])
                
                if i == last_user_idx:
                    if self.valves.DEBUG_MODE:
                        raw_filter = body.get("raw_files_from_filter")
                        if raw_filter:
                             try: dump = std_json.dumps(raw_filter, indent=2, default=str)
                             except: dump = str(raw_filter)
                             self.debug_log.append(f"📦 Filter Files: {dump[:200]}...")

                    raw_from_filter = body.get("raw_files_from_filter")
                    if raw_from_filter:
                        raw_list.extend(raw_from_filter)

                    if extra_files: 
                        ex = extra_files if isinstance(extra_files, list) else [extra_files]
                        raw_list.extend(ex)

                file_parts = await self._process_files_for_message(raw_list)
                parts.extend(file_parts)
                
                # REVERT v136.25: Suppression de la déduplication d'images sur demande utilisateur.
                # On traite systématiquement les images provenant du contenu (historique)
                # même si des fichiers ont déjà été traités.

                content_txt = m.get("content", "")
                if isinstance(content_txt, str) and content_txt.strip():
                    parts.append({"text": content_txt})
                
                elif isinstance(content_txt, list):
                    for item in content_txt:
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
                
                if parts: contents.append({"role": "user", "parts": parts})
            
            i += 1
        return contents

    def estimate_tokens(self, contents: List[Dict]) -> int:
        total = 0
        for item in contents:
            for part in item.get("parts", []):
                if "text" in part: 
                    total += len(part["text"]) // 4
                elif "inlineData" in part:
                    # Estimation pour Gemini 3 (High Res / Default) = ~1120 tokens
                    total += 1120
        return total

# ==============================================================================
# SECTION 6 : ADAPTER STANDARD
# ==============================================================================
class GeminiAdapter:
    def __init__(self, base_url):
        self.base_url = base_url

    def build(self, project_id, contents, system_instr, temp, max_tok, think_level, model_id, tools=None):
        gen_config = {"temperature": temp, "maxOutputTokens": max_tok}
        if "gemini-3" in model_id:
            t_level = think_level.lower()
            if t_level == "dynamic": t_level = "high"
            gen_config["thinkingConfig"] = {"includeThoughts": True, "thinkingLevel": t_level}
        
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
    def __init__(self, context_window: int, debug=False, chat_id=None, sig_manager=None, show_metrics=False, initial_label="Réponse", file_stats=None):
        self.debug = debug
        self.chat_id = chat_id
        self.sig_manager = sig_manager
        self.show_metrics = show_metrics
        self.context_window = context_window
        self.initial_label = initial_label
        self.usage_stats = None
        self.file_stats = file_stats or []
        self.current_sig = None
        self.stats_dir = "/app/backend/data/stats"
        os.makedirs(self.stats_dir, exist_ok=True)

    def _update_stats(self, data):
        if "response" in data and "usageMetadata" in data["response"]:
            self.usage_stats = data["response"]["usageMetadata"]
        elif "usageMetadata" in data:
            self.usage_stats = data["usageMetadata"]
        
        if self.usage_stats and self.chat_id:
             try:
                safe_id = "".join(x for x in str(self.chat_id) if x.isalnum() or x in "-_")
                with open(f"{self.stats_dir}/{safe_id}.json", "w") as f:
                    std_json.dump(self.usage_stats, f)
             except: pass

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False
        tool_index = 0
        step_label = self.initial_label
        ct = response.headers.get("content-type", "")
        if "application/json" in ct:
            yield f"⚠️ API Error: {await response.aread()}"
            return

        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        buffer = ""
        
        async for chunk in response.aiter_bytes():
            try:
                buffer += decoder.decode(chunk, final=False)
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line: continue
                    if line.startswith("data:"):
                        data = std_json.loads(line[6:])
                        
                        self._update_stats(data)

                        cand = data.get("candidates", []) or data.get("response", {}).get("candidates", [])
                        if cand and cand[0].get("content"):
                            for part in cand[0]["content"]["parts"]:
                                txt = part.get("text", "")
                                func_call = part.get("functionCall")

                                if "thoughtSignature" in part:
                                    self.current_sig = part["thoughtSignature"]
                                    if self.chat_id and self.sig_manager:
                                        self.sig_manager.save_signature(self.chat_id, self.current_sig)

                                if part.get("thought"):
                                    if not in_think: yield "<think>\n"; in_think = True
                                    yield txt
                                
                                elif func_call:
                                    step_label = f"Pré-{func_call.get('name', 'Action')}"
                                    if in_think: yield "\n</think>\n"; in_think = False
                                    
                                    args = func_call.get("args", {})
                                    if self.current_sig: args["_thought_signature"] = self.current_sig
                                    
                                    yield {
                                        "choices": [{
                                            "index": 0, "delta": {
                                                "tool_calls": [{
                                                    "index": tool_index, 
                                                    "id": f"call_{secrets.token_hex(8)}",
                                                    "type": "function", 
                                                    "function": {"name": func_call["name"], "arguments": std_json.dumps(args)}
                                                }]
                                            }, "finish_reason": "tool_calls"
                                        }]
                                    }
                                    tool_index += 1

                                elif "text" in part:
                                    if in_think: yield "\n</think>\n"; in_think = False
                                    yield txt
            except: pass
        
        remaining = decoder.decode(b"", final=True)
        buffer += remaining
        
        if buffer and buffer.strip().startswith("data:"):
            try:
                line = buffer.strip()
                data = std_json.loads(line[6:])
                self._update_stats(data) 
            except: pass

        if in_think: yield "\n</think>\n"

        if self.show_metrics:
            stats_content = "\n\n" 
            has_content = False

            if self.file_stats and self.debug:
                stats_content += "**📁 Fichiers Traités**\n\n"
                stats_content += "| Fichier | Type | Taille | Statut |\n| :--- | :--- | :--- | :--- |\n"
                for f in self.file_stats:
                    size_mb = f['size'] / (1024*1024)
                    stats_content += f"| {f['name']} | {f['type']} | {size_mb:.2f} MB | {f['status']} |\n"
                stats_content += "\n"
                has_content = True
            
            if self.usage_stats:
                p_tok = self.usage_stats.get("promptTokenCount", 0)
                c_tok = self.usage_stats.get("candidatesTokenCount", 0)
                t_tok = self.usage_stats.get("totalTokenCount", 0)
                # Cache supprimé : on ne l'affiche plus dans les stats
                pct = (t_tok / self.context_window) * 100
                bar = "█" * int(pct/10) + "░" * (10 - int(pct/10))
                
                stats_content += f"""<details>
<summary>⚡ Contexte [{step_label}]: {pct:.1f}% {bar}</summary>

| Métrique | Valeur |
| :--- | :--- |
| **Prompt** | {p_tok:,} |
| **Réponse** | {c_tok:,} |
| **Total** | {t_tok:,} / {self.context_window:,} |
</details>\n"""
                has_content = True

            if has_content:
                yield stats_content

        if self.usage_stats:
            yield {
                "usage": {
                    "prompt_tokens": self.usage_stats.get("promptTokenCount", 0),
                    "completion_tokens": self.usage_stats.get("candidatesTokenCount", 0),
                    "total_tokens": self.usage_stats.get("totalTokenCount", 0)
                }
            }

# ==============================================================================
# SECTION 8 : LE PIPE (POINT D'ENTRÉE)
# ==============================================================================
class Pipe:
    class Valves(BaseModel):
        MODEL_SELECTION: Literal["gemini-3-pro-preview", "gemini-2.5-pro"] = Field(default="gemini-3-pro-preview", description="Modèle")
        SYSTEM_PROMPT: str = Field(default="Tu es un assistant expert.", description="Prompt Système")
        THINKING_LEVEL: Literal["DYNAMIC", "LOW", "HIGH"] = Field(default="DYNAMIC", description="Niveau de réflexion")
        TEMPERATURE: float = Field(default=1.0, description="Température")
        MAX_TOKENS: int = Field(default=65536, description="Max Tokens")
        MAX_CONTEXT_SIZE: int = Field(default=1048576, description="📚 Taille Contexte Max")

        GEMINI_MIME_MAPPING_TXT: str = Field(
            default='{"text/plain": [".bat",".c",".conf",".cpp",".cs",".css",".csv",".dockerfile",".editorconfig",".env",".gitignore",".go",".h",".hpp",".ini",".java",".js",".json",".kt",".lua",".md",".php",".pl",".ps1",".py",".r",".rb",".rs",".sh",".sql",".swift",".toml",".ts",".txt",".vb",".xml",".yaml",".yml","dockerfile"], "text/html": [".html", ".htm"]}',
            description="📄 Mapping Texte (JSON: Mime -> [Exts])"
        )
        
        GEMINI_MIME_MAPPING_BIN: str = Field(
            default='{"video/x-flv": [".flv"], "video/quicktime": [".mov"], "video/mpeg": [".mpeg", ".mpg", ".mpe"], "video/mpegps": [".mpegps"], "video/mp4": [".mp4"], "video/webm": [".webm"], "video/wmv": [".wmv"], "video/3gpp": [".3gpp"], "audio/aac": [".aac"], "audio/flac": [".flac"], "audio/mp3": [".mp3"], "audio/m4a": [".m4a", ".mpa"], "audio/mpga": [".mpga"], "audio/opus": [".opus"], "audio/pcm": [".pcm"], "audio/wav": [".wav"], "image/png": [".png"], "image/jpeg": [".jpeg", ".jpg"], "image/webp": [".webp"], "image/heic": [".heic"], "image/heif": [".heif"], "application/pdf": [".pdf"]}',
            description="🖼️ Mapping Binaire (JSON: Mime -> [Exts])"
        )
        
        SHOW_METRICS: bool = Field(default=True, description="📊 Afficher Métriques")
        API_RETRY_COUNT: int = Field(default=3, description="🔄 Nombre d'essais en cas d'erreur API")

        HTTP_CLIENT_TIMEOUT: int = Field(default=300, description="⏱️ Autokill Client HTTP (sec)")
        ENABLE_HTTP2: bool = Field(default=True, description="🚀 Activer HTTP/2 (Multiplexing)")
        
        ENABLE_UPSTREAM_GZIP: bool = Field(default=True, description="📦 Activer Compression GZIP Upstream")
        GZIP_LEVEL: int = Field(default=1, description="🎚️ Niveau Compression GZIP (1-9)")
        GZIP_THRESHOLD_KB: int = Field(default=10240, description="🚫 Désactiver GZIP si > Ko (Evite overhead binaire)")

        ENABLE_DATE_TIME: bool = Field(default=True, description="🕒 Injecter Temps")
        ENABLE_AUTO_LOCATION: bool = Field(default=True, description="📍 Injecter Lieu")
        OVERRIDE_LOCATION: str = Field(default="", description="✏️ Forcer Lieu")

        DEBUG_MODE: bool = Field(default=False, description="🐞 DEBUG MODE")
        MAX_INLINE_SIZE_KB: int = Field(default=10240, description="Seuil d'alerte taille (Ko)")

    def __init__(self):
        self.valves = self.Valves()
        self.data_dir = "/app/backend/data"
        os.makedirs(self.data_dir, exist_ok=True)
        self.auth = AuthService(self.data_dir)
        self.base_url = GOOGLE_API_BASE_URL

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __request__: Optional[any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        chat_id = body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, self.data_dir)
        
        ac = orch.check_for_auth_code(body.get("messages", []))
        if ac:
            success, msg = self.auth.exchange_code(ac)
            yield f"✅ **{msg}**" if success else f"❌ **Échec** : `{msg}`"; return
        
        creds = self.auth.get_valid_credentials()
        if not creds: yield self.auth.get_auth_url(); return
        pid, debug_log = self.auth.get_project_id(creds, self.valves.DEBUG_MODE)
        
        if not pid: 
             yield f"❌ **Erreur Projet**\n{debug_log}"; return

        tools = orch.convert_owui_tools(body.get("tools"))
        files = body.get("files") or kwargs.get("__files__")
        
        context = await orch.prepare_context(body, chat_id, creds.token, extra_files=files)

        if self.valves.DEBUG_MODE and orch.debug_log:
             for log in orch.debug_log: yield f"{log}\n"
        
        initial_label = "Réponse"
        if body.get("messages") and body.get("messages")[-1].get("role") == "tool":
            initial_label = "Post-Action"

        # --- ADAPTER STANDARD ---
        adapter = GeminiAdapter(self.base_url)
        req = adapter.build(pid, context, orch.get_system_instruction(), self.valves.TEMPERATURE, self.valves.MAX_TOKENS, self.valves.THINKING_LEVEL, self.valves.MODEL_SELECTION, tools)
        req["headers"]["Authorization"] = f"Bearer {creds.token}"

        if self.valves.DEBUG_MODE:
             # Utilisation native orjson (Bytes)
             log_req = orjson.loads(orjson.dumps(req['json']))

             contents = log_req.get("contents", [])
             if "request" in log_req: contents = log_req["request"].get("contents", [])
             
             for c in contents:
                 for p in c.get("parts", []):
                     if "inlineData" in p: 
                         len_b64 = len(p["inlineData"].get("data", ""))
                         p["inlineData"]["data"] = f"<BASE64_BLOB_LEN_{len_b64}>"
                     if "inline_data" in p: 
                         len_b64 = len(p["inline_data"].get("data", ""))
                         p["inline_data"]["data"] = f"<BASE64_BLOB_LEN_{len_b64}>"
            
             yield f"🐞 **API REQ** `[{req['url']}]`\n```json\n{std_json.dumps(log_req, indent=2)}\n```\n"
             yield "🚀 **Turbo JSON (orjson)** Active\n"

        proc = StreamProcessor(
            self.valves.MAX_CONTEXT_SIZE,
            self.valves.DEBUG_MODE, 
            chat_id, 
            sig_manager=orch.sig_manager,
            show_metrics=self.valves.SHOW_METRICS, 
            initial_label=initial_label,
            file_stats=orch.files_processed_info
        )

        try:
            # --- CONNECTION POOLING OPTIMIZATION ---
            # On récupère le client partagé au lieu d'en créer un nouveau
            client = await _get_global_client(self.valves.HTTP_CLIENT_TIMEOUT, self.valves.ENABLE_HTTP2)
            
            # --- TURBO OPTIMIZATION (Strict orjson) ---
            req_content = orjson.dumps(req["json"])
            
            # --- UPSTREAM GZIP COMPRESSION (SMART) ---
            if self.valves.ENABLE_UPSTREAM_GZIP:
                # Check size before compressing to avoid "Zip Bomb" limits or useless CPU usage on binaries
                if len(req_content) < (self.valves.GZIP_THRESHOLD_KB * 1024):
                    req_content = gzip.compress(req_content, compresslevel=self.valves.GZIP_LEVEL)
                    req["headers"]["Content-Encoding"] = "gzip"
                    if self.valves.DEBUG_MODE:
                        # Petite info pour savoir si on est en multi-thread
                        try:
                            import mgzip
                            is_mgzip = (gzip == mgzip)
                        except:
                            is_mgzip = False
                        
                        engine_name = "mgzip (Multi-threaded)" if is_mgzip else "gzip (Standard)"
                        yield f"📦 **GZIP Encoded** ({engine_name}, Level {self.valves.GZIP_LEVEL})\n"
                elif self.valves.DEBUG_MODE:
                     yield f"⏭️ **GZIP Skipped** (Size > {self.valves.GZIP_THRESHOLD_KB}KB)\n"

            # RETRY LOOP (Native, v136.23)
            # Utilisation de client.stream pour le pooling
            for attempt in range(self.valves.API_RETRY_COUNT):
                try:
                    async with client.stream("POST", req["url"], content=req_content, headers=req["headers"]) as r:
                        if r.status_code == 200:
                            # Succès, on stream et on sort de la boucle
                            async for token in proc.process(r): yield token
                            break
                        
                        # Gestion des erreurs "Retentables" (429, 5xx)
                        if r.status_code in [429, 500, 502, 503, 504]:
                             err = await r.aread()
                             err_text = err.decode(errors='ignore')
                             yield f"⚠️ Erreur {r.status_code} ({err_text[:100]}...), tentative {attempt+1}/{self.valves.API_RETRY_COUNT}...\n"
                             
                             # Backoff exponentiel simple (1s, 2s, 3s...)
                             await asyncio.sleep(1 * (attempt + 1))
                             continue
                        
                        # Erreur Fatale (400, 401, 403, 404...) -> On s'arrête
                        err = await r.aread()
                        err_text = err.decode(errors='ignore')
                        if self.valves.DEBUG_MODE:
                            yield f"🔥 **API CRASH {r.status_code}**\nURL: `{req['url']}`\nResponse:\n```json\n{err_text}\n```"
                        else:
                            yield f"⚠️ **API ERROR {r.status_code}**\n`{err_text}`"
                        return

                except Exception as e:
                    # Erreur réseau de bas niveau (ConnectionReset, Timeout...)
                    if attempt < self.valves.API_RETRY_COUNT - 1:
                         yield f"⚠️ Erreur Réseau: {str(e)}, tentative {attempt+1}/{self.valves.API_RETRY_COUNT}...\n"
                         await asyncio.sleep(1)
                    else:
                         raise e # On laisse planter si c'était la dernière chance

        except Exception as e: yield f"🔥 **CRASH** : `{str(e)}`"