"""
title: ECHO Engine
author: Wilfried BARNAVON
version: 169.0
description: 169.0: Dynamic Model Routing via User Valves and Gemma 3 Judge.
"""

# ==============================================================================
# SECTION 0 : IMPORTS & CONFIGURATION
# ==============================================================================
import os
import sys
import secrets
import hashlib
import re
import time
import random
import pybase64 as base64
import codecs
import asyncio
import orjson as std_json 
import sqlite3
import zlib
import mgzip as gzip
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, EchoStateManager, get_echo_version, split_thought_process
from echo_constants import *
from echo_auth import AuthService

# --- IMPORTATIONS TIERCES CRITIQUES ---
try:
    import httpx
    import orjson
    import pybase64
    from pydantic import BaseModel, Field
    # Protocole HTTP/2 obligatoire (h2)
    import h2
except ImportError as e:
    missing_module = e.name or "inconnu"
    raise ImportError(f"❌ Module critique manquant : '{missing_module}'. ECHO exige httpx, orjson, pybase64 et h2 (HTTP/2).") from e

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
                f.write(std_json.dumps(entry).decode('utf-8') + "\n")
        except Exception: pass

_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(
    timeout: int = 600, 
    max_connections: int = 100,
    max_keepalive: int = 20,
    keepalive_expiry: int = 300
) -> httpx.AsyncClient:
    """Gestionnaire de client HTTP/2 STRICT."""
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
# SECTION 4 : USER DATA MANAGER (PROXY)
# ==============================================================================
class UserDataManager:
    def __init__(self, user_id: str = "system", chat_id: Optional[str] = None, debug_mode: bool = False):
        self.state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
        self.identity_manager = EchoStateManager(user_id=user_id, chat_id=None)
        self.debug_mode = debug_mode

    def calculate_invariant(self, role: str, content: Any, tool_io: dict = None) -> str:
        return self.state_manager.calculate_invariant_hash(role, content, tool_io)

    def calculate_cumulative(self, invariant: str, parent: str = None) -> str:
        return self.state_manager.calculate_cumulative_hash(invariant, parent)

    def get_shadow(self, message_id: str, updated_at: int) -> Optional[List[dict]]:
        """Restoration Bit-Perfect par ID physique et Timestamp (Verrou de Version)."""
        return self.state_manager.get_message_shadow(message_id, updated_at)

    def save_shadow(self, message_id: str, updated_at: int, parts: List[dict], chat_id: str, role: str):
        """Scellement définitif de l'ombre d'un message."""
        self.state_manager.save_message_shadow(message_id, chat_id, role, parts)

    def mark_processed(self, chat_id: str, fid: str, name: str, mime: str, status: str, content: Optional[str] = None, message_id: Optional[str] = None):
        """Scellement du registre des fichiers."""
        self.state_manager.mark_processed(chat_id, fid, name, mime, status, content, message_id)

    def get_rich_payload(self, invariant: str) -> Optional[List[dict]]:
        return self.state_manager.get_rich_payload(invariant)

    def save_rich_payload(self, invariant_hash: str, parts: List[dict], message_id: str = None):
        self.state_manager.save_rich_payload(invariant_hash, parts, message_id)

    def index_suture(self, cumul: str, chat_id: str, inv: str, parent: str = None, message_id: str = None):
        self.state_manager.index_suture(cumul, chat_id, inv, parent, message_id)

    def get_signature_by_id(self, message_id: str) -> Optional[str]:
        return self.state_manager.get_signature_by_id(message_id)

    def save_signature_by_id(self, message_id: str, signature: str):
        self.state_manager.save_signature_by_id(message_id, signature)

    def get_signature(self, cumul: str) -> Optional[str]:
        return self.state_manager.get_thought_signature(cumul)

    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        return self.state_manager.get_call_bridge(call_id)

    def save_cognitive(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None):
        self.state_manager.save_cognitive_data(cumul, sig, thought, tool_io, message_id)

    def save_call_bridge(self, call_id: str, sig: str, func_name: str, args: dict = None):
        self.state_manager.save_call_bridge(call_id, sig, func_name, args)

    def save_auth_data(self, key: str, value: str):
        self.identity_manager.save_auth_data(key, value)

    def get_auth_data(self, key: str) -> Optional[Tuple[str, int]]:
        return self.identity_manager.get_auth_data(key)

    def delete_auth_data(self, key: str):
        self.identity_manager.delete_auth_data(key)

    def save_context_stats(self, stats: dict):
        self.state_manager.save_context_stats(stats)

    def get_last_context_stats(self) -> dict:
        return self.state_manager.get_last_context_stats()

