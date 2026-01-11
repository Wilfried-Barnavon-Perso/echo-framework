"""
title: Gemini Pro Unified System (Platinum Agentic V134.72 - Contextual Labeling)
author: Wilfried BARNAVON
version: 134.72
description: v134.72: Affichage contextuel des métriques : "Pré-[NomOutil]" (Appel), "Post-Action" (Retour outil) ou "Réponse" (Standard).
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
GOOGLE_API_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# --- CONSTANTES MAGIQUES ---
MAGIC_KEY_SKIP_VALIDATION = "skip_thought_signature_validator"

# Fallback extensions si MIME type inconnu (pour code source)
TEXT_EXTENSIONS = {
    '.py', '.js', '.html', '.css', '.java', '.c', '.cpp', '.h', '.hpp', '.rs', '.go', '.ts', 
    '.json', '.yaml', '.yml', '.toml', '.xml', '.md', '.txt', '.sh', '.bat', '.ps1', 
    '.dockerfile', 'dockerfile', '.env', '.gitignore', '.editorconfig', '.conf', '.ini'
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
# SECTION 3 : SERVICE D'AUTHENTIFICATION (IDENTITY PROVIDER)
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
        if not HAS_GOOGLE_LIBS:
            return "❌ **Erreur** : Librairies `google-auth` manquantes."

        should_generate_new = True
        verifier = None

        if os.path.exists(self.pkce_path):
            try:
                creation_time = os.path.getmtime(self.pkce_path)
                if time.time() - creation_time < 300:
                    with open(self.pkce_path, "r") as f:
                        existing_verifier = f.read().strip()
                    if len(existing_verifier) > 10:
                        verifier = existing_verifier
                        should_generate_new = False
            except: pass

        if should_generate_new:
            verifier, challenge = self._generate_pkce()
            try:
                with open(self.pkce_path, "w") as f: f.write(verifier)
            except Exception as e: return f"❌ Erreur IO: {str(e)}"
        else:
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            import base64
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

        flow = Flow.from_client_config(
            OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False
        )
        flow.redirect_uri = GOOGLE_REDIRECT_URI
        url, _ = flow.authorization_url(prompt="consent", access_type="offline", code_challenge=challenge, code_challenge_method="S256")

        return (
            f"### 🔐 Authentification Requise\n\n"
            f"1. **[Cliquez ici]({url})**\n"
            f"2. Connectez-vous.\n"
            f"3. Copiez le code `4/...`.\n"
            f"4. **Collez-le ici**."
        )

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
        if os.path.exists(self.internal_project_cache) and not debug_mode:
            with open(self.internal_project_cache, "r") as f: return f.read().strip(), "Cache."
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
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
        except Exception as e: return None, str(e)
        return None, "Fail."

    def reset_storage(self):
        for p in [self.token_path, self.pkce_path, self.internal_project_cache]:
            if os.path.exists(p): os.remove(p)

# ==============================================================================
# SECTION 4 : SIGNATURE MANAGER
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
# SECTION 5 : ORCHESTRATEUR (SMART FILE HANDLING V134.61)
# ==============================================================================
class Orchestrator:
    def __init__(self, valves, data_dir):
        self.valves = valves
        self.location_cache_file = "/app/backend/data/gemini_geo_cache_v2.json"
        self.uploads_dir = "/app/backend/data/uploads" 
        self.tool_map = {}
        self.sig_manager = SignatureManager(data_dir)
        self.debug_log = []

    def check_for_auth_code(self, messages: List[Dict]) -> Optional[str]:
        if not messages: return None
        last_msg = messages[-1].get("content", "")
        if isinstance(last_msg, list): return None
        last_msg = str(last_msg).strip()
        match = re.search(r"(4/[a-zA-Z0-9_-]+)", last_msg)
        if match and len(match.group(1)) > 30: return match.group(1)
        return None

    def _get_geo_info(self) -> Tuple[str, str]:
        loc, tz = "Paris, France", "Europe/Paris"
        if getattr(self.valves, "OVERRIDE_LOCATION", ""): return self.valves.OVERRIDE_LOCATION, tz
        if getattr(self.valves, "ENABLE_AUTO_LOCATION", True) and os.path.exists(self.location_cache_file):
            try:
                if time.time() - os.path.getmtime(self.location_cache_file) < 86400:
                    with open(self.location_cache_file, "r") as f:
                        c = json.load(f)
                        return c.get("location", loc), c.get("timezone", tz)
            except: pass
        return loc, tz

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        if not tools: return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                funcs.append({
                    "name": f.get("name"),
                    "description": f.get("description", ""),
                    "parameters": f.get("parameters", {"type": "object", "properties": {}})
                })
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
        """Helper to list files in uploads dir for debug."""
        try:
            if not os.path.exists(self.uploads_dir):
                return f"❌ Dir not found: {self.uploads_dir}"
            files = os.listdir(self.uploads_dir)
            return f"✅ Dir exists. {len(files)} files."
        except Exception as e:
            return f"❌ Error listing dir: {str(e)}"

    def _resolve_local_path(self, provided_path: str, f_id: str, f_name: str) -> Optional[str]:
        # 1. Test direct
        if provided_path and os.path.exists(provided_path):
            self.debug_log.append(f"✅ Path found (Direct): {provided_path}")
            return provided_path

        # 2. Construction locale
        candidates = []
        if f_name:
            clean_name = f_name.replace("/", "_").replace("\\", "_")
            candidates.append(os.path.join(self.uploads_dir, f"{f_id}_{clean_name}"))
        
        search_pattern = os.path.join(self.uploads_dir, f"{f_id}_*")
        matches = glob.glob(search_pattern)
        if matches: candidates.extend(matches)

        for p in candidates:
            if os.path.exists(p) and os.path.isfile(p):
                self.debug_log.append(f"✅ Path found (Glob): {p}")
                return p
        
        return None

    def _get_file_content(self, f_id: str, f_name: str, owui_path: str = None) -> Tuple[Optional[str], Optional[str], bool, str]:
        if not f_id: return None, None, False, "No File ID"
        
        real_path = self._resolve_local_path(owui_path, f_id, f_name)
        
        if not real_path:
            return None, None, False, f"File not found on disk: {f_id}"

        mime_type, _ = mimetypes.guess_type(real_path)
        ext = os.path.splitext(real_path)[1].lower()

        is_text_mime = False
        if mime_type:
            if mime_type.startswith("text/"): is_text_mime = True
            if mime_type in ["application/json", "application/javascript", "application/xml", "application/x-yaml"]: is_text_mime = True
            
        if not is_text_mime and ext in TEXT_EXTENSIONS:
            is_text_mime = True

        if is_text_mime:
            try:
                with open(real_path, "r", encoding="utf-8", errors="ignore") as f:
                    text_content = f.read()
                    if len(text_content) > 500000: text_content = text_content[:500000] + "\n...[TRUNCATED]..."
                    return text_content, "text/plain", True, ""
            except Exception as e: return None, None, False, f"Text Read Error: {str(e)}"

        try:
            if not mime_type:
                ext_clean = ext.lower()
                if ext_clean in ['.mp4', '.m4v']: mime_type = "video/mp4"
                elif ext_clean in ['.webm']: mime_type = "video/webm"
                elif ext_clean in ['.mpeg', '.mpg']: mime_type = "video/mpeg"
                elif ext_clean in ['.mov']: mime_type = "video/quicktime"
                elif ext_clean in ['.avi']: mime_type = "video/x-msvideo"
                elif ext_clean in ['.mp3']: mime_type = "audio/mp3"
                elif ext_clean in ['.wav']: mime_type = "audio/wav"
                elif ext_clean in ['.ogg']: mime_type = "audio/ogg"
                elif ext_clean in ['.flac']: mime_type = "audio/flac"
                elif ext_clean in ['.aac']: mime_type = "audio/aac"
                elif ext_clean in ['.m4a']: mime_type = "audio/mp4"
                elif ext_clean in ['.pdf']: mime_type = "application/pdf"
                elif ext_clean in ['.jpg', '.jpeg']: mime_type = "image/jpeg"
                elif ext_clean in ['.png']: mime_type = "image/png"
                elif ext_clean in ['.webp']: mime_type = "image/webp"
                else: mime_type = "application/octet-stream"

            with open(real_path, "rb") as f:
                raw_data = f.read()
                data = base64.standard_b64encode(raw_data).decode("utf-8")
                return data, mime_type, False, ""
        except Exception as e: return None, None, False, f"Binary Read Error: {str(e)}"

    def _parse_data_uri(self, data_uri: str) -> Dict:
        try:
            header, data = data_uri.split(",", 1)
            mime_type = header.split(":")[1].split(";")[0]
            return {"inlineData": {"mimeType": mime_type, "data": data}}
        except: return {"text": "[Error parsing data URI]"}

    def prepare_context(self, body: Dict, chat_id: str, extra_files: Any = None) -> List[Dict]:
        messages = body.get("messages", [])
        contents = []
        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if "id" in tc and "function" in tc:
                        self.tool_map[tc["id"]] = tc["function"].get("name")

        # 1. Identify Last User Message Index
        last_user_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx]["role"] == "user":
                last_user_idx = idx
                break
        
        if self.valves.DEBUG_MODE:
             # Force disk probe (Simpler)
             probe_info = self._probe_disk()
             self.debug_log.append(f"🔍 **DISK**: `{probe_info}`")
             
             # Report on ROOT FILTER FILES (Audit Strategy)
             root_files = body.get("raw_files_from_filter", [])
             if root_files:
                 self.debug_log.append(f"📦 **ROOT FILTER FILES**: Found {len(root_files)} files.")
             else:
                 self.debug_log.append(f"📦 **ROOT FILTER FILES**: None")

        i = 0
        while i < len(messages):
            m = messages[i]
            role = m["role"]
            if role == "system": i+=1; continue

            if self.valves.DEBUG_MODE and role == "user" and i == last_user_idx:
                 # Simplified target msg log
                self.debug_log.append(f"🔍 [Target Msg] Found User Message")

            raw_content = m.get("content", "")
            if role == "user" and isinstance(raw_content, str) and ("4/" in raw_content and len(raw_content) > 30):
                if re.search(r"(4/[a-zA-Z0-9_-]+)", raw_content): i += 1; continue

            if role == "tool":
                parts = []
                while i < len(messages) and messages[i]["role"] == "tool":
                    tm = messages[i]
                    tool_name = self.tool_map.get(tm.get("tool_call_id"), "unknown_tool")
                    try: val = json.loads(tm.get("content", "{}"))
                    except: val = {"result": str(tm.get("content", ""))}
                    parts.append({"functionResponse": {"name": tool_name, "response": val}})
                    i += 1
                if contents and contents[-1]["role"] == "user": contents[-1]["parts"].extend(parts)
                else: contents.append({"role": "user", "parts": parts})
                continue

            elif role in ["assistant", "model"]:
                parts = []
                text_content = ""
                if isinstance(raw_content, str): text_content = raw_content
                elif isinstance(raw_content, list):
                    for p in raw_content:
                        if isinstance(p, dict) and "text" in p: text_content += p["text"]

                text_content = re.sub(r'<think>.*?</think>', '', text_content, flags=re.DOTALL).strip()
                text_content = re.sub(r'\[\s*\]\(context://thought_signature/[^\)]+\)', '', text_content).strip()
                
                # --- NETTOYAGE ANTI-POLLUTION (Stats & Citations) ---
                text_content = re.sub(r'<div.*?>.*?Stats:.*?</div>', '', text_content, flags=re.DOTALL | re.IGNORECASE).strip()
                text_content = re.sub(r'<details.*?>.*?Métriques de Flux.*?</details>', '', text_content, flags=re.DOTALL | re.IGNORECASE).strip()
                text_content = re.sub(r'> \*\*⚡ Métriques de Flux.*?\n', '', text_content).strip()

                if text_content: parts.append({"text": text_content})

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
                seen_ids = set()

                # A. From Message 'files' list (Standard OWUI)
                if "files" in m and isinstance(m["files"], list):
                    for f in m["files"]:
                        if isinstance(f, dict):
                            fid = f.get("id") or f.get("file", {}).get("id")
                            if fid and fid not in seen_ids:
                                files_to_process.append(f); seen_ids.add(fid)
                
                # B. From Filter (Priority to ROOT KEY as per Audit)
                if i == last_user_idx:
                    # B1. Root Key (The fix - raw_files_from_filter)
                    raw_files = body.get("raw_files_from_filter", [])
                    
                    if raw_files:
                          for f in raw_files:
                            if isinstance(f, dict):
                                fid = f.get("id") or f.get("file", {}).get("id")
                                if fid and fid not in seen_ids:
                                    files_to_process.append(f); seen_ids.add(fid)

                # C. From kwargs 'extra_files' (Backup)
                if i == last_user_idx and extra_files:
                    extras = extra_files if isinstance(extra_files, list) else [extra_files]
                    for f in extras:
                        if isinstance(f, dict):
                            fid = f.get("id") or f.get("file", {}).get("id")
                            if fid and fid not in seen_ids:
                                files_to_process.append(f); seen_ids.add(fid)

                if self.valves.DEBUG_MODE and i == last_user_idx:
                    self.debug_log.append(f"🔍 Files to process: {len(files_to_process)} (IDs: {seen_ids})")

                for f_obj in files_to_process:
                    try:
                        # Deep inspection to find ID/Path/Name
                        f_real = f_obj.get("file", f_obj) 
                        
                        f_id = f_real.get("id")
                        f_name = f_real.get("filename") or f_real.get("meta", {}).get("name")
                        f_owui_path = f_real.get("path")

                        data, mime_type, is_text, error_msg = self._get_file_content(f_id, f_name, f_owui_path)

                        if error_msg:
                            if self.valves.DEBUG_MODE: self.debug_log.append(f"⚠️ Load Error ({f_name}): {error_msg}")
                            parts.append({"text": f"\n[SYSTEM ERROR: Could not load file {f_name}. Reason: {error_msg}.]\n"})
                        
                        elif data:
                            if is_text:
                                parts.append({"text": f"--- FILE: {f_name} ---\n{data}\n--- END FILE ---\n"})
                            elif mime_type:
                                parts.append({"inlineData": {"mimeType": mime_type, "data": data}})
                                if self.valves.DEBUG_MODE: self.debug_log.append(f"🚀 INJECTED: {f_name} ({mime_type})")
                    except Exception as e:
                            if self.valves.DEBUG_MODE: self.debug_log.append(f"🔥 Loop Error: {str(e)}")

                content = m.get("content", "")
                if isinstance(content, str):
                    content = re.sub(r'!\[.*?\]\(data:[^)]+\)', '', content)
                    content = re.sub(r'data:[a-zA-Z0-9/.-]+;base64,[a-zA-Z0-9+/=]+', '', content)
                    content = content.strip()
                    if content:
                        parts.append({"text": content})

                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                parts.append({"text": item.get("text", "")})
                            elif item.get("type") == "image_url":
                                url = item.get("image_url", {}).get("url", "")
                                if url.startswith("data:"):
                                    parts.append(self._parse_data_uri(url))
                            elif "image" in item and item["image"].startswith("data:"):
                                parts.append(self._parse_data_uri(item["image"]))

                if parts:
                    if contents and contents[-1]["role"] == "user":
                        contents[-1]["parts"].extend(parts)
                    else:
                        contents.append({"role": "user", "parts": parts})
            i += 1
        return contents

# ==============================================================================
# SECTION 6 : ADAPTATEUR API
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
            "model": model_id, "project": project_id, "user_prompt_id": hex(random.getrandbits(64))[2:],
            "request": {
                "systemInstruction": system_instr, "contents": contents,
                "generationConfig": gen_config, "session_id": str(uuid.uuid4()),
            },
        }
        if tools:
            payload["request"]["tools"] = tools
            payload["request"]["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        return {
            "url": f"{self.base_url}:streamGenerateContent?alt=sse",
            "headers": {"Content-Type": "application/json", "User-Agent": "GeminiCLI/0.20.0"},
            "json": payload,
        }

# ==============================================================================
# SECTION 7 : PROCESSEUR DE FLUX
# ==============================================================================
class StreamProcessor:
    def __init__(self, debug=False, chat_id=None, sig_manager=None, show_metrics=False, context_window=1048576, initial_label="Réponse"):
        self.debug = debug
        self.chat_id = chat_id
        self.sig_manager = sig_manager
        self.show_metrics = show_metrics
        self.context_window = context_window
        self.initial_label = initial_label # Label de départ (dérivé du contexte pipe)
        self.current_sig = None
        self.usage_stats = None
        self.stats_dir = "/app/backend/data/stats"
        os.makedirs(self.stats_dir, exist_ok=True)

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False
        tool_index = 0
        buffer = ""
        step_label = self.initial_label # Commence avec "Réponse" ou "Post-Action"

        ct = response.headers.get("content-type", "")
        if "application/json" in ct:
            err_body = await response.aread()
            try:
                err_json = json.loads(err_body)
                err_msg = err_json.get("error", {}).get("message", str(err_body))
                yield f"⚠️ **API ERROR**\n`{err_msg}`"
            except: yield f"⚠️ **API ERROR (Raw)**\n`{err_body.decode(errors='ignore')}`"
            return

        async for chunk in response.aiter_bytes():
            try: buffer += chunk.decode("utf-8", errors="ignore")
            except: continue

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line: continue

                if line.startswith("{") and "error" in line:
                    try:
                        data = json.loads(line)
                        if "error" in data:
                            yield f"⚠️ **Stream Error**\n`{data['error'].get('message', line)}`"
                            continue
                    except: pass

                if not line.startswith("data:"): continue
                try:
                    data = json.loads(line[6:])
                    if self.debug: yield f"\n`[SSE] {json.dumps(data, ensure_ascii=False)}`\n"

                    # Capture Metadata
                    meta = data.get("response", {}).get("usageMetadata")
                    if meta:
                        self.usage_stats = meta
                        if self.debug: yield f"\n🐞 **DEBUG** Usage Metadata received: `{json.dumps(self.usage_stats)}`\n"
                        if self.chat_id:
                            try:
                                safe_id = "".join(x for x in str(self.chat_id) if x.isalnum() or x in "-_")
                                with open(f"{self.stats_dir}/{safe_id}.json", "w") as f:
                                    json.dump(self.usage_stats, f)
                            except: pass

                    cand = data.get("candidates", [])
                    if not cand:
                        cand = data.get("response", {}).get("candidates", [])

                    if cand:
                        first_cand = cand[0]
                        if "content" in first_cand:
                            parts = first_cand["content"].get("parts", [])
                            for part in parts:
                                txt = part.get("text", "")
                                is_think = part.get("thought", False)
                                func_call = part.get("functionCall")

                                if "thoughtSignature" in part:
                                    self.current_sig = part["thoughtSignature"]
                                    if self.chat_id and self.sig_manager:
                                        self.sig_manager.save_signature(self.chat_id, self.current_sig)

                                if is_think:
                                    if not in_think: yield "<think>\n"; in_think = True
                                    yield txt
                                elif func_call:
                                    step_label = f"Pré-{func_call.get('name', 'Action')}" # Update du label : on prépare l'action
                                    if in_think: yield "\n</think>\n"; in_think = False
                                    args = func_call.get("args", {})
                                    if self.current_sig: args["_thought_signature"] = self.current_sig
                                    yield {
                                        "choices": [{
                                            "index": 0, "delta": {
                                                "tool_calls": [{
                                                    "index": tool_index, "id": f"call_{secrets.token_hex(8)}",
                                                    "type": "function", "function": {"name": func_call["name"], "arguments": json.dumps(args)}
                                                }]
                                            }, "finish_reason": "tool_calls"
                                        }]
                                    }
                                    tool_index += 1
                                else:
                                    if in_think: yield "\n</think>\n"; in_think = False
                                    if txt: yield txt
                        else:
                             reason = first_cand.get("finishReason", "UNKNOWN")
                             if reason != "STOP":
                                 yield f"⚠️ **Stop Reason: {reason}**\n"
                                 
                except: pass
        if in_think: yield "\n</think>\n"
        
        # --- INJECTION DES MÉTRIQUES (SYSTEMATIQUE) ---
        if self.usage_stats:
            p_tok = self.usage_stats.get("promptTokenCount", 0)
            c_tok = self.usage_stats.get("candidatesTokenCount", 0)
            t_tok = self.usage_stats.get("totalTokenCount", 0)
            
            if self.debug: yield f"\n🐞 **DEBUG** Injecting Stats: P={p_tok}, C={c_tok}, T={t_tok}\n"

            # Condition simple : Si l'utilisateur veut les métriques, on les affiche.
            if self.show_metrics:
                # Calcul pourcentage occupation contexte
                percent = 0
                if self.context_window > 0:
                    percent = (t_tok / self.context_window) * 100
                
                # Barre de progression simple (10 blocs)
                filled = int(percent / 10)
                bar = "█" * filled + "░" * (10 - filled)

                # Format Markdown
                stats_md = f"""\n\n<details>
