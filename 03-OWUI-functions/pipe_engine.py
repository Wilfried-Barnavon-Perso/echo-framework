"""
title: Gemini Pro Unified System (Platinum Agentic V135.11 - Context Gauge Fix)
author: Wilfried BARNAVON
version: 135.11
description: v135.11: Rétablissement de la persistance des stats JSON sur disque (fix `context_gauge.py`).
"""

# ==============================================================================
# SECTION 0 : IMPORTATIONS & CONSTANTES GLOBALES
# ==============================================================================
import os
import json
import sys
import secrets
import hashlib
import random
import re
import time
import uuid
import httpx
import base64
import mimetypes
import glob
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# --- CONSTANTES DE CONFIGURATION GOOGLE ---
GOOGLE_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REDIRECT_URI = "https://codeassist.google.com/authcode"
GOOGLE_API_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal" # API Interne (Fallback/Chat)
GOOGLE_UPLOAD_BASE_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files" # API Publique (Upload fichiers)

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# --- REGISTRE DE CACHE GLOBAL ---
_LOCAL_CACHE_REGISTRY = {}

# --- CONSTANTES MAGIQUES ---
MAGIC_KEY_SKIP_VALIDATION = "skip_thought_signature_validator"
MIN_ABSOLUTE_TOKENS_PRO = 4096