# ==============================================================================
# SECTION 5 : ORCHESTRATEUR (SUTURE & PROTOCOLE)
# ==============================================================================
class Orchestrator:
    def __init__(self, valves, user_valves, data_dir: str, user_id: str, chat_id: str = None):
        self.valves = valves; self.user_valves = user_valves
        self.user_data_manager = UserDataManager(user_id, chat_id, valves.DEBUG_MODE)
        self.tool_map = {}; self.logger = DebugLogger(data_dir, chat_id) if valves.DEBUG_MODE else None

    def _resolve_placeholders(self, text: str, model_id: str) -> str:
        if not isinstance(text, str): return text
        version = get_echo_version() or "##VERSION_ERR##"
        resolved = text.replace("##ECHO_VERSION##", version)
        resolved = resolved.replace("##GEMINI_ENGINE##", model_id)
        resolved = resolved.replace("##MODEL_ID##", model_id)
        return resolved

    def _ensure_gemini_parts(self, content: Any, model_id: str = "unknown") -> List[Dict]:
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
        if isinstance(content, str):
            try: content = orjson.loads(content)
            except: content = {"text": str(content), "status": {"status": "legacy_fallback"}}
        if not isinstance(content, dict): content = {"text": str(content), "status": {"status": "error_format"}}
        
        text_body = content.get("text", "")
        status_meta = content.get("status", {"status": "success"})
        rich_multiparts = content.get("echo_tool_multiparts", [])

        response_dict = status_meta.copy()
        if text_body:
            response_dict["result"] = self._resolve_placeholders(text_body, model_id)

        func_resp_part = {
            "functionResponse": {
                "name": name,
                "response": response_dict
            }
        }

        final_parts = [func_resp_part]
        for mp in rich_multiparts:
            m_type = mp.get("type")
            if m_type == "thought" and mp.get("content"): 
                response_dict["tool_thought"] = mp["content"]
            elif m_type == "media" and mp.get("data"): 
                final_parts.append({
                    "inlineData": {
                        "mimeType": mp.get("mime_type", "image/png"), 
                        "data": mp["data"]
                    }
                })
        return final_parts

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        if not tools: return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                funcs.append({"name": f.get("name"), "description": f.get("description", ""), "parameters": f.get("parameters", {"type": "object", "properties": {}})})
        return [{"functionDeclarations": funcs}] if funcs else None

    async def prepare_context(self, body: Dict, chat_id: str, target_model: str, __metadata__: Optional[Dict] = None, events: Optional[EchoEvents] = None) -> List[Dict]:
        """RESTAURATION Bit-Perfect avec Contrôle Temporel Strict (Anti-Ghosting)."""
        messages = body.get("messages", []); meta = __metadata__ or body.get("metadata", {})
        model_id = target_model
        final_contents = []; last_cumul = None
        i = 0
        while i < len(messages):
            m = messages[i]; role = m.get("role"); content = m.get("content", "")
            msg_id = m.get("id"); updated_at = m.get("updated_at")
            
            if role == "system" or any(x in str(content) for x in ["ECHO_SESSION_AUTH_PENDING", "Authentification ECHO"]) or str(content).startswith("4/"): 
                i += 1; continue

            # --- PRIORITÉ 1 : SHADOW BIT-PERFECT (ID + TIMESTAMP) ---
            if msg_id and updated_at:
                shadow_parts = self.user_data_manager.get_shadow(msg_id, updated_at)
                if shadow_parts:
                    role_gemini = "model" if role in ["assistant", "model"] else "user"
                    final_contents.append({"role": role_gemini, "parts": shadow_parts})
                    inv_hash = self.user_data_manager.calculate_invariant(role, shadow_parts)
                    last_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                    i += 1; continue

            # --- PRIORITÉ 2 : RECONSTRUCTION NORMALE (Fallback ou Cache Miss Temporel) ---
            restored_parts = []
            if role == "tool":
                aggregated_tool_parts = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    m_tool = messages[i]; content_tool = m_tool.get("content", "")
                    call_id = m_tool.get("tool_call_id"); bridge = self.user_data_manager.get_call_bridge(call_id)
                    func_name = bridge["name"] if bridge else "unknown"
                    rich_tool_parts = self._unbox_tool_output(func_name, content_tool, model_id)
                    aggregated_tool_parts.extend(rich_tool_parts)
                    inv_hash = self.user_data_manager.calculate_invariant("tool", content_tool)
                    last_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                    i += 1
                restored_parts = aggregated_tool_parts
                final_contents.append({"role": "user", "parts": restored_parts})
            
            else:
                # USER / ASSISTANT
                if role in ["assistant", "model"]:
                    content, _ = split_thought_process(content if isinstance(content, str) else str(content))

                draft_parts = meta.get("_echo_user_parts_draft") if (role == "user" and i == len(messages)-1) else None
                
                if role == "user":
                    if draft_parts is not None:
                        restored_parts = []
                        restored_parts.extend(self._ensure_gemini_parts(draft_parts, model_id))
                        user_text = content if isinstance(content, str) else ""
                        if user_text.strip(): restored_parts.append({"text": self._resolve_placeholders(user_text, model_id)})
                    else:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        restored_parts = self.user_data_manager.get_rich_payload(inv_hash) or self._ensure_gemini_parts(content, model_id)
                else:
                    # Assistant
                    sig = self.user_data_manager.get_signature_by_id(msg_id) if msg_id else None
                    if not sig:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        current_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                        sig = self.user_data_manager.get_signature(current_cumul)
                    
                    restored_parts = self._ensure_gemini_parts(content, model_id)
                    tool_calls = m.get("tool_calls", [])
                    if tool_calls:
                        restored_parts = [{"functionCall": {"name": tc["function"]["name"], "args": orjson.loads(tc["function"]["arguments"])}} for tc in tool_calls] + restored_parts
                    else:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        current_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                        tool_io = self.user_data_manager.state_manager.get_tool_io(current_cumul)
                        if tool_io: restored_parts = [{"functionCall": {"name": tc["name"], "args": tc["args"]}} for tc in tool_io.get("calls", [])] + restored_parts

                    if sig and restored_parts:
                        for p in restored_parts:
                            if "functionCall" in p: p["thoughtSignature"] = sig; break
                        else:
                            for p in restored_parts:
                                if "text" in p: p["thoughtSignature"] = sig; break
                    elif tool_calls:
                        for p in restored_parts:
                            if "functionCall" in p: p["thoughtSignature"] = MAGIC_KEY_SKIP_VALIDATION; break

                restored_parts = self._ensure_gemini_parts(restored_parts, model_id)
                role_gemini = "model" if role in ["assistant", "model"] else "user"
                final_contents.append({"role": role_gemini, "parts": restored_parts})
                i += 1

            # --- RÉPARATION DE LA SUTURE (Scellement immédiat pour le tour suivant) ---
            if msg_id and updated_at and restored_parts:
                self.user_data_manager.save_shadow(msg_id, updated_at, restored_parts, chat_id, role)

            inv_hash = self.user_data_manager.calculate_invariant(role, restored_parts)
            last_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)

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
        return {"index": tool_index, "id": tc_id, "type": "function", "function": {"name": func_call["name"], "arguments": std_json.dumps(func_call.get("args", {})).decode('utf-8')}}

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
                        if self.usage_stats is None: self.usage_stats = {}
                        self.usage_stats.update(target["usageMetadata"])
                    
                    if "remainingCredits" in target:
                        if self.usage_stats is None: self.usage_stats = {}
                        for credit in target["remainingCredits"]:
                            if credit.get("creditType") == "GOOGLE_ONE_AI":
                                self.usage_stats["google_credits"] = str(credit.get("creditAmount", "0"))
                                self.user_data_manager.save_auth_data('google_credits', self.usage_stats["google_credits"])
                                break
                    
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
                                if "<EPHEMERAL_MESSAGE>" in raw_t or "CRITICAL INSTRUCTION" in raw_t: continue
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
        HTTP_CLIENT_TIMEOUT: int = Field(default=600)
        HTTP_MAX_CONNECTIONS: int = Field(default=100); HTTP_MAX_KEEPALIVE: int = Field(default=20)
        HTTP_KEEPALIVE_EXPIRY: int = Field(default=300)
        DEBUG_MODE: bool = Field(default=False); MAX_CONTEXT_SIZE: int = Field(default=1048576)
        RETRY_TIMEBASE: int = Field(default=2); MAX_RETRIES: int = Field(default=5)
    class UserValves(BaseModel):
        SHOW_CONTEXT_METRICS: bool = Field(default=True)
        MODEL_SELECTION: Literal["gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "echo-auto", "echo-auto-pro"] = Field(default="echo-auto")
        PRO_THINKING_LEVEL: Literal["LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        FLASH_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        LITE_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        TEMPERATURE: float = Field(default=1.0); MAX_TOKENS: int = Field(default=65536)

    def __init__(self): self.valves, self.data_dir = self.Valves(), "/app/backend/data"

    def _evaluate_complexity_heuristics(self, prompt: str, estimated_prompt_tokens: int, history_tokens: int) -> int:
        score = 0
        if not prompt: return 0
        # 1. Marqueurs de Code (+4)
        if re.search(r"(\[|{)\s*\"|def\s+\w+\(|function\s*\(|class\s+\w+|```", prompt, re.IGNORECASE):
            score += 4
        # 2. Intention Cognitive (+3)
        if re.search(ECHO_COGNITIVE_TERMS, prompt, re.IGNORECASE):
            score += 3
        # 3. Taille du Prompt (+2)
        if estimated_prompt_tokens > 1000:
            score += 2
        # 4. Charge Contextuelle (+3)
        if (history_tokens + estimated_prompt_tokens) > 8000:
            score += 3
        return min(score, 10)

    async def _call_router_model(self, prompt: str, api_key: str) -> int:
        url = f"{GOOGLE_API_BASE_URL}/models/{MODEL_ROUTER}:generateContent?key={api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": "Évalue la complexité de cette question de 1 à 10. Ne réponds que par un chiffre."}]},
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 3}
        }
        try:
            async with httpx.AsyncClient(http2=True) as client:
                resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=3.0)
                if resp.status_code == 200:
                    data = resp.json()
                    text_response = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                    match = re.search(r"\d+", text_response)
                    if match: return min(max(int(match.group()), 1), 10)
        except: pass
        return 5 # Fallback sécurisé dans la zone grise

    async def _determine_best_model(self, messages: list, router_type: str, api_key: str, user_data_manager: UserDataManager) -> Tuple[str, int]:
        last_prompt = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_prompt = m.get("content", "")
                if isinstance(last_prompt, list):
                    last_prompt = " ".join([p.get("text", "") for p in last_prompt if isinstance(p, dict) and "text" in p])
                break
                
        estimated_prompt_tokens = len(str(last_prompt)) // 4
        stats = user_data_manager.get_last_context_stats()
        history_tokens = stats.get("totalTokenCount", 0)
        
        score = self._evaluate_complexity_heuristics(str(last_prompt), estimated_prompt_tokens, history_tokens)
        
        if 3 <= score <= 5 and len(str(last_prompt)) > 150:
            llm_score = await self._call_router_model(str(last_prompt), api_key)
            score = (score + llm_score) // 2
            
        target_model = MODEL_LITE
        if router_type == "echo-auto-pro":
            if score >= 7: target_model = MODEL_PRO
            elif score >= 4: target_model = MODEL_FLASH
        elif router_type == "echo-auto":
            if score >= 4: target_model = MODEL_FLASH
            
        return target_model, score

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __event_emitter__: Optional[any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        events = EchoEvents(__event_emitter__)
        if not __user__: yield "❌ Identité manquante."; return
        user_valves = __user__.get("valves") or self.UserValves()
        chat_id = body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, user_valves, self.data_dir, __user__["id"], chat_id)
        auth = AuthService(orch.user_data_manager)
        
        # --- [NOUVEAU] DETECTION ET INTERCEPTION DE CLÉ API ---
        api_key_from_filter = body.get("_api_key")
        
        if api_key_from_filter:
            await events.status("🔐 Validation de la clé API Google AI Studio...")
            success, msg = await auth.validate_and_save_api_key(api_key_from_filter)
            if success:
                yield "✅ **Configuration ECHO Réussie**\n\nVotre clé API Google AI Studio a été validée et enregistrée de manière sécurisée dans votre coffre-fort ECHO.\n\nVous pouvez maintenant poser votre question."
                return
            else:
                yield f"❌ **Échec de validation**\n\n{msg}\n\n" + auth.get_auth_prompt()
                return

        # --- [NOUVEAU] VÉRIFICATION DE PRÉSENCE ---
        api_key_data = auth.get_valid_credentials()
        if not api_key_data:
            yield auth.get_auth_prompt()
            return
        
        api_key = api_key_data[0]
        
        # --- [NOUVEAU] ROUTAGE DYNAMIQUE ---
        target_model = user_valves.MODEL_SELECTION
        if target_model in ["echo-auto", "echo-auto-pro"]:
            await events.status(f"🧠 Évaluation de la complexité en cours...")
            target_model, score = await self._determine_best_model(body.get("messages", []), target_model, api_key, orch.user_data_manager)
            await events.status(f"Model Auto ({score}/10) : {target_model}")
        else:
            await events.status(f"Model Fixé : {target_model}")
        
        # Reconstruction contexte
        context = await orch.prepare_context(body, chat_id, target_model, __metadata__, events)
        sys_instr = {"parts": [{"text": "\n".join([m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"]) or "Tu es ECHO."}]}
        
        # Sélection du niveau de réflexion selon le modèle
        if target_model == MODEL_PRO:
            think = user_valves.PRO_THINKING_LEVEL
        elif target_model == MODEL_LITE:
            think = user_valves.LITE_THINKING_LEVEL
        else:
            think = user_valves.FLASH_THINKING_LEVEL
        
        # URL et Payload adaptés pour AI Studio (Le modèle est dans l'URL)
        api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:streamGenerateContent?key={api_key}&alt=sse"
        payload = {
            "contents": context,
            "systemInstruction": sys_instr,
            "generationConfig": {
                "temperature": user_valves.TEMPERATURE,
                "maxOutputTokens": user_valves.MAX_TOKENS,
                "thinkingConfig": {
                    "includeThoughts": True,
                    "thinkingLevel": think.lower()
                }
            }
        }
        
        tools = orch.convert_owui_tools(body.get("tools"))
        if tools:
            payload["tools"] = tools
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        
        if orch.logger: orch.logger.log("google_request", payload)
        proc = StreamProcessor(orch.user_data_manager, chat_id, events, logger=orch.logger)
        client = await _get_global_client(self.valves.HTTP_CLIENT_TIMEOUT, self.valves.HTTP_MAX_CONNECTIONS, self.valves.HTTP_MAX_KEEPALIVE, self.valves.HTTP_KEEPALIVE_EXPIRY)
        
        current_delay = self.valves.RETRY_TIMEBASE
        headers = {
            "x-goog-api-key": api_key, 
            "Content-Type": "application/json", 
            "User-Agent": ECHO_USER_AGENT
        }

        for attempt in range(self.valves.MAX_RETRIES + 1):
            try:
                async with client.stream("POST", api_url, content=orjson.dumps(payload), headers=headers) as r:
                    if r.status_code in [429, 500, 503]:
                        if attempt < self.valves.MAX_RETRIES:
                            # Retry Jitter : +/- 30% de variation aléatoire
                            wait_time = current_delay * random.uniform(0.7, 1.3)
                            await events.status(f"⚠️ Surcharge API Google ({r.status_code}). Essai {attempt + 1}/{self.valves.MAX_RETRIES} dans {wait_time:.1f}s...")
                            await asyncio.sleep(wait_time)
                            current_delay *= 3
                            continue
                        else: yield f"❌ Erreur API Google ({r.status_code})."; return
                    r.raise_for_status()
                    
                    # Vérification HTTP/2
                    if r.http_version != "HTTP/2":
                        yield "❌ Erreur de protocole : HTTP/2 obligatoire pour Gemini AI Studio."; return
                        
                    async for chunk in proc.process(r): yield chunk
                break
            except Exception as e:
                if attempt < self.valves.MAX_RETRIES:
                    wait_time = current_delay * random.uniform(0.7, 1.3)
                    await events.status(f"⚠️ Instabilité réseau. Essai {attempt + 1}/{self.valves.MAX_RETRIES} dans {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                    current_delay *= 2
                    continue
                else: yield f"❌ Erreur système : {str(e)}"; return

        # --- SCELLEMENT FINAL (SUTURE DÉFINITIVE) ---
        if chat_id and proc.usage_stats:
            meta = __metadata__ or body.get("metadata", {})
            
            # 1. Scellement message Utilisateur (Le Draft complet)
            user_msg_id = meta.get("_echo_user_msg_id")
            user_updated_at = meta.get("_echo_user_msg_updated_at")
            user_draft = meta.get("_echo_user_parts_draft")
            if user_msg_id and user_draft:
                user_text = body['messages'][-1].get('content', "")
                full_user_parts = orch._ensure_gemini_parts(user_draft, target_model)
                if user_text: full_user_parts.append({"text": orch._resolve_placeholders(user_text, target_model)})
                orch.user_data_manager.save_shadow(user_msg_id, user_updated_at, full_user_parts, chat_id, "user")

            # 2. Scellement du Registre des Fichiers et Rangement
            files_to_seal = meta.get("_echo_files_to_seal", [])
            for f in files_to_seal:
                if f.get("status") == "success":
                    content_to_save = None
                    if f.get("type") == "summarized": content_to_save = f.get("content")
                    elif f.get("type") == "transmitted" and f.get("sub_type") == "text": content_to_save = f.get("content")
                    orch.user_data_manager.state_manager.mark_processed(chat_id, f['fid'], f['name'], f['mime'], f['type'], content_to_save, user_msg_id)
                    orch.user_data_manager.state_manager.move_to_vault(f['fid'], f['name'])

            # 3. Scellement Cognitif Assistant (Legacy Bridge & Tool Mapping)
            tool_io = {"calls": [{"name": c["name"], "args": c["args"]} for c in proc.accumulated_calls]} if proc.accumulated_calls else None
            inv = orch.user_data_manager.calculate_invariant("assistant", proc.accumulated_text, tool_io=tool_io)
            new_cumul = orch.user_data_manager.calculate_cumulative(inv, body.get("_echo_last_cumul"))
            orch.user_data_manager.save_cognitive(new_cumul, proc.captured_sig, None, tool_io)
            for c in proc.accumulated_calls: orch.user_data_manager.save_call_bridge(c["id"], proc.captured_sig, c["name"], c["args"])
            orch.user_data_manager.index_suture(new_cumul, chat_id, inv, body.get("_echo_last_cumul"))

        # --- HUD METRICS ---
        if user_valves.SHOW_CONTEXT_METRICS and proc.usage_stats:
            p_t = proc.usage_stats.get("promptTokenCount", 0)
            c_t = proc.usage_stats.get("cachedContentTokenCount", 0)
            g_t = proc.usage_stats.get("candidatesTokenCount", 0)
            max_t = self.valves.MAX_CONTEXT_SIZE
            
            plan_data = orch.user_data_manager.get_auth_data('google_plan_name')
            plan_name = plan_data[0] if plan_data else ""
            credits_data = orch.user_data_manager.get_auth_data('google_credits')
            credits_val = credits_data[0] if credits_data else "0"
            q_rem = orch.user_data_manager.get_auth_data('google_quota_remaining')
            q_lim = orch.user_data_manager.get_auth_data('google_quota_limit')
            q_rem_str = q_rem[0] if q_rem else "?"
            q_lim_str = q_lim[0] if q_lim else "?"
            quota_str = f"🎯 {q_rem_str}/{q_lim_str} req. | " if (q_rem_str != "?" and q_lim_str != "?") else ""
            
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
