"""
title: ECHO Engine
author: Wilfried BARNAVON
version: 167.20
description: 167.20: No plans Limit given ... to be continued.
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
import random
import base64
import codecs
import asyncio
import json as std_json 
import sqlite3
import zlib
import gzip
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, EchoStateManager, get_echo_version, split_thought_process
from echo_constants import *

# --- IMPORTATIONS TIERCES CRITIQUES ---
try:
    import httpx
    import orjson
    import pybase64
    import mgzip as gzip
    from pydantic import BaseModel, Field
except ImportError as e:
    missing_module = e.name or "inconnu"
    raise ImportError(f"❌ Module critique manquant : '{missing_module}'.") from e

# Vérification HTTP/2
HAS_HTTP2 = False
try:
    import h2
    HAS_HTTP2 = True
except ImportError:
    HAS_HTTP2 = False

MAGIC_KEY_SKIP_VALIDATION = "context_engineering_is_the_way_to_go"

# ==============================================================================
# SECTION 1 : LOGGER TECHNIQUE
# ==============================================================================

class DebugLogger:
    def __init__(self, data_dir: str, chat_id: str):
        self.log_dir = os.path.join(data_dir, "debug_logs")
        os.makedirs(self.log_dir, exist_ok=True)
        safe_id = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_") if chat_id else "unknown_chat"
        self.log_path = os.path.join(self.log_dir, f"debug_{safe_id}.json")

    def log(self, event_type: str, payload: Any, metadata: Dict = None):
        entry = {"timestamp": datetime.now().isoformat(), "type": event_type, "metadata": metadata or {}, "data": payload}
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(std_json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception: pass

_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(
    timeout: int = 600, 
    enable_http2: bool = True,
    max_connections: int = 100,
    max_keepalive: int = 20,
    keepalive_expiry: int = 300
) -> httpx.AsyncClient:
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
    now = time.time()
    try:
        if _SHARED_ASYNC_CLIENT and not _SHARED_ASYNC_CLIENT.is_closed:
            if hasattr(_SHARED_ASYNC_CLIENT, "_transport") and hasattr(_SHARED_ASYNC_CLIENT._transport, "_pool"):
                 client_loop = getattr(_SHARED_ASYNC_CLIENT._transport._pool, "_loop", None)
                 if client_loop and client_loop != asyncio.get_running_loop():
                     await _SHARED_ASYNC_CLIENT.aclose(); _SHARED_ASYNC_CLIENT = None
    except: _SHARED_ASYNC_CLIENT = None

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
        use_h2 = enable_http2 and HAS_HTTP2
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=use_h2)
    
    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

# ==============================================================================
# SECTION 4 : USER DATA MANAGER (PROXY)
# ==============================================================================
class UserDataManager:
    def __init__(self, user_id: str = "system", debug_mode: bool = False):
        self.state_manager = EchoStateManager(ECHO_USER_DBS_DIR, user_id)
        self.debug_mode = debug_mode

    def calculate_invariant(self, role: str, content: Any, tool_io: dict = None) -> str:
        return self.state_manager.calculate_invariant_hash(role, content, tool_io)

    def calculate_cumulative(self, invariant: str, parent: str = None) -> str:
        return self.state_manager.calculate_cumulative_hash(invariant, parent)

    def get_rich_payload(self, invariant: str) -> Optional[List[dict]]:
        return self.state_manager.get_rich_payload(invariant)

    def save_rich_payload(self, invariant_hash: str, parts: List[dict]):
        self.state_manager.save_rich_payload(invariant_hash, parts)

    def index_suture(self, cumul: str, chat_id: str, inv: str, parent: str = None):
        self.state_manager.index_suture(cumul, chat_id, inv, parent)

    def get_signature(self, cumul: str) -> Optional[str]:
        return self.state_manager.get_thought_signature(cumul)

    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        return self.state_manager.get_call_bridge(call_id)

    def save_cognitive(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None):
        self.state_manager.save_cognitive_data(cumul, sig, thought, tool_io)

    def save_call_bridge(self, call_id: str, sig: str, func_name: str, args: dict = None):
        self.state_manager.save_call_bridge(call_id, sig, func_name, args)

    def save_auth_data(self, key: str, value: str):
        self.state_manager.save_auth_data(key, value)

    def get_auth_data(self, key: str) -> Optional[Tuple[str, int]]:
        return self.state_manager.get_auth_data(key)

    def delete_auth_data(self, key: str):
        self.state_manager.delete_auth_data(key)

    def save_context_stats(self, stats: dict):
        self.state_manager.save_context_stats(stats)

# ==============================================================================
# SECTION 3 : SERVICE D'AUTHENTIFICATION
# ==============================================================================
class AuthService:
    def __init__(self, user_data_manager: UserDataManager):
        self.user_data_manager = user_data_manager
        self.base_url = GOOGLE_API_BASE_URL

    def _generate_pkce(self):
        """Génération PKCE robuste conforme v150.3."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        """Génère l'URL d'authentification avec challenge PKCE (S256) via httpx.QueryParams. Réutilise le verifier s'il est actif."""
        existing_data = self.user_data_manager.get_auth_data('pkce_verifier')
        if existing_data and (int(time.time()) - existing_data[1] < 290):
            verifier = existing_data[0]
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        else:
            verifier, challenge = self._generate_pkce()
            self.user_data_manager.save_auth_data('pkce_verifier', verifier)
        
        params = httpx.QueryParams({
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent"
        })
        url = f"{GOOGLE_AUTH_URI}?{params}"
        return (
            f"🔐 **Authentification ECHO Requise**\n\n"
            f"Votre session Google Cloud a expiré ou a été réinitialisée.\n\n"
            f"1. [Cliquez ici pour autoriser ECHO]({url})\n"
            f"2. Copiez le code affiché (ex: `4/0Af...`)\n"
            f"3. Collez-le simplement dans ce chat.\n\n"
            f"*(ECHO_SESSION_AUTH_PENDING)*"
        )

    def exchange_code(self, code: str) -> Tuple[bool, str]:
        pkce_data = self.user_data_manager.get_auth_data('pkce_verifier')
        if not pkce_data: return False, "Session expirée."
        
        # Contrôle strict de 300 secondes (5 minutes)
        if int(time.time()) - pkce_data[1] > 300:
            self.user_data_manager.delete_auth_data('pkce_verifier')
            return False, "Le délai de 5 minutes est dépassé. Veuillez générer un nouveau lien."

        try:
            from google_auth_oauthlib.flow import Flow
            OFFICIAL_CLIENT_CONFIG = {"installed": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "auth_uri": GOOGLE_AUTH_URI, "token_uri": GOOGLE_TOKEN_URI, "redirect_uris": [GOOGLE_REDIRECT_URI]}}
            flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False)
            flow.redirect_uri = GOOGLE_REDIRECT_URI
            flow.fetch_token(code=code.strip(), code_verifier=pkce_data[0])
            self.user_data_manager.save_auth_data('google_token', flow.credentials.to_json())
            
            # Suppression uniquement en cas de succès
            self.user_data_manager.delete_auth_data('pkce_verifier')
            return True, "Succès."
        except Exception as e: return False, str(e)

    def get_valid_credentials(self):
        token_data = self.user_data_manager.get_auth_data('google_token')
        if not token_data: return None
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GoogleAuthRequest
            creds = Credentials.from_authorized_user_info(std_json.loads(token_data[0]), GOOGLE_SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(GoogleAuthRequest()); self.user_data_manager.save_auth_data('google_token', creds.to_json())
            return creds if (creds and creds.valid) else None
        except: return None

    def get_project_id(self, creds, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        cached = self.user_data_manager.get_auth_data('google_project_id')
        plan_cached = self.user_data_manager.get_auth_data('google_plan_name')
        if cached and plan_cached and not debug_mode: return cached[0], "Cache."
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
        try:
            resp = httpx.post(f"{GOOGLE_API_BASE_URL}:loadCodeAssist", headers=headers, json={"metadata": {"ideType": "IDE_UNSPECIFIED", "pluginType": "GEMINI"}}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                
                # --- [Nouveau] Récupération du Plan et des Crédits ---
                paid_tier = data.get("paidTier") or data.get("currentTier") or {}
                plan_name = paid_tier.get("name", paid_tier.get("id", "Plan Inconnu"))
                self.user_data_manager.save_auth_data('google_plan_name', plan_name)
                
                available_credits = paid_tier.get("availableCredits", [])
                for credit in available_credits:
                    if credit.get("creditType") == "GOOGLE_ONE_AI":
                        self.user_data_manager.save_auth_data('google_credits', str(credit.get("creditAmount", "0")))
                        break
                # -----------------------------------------------------

                pid = data.get("cloudaicompanionProject", {}).get("id") if isinstance(data.get("cloudaicompanionProject"), dict) else data.get("cloudaicompanionProject")
                if pid:
                    pid = pid.replace("projects/", ""); self.user_data_manager.save_auth_data('google_project_id', pid)
                    return pid, "API OK."
            return None, "Handshake Fail."
        except Exception as e: return None, str(e)

    async def fetch_user_quota_async(self, creds, pid: str, model_id: str):
        """Récupère le quota principal (Pooled) de manière asynchrone."""
        if not creds or not pid: return
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
        payload = {"project": pid, "userAgent": ECHO_USER_AGENT}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{GOOGLE_API_BASE_URL}:retrieveUserQuota", headers=headers, json=payload, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    buckets = data.get("buckets", [])
                    
                    total_rem = 0
                    total_lim = 0
                    furthest_reset = ""
                    
                    for b in buckets:
                        rem_str = b.get("remainingAmount")
                        frac = b.get("remainingFraction")
                        r_time = b.get("resetTime", "")
                        
                        if rem_str and frac is not None and float(frac) > 0:
                            rem = int(rem_str)
                            lim = round(rem / float(frac))
                            total_rem += rem
                            total_lim += lim
                            if r_time > furthest_reset: furthest_reset = r_time
                    
                    if total_lim > 0:
                        self.user_data_manager.save_auth_data("google_quota_remaining", str(total_rem))
                        self.user_data_manager.save_auth_data("google_quota_limit", str(total_lim))
                        self.user_data_manager.save_auth_data("google_quota_reset_time", furthest_reset)
                        self.user_data_manager.save_auth_data("google_quota_last_sync", str(int(time.time())))
        except Exception: pass

# ==============================================================================
# SECTION 5 : ORCHESTRATEUR (SUTURE & PROTOCOLE)
# ==============================================================================
class Orchestrator:
    def __init__(self, valves, user_valves, data_dir: str, user_id: str, chat_id: str = None):
        self.valves = valves; self.user_valves = user_valves
        self.user_data_manager = UserDataManager(user_id, valves.DEBUG_MODE)
        self.tool_map = {}; self.logger = DebugLogger(data_dir, chat_id) if valves.DEBUG_MODE else None

    def _resolve_placeholders(self, text: str, model_id: str) -> str:
        """Résolution dynamique des balises techniques."""
        if not isinstance(text, str): return text
        version = get_echo_version() or "##VERSION_ERR##"
        resolved = text.replace("##ECHO_VERSION##", version)
        resolved = resolved.replace("##GEMINI_ENGINE##", model_id)
        resolved = resolved.replace("##MODEL_ID##", model_id)
        return resolved

    def _ensure_gemini_parts(self, content: Any, model_id: str = "unknown") -> List[Dict]:
        """Nettoyage NON-DESTRUCTIF avec résolution de placeholders et conversion multimodale."""
        parts = []
        if isinstance(content, str):
            if content.strip(): parts.append({"text": self._resolve_placeholders(content, model_id)})
        elif isinstance(content, list):
            for p in content:
                if not isinstance(p, dict): continue
                new_part = {}
                if "text" in p: 
                    new_part["text"] = self._resolve_placeholders(p["text"], model_id)
                elif p.get("type") == "image_url" and "image_url" in p:
                    # Conversion format OpenAI (OWUI) -> Gemini inlineData
                    url = p["image_url"].get("url", "")
                    if url.startswith("data:"):
                        try:
                            mime, b64 = url.split(";", 1)[0].replace("data:", ""), url.split(",", 1)[1]
                            new_part["inlineData"] = {"mimeType": mime, "data": b64}
                        except: pass
                elif "inlineData" in p: 
                    new_part["inlineData"] = p["inlineData"]
                elif "inline_data" in p: 
                    new_part["inlineData"] = {"mimeType": p["inline_data"]["mime_type"], "data": p["inline_data"]["data"]}
                elif "functionCall" in p: 
                    new_part["functionCall"] = p["functionCall"]
                elif "functionResponse" in p: 
                    new_part["functionResponse"] = p["functionResponse"]
                
                if "thoughtSignature" in p and new_part: 
                    new_part["thoughtSignature"] = p["thoughtSignature"]
                
                if new_part: 
                    parts.append(new_part)
        return parts

    def _unbox_tool_output(self, name: str, content: Any, model_id: str) -> List[Dict]:
        """Déballeur Strict ECHO Multi-Parts conforme à la doc Gemini officielle."""
        if isinstance(content, str):
            try: content = orjson.loads(content)
            except: content = {"text": str(content), "status": {"status": "legacy_fallback"}}
        if not isinstance(content, dict): content = {"text": str(content), "status": {"status": "error_format"}}
        
        text_body = content.get("text", "")
        status_meta = content.get("status", {"status": "success"})
        rich_multiparts = content.get("echo_tool_multiparts", [])

        # Le texte métier DOIT être dans le dictionnaire `response` pour que le modèle le lise comme résultat de l'outil
        response_dict = status_meta.copy()
        if text_body:
            response_dict["result"] = self._resolve_placeholders(text_body, model_id)

        # Construction du functionResponse
        func_resp = {
            "name": name,
            "response": response_dict
        }

        # S'il y a des parts multimodales, elles s'imbriquent DANS le functionResponse
        func_parts = []
        for mp in rich_multiparts:
            m_type = mp.get("type")
            if m_type == "thought" and mp.get("content"): 
                response_dict["tool_thought"] = mp["content"]
            elif m_type == "media" and mp.get("data"): 
                func_parts.append({
                    "inlineData": {
                        "mimeType": mp.get("mime_type", "image/png"), 
                        "data": mp["data"]
                    }
                })
        
        if func_parts:
            func_resp["parts"] = func_parts

        return [{"functionResponse": func_resp}]

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        if not tools: return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                funcs.append({"name": f.get("name"), "description": f.get("description", ""), "parameters": f.get("parameters", {"type": "object", "properties": {}})})
        return [{"functionDeclarations": funcs}] if funcs else None

    async def prepare_context(self, body: Dict, chat_id: str, __metadata__: Optional[Dict] = None, events: Optional[EchoEvents] = None) -> List[Dict]:
        """RESTAURATION Bit-Perfect avec Standard ECHO Multi-Parts."""
        messages = body.get("messages", []); meta = __metadata__ or body.get("metadata", {})
        all_files = meta.get("_echo_files", [])
        model_id = self.user_valves.MODEL_SELECTION
        
        final_contents = []; last_cumul = None
        i = 0
        while i < len(messages):
            m = messages[i]; role = m.get("role"); content = m.get("content", "")
            if role == "system" or any(x in str(content) for x in ["ECHO_SESSION_AUTH_PENDING", "Authentification ECHO"]) or str(content).startswith("4/"): 
                i += 1; continue

            if role == "tool":
                aggregated_tool_parts = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    m_tool = messages[i]; content_tool = m_tool.get("content", "")
                    inv_hash = self.user_data_manager.calculate_invariant("tool", content_tool)
                    current_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                    call_id = m_tool.get("tool_call_id"); bridge = self.user_data_manager.get_call_bridge(call_id)
                    func_name = bridge["name"] if bridge else "unknown"
                    rich_tool_parts = self._unbox_tool_output(func_name, content_tool, model_id)
                    aggregated_tool_parts.extend(rich_tool_parts)
                    self.user_data_manager.index_suture(current_cumul, chat_id, inv_hash, last_cumul)
                    last_cumul = current_cumul; i += 1
                final_contents.append({"role": "user", "parts": aggregated_tool_parts})
                continue

            # --- CAS GÉNÉRAUX : USER / ASSISTANT ---
            if role in ["assistant", "model"]:
                # PURGE DES PENSÉES (Doctrine v5.48.8) : Ne jamais renvoyer les pensées au modèle.
                # Permet de maintenir l'invariance du hash pour la Suture.
                content, _ = split_thought_process(content if isinstance(content, str) else str(content))

            inv_hash = meta.get("_echo_invariant_hash") if (role == "user" and i == len(messages)-1) else self.user_data_manager.calculate_invariant(role, content)
            current_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
            restored_parts = None
            
            if role == "user":
                if i == len(messages) - 1:
                    rich = meta.get("_echo_rich_parts"); restored_parts = []
                    if rich: restored_parts.extend(self._ensure_gemini_parts(rich, model_id))
                    user_prompt = content if isinstance(content, str) else ""
                    if not user_prompt and isinstance(content, list):
                        for p in reversed(content):
                            if isinstance(p, dict) and p.get("type") == "text": user_prompt = p.get("text", ""); break
                            elif isinstance(p, str): user_prompt = p; break
                    if user_prompt.strip(): restored_parts.append({"text": self._resolve_placeholders(user_prompt, model_id)})
                    restored_parts = self._ensure_gemini_parts(restored_parts, model_id)
                    self.user_data_manager.save_rich_payload(inv_hash, restored_parts)
                else:
                    payload = self.user_data_manager.get_rich_payload(inv_hash)
                    if payload: restored_parts = self._ensure_gemini_parts(payload, model_id)

            elif role in ["assistant", "model"]:
                sig = None; tool_calls = m.get("tool_calls", [])
                if tool_calls:
                    bridge = self.user_data_manager.get_call_bridge(tool_calls[0].get("id"))
                    if bridge: sig = bridge["signature"]
                if not sig: sig = self.user_data_manager.get_signature(current_cumul)
                restored_parts = self._ensure_gemini_parts(content, model_id)
                if not tool_calls:
                    tool_io = self.user_data_manager.state_manager.get_tool_io(current_cumul)
                    if tool_io: restored_parts = [{"functionCall": {"name": tc["name"], "args": tc["args"]}} for tc in tool_io.get("calls", [])] + restored_parts
                else:
                    restored_parts = [{"functionCall": {"name": tc["function"]["name"], "args": orjson.loads(tc["function"]["arguments"])}} for tc in tool_calls] + restored_parts
                
                if sig and restored_parts:
                    injected = False
                    for p in restored_parts:
                        if "functionCall" in p: p["thoughtSignature"] = sig; injected = True; break
                    if not injected:
                        for p in reversed(restored_parts):
                            if "text" in p: p["thoughtSignature"] = sig; break
                elif tool_calls:
                    # Le parachute MAGIC_KEY est validé par la doc Google pour les historiques manuels
                    for p in restored_parts:
                        if "functionCall" in p: p["thoughtSignature"] = MAGIC_KEY_SKIP_VALIDATION; break
                    print(f"!!! [ECHO INFO] SUTURE : Signature manquante pour {tool_calls[0].get('id')}. Utilisation de la clé de secours officielle (Doc Google).", flush=True)

            if restored_parts: final_contents.append({"role": "model" if role in ["assistant", "model"] else "user", "parts": restored_parts})
            else: final_contents.append({"role": "model" if role in ["assistant", "model"] else "user", "parts": self._ensure_gemini_parts(content, model_id)})
            self.user_data_manager.index_suture(current_cumul, chat_id, inv_hash, last_cumul)
            last_cumul = current_cumul; i += 1

        body["_echo_last_cumul"] = last_cumul
        if self.logger: self.logger.log("context_reconstructed", final_contents)
        return final_contents

# ==============================================================================
# SECTION 6 : STREAM PROCESSOR
# ==============================================================================
class StreamProcessor:
    def __init__(self, user_data_manager: UserDataManager, chat_id=None, events=None, logger=None):
        self.user_data_manager = user_data_manager; self.chat_id = chat_id
        self.events = events or EchoEvents(); self.logger = logger
        self.usage_stats = None; self.captured_sig = None; self.accumulated_text = ""
        self.accumulated_calls = []; self.full_raw_accumulator = []

    def _create_tool_call_part(self, func_call: dict, tool_index: int) -> dict:
        tc_id = f"echo-{secrets.token_hex(8)}"
        self.accumulated_calls.append({"id": tc_id, "name": func_call["name"], "args": func_call.get("args", {})})
        return {"index": tool_index, "id": tc_id, "type": "function", "function": {"name": func_call["name"], "arguments": std_json.dumps(func_call.get("args", {}))}}

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False; buffer = ""; decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        async for chunk in response.aiter_bytes():
            buffer += decoder.decode(chunk, final=False)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1); line = line.strip()
                if not line.startswith("data:"): continue
                try:
                    data = orjson.loads(line[6:]); self.full_raw_accumulator.append(data)
                    target = data.get("response", {}) if "response" in data else data
                    if "usageMetadata" in target:
                        if self.usage_stats is None:
                            self.usage_stats = {}
                        self.usage_stats.update(target["usageMetadata"])
                    
                    # --- [Nouveau] Récupération des Crédits au fil de l'eau ---
                    if "remainingCredits" in target:
                        if self.usage_stats is None: self.usage_stats = {}
                        for credit in target["remainingCredits"]:
                            if credit.get("creditType") == "GOOGLE_ONE_AI":
                                self.usage_stats["google_credits"] = str(credit.get("creditAmount", "0"))
                                self.user_data_manager.save_auth_data('google_credits', self.usage_stats["google_credits"])
                                break
                    # ----------------------------------------------------------
                    
                    if "usageMetadata" in target or "remainingCredits" in target:
                        self.user_data_manager.save_context_stats(self.usage_stats)
                    cand = data.get("candidates", []) or data.get("response", {}).get("candidates", [])
                    if cand and cand[0].get("content"):
                        for part in cand[0]["content"]["parts"]:
                            if "thoughtSignature" in part: self.captured_sig = part["thoughtSignature"]
                            if part.get("thought"):
                                if not in_think: yield "<think>\n"; in_think = True
                                yield part.get("text", "")
                            elif part.get("functionCall"):
                                if in_think: yield "\n</think>\n"; in_think = False
                                tool_call = self._create_tool_call_part(part["functionCall"], len(self.accumulated_calls))
                                yield {"choices": [{"index": 0, "delta": {"tool_calls": [tool_call]}}]}
                            elif "text" in part:
                                if in_think: yield "\n</think>\n"; in_think = False
                                raw_t = part["text"]
                                # INTERCEPTION DES MESSAGES ÉPHÉMÈRES (Garde-fous Google)
                                if "<EPHEMERAL_MESSAGE>" in raw_t or "CRITICAL INSTRUCTION" in raw_t:
                                    print(f"!!! [ECHO INTERCEPT] Garde-fou Google détecté et masqué : {raw_t}", flush=True)
                                    continue
                                self.accumulated_text += raw_t; yield raw_t
                except: pass
        if in_think: yield "\n</think>\n"
        if self.logger: self.logger.log("api_response", self.full_raw_accumulator)
        if self.usage_stats:
            yield {"usage": {"prompt_tokens": self.usage_stats.get("promptTokenCount", 0), "completion_tokens": self.usage_stats.get("candidatesTokenCount", 0), "total_tokens": self.usage_stats.get("totalTokenCount", 0)}}

# ==============================================================================
# SECTION 8 : LE PIPE
# ==============================================================================
class Pipe:
    class Valves(BaseModel):
        HTTP_CLIENT_TIMEOUT: int = Field(default=600); ENABLE_HTTP2: bool = Field(default=True)
        HTTP_MAX_CONNECTIONS: int = Field(default=100); HTTP_MAX_KEEPALIVE: int = Field(default=20)
        HTTP_KEEPALIVE_EXPIRY: int = Field(default=300)
        DEBUG_MODE: bool = Field(default=False); MAX_CONTEXT_SIZE: int = Field(default=1048576)
        RETRY_TIMEBASE: int = Field(default=2, description="Délai initial (sec) avant relance sur erreur serveur (429/50x).")
        MAX_RETRIES: int = Field(default=5, description="Nombre max d'essais supplémentaires sur erreur serveur.")
    class UserValves(BaseModel):
        SHOW_CONTEXT_METRICS: bool = Field(default=True, description="Affiche une barre HUD des tokens et du cache utilisés à la fin du message.")
        MODEL_SELECTION: Literal["gemini-3.1-pro-preview", "gemini-3-flash-preview"] = Field(default="gemini-3.1-pro-preview")
        PRO_THINKING_LEVEL: Literal["LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        FLASH_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        TEMPERATURE: float = Field(default=1.0); MAX_TOKENS: int = Field(default=65536)

    def __init__(self): self.valves, self.data_dir = self.Valves(), "/app/backend/data"

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __event_emitter__: Optional[any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        events = EchoEvents(__event_emitter__)
        if not __user__: yield "❌ Identité manquante."; return
        user_valves = __user__.get("valves") or self.UserValves()
        chat_id = body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, user_valves, self.data_dir, __user__["id"], chat_id)
        auth = AuthService(orch.user_data_manager)
        
        # --- NOUVEAU : Interception du code d'autorisation transmis par le filtre ---
        auth_token = body.get("_auth_token")
        if auth_token:
            success, msg = auth.exchange_code(auth_token)
            if not success:
                yield f"❌ **Échec de l'authentification :** {msg}\n\n" + auth.get_auth_url()
                return
            yield "✅ **Authentification réussie !** Vous pouvez maintenant poser votre question.\n"
            return
        
        creds = auth.get_valid_credentials()
        if not creds: yield auth.get_auth_url(); return
        pid, _ = auth.get_project_id(creds, self.valves.DEBUG_MODE)
        if not pid: yield "❌ Erreur Projet."; return
        
        # --- [Nouveau] Récupération du Quota en arrière-plan pour les nouvelles sessions ---
        messages = body.get("messages", [])
        if len(messages) <= 2:
            await auth.fetch_user_quota_async(creds, pid, user_valves.MODEL_SELECTION)
        # -----------------------------------------------------------------------------------
        
        context = await orch.prepare_context(body, chat_id, __metadata__, events)
        sys_instr = {"parts": [{"text": "\n".join([m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"]) or "Tu es ECHO."}]}
        think = user_valves.PRO_THINKING_LEVEL if user_valves.MODEL_SELECTION == "gemini-3.1-pro-preview" else user_valves.FLASH_THINKING_LEVEL
        payload = {"model": user_valves.MODEL_SELECTION, "project": pid, "request": {"systemInstruction": sys_instr, "contents": context, "generationConfig": {"temperature": user_valves.TEMPERATURE, "maxOutputTokens": user_valves.MAX_TOKENS, "thinkingConfig": {"includeThoughts": True, "thinkingLevel": think.lower()}}}}
        tools = orch.convert_owui_tools(body.get("tools"))
        if tools: payload["request"]["tools"] = tools; payload["request"]["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        if orch.logger: orch.logger.log("google_request", payload)
        proc = StreamProcessor(orch.user_data_manager, chat_id, events, logger=orch.logger)
        client = await _get_global_client(
            self.valves.HTTP_CLIENT_TIMEOUT, 
            self.valves.ENABLE_HTTP2,
            self.valves.HTTP_MAX_CONNECTIONS,
            self.valves.HTTP_MAX_KEEPALIVE,
            self.valves.HTTP_KEEPALIVE_EXPIRY
        )
        
        current_delay = self.valves.RETRY_TIMEBASE
        
        for attempt in range(self.valves.MAX_RETRIES + 1):
            try:
                async with client.stream("POST", f"{GOOGLE_API_BASE_URL}:streamGenerateContent?alt=sse", content=orjson.dumps(payload), headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}) as r:
                    if r.status_code in [429, 500, 503]:
                        error_payload = await r.aread()
                        code = r.status_code
                        
                        # --- [Nouveau] Synchronisation forcée du Quota sur erreur 429 ---
                        if code == 429:
                            await auth.fetch_user_quota_async(creds, pid, user_valves.MODEL_SELECTION)
                        # ----------------------------------------------------------------
                        
                        if attempt < self.valves.MAX_RETRIES:
                            await events.status(f"⚠️ Erreur API {code} (Essai {attempt+1}/{self.valves.MAX_RETRIES}). Pause de {current_delay}s...", done=False)
                            await asyncio.sleep(current_delay)
                            current_delay *= 3
                            continue
                        else:
                            # --- [Nouveau] Affichage de l'heure de réinitialisation ---
                            reset_msg = ""
                            if code == 429:
                                r_time = auth.user_data_manager.get_auth_data("google_quota_reset_time")
                                if r_time and r_time[0]: reset_msg = f" (Réinitialisation prévue à : {r_time[0]})"
                            # ----------------------------------------------------------
                            await events.status(f"❌ Échec API {code} : Veuillez essayer avec un autre modèle.", done=True)
                            yield f"❌ Échec définitif (Erreur {code}) après {self.valves.MAX_RETRIES} relances.{reset_msg}\n\n```json\n{error_payload.decode('utf-8', errors='ignore')}\n```"
                            return
                    elif r.status_code == 200:
                        async for token in proc.process(r): yield token
                        if chat_id and proc.usage_stats:
                            tool_io = {"calls": [{"name": c["name"], "args": c["args"]} for c in proc.accumulated_calls]} if proc.accumulated_calls else None
                            inv = orch.user_data_manager.calculate_invariant("assistant", proc.accumulated_text, tool_io=tool_io)
                            new_cumul = orch.user_data_manager.calculate_cumulative(inv, body.get("_echo_last_cumul"))
                            orch.user_data_manager.save_cognitive(new_cumul, proc.captured_sig, None, tool_io)
                            for c in proc.accumulated_calls: orch.user_data_manager.save_call_bridge(c["id"], proc.captured_sig, c["name"], c["args"])
                            orch.user_data_manager.index_suture(new_cumul, chat_id, inv, body.get("_echo_last_cumul"))
                        
                        if user_valves.SHOW_CONTEXT_METRICS and proc.usage_stats:
                            max_t = self.valves.MAX_CONTEXT_SIZE
                            p_t = proc.usage_stats.get("promptTokenCount", 0)
                            
                            # --- [Nouveau] Variables HUD ---
                            plan_data = auth.user_data_manager.get_auth_data('google_plan_name')
                            plan_name = plan_data[0] if plan_data else ""
                            
                            credits_val = proc.usage_stats.get("google_credits", "")
                            if not credits_val:
                                c_data = auth.user_data_manager.get_auth_data('google_credits')
                                credits_val = c_data[0] if c_data else ""
                                
                            q_rem = auth.user_data_manager.get_auth_data('google_quota_remaining')
                            q_lim = auth.user_data_manager.get_auth_data('google_quota_limit')
                            q_rem_str = q_rem[0] if q_rem else "?"
                            q_lim_str = q_lim[0] if q_lim else "?"
                            quota_str = f"🎯 {q_rem_str}/{q_lim_str} req. | " if (q_rem_str != "?" and q_lim_str != "?") else ""
                            # -------------------------------
                            c_t = proc.usage_stats.get("cachedContentTokenCount", 0)
                            g_t = proc.usage_stats.get("candidatesTokenCount", 0)
                            active_p_t = max(0, p_t - c_t)
                            
                            cache_pct = min(100, (c_t / max_t) * 100)
                            prompt_pct = min(100, (active_p_t / max_t) * 100)
                            gen_pct = min(100, (g_t / max_t) * 100)
                            
                            js_code = f"""
                            (function() {{
                                var navContainer = document.querySelector('nav div.flex.items-center.w-full.max-w-full');
                                if (!navContainer) return;
                                
                                var rightControls = navContainer.querySelector('div.self-start.flex.flex-none.items-center');
                                
                                var oldHud = document.getElementById('echo-nav-context-hud');
                                if (oldHud) oldHud.remove();
                                
                                var hud = document.createElement('div');
                                hud.id = 'echo-nav-context-hud';
                                hud.style.display = 'flex';
                                hud.style.alignItems = 'center';
                                hud.style.margin = '0 12px';
                                hud.style.flexGrow = '8';
                                hud.style.width = '66%';
                                hud.style.minWidth = '350px';
                                hud.style.opacity = '0.9';
                                hud.style.transition = 'opacity 0.2s';
                                hud.onmouseover = function() {{ this.style.opacity = '1'; }};
                                hud.onmouseout = function() {{ this.style.opacity = '0.9'; }};
                                
                                var planName = "{plan_name}";
                                var credits = "{credits_val}";
                                var quotaInfo = "{quota_str}";
                                var billingInfo = "";
                                if (planName) billingInfo += `💳 ${{planName}} | ` + quotaInfo;
                                if (credits && credits !== "0") billingInfo += `🔋 ${{credits}} crédits IA | `;
                                
                                hud.title = billingInfo + `🟪 Cache: {c_t} | 🟩 User/Prompt: {active_p_t} | 🟧 Generated: {g_t} | ⬜ Max: {max_t}`;
                                
                                var label = document.createElement('span');
                                label.innerText = 'CTX';
                                label.style.fontSize = '10px';
                                label.style.fontWeight = 'bold';
                                label.style.color = 'var(--color-gray-500, #6b7280)';
                                label.style.marginRight = '6px';
                                label.style.whiteSpace = 'nowrap';
                                if (window.innerWidth < 640) label.style.display = 'none';
                                hud.appendChild(label);
                                
                                var barContainer = document.createElement('div');
                                barContainer.style.display = 'flex';
                                barContainer.style.width = '100%';
                                barContainer.style.height = '8px';
                                barContainer.style.backgroundColor = 'rgba(128, 128, 128, 0.2)';
                                barContainer.style.borderRadius = '4px';
                                barContainer.style.overflow = 'hidden';
                                
                                var cacheBar = document.createElement('div');
                                cacheBar.style.width = '{cache_pct:.2f}%';
                                cacheBar.style.backgroundColor = '#8b5cf6';
                                barContainer.appendChild(cacheBar);
                                
                                var promptBar = document.createElement('div');
                                promptBar.style.width = '{prompt_pct:.2f}%';
                                promptBar.style.backgroundColor = '#10b981';
                                barContainer.appendChild(promptBar);
                                
                                var genBar = document.createElement('div');
                                genBar.style.width = '{gen_pct:.2f}%';
                                genBar.style.backgroundColor = '#f59e0b';
                                barContainer.appendChild(genBar);
                                
                                hud.appendChild(barContainer);
                                
                                if (rightControls) {{
                                    navContainer.insertBefore(hud, rightControls);
                                }} else {{
                                    navContainer.appendChild(hud);
                                }}
                            }})();
                            """
                            await events.emit("execute", {"code": js_code})

                        break # Succès, on sort de la boucle de retry
                    elif r.status_code in [401, 403]:
                        err_data = await r.aread()
                        raw_err = err_data.decode(errors='ignore')
                        print(f"[ECHO ENGINE] AUTH ERROR {r.status_code} RAW: {raw_err}", flush=True)
                        validation_url = None
                        try:
                            e_json = std_json.loads(raw_err)
                            details = e_json.get("error", {}).get("details", [])
                            for d in details:
                                if d.get("reason") == "VALIDATION_REQUIRED":
                                    validation_url = d.get("metadata", {}).get("validation_url")
                                    if validation_url: break
                                if "links" in d:
                                    validation_url = d["links"][0].get("url")
                                    if validation_url: break
                        except: pass
                        if validation_url:
                            yield (
                                f"## 🔐 Action requise sur votre compte Google\n\n"
                                f"Une validation supplémentaire est nécessaire pour débloquer l'accès à Gemini.\n\n"
                                f"> 🔗 **[Cliquez ici pour vérifier votre compte]({validation_url})**\n\n"
                                f"Une fois la validation terminée sur le site de Google, renvoyez simplement votre message."
                            )
                        else:
                            try: err_msg = std_json.loads(raw_err).get("error", {}).get("message", raw_err)
                            except: err_msg = raw_err
                            yield f"🔐 **Authentification requise ({r.status_code})**\n`{err_msg}`"
                        break # Pas de retry sur les erreurs d'auth
                    else: 
                        err_text = await r.aread(); yield f"❌ Erreur API {r.status_code}. {err_text.decode('utf-8', errors='ignore')}"
                        break # Pas de retry sur les autres erreurs inattendues
            except (httpx.ReadTimeout, httpx.ProtocolError) as e:
                if attempt < self.valves.MAX_RETRIES:
                    await events.status(f"⚠️ Micro-coupure réseau. Reconnexion dans {current_delay}s...", done=False)
                    await asyncio.sleep(current_delay)
                    current_delay *= 3
                    continue
                else:
                    await events.status("❌ Erreur réseau persistante.", done=True)
                    yield f"❌ Erreur réseau persistante : {str(e)}"
                    return
            except Exception as e: 
                yield f"❌ Erreur système : {str(e)}"
                return