TEXT_EXTENSIONS = {
    '.py', '.js', '.html', '.css', '.java', '.c', '.cpp', '.h', '.hpp', '.rs', '.go', '.ts', 
    '.json', '.yaml', '.yml', '.toml', '.xml', '.md', '.txt', '.sh', '.bat', '.ps1', 
    '.dockerfile', 'dockerfile', '.env', '.gitignore', '.editorconfig', '.conf', '.ini',
    '.rb', '.php', '.pl', '.swift', '.kt', '.cs', '.vb', '.lua', '.r', '.sql'
}

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
        if os.path.exists(self.internal_project_cache) and not debug_mode:
            with open(self.internal_project_cache, "r") as f: return f.read().strip(), "Cache."
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
        try:
            resp = httpx.post(f"{self.base_url}:loadCodeAssist", headers=headers, json={"metadata": {"ideType": "IDE_UNSPECIFIED", "pluginType": "GEMINI"}}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("cloudaicompanionProject")
                pid = raw.get("id") if isinstance(raw, dict) else raw
                if pid:
                    pid = pid.replace("projects/", "")
                    with open(self.internal_project_cache, "w") as f: f.write(pid)
                    return pid, "API OK."
        except Exception as e: return None, str(e)
        return None, "Fail."

    def reset_storage(self):
        for p in [self.token_path, self.pkce_path, self.internal_project_cache]:
            if os.path.exists(p): os.remove(p)

# ==============================================================================
# SECTION 4 : GESTIONNAIRE DE FICHIERS, REGISTRE CAS & SIGNATURES
# ==============================================================================
class SignatureManager:
    """Gère la persistance des signatures de pensée (CoT) pour assurer la continuité des conversations."""
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

class FileRegistry:
    """
    Registre local (JSON) des fichiers uploadés sur Google.
    - Clé primaire : SHA256 (Content-Addressable).
    - Permet la déduplication et le respect du TTL (48h).
    """
    def __init__(self, data_dir: str, chat_id: str):
        self.chat_id = chat_id if chat_id else "global"
        self.path = os.path.join(data_dir, f"{self.chat_id}_files.json")
        self.registry = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f: return json.load(f)
            except: pass
        return {}

    def _save(self):
        try:
            with open(self.path, "w") as f: json.dump(self.registry, f, indent=2)
        except: pass

    def get_entry(self, sha256: str) -> Optional[Dict]:
        """Vérifie si le fichier existe et si son URI est encore valide (< 47h pour marge sécu)."""
        if sha256 in self.registry:
            entry = self.registry[sha256]
            upload_ts = entry.get("upload_ts", 0)
            if (time.time() - upload_ts) < (47 * 3600):
                return entry
            else:
                del self.registry[sha256]
                self._save()
        return None

    def add_entry(self, sha256: str, uri: str, mime: str, size: int, name: str):
        """Enregistre un nouveau fichier uploadé avec son timestamp."""
        self.registry[sha256] = {
            "uri": uri,
            "mime": mime,
            "size": size,
            "name": name,
            "upload_ts": time.time()
        }
        self._save()

class GoogleFileManager:
    """
    Client HTTP pour l'API Google Files.
    Gère le protocole 'Resumable Upload' obligatoire pour les gros fichiers.
    """
    def __init__(self, auth_token: str):
        self.auth_token = auth_token
        self.upload_base_url = GOOGLE_UPLOAD_BASE_URL

    async def upload_file(self, file_path: str, mime_type: str) -> Optional[str]:
        file_size = os.path.getsize(file_path)
        display_name = os.path.basename(file_path)

        # 1. Initialisation
        headers_init = {
            "Authorization": f"Bearer {self.auth_token}",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json"
        }
        meta_body = {"file": {"displayName": display_name}}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp_init = await client.post(self.upload_base_url, headers=headers_init, json=meta_body)
                if resp_init.status_code != 200:
                    print(f"❌ [UPLOAD INIT FAIL] {resp_init.status_code}: {resp_init.text}")
                    # Retourne le texte de l'erreur pour affichage utilisateur potentiel via debug log
                    return None
                
                upload_url = resp_init.headers.get("x-goog-upload-url")
                if not upload_url: return None

                # 2. Transfert
                with open(file_path, "rb") as f:
                    file_data = f.read()

                headers_upload = {
                    "Content-Length": str(file_size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize"
                }

                # Timeout généreux (10min) pour les gros fichiers
                resp_upload = await client.post(upload_url, headers=headers_upload, content=file_data, timeout=600)
                
                if resp_upload.status_code == 200:
                    result = resp_upload.json()
                    file_uri = result.get("file", {}).get("uri")
                    print(f"✅ [UPLOAD SUCCESS] URI: {file_uri}")
                    return file_uri
                else:
                    print(f"❌ [UPLOAD DATA FAIL] {resp_upload.status_code}: {resp_upload.text}")
                    return None

        except Exception as e:
            print(f"❌ [UPLOAD EXCEPTION] {str(e)}")
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
        
        # Recherche par ID et Nom (Standard OWUI)
        candidates = []
        if f_name:
            clean_name = f_name.replace("/", "_").replace("\\", "_")
            # Pattern 1: ID_Nom (Standard)
            candidates.append(os.path.join(self.uploads_dir, f"{f_id}_{clean_name}"))
            # Pattern 2: Nom seul (Parfois utilisé par l'upload direct)
            candidates.append(os.path.join(self.uploads_dir, clean_name))
            
        # Pattern 3: ID_* (Fallback)
        matches = glob.glob(os.path.join(self.uploads_dir, f"{f_id}_*"))
        candidates.extend(matches)

        for cand in candidates:
            if os.path.exists(cand):
                return cand

        # Debug failure
        try:
            files_in_dir = os.listdir(self.uploads_dir)
            self.debug_log.append(f"❌ File Not Found. Looking for ID: {f_id}, Name: {f_name}")
            self.debug_log.append(f"📂 Content of {self.uploads_dir} ({len(files_in_dir)} files): {str(files_in_dir)[:300]}...")
        except Exception as e:
            self.debug_log.append(f"❌ Cannot list {self.uploads_dir}: {str(e)}")
            
        return None

    def _get_file_info(self, f_id: str, f_name: str, owui_path: str) -> Tuple[str, bool, str, Optional[str]]:
        """Identifie le type de fichier (Texte vs Binaire) et son chemin."""
        if not f_id: return "", False, "No ID", None
        real_path = self._resolve_local_path(owui_path, f_id, f_name)
        if not real_path: return "", False, f"Not Found: {f_id}", None

        mime_type, _ = mimetypes.guess_type(real_path)
        ext = os.path.splitext(real_path)[1].lower()

        is_text = False
        if mime_type and (mime_type.startswith("text/") or mime_type in ["application/json", "application/javascript", "application/xml"]): is_text = True
        if not is_text and ext in TEXT_EXTENSIONS: is_text = True

        if not mime_type:
            if ext == '.pdf': mime_type = "application/pdf"
            elif ext in ['.mp4', '.mov', '.avi']: mime_type = "video/mp4"
            elif ext in ['.mp3', '.wav']: mime_type = "audio/mp3"
            else: mime_type = "application/octet-stream"

        return mime_type, is_text, "", real_path

    async def prepare_context(self, body: Dict, chat_id: str, auth_token: str, extra_files: Any = None) -> List[Dict]:
        """
        Prépare la liste 'contents' pour Gemini.
        - Texte : injecté directement.
        - Fichiers Binaires : Uploadé si nécessaire (CAS), puis injecté via fileData (URI).
        """
        self.files_processed_info = []
        messages = body.get("messages", [])
        contents = []

        file_registry = FileRegistry(self.data_dir, chat_id)
        file_manager = GoogleFileManager(auth_token) if auth_token else None
        
        # Mapping des tools pour le décodage des réponses
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

            # --- GESTION DES TOOLS (Réintégrée) ---
            if role == "tool":
                parts = []
                while i < len(messages) and messages[i]["role"] == "tool":
                    tm = messages[i]
                    tool_name = self.tool_map.get(tm.get("tool_call_id"), "unknown_tool")
                    try: val = json.loads(tm.get("content", "{}"))
                    except: val = {"result": str(tm.get("content", ""))}
                    parts.append({"functionResponse": {"name": tool_name, "response": val}})
                    i += 1
                
                # Attacher la réponse au dernier message utilisateur (Gemini requirement)
                if contents and contents[-1]["role"] == "user":
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": "user", "parts": parts})
                continue

            elif role in ["assistant", "model"]:
                parts = []
                txt = m.get("content", "")
                if isinstance(txt, list): txt = "".join([x.get("text","") for x in txt if "text" in x])
                
                # Nettoyage
                txt = re.sub(r'<think>.*?</think>', '', str(txt), flags=re.DOTALL).strip()
                txt = re.sub(r'<details>.*?</details>', '', txt, flags=re.DOTALL).strip()
                if txt: parts.append({"text": txt})

                # Gestion CoT & Function Call (Réintégrée)
                found_in_band_sig = None
                tool_calls_in_msg = False
                if m.get("tool_calls"):
                    tool_calls_in_msg = True
                    for tc in m["tool_calls"]:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            if "_thought_signature" in args: found_in_band_sig = args.pop("_thought_signature")
                            parts.append({"functionCall": {"name": tc["function"]["name"], "args": args}})
                        except: pass
                
                if not parts: parts.append({"text": " "})
                
                is_last_model_msg = True
                for j in range(i + 1, len(messages)):
                    if messages[j]["role"] in ["assistant", "model"]: is_last_model_msg = False; break

                if tool_calls_in_msg or is_last_model_msg:
                    sig_to_use = found_in_band_sig
                    if not sig_to_use and chat_id: sig_to_use = self.sig_manager.get_signature(chat_id)
                    if not sig_to_use and tool_calls_in_msg: sig_to_use = MAGIC_KEY_SKIP_VALIDATION
                    if sig_to_use and parts:
                         for part in parts: part["thoughtSignature"] = sig_to_use
                
                contents.append({"role": "model", "parts": parts})
            
            else: # USER
                parts = []
                files_to_process = []
                
                # Récupération de tous les fichiers
                raw_list = []
                if "files" in m and isinstance(m["files"], list): raw_list.extend(m["files"])
                
                if i == last_user_idx:
                    if body.get("raw_files_from_filter"): raw_list.extend(body.get("raw_files_from_filter"))
                    if extra_files: 
                        ex = extra_files if isinstance(extra_files, list) else [extra_files]
                        raw_list.extend(ex)

                seen_ids = set()
                for f in raw_list:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    if fid and fid not in seen_ids:
                        files_to_process.append(f); seen_ids.add(fid)

                for f_obj in files_to_process:
                    f_real = f_obj.get("file", f_obj)
                    f_id = f_real.get("id")
                    f_name = f_real.get("filename") or f_real.get("meta", {}).get("name")
                    f_path = f_real.get("path")

                    mime, is_text, err, real_path = self._get_file_info(f_id, f_name, f_path)

                    if err:
                        if self.valves.DEBUG_MODE: self.debug_log.append(f"⚠️ {f_name}: {err}")
                        continue

                    # Cas 1 : Texte -> Inline
                    if is_text:
                        try:
                            with open(real_path, "r", encoding="utf-8", errors="ignore") as f:
                                data = f.read()
                            parts.append({"text": f"--- FILE: {f_name} ---\n{data}\n--- END FILE ---\n"})
                            self.files_processed_info.append({"name": f_name, "type": "Text", "status": "Embedded 📄"})
                        except: pass
                        continue

                    # Cas 2 : Binaire -> CAS (Upload Unique)
                    if not file_manager:
                        parts.append({"text": f"[Error: Auth required for file {f_name}]"})
                        continue

                    file_size = os.path.getsize(real_path)
                    
                    # Hashage pour identifier le contenu de manière unique
                    with open(real_path, "rb") as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()

                    entry = file_registry.get_entry(file_hash)
                    final_uri = None
                    status_ui = "Unknown"

                    if entry:
                        # HIT : Déjà uploadé et valide
                        final_uri = entry["uri"]
                        status_ui = "Cache HIT ⚡"
                        if self.valves.DEBUG_MODE: self.debug_log.append(f"⚡ CAS HIT: {f_name}")
                    else:
                        # MISS : Upload nécessaire
                        if self.valves.DEBUG_MODE: self.debug_log.append(f"🔼 Uploading {f_name} ({file_size} bytes)...")
                        uri = await file_manager.upload_file(real_path, mime)
                        if uri:
                            final_uri = uri
                            file_registry.add_entry(file_hash, uri, mime, file_size, f_name)
                            status_ui = "Uploaded 🔼"
                        else:
                            status_ui = "Failed ❌"

                    if final_uri:
                        # Utilisation de l'URI Google au lieu du Base64
                        parts.append({"file_data": {"mime_type": mime, "file_uri": final_uri}})
                        self.files_processed_info.append({"name": f_name, "type": mime.split('/')[-1].upper(), "size": file_size, "status": status_ui})

                content_txt = m.get("content", "")
                if isinstance(content_txt, str) and content_txt.strip():
                    parts.append({"text": content_txt})
                elif isinstance(content_txt, list):
                    for item in content_txt:
                         if item.get("type") == "text": parts.append({"text": item.get("text", "")})
                
                if parts: contents.append({"role": "user", "parts": parts})
            
            i += 1
        return contents

    def estimate_tokens(self, contents: List[Dict]) -> int:
        total = 0
        for item in contents:
            for part in item.get("parts", []):
                if "text" in part: total += len(part["text"]) // 4
        return total

# ==============================================================================
# SECTION 6 : CACHE MANAGER (TEXTE) & ADAPTERS
# ==============================================================================
class ContextCacheManager:
    def __init__(self, auth_token: str):
        self.auth_token = auth_token
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def count_tokens(self, model: str, system_inst: dict, contents: list, tools: list = None) -> int:
        real_model = model if model.startswith("models/") else f"models/{model}"
        url = f"{self.base_url}/{real_model}:countTokens"
        payload = {"contents": contents, "systemInstruction": system_inst}
        if tools: payload["tools"] = tools
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200: return int(resp.json().get("totalTokens", 0))
        except: pass
        return -1

    async def create(self, model: str, system_inst: dict, contents: list, ttl: int = 600) -> Tuple[Optional[str], Optional[str]]:
        real_model = model if model.startswith("models/") else f"models/{model}"
        url = f"{self.base_url}/cachedContents"
        payload = {"model": real_model, "contents": contents, "systemInstruction": system_inst, "ttl": f"{ttl}s"}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.auth_token}"}
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200: return resp.json().get("name"), None
                return None, f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e: return None, str(e)

class SmartCacheStrategy:
    def __init__(self, cache_manager):
        self.mgr = cache_manager
        global _LOCAL_CACHE_REGISTRY
        self.registry = _LOCAL_CACHE_REGISTRY

    def _compute_hash(self, model: str, system_inst: Dict, contents: List[Dict]) -> str:
        data = {"model": model, "contents": contents}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    async def get_or_create_cache(self, model: str, system_inst: dict, contents: list, ttl: int, tools: list = None) -> Tuple[Optional[str], Optional[str]]:
        current_hash = self._compute_hash(model, system_inst, contents)
        now = time.time()
        
        if current_hash in self.registry:
            entry = self.registry[current_hash]
            if now < entry["expires_at"]: return entry["name"], None

        real_tokens = await self.mgr.count_tokens(model, system_inst, contents, tools)
        if real_tokens < MIN_ABSOLUTE_TOKENS_PRO: return None, None

        name, err = await self.mgr.create(model, system_inst, contents, ttl)
        if name:
            self.registry[current_hash] = {"name": name, "expires_at": now + ttl - 60}
        return name, err

class PublicGeminiOAuthAdapter:
    def __init__(self, auth_token: str):
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.auth_token = auth_token

    def build(self, model, contents, temp, max_tok, cached_name, tools=None):
        real_model = model if model.startswith("models/") else f"models/{model}"
        url = f"{self.base_url}/{real_model}:streamGenerateContent?alt=sse"
        payload = {"contents": contents, "cachedContent": cached_name, "generationConfig": {"temperature": temp, "maxOutputTokens": max_tok}}
        if tools: payload["tools"] = tools
        return {"url": url, "headers": {"Content-Type": "application/json", "Authorization": f"Bearer {self.auth_token}"}, "json": payload}

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
    def __init__(self, debug=False, chat_id=None, sig_manager=None, show_metrics=False, context_window=1000000, initial_label="Réponse", file_stats=None):
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
                    json.dump(self.usage_stats, f)
             except: pass

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False
        tool_index = 0
        step_label = self.initial_label
        ct = response.headers.get("content-type", "")
        if "application/json" in ct:
            yield f"⚠️ API Error: {await response.aread()}"
            return

        buffer = ""
        async for chunk in response.aiter_bytes():
            try:
                buffer += chunk.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line: continue
                    if line.startswith("data:"):
                        data = json.loads(line[6:])
                        
                        self._update_stats(data)

                        cand = data.get("candidates", []) or data.get("response", {}).get("candidates", [])
                        if cand and cand[0].get("content"):
                            for part in cand[0]["content"]["parts"]:
                                txt = part.get("text", "")
                                func_call = part.get("functionCall")

                                # 1. Capture Signature (Restauré)
                                if "thoughtSignature" in part:
                                    self.current_sig = part["thoughtSignature"]
                                    if self.chat_id and self.sig_manager:
                                        self.sig_manager.save_signature(self.chat_id, self.current_sig)

                                if part.get("thought"):
                                    if not in_think: yield "<think>\n"; in_think = True
                                    yield txt
                                
                                # 2. Tool Calls (Restauré)
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
                                                    "function": {"name": func_call["name"], "arguments": json.dumps(args)}
                                                }]
                                            }, "finish_reason": "tool_calls"
                                        }]
                                    }
                                    tool_index += 1

                                elif "text" in part:
                                    if in_think: yield "\n</think>\n"; in_think = False
                                    yield txt
            except: pass
        
        # --- CORRECTIF CRITIQUE : TRAITEMENT DU RELIQUAT DE BUFFER ---
        # Les métadonnées arrivent souvent dans le dernier chunk qui peut ne pas avoir de \n final.
        if buffer and buffer.strip().startswith("data:"):
            try:
                line = buffer.strip()
                data = json.loads(line[6:])
                self._update_stats(data) # Application de la fusion intelligente (Fix v135.08)
            except: pass

        if in_think: yield "\n</think>\n"

        if self.show_metrics and (self.usage_stats or self.file_stats):
            stats_content = ""
            
            if self.file_stats:
                stats_content += "\n\n**📁 Fichiers Traités**\n"
                stats_content += "| Fichier | Type | Taille | Statut |\n| :--- | :--- | :--- | :--- |\n"
                for f in self.file_stats:
                    size_mb = f['size'] / (1024*1024)
                    stats_content += f"| {f['name']} | {f['type']} | {size_mb:.2f} MB | {f['status']} |\n"
            
            if self.usage_stats:
                p_tok = self.usage_stats.get("promptTokenCount", 0)
                c_tok = self.usage_stats.get("candidatesTokenCount", 0)
                t_tok = self.usage_stats.get("totalTokenCount", 0)
                cache_tok = self.usage_stats.get("cachedContentTokenCount", 0)
                pct = (t_tok / self.context_window) * 100
                bar = "█" * int(pct/10) + "░" * (10 - int(pct/10))
                cache_row = f"| **Cache (Hit)** | {cache_tok:,} |\n" if cache_tok > 0 else ""
                
                stats_content += f"""\n<details>
<summary>⚡ Contexte [{step_label}]: {pct:.1f}% {bar}</summary>

| Métrique | Valeur |
| :--- | :--- |
| **Prompt** | {p_tok:,} |
{cache_row}| **Réponse** | {c_tok:,} |
| **Total** | {t_tok:,} / {self.context_window:,} |
</details>\n"""
                yield stats_content

        # 3. Yield Final Usage (Restauré)
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
        RUN_DIAGNOSTICS: bool = Field(default=False, description="🚑 DIAGNOSTICS")
        FORCE_RESET_AUTH: bool = Field(default=False, description="🔴 RESET AUTH")
        DEBUG_MODE: bool = Field(default=False, description="🐞 DEBUG MODE")
        SHOW_METRICS: bool = Field(default=True, description="📊 Afficher Métriques")
        
        ENABLE_CACHING: bool = Field(default=True, description="🧠 Smart Cache (Text)")
        CACHE_TTL: int = Field(default=604800, description="⏱️ Durée Cache (sec)")
        MIN_CACHE_TOKENS: int = Field(default=4096, description="⚖️ Min Tokens (Text)")
        
        MODEL_SELECTION: Literal["gemini-3-pro-preview", "gemini-2.5-pro"] = Field(default="gemini-3-pro-preview", description="Modèle")
        TEMPERATURE: float = Field(default=1.0, description="Température")
        MAX_TOKENS: int = Field(default=65536, description="Max Tokens")
        MAX_CONTEXT_SIZE: int = Field(default=1048576, description="📚 Taille Contexte Max")
        THINKING_LEVEL: Literal["DYNAMIC", "LOW", "HIGH"] = Field(default="DYNAMIC", description="Niveau de réflexion")
        SYSTEM_PROMPT: str = Field(default="Tu es un assistant expert.", description="Prompt Système")
        ENABLE_DATE_TIME: bool = Field(default=True, description="🕒 Injecter Temps")
        ENABLE_AUTO_LOCATION: bool = Field(default=True, description="📍 Injecter Lieu")
        OVERRIDE_LOCATION: str = Field(default="", description="✏️ Forcer Lieu")

    def __init__(self):
        self.valves = self.Valves()
        self.data_dir = "/app/backend/data"
        os.makedirs(self.data_dir, exist_ok=True)
        self.auth = AuthService(self.data_dir)
        self.base_url = GOOGLE_API_BASE_URL

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __request__: Optional[any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        chat_id = body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, self.data_dir)
        
        # 1. AUTHENTIFICATION
        ac = orch.check_for_auth_code(body.get("messages", []))
        if ac:
            success, msg = self.auth.exchange_code(ac)
            yield f"✅ **{msg}**" if success else f"❌ **Échec** : `{msg}`"; return
        
        creds = self.auth.get_valid_credentials()
        if not creds: yield self.auth.get_auth_url(); return
        pid, _ = self.auth.get_project_id(creds, self.valves.DEBUG_MODE)

        # 2. PRÉPARATION CONTEXTE
        tools = orch.convert_owui_tools(body.get("tools"))
        files = body.get("files") or kwargs.get("__files__")
        context = await orch.prepare_context(body, chat_id, creds.token, extra_files=files)

        if self.valves.DEBUG_MODE and orch.debug_log:
             for log in orch.debug_log: yield f"{log}\n"
        
        initial_label = "Réponse"
        if body.get("messages") and body.get("messages")[-1].get("role") == "tool":
            initial_label = "Post-Action"

        # 3. DÉCISION DE CACHE (TEXTE)
        req = None
        estimated_tokens = orch.estimate_tokens(context)
        
        if self.valves.ENABLE_CACHING and estimated_tokens >= self.valves.MIN_CACHE_TOKENS:
             history_to_cache = []
             for msg in context[:-1]:
                  clean_parts = [p for p in msg.get("parts", []) if "text" in p]
                  if clean_parts: history_to_cache.append({"role": msg["role"], "parts": clean_parts})
             
             trigger_content = [context[-1]]
             
             if history_to_cache:
                 cache_mgr = ContextCacheManager(creds.token)
                 strategy = SmartCacheStrategy(cache_mgr)
                 cache_name, _ = await strategy.get_or_create_cache(
                     self.valves.MODEL_SELECTION,
                     orch.get_system_instruction(),
                     history_to_cache,
                     self.valves.CACHE_TTL,
                     tools
                 )
                 
                 if cache_name:
                     if self.valves.DEBUG_MODE: yield f"✅ **TEXT CACHE LOCKED**: `{cache_name}`\n"
                     adapter = PublicGeminiOAuthAdapter(creds.token)
                     req = adapter.build(self.valves.MODEL_SELECTION, trigger_content, self.valves.TEMPERATURE, self.valves.MAX_TOKENS, cache_name, tools)

        # 4. FALLBACK
        if not req:
            adapter = GeminiAdapter(self.base_url)
            req = adapter.build(pid, context, orch.get_system_instruction(), self.valves.TEMPERATURE, self.valves.MAX_TOKENS, self.valves.THINKING_LEVEL, self.valves.MODEL_SELECTION, tools)
            req["headers"]["Authorization"] = f"Bearer {creds.token}"

        if self.valves.DEBUG_MODE:
             log_req = json.loads(json.dumps(req['json']))
             yield f"🐞 **API REQ**\n`{json.dumps(log_req)[:500]}...`\n"

        # 5. EXECUTION
        proc = StreamProcessor(
            self.valves.DEBUG_MODE, 
            chat_id, 
            sig_manager=orch.sig_manager,
            show_metrics=self.valves.SHOW_METRICS, 
            context_window=self.valves.MAX_CONTEXT_SIZE,
            initial_label=initial_label,
            file_stats=orch.files_processed_info
        )

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("POST", req["url"], json=req["json"], headers=req["headers"]) as r:
                    if r.status_code != 200:
                        err = await r.aread()
                        yield f"⚠️ **API ERROR {r.status_code}**\n`{err.decode(errors='ignore')}`"
                        return
                    async for token in proc.process(r): yield token
        except Exception as e: yield f"🔥 **CRASH** : `{str(e)}`"