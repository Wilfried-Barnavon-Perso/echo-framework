"""
title: ECHO Engine
author: Wilfried BARNAVON
version: 180.2
description: 180.2: Alignement final avec le Registre Cognitif v1.21 (Unification des UI).
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
from echo_utils import EchoEvents, EchoStateManager, get_echo_version, split_thought_process, EchoGeminiClient, _get_global_client, EchoUI
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

    def save_cognitive(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None, model_id: str = None):
        self.state_manager.save_cognitive_data(cumul, sig, thought, tool_io, message_id, model_id)

    def get_last_active_model(self) -> Optional[str]:
        return self.state_manager.get_last_active_model()

    def save_call_bridge(self, call_id: str, sig: str, func_name: str, args: dict = None):
        self.state_manager.save_call_bridge(call_id, sig, func_name, args)

    def save_auth_data(self, key: str, value: str):
        self.state_manager.save_auth_data(key, value)

    def save_context_stats(self, stats: dict):
        self.state_manager.save_context_stats(stats)

    def get_last_context_stats(self) -> dict:
        return self.state_manager.get_last_context_stats()

# ==============================================================================
# SECTION 5 : ORCHESTRATEUR (SUTURE & PROTOCOLE)
# ==============================================================================
class Orchestrator:
    def __init__(self, valves, user_valves, data_dir: str, user_id: str, chat_id: str = None, model_origin: str = "unknown"):
        self.valves = valves; self.user_valves = user_valves
        self.user_data_manager = UserDataManager(user_id, chat_id, valves.DEBUG_MODE)
        self.tool_map = {}; self.logger = DebugLogger(data_dir, chat_id) if valves.DEBUG_MODE else None
        self.model_origin = model_origin

    def _build_identity(self, m_id: str) -> str:
        if m_id == "aucun": return "aucun"
        cat = MODEL_IDENTITY.get(m_id, "UNKNOWN")
        return f"{cat} ({m_id})"

    def _resolve_placeholders(self, text: str, model_id: str) -> str:
        if not isinstance(text, str): return text
        version = get_echo_version() or "##VERSION_ERR##"
        resolved = text.replace("##ECHO_VERSION##", version)
        resolved = resolved.replace("##MODEL_ID##", self._build_identity(model_id))
        resolved = resolved.replace("##MODEL_ORIGIN##", self._build_identity(self.model_origin))
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
        self.escalation_requested = None

    def _create_tool_call_part(self, func_call: dict, tool_index: int) -> Optional[dict]:
        name = func_call["name"]
        args = func_call.get("args", {})
        if name == "changement_niveau_cognitif":
            self.escalation_requested = args
            return None
        tc_id = f"echo-{secrets.token_hex(8)}"
        self.accumulated_calls.append({"id": tc_id, "name": name, "args": args})
        return {"index": tool_index, "id": tc_id, "type": "function", "function": {"name": name, "arguments": std_json.dumps(args).decode('utf-8')}}

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
                                if tool_call:
                                    yield {"choices": [{"index": 0, "delta": {"tool_calls": [tool_call]}}]}
                                else:
                                    return # Escalade
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
        KEY_SWITCH_THRESHOLD: int = Field(default=2, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
    class UserValves(BaseModel):
        SHOW_CONTEXT_METRICS: bool = Field(default=True)
        MODEL_SELECTION: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO", "AUTO", "AUTO_PRO"] = Field(default="AUTO")
        PRO_THINKING_LEVEL: Literal["LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        FLASH_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        LITE_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH")
        TEMPERATURE: float = Field(default=1.0); MAX_TOKENS: int = Field(default=65536)
        MAX_CASCADE_ATTEMPTS: int = Field(default=5, ge=3, le=10, description="Nombre max de transferts de modèles autorisés par tour.")

    def __init__(self): self.valves, self.data_dir = self.Valves(), "/app/backend/data"

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __event_emitter__: Optional[any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        events = EchoEvents(__event_emitter__)
        if not __user__: yield "❌ Identité manquante."; return
        user_valves = __user__.get("valves") or self.UserValves()
        chat_id = body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, user_valves, self.data_dir, __user__["id"], chat_id)
        auth = AuthService(user_id=__user__["id"])
        from echo_utils import EchoAuth
        echo_auth = EchoAuth(user_id=__user__["id"])

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
        api_keys = echo_auth.get_api_keys()
        if not api_keys:
            yield auth.get_auth_prompt()
            return
        # On prend la première clé pour le routage dynamique initial
        api_key = api_keys[0]

        # --- [NOUVEAU] ROUTAGE DYNAMIQUE (Fluctuation Continue) ---
        model_selection = user_valves.MODEL_SELECTION
        last_model = orch.user_data_manager.get_last_active_model()
        
        if model_selection in ["AUTO", "AUTO_PRO"]:
            if last_model and last_model in [MODEL_LITE, MODEL_FLASH, MODEL_PRO]:
                target_model = last_model
                origine_model = last_model
                await events.status(f"🧠 Reprise du contexte ({target_model})...")
            else:
                target_model = MODEL_LITE
                origine_model = "aucun"
                await events.status(f"🧠 Initialisation de session (MODEL_LITE)...")
        else:
            # Résolution de l'étiquette UI vers le modèle technique via le Registre Global
            target_model = MODEL_ROUTING.get(model_selection, MODEL_LITE)
            origine_model = last_model if last_model else "aucun"
            await events.status(f"Model Fixé : {target_model}")

        # L'origine est passée à l'Orchestrateur pour la résolution des placeholders
        orch.model_origin = origine_model
        
        # Reconstruction contexte (Bit-Perfect)
        context = await orch.prepare_context(body, chat_id, target_model, __metadata__, events)

        # --- [NOUVEAU] CONFIGURATION CASCADE ---
        is_auto = user_valves.MODEL_SELECTION in ["AUTO", "AUTO_PRO"]
        # Détermination des niveaux autorisés pour le schéma de l'outil (Approach: Clean Prompt)
        niveaux_autorises = ["MODEL_FLASH"]
        if user_valves.MODEL_SELECTION == "AUTO_PRO": niveaux_autorises.append("MODEL_PRO")
        
        max_cascade_attempts = user_valves.MAX_CASCADE_ATTEMPTS
        cascade_attempt = 0
        
        while cascade_attempt < max_cascade_attempts:
            cascade_attempt += 1
            
            # --- [NOUVEAU] RÉSOLUTION DYNAMIQUE DES INSTRUCTIONS SYSTÈME ---
            sys_instr_raw = "\n".join([m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"]) or "Tu es ECHO."
            resolved_sys = orch._resolve_placeholders(sys_instr_raw, target_model)
            sys_instr = {"parts": [{"text": resolved_sys}]}

            # Sélection du niveau de réflexion selon le modèle
            if target_model == MODEL_PRO:
                think = user_valves.PRO_THINKING_LEVEL
            elif target_model == MODEL_LITE:
                think = user_valves.LITE_THINKING_LEVEL
            else:
                think = user_valves.FLASH_THINKING_LEVEL

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
            
            # --- [NOUVEAU] INJECTION OUTIL CHANGEMENT COGNITIF (BIDIRECTIONNEL) ---
            if is_auto:
                # Menu évolutif selon le modèle actuel
                menu_escalade = []
                if target_model == MODEL_LITE:
                    menu_escalade = ["MODEL_FLASH"]
                    if user_valves.MODEL_SELECTION == "AUTO_PRO": menu_escalade.append("MODEL_PRO")
                elif target_model == MODEL_FLASH:
                    menu_escalade = ["MODEL_LITE"]
                    if user_valves.MODEL_SELECTION == "AUTO_PRO": menu_escalade.append("MODEL_PRO")
                elif target_model == MODEL_PRO:
                    menu_escalade = ["MODEL_LITE", "MODEL_FLASH"]
                
                if menu_escalade:
                    escalation_tool = {
                        "name": "changement_niveau_cognitif",
                        "description": (
                            "Ajuste la puissance de calcul d'ECHO selon la nature de la tâche et la charge contextuelle.\n\n"
                            "1. Lois de Sélection du Modèle :\n"
                            "- MODEL_LITE (Réflexe) : Salutations, remerciements, extractions simples, traduction courte, questions de culture générale basiques.\n"
                            "- MODEL_FLASH (Exécution) : Recherche web, écriture de scripts/fonctions isolés, analyse sémantique de fichiers unitaires, synthèse de documents, exécution d'outils simples.\n"
                            "- MODEL_PRO (Expertise) : Architectures, orchestration de tâche, exécution d'outils complexes, refactoring multi-fichiers, logique mathématique complexe, philosophie profonde.\n\n"
                            "2. Loi de Corrélation Contextuelle (Vallée de la Mort) :\n"
                            "Plus le contexte (tokens) est chargé, plus le niveau cognitif doit être élevé, indépendamment de la simplicité apparente de la tâche.\n"
                            "- [0-25%] (SAFE) : Le modèle actuel traite la tâche si elle correspond à sa catégorie.\n"
                            "- [25-50%] (WARNING) : Si vous êtes en MODEL_LITE/MODEL_FLASH, privilégiez une montée d'un cran pour éviter la dérive sémantique.\n"
                            "- [> 50%] (CRITICAL) : Délégation impérative au plus haut niveau (MODEL_PRO/MODEL_FLASH) pour tout traitement exigeant la lecture de l'historique lointain.\n\n"
                            "Usage : Utilisez context_gauge pour situer votre position dans la Vallée de la Mort avant de décider."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "niveau_requis": {"type": "string", "enum": menu_escalade},
                                "plan_de_transfert": {"type": "string", "description": "Plan Markdown structuré (Objectif, Analyse, Stratégie, Contraintes)."},
                                "raison": {"type": "string"}
                            },
                            "required": ["niveau_requis", "plan_de_transfert"]
                        }
                    }
                    if not tools: tools = [{"functionDeclarations": []}]
                    tools[0]["functionDeclarations"].append(escalation_tool)

            if tools:
                payload["tools"] = tools
                payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

            if orch.logger: orch.logger.log("google_request", payload, metadata={"cascade_attempt": cascade_attempt, "model": target_model})
            proc = StreamProcessor(orch.user_data_manager, chat_id, events, logger=orch.logger)

            # Tentative d'appel au moteur Gemini
            try:
                # Appel au client factorisé avec gestion du fallback
                async for chunk in EchoGeminiClient.stream(
                    keys=api_keys,
                    target_model=target_model,
                    payload=payload,
                    threshold=self.valves.KEY_SWITCH_THRESHOLD,
                    max_retries=self.valves.MAX_RETRIES,
                    events=events,
                    process_callback=proc.process,
                    timeout=self.valves.HTTP_CLIENT_TIMEOUT
                ):
                    # On ne yield le texte que si aucune escalade n'est en cours (Gateway Pattern)
                    if isinstance(chunk, dict) or not proc.escalation_requested:
                        yield chunk
            except Exception as e:
                # GESTION DES ÉCHECS TECHNIQUES (API/RÉSEAU)
                if is_auto and cascade_attempt < max_cascade_attempts:
                    await events.status(f"⚠️ Indisponibilité technique du modèle cible. Repli...")
                    # Suture d'échec technique pour le modèle actuel
                    context.append({
                        "role": "user",
                        "parts": [{"text": f"### ⚠️ ERREUR SYSTÈME\nL'appel au modèle expert a échoué ({str(e)}). Veuillez poursuivre le traitement avec vos ressources actuelles ou proposer une alternative."}]
                    })
                    continue # On reboucle avec le même modèle (target_model n'a pas encore changé)
                else:
                    yield f"❌ Erreur critique lors de la communication API : {str(e)}"
                    break

            # --- [NOUVEAU] GESTION DE LA CASCADE ---
            if is_auto and proc.escalation_requested:
                req = proc.escalation_requested
                target_req = req.get("niveau_requis")
                
                # Mapping explicite pour gérer la montée ET la redescente
                new_target = MODEL_ROUTING.get(target_req)
                
                if not new_target:
                    # Signalement d'erreur de paramètre au modèle actuel
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "changement_niveau_cognitif", "args": req}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "changement_niveau_cognitif", "response": {"status": "error", "message": f"ERREUR : Niveau '{target_req}' inconnu. Choisissez parmi MODEL_LITE, MODEL_FLASH ou MODEL_PRO."}}}]
                    })
                    continue
                
                # Vérification des droits (Valve)
                if user_valves.MODEL_SELECTION == "AUTO" and new_target == MODEL_PRO:
                    await events.status(f"⚠️ Transfert vers MODEL_PRO refusé (Valve AUTO).")
                    # Signalement de refus au modèle actuel
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "changement_niveau_cognitif", "args": req}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "changement_niveau_cognitif", "response": {"status": "denied", "message": "ÉCHEC : Le transfert vers MODEL_PRO est refusé par la configuration utilisateur (Valve AUTO). Veuillez traiter la demande immédiatement avec vos capacités actuelles."}}}]
                    })
                    continue # On reboucle avec le MÊME target_model
                
                if new_target == target_model:
                    break

                await events.status(f"🚀 Transfert cognitif vers {new_target}...")
                
                if proc.captured_sig:
                    orch.user_data_manager.save_call_bridge(f"esc-{secrets.token_hex(4)}", proc.captured_sig, "changement_niveau_cognitif", req)
                
                plan_md = req.get("plan_de_transfert", "Exécution du relais.")
                
                # 1. Mutation Chirurgicale de l'identité dans le contexte via Regex
                identity_format = orch._build_identity(new_target)
                origin_format = orch._build_identity(target_model)

                for part in context[-1]["parts"]:
                    if "text" in part and "modèle_actuel" in part["text"]:
                        # Remplacement de l'identité actuelle
                        part["text"] = re.sub(r'("modèle_actuel"\s*:\s*")[^"]+(")', rf'\g<1>{identity_format}\g<2>', part["text"])
                        # Mise à jour de l'origine pour le modèle suivant
                        part["text"] = re.sub(r'("modèle_origine"\s*:\s*")[^"]+(")', rf'\g<1>{origin_format}\g<2>', part["text"])
                
                # Mise à jour de l'état de l'orchestrateur pour les placeholders système
                orch.model_origin = target_model
                
                # 2. Suture Sémantique (Relais Protocolé)
                context.append({
                    "role": "model",
                    "parts": [
                        {"functionCall": {"name": "changement_niveau_cognitif", "args": req}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}
                    ]
                })
                context.append({
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": "changement_niveau_cognitif", "response": {"status": "success", "message": f"Relais vers {new_target} activé. Exécutez le plan maintenant.", "plan": plan_md}}}
                    ]
                })

                target_model = new_target
                continue
            else:
                # Pas d'escalade demandée, on sort de la boucle de cascade
                break

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
            orch.user_data_manager.save_cognitive(new_cumul, proc.captured_sig, None, tool_io, user_msg_id, target_model)
            for c in proc.accumulated_calls: orch.user_data_manager.save_call_bridge(c["id"], proc.captured_sig, c["name"], c["args"])
            orch.user_data_manager.index_suture(new_cumul, chat_id, inv, body.get("_echo_last_cumul"))

        # --- HUD METRICS ---
        if user_valves.SHOW_CONTEXT_METRICS and proc.usage_stats:
            p_t = proc.usage_stats.get("promptTokenCount", 0)
            c_t = proc.usage_stats.get("cachedContentTokenCount", 0)
            g_t = proc.usage_stats.get("candidatesTokenCount", 0)
            max_t = self.valves.MAX_CONTEXT_SIZE
            
            plan_data = echo_auth.get_api_keys() # Fallback for now
            plan_name = "ECHO Standard" if plan_data else ""
            credits_val = "0"
            q_rem_str = "?"
            q_lim_str = "?"
            quota_str = f"🎯 {q_rem_str}/{q_lim_str} req. | " if (q_rem_str != "?" and q_lim_str != "?") else ""
            
            active_p_t = max(0, p_t - c_t)
            cache_pct = min(100, (c_t / max_t) * 100)
            prompt_pct = min(100, (active_p_t / max_t) * 100)
            gen_pct = min(100, (g_t / max_t) * 100)

            await EchoUI.deploy_context_gauge(
                events=events, plan_name=plan_name, credits_val=credits_val, quota_str=quota_str,
                c_t=c_t, active_p_t=active_p_t, g_t=g_t, max_t=max_t,
                cache_pct=cache_pct, prompt_pct=prompt_pct, gen_pct=gen_pct
            )