<summary>⚡ Contexte [{step_label}]: {percent:.1f}% {bar}</summary>

| Métrique | Valeur |
| :--- | :--- |
| **Prompt** | {p_tok:,} |
| **Réponse** | {c_tok:,} |
| **Total** | {t_tok:,} / {self.context_window:,} |
</details>\n"""
                yield stats_md

            # Envoi aussi du protocole standard pour la DB (Toujours envoyé pour update du système)
            yield {
                "usage": {
                    "prompt_tokens": p_tok,
                    "completion_tokens": c_tok,
                    "total_tokens": t_tok
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
        MODEL_SELECTION: Literal["gemini-3-pro-preview", "gemini-2.5-pro"] = Field(default="gemini-3-pro-preview", description="Modèle")
        TEMPERATURE: float = Field(default=1.0, description="Température")
        MAX_TOKENS: int = Field(default=65536, description="Max Tokens")
        MAX_CONTEXT_SIZE: int = Field(default=1048576, description="📚 Taille Contexte Max")
        THINKING_LEVEL: Literal["DYNAMIC", "LOW", "HIGH"] = Field(default="DYNAMIC", description="Niveau de réflexion (Gemini 3)")
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

    # Ajout de **kwargs pour capturer les fichiers cachés d'OWUI
    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __request__: Optional[any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        chat_id = body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None) or (__metadata__.get("session_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, self.data_dir)
        
        # Détection basique du contexte "Post-Action" via l'historique immédiat
        initial_label = "Réponse"
        msgs = body.get("messages", [])
        if msgs and msgs[-1].get("role") == "tool":
             initial_label = "Post-Action"
        
        proc = StreamProcessor(
            self.valves.DEBUG_MODE, 
            chat_id, 
            orch.sig_manager, 
            self.valves.SHOW_METRICS, 
            self.valves.MAX_CONTEXT_SIZE,
            initial_label # On passe le label déduit
        )

        if self.valves.DEBUG_MODE: yield f"🐞 **DEBUG**\nChatID: `{chat_id}`\n"

        ac = orch.check_for_auth_code(body.get("messages", []))
        if ac:
            success, msg = self.auth.exchange_code(ac)
            yield f"✅ **{msg}**" if success else f"❌ **Échec** : `{msg}`"
            return

        if self.valves.FORCE_RESET_AUTH:
            self.auth.reset_storage(); yield "🔄 **Reset.**"; return

        creds = self.auth.get_valid_credentials()
        if not creds: yield self.auth.get_auth_url(); return

        pid, debug_log = self.auth.get_project_id(creds, self.valves.DEBUG_MODE)
        if not pid: yield f"❌ **Erreur Projet**\n{debug_log}"; return

        tools = orch.convert_owui_tools(body.get("tools"))
        adapter = GeminiAdapter(self.base_url)
        
        # Récupération sécurisée des fichiers via l'argument spécial __files__ s'il existe
        files = body.get("files") or kwargs.get("__files__") # Capture prioritaire via kwargs si body vide
        
        # NOTE: On passe 'body' en entier pour le support du Filtre
        context = orch.prepare_context(body, chat_id, extra_files=files)

        # DEBUG: Output raw incoming OWUI message logs if available
        if self.valves.DEBUG_MODE and hasattr(orch, 'debug_log') and orch.debug_log:
            for log in orch.debug_log:
                yield f"{log}\n"

        req = adapter.build(pid, context, orch.get_system_instruction(), self.valves.TEMPERATURE, self.valves.MAX_TOKENS, self.valves.THINKING_LEVEL, self.valves.MODEL_SELECTION, tools)
        req["headers"]["Authorization"] = f"Bearer {creds.token}"

        if self.valves.DEBUG_MODE:
            # Clean up the request for concise logging, especially the large base64 data
            log_req = json.loads(json.dumps(req['json'])) # Deep copy
            for content in log_req.get("request", {}).get("contents", []):
                for part in content.get("parts", []):
                    if "inlineData" in part and "data" in part["inlineData"]:
                        part["inlineData"]["data"] = f"[...base64 data of type {part['inlineData'].get('mimeType', 'unknown')}...]"
            yield f"🐞 **API REQ**\n`{json.dumps(log_req)[:1000]}...`\n"

            # DEBUG: Log Structure
            if self.valves.DEBUG_MODE:
                summary = []
                for c in log_req.get("request", {}).get("contents", []):
                    role = c.get("role", "?")
                    parts_len = len(c.get("parts", []))
                    summary.append(f"{role}[{parts_len}]")
                yield f"\n🐞 **MSG STRUCTURE**: {' -> '.join(summary)}\n"


        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream("POST", req["url"], json=req["json"], headers=req["headers"]) as r:
                    if r.status_code != 200:
                        err = await r.aread()
                        yield f"⚠️ **API ERROR {r.status_code}**\n`{err.decode(errors='ignore')}`"
                        return
                    async for token in proc.process(r): yield token
        except Exception as e: yield f"🔥 **CRASH** : `{str(e)}`"