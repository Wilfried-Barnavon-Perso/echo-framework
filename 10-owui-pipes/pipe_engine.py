"""
title: ECHO Engine
author: Wilfried BARNAVON
version: 192.1
requirements: asyncssh
description: 190.8: Migration type 'summarized' -> 'rag_ephemeral'.
             191.0: Remplacement Device Flow par PKCE + Authorization Code.
             191.1: Fix regression auth - callback PKCE background task.
             192.0: Tunnel SSH ephemere asyncssh pour callback OAuth2 PKCE.
             Ports dynamiques multi-user. __request__ injecte pour detection IP.
             192.1: Centralisation des paramètres de génération — suppression valves
             TEMPERATURE/TOP_P/THINKING_LEVEL. Remplacement par constantes echo_constants.
             192.2: import get_ca_model_id, HUD quota par modèle CA courant, crédits réels, reset en minutes.
             192.3: HUD : RPD, RPM et modèle CA ajoutés au tooltip quota. Extraction RPD, RPM et modèle CA depuis model_quota, passage à deploy_context_gauge.
             192.4: Docstring new_cognitive_level : FLASH = moteur agentique, PRO = dernier recours.
             Valence de la Mort atténuée par RAG éphémère (memorize_that).
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
import ast
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, EchoStateManager, get_echo_version, split_thought_process, EchoGeminiClient, _get_global_client, get_ca_model_id
from echo_ui import EchoUI
from echo_constants import (
    MODEL_PRO, MODEL_FLASH, MODEL_LITE, MODEL_ROUTING, MODEL_IDENTITY,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES, ECHO_RETRY_BASE_DELAY,
    TEMP_DEFAULT, TOP_P_DEFAULT,
    THINKING_LEVEL_PRO, THINKING_LEVEL_FLASH, THINKING_LEVEL_LITE,
    MAX_TOKENS_DEFAULT
)
from echo_auth import AuthService

# --- IMPORTATIONS TIERCES CRITIQUES ---
try:
    import httpx
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
        self.state_manager.save_message_shadow(message_id, chat_id, role, parts, updated_at)

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
            try:
                # Utilisation du lecteur Python sécurisé (ast) pour gérer les guillemets simples du stockage SQL
                content = ast.literal_eval(content)
            except:
                # Échec total de lecture : marquage comme donnée non structurée
                content = {"text": str(content), "status": {"status": "unstructured_data"}}
        
        if not isinstance(content, dict):
            content = {"text": str(content), "status": {"status": "error_format"}}
        
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
        return [{"function_declarations": funcs}] if funcs else None

    async def prepare_context(self, body: Dict, chat_id: str, target_model: str, __metadata__: Optional[Dict] = None, events: Optional[EchoEvents] = None) -> List[Dict]:
        """RESTAURATION Bit-Perfect avec Contrôle Temporel Strict (Anti-Ghosting)."""
        messages = body.get("messages", []); meta = __metadata__ or body.get("metadata", {})
        model_id = target_model
        final_contents = []; last_cumul = None
        i = 0
        while i < len(messages):
            m = messages[i]; role = m.get("role"); content = m.get("content", "")
            msg_id = m.get("id"); updated_at = m.get("updated_at")
            # Exclusion des messages système et des tours d'authentification (ne pas injecter dans le contexte Gemini)
            auth_markers = ["ECHO_SESSION_AUTH_PENDING", "Authentification ECHO", "Authentification requise", "Antigravity 2.1", "PKCE"]
            if role == "system" or any(x in str(content) for x in auth_markers):
                i += 1; continue

            # --- PRIORITÉ 1 : SHADOW BIT-PERFECT (ID + TIMESTAMP) ---
            if msg_id and updated_at:
                shadow_data = self.user_data_manager.get_shadow(msg_id, updated_at)
                if shadow_data:
                    # Support des Shadows Multi-Messages (Cascade Cognitive)
                    if isinstance(shadow_data, list) and len(shadow_data) > 0 and "role" in shadow_data[0]:
                        for msg in shadow_data:
                            final_contents.append(msg)
                            inv_hash = self.user_data_manager.calculate_invariant(msg["role"], msg["parts"])
                            last_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                        i += 1; continue
                    else:
                        # Shadow Classique (Parts uniquement)
                        role_gemini = "model" if role in ["assistant", "model"] else "user"
                        final_contents.append({"role": role_gemini, "parts": shadow_data})
                        inv_hash = self.user_data_manager.calculate_invariant(role, shadow_data)
                        last_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                        i += 1; continue

            # --- PRIORITÉ 2 : RECONSTRUCTION NORMALE (Fallback ou Cache Miss Temporel) ---
            restored_parts = []
            role_gemini = "model" if role in ["assistant", "model"] else "user"

            if role == "tool":
                aggregated_tool_parts = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    m_tool = messages[i]; content_tool = m_tool.get("content", "")
                    call_id = m_tool.get("tool_call_id"); bridge = self.user_data_manager.get_call_bridge(call_id)
                    func_name = bridge["name"] if bridge else "unknown"
                    rich_tool_parts = self._unbox_tool_output(func_name, content_tool, model_id)
                    aggregated_tool_parts.extend(rich_tool_parts)
                    i += 1
                restored_parts = aggregated_tool_parts
                role_gemini = "user" # Les réponses d'outils sont toujours 'user' pour Gemini
            
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
                        restored_parts = [{"functionCall": {"name": tc["function"]["name"], "args": std_json.loads(tc["function"]["arguments"])}} for tc in tool_calls] + restored_parts
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
                i += 1

            # --- RÉPARATION DE LA SUTURE (Scellement immédiat pour le tour suivant) ---
            final_contents.append({"role": role_gemini, "parts": restored_parts})
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
        if name == "new_cognitive_level":
            self.escalation_requested = args
            return None
        tc_id = f"echo-{secrets.token_hex(8)}"
        self.accumulated_calls.append({"id": tc_id, "name": name, "args": args})
        return {"index": tool_index, "id": tc_id, "type": "function", "function": {"name": name, "arguments": std_json.dumps(args).decode('utf-8')}}

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False; buffer = ""; decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        buffered_lines = []
        async for chunk in response.aiter_bytes():
            buffer += decoder.decode(chunk, final=False)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1); line = line.strip()
                if line.startswith("data:"):
                    buffered_lines.append(line[6:].strip())
                elif line == "" and buffered_lines:
                    # Fin d'un bloc SSE : accumulation et parsing du JSON complet
                    full_json_str = "\n".join(buffered_lines)
                    buffered_lines = []
                    try:
                        data = std_json.loads(full_json_str); self.full_raw_accumulator.append(data)
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
        RETRY_TIMEBASE: int = Field(default=ECHO_RETRY_BASE_DELAY)
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES)
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
    class UserValves(BaseModel):
        SHOW_CONTEXT_METRICS: bool = Field(default=True)
        MODEL_SELECTION: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO", "AUTO", "AUTO_PRO"] = Field(default="AUTO")
        # Thinking, temperature, topP et max_tokens sont des constantes ECHO (echo_constants.py v4.8).
        # Plus de valves pour ces paramètres.
        MAX_CASCADE_ATTEMPTS: int = Field(default=5, ge=3, le=10, description="Nombre max de transferts de modèles autorisés par tour.")

    def __init__(self): self.valves, self.data_dir = self.Valves(), "/app/backend/data"

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __event_emitter__: Optional[any] = None, __request__: Optional[Any] = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        events = EchoEvents(__event_emitter__)
        if not __user__: yield "❌ Identité manquante."; return
        user_valves = __user__.get("valves") or self.UserValves()
        chat_id = kwargs.get("__chat_id__") or body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, user_valves, self.data_dir, __user__["id"], chat_id)
        auth = AuthService(user_id=__user__["id"])
        from echo_utils import EchoAuth
        echo_auth = EchoAuth(user_id=__user__["id"])

        # --- [NOUVEAU] DETECTION ET INTERCEPTION DE CLÉ API ---
        api_key_from_filter = body.get("_api_key")

        # Résolution du Registre des Fournisseurs d'Accès (Cache local pour ce tour de pipe)
        auth_providers = await echo_auth.get_ordered_auth_providers(__user__["id"])

        if api_key_from_filter:
            await events.status("🔐 Validation de l'authentification Google...")
            success, msg = await auth.validate_and_save_api_key(api_key_from_filter)
            if success:
                yield (
                    "✅ **Configuration d'accès ECHO Configurée avec Succès**\n\n"
                    f"{msg}\n\n"
                    "Vos accès Google ont été validés et enregistrés de manière sécurisée dans votre Espace Personnel ECHO.\n\n"
                    "Vous pouvez maintenant poser votre question."
                )
                return
            else:
                yield f"❌ **Échec de validation**\n\n{msg}\n\n" + auth.get_auth_prompt()
                return

        # --- AUTHENTIFICATION PKCE (Authorization Code + PKCE RFC 7636) ---
        # Tunnel SSH ephemere asyncssh - ports dynamiques - multi-user natif.
        if not auth_providers:
            from echo_utils import EchoAuth as _EchoAuth
            _ea = _EchoAuth(user_id=__user__["id"])
            pkce_pending = _ea.get_auth_data("pkce_status") == "pending"

            try:
                if pkce_pending:
                    # Flow deja en cours - reafficher les instructions
                    stored_url = _ea.get_auth_data("pkce_auth_url") or ""
                    yield (
                        f"\U0001f510 **Authentification en attente**\n\n"
                        f"Le lien est toujours valide.\n\n"
                        f"[\U0001f517 **Autoriser ECHO avec Google**]({stored_url})\n\n"
                        f"---\n"
                        f"*Ou collez directement une cl\u00e9 `AIza\u2026` pour utiliser AI Studio.*"
                    )
                    return

                await events.status("\U0001f510 Lancement authentification PKCE...")
                ok, auth_url, server_ip, ssh_port, cb_port, temp_pwd = \
                    await auth.initiate_pkce_flow(request=__request__)
                if not ok:
                    yield f"\u274c Impossible de lancer le flow PKCE.\n\n" + auth.get_auth_prompt()
                    return

                # Persister l'URL pour les messages suivants
                _ea.save_api_key("pkce_auth_url", auth_url)

                # Lancer le serveur callback en background (non bloquant)
                asyncio.create_task(auth.await_pkce_callback())

                yield auth.get_auth_prompt(
                    auth_url  = auth_url,
                    server_ip = server_ip,
                    ssh_port  = ssh_port,
                    cb_port   = cb_port,
                    temp_pwd  = temp_pwd,
                )

            except Exception as e:
                yield f"\u274c Erreur PKCE : {str(e)}\n\n" + auth.get_auth_prompt()
            return

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
        cumulative_usage_stats = {"promptTokenCount": 0, "cachedContentTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0}
        
        # [NOUVEAU] HISTORIQUE DE CASCADE POUR SUTURE & SHADOW
        cascade_history = [] 
        current_cumul = body.get("_echo_last_cumul")
        user_msg_id = (__metadata__ or {}).get("_echo_user_msg_id")

        while cascade_attempt < max_cascade_attempts:
            cascade_attempt += 1
            
            # --- [NOUVEAU] RÉSOLUTION DYNAMIQUE DES INSTRUCTIONS SYSTÈME ---
            sys_instr_raw = "\n".join([m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"]) or "Tu es ECHO."
            resolved_sys = orch._resolve_placeholders(sys_instr_raw, target_model)
            sys_instr = {"parts": [{"text": resolved_sys}]}

            # Sélection du niveau de réflexion par constante ECHO (echo_constants.py v4.8)
            if target_model == MODEL_PRO:
                think = THINKING_LEVEL_PRO
            elif target_model == MODEL_LITE:
                think = THINKING_LEVEL_LITE
            else:
                think = THINKING_LEVEL_FLASH

            payload = {
                "contents": context,
                "systemInstruction": sys_instr,
                "generationConfig": {
                    "temperature": TEMP_DEFAULT,
                    "topP": TOP_P_DEFAULT,
                    "maxOutputTokens": MAX_TOKENS_DEFAULT,
                    "thinkingConfig": {
                        "includeThoughts": True,
                        "thinkingLevel": think
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
                        "name": "new_cognitive_level",
                        "description": (
                            "Ajuste la puissance de calcul d'ECHO selon la nature de la tâche.\n\n"
                            "## Règles de sélection\n"
                            "- **MODEL_LITE** (Réflexe — défaut) : Salutations, remerciements, extractions simples, "
                            "traduction courte, questions factuelles basiques.\n"
                            "- **MODEL_FLASH** (Exécution — moteur agentique) : Toute tâche non-triviale. "
                            "Recherche web, écriture de code, analyse sémantique, synthèse de documents, "
                            "orchestration d'outils, réponses structurées, raisonnement multi-étapes ordinaire.\n"
                            "  \u2192 Préférer FLASH dès que la tâche dépasse le simple réflexe.\n"
                            "- **MODEL_PRO** (Expertise — dernier recours) : Uniquement si FLASH est "
                            "manifestement insuffisant. Architectures systèmes complexes, refactoring multi-fichiers "
                            "avec contraintes imbriquées, logique mathématique formelle, raisonnement philosophique profond.\n"
                            "  \u2192 Ne pas escalader vers PRO par défaut ou par précaution. Justifier explicitement l'insuffisance de FLASH.\n\n"
                            "## Corrélation contextuelle\n"
                            "La saturation contextuelle est atténuée par le RAG éphémère (memorize_that stocke "
                            "les éléments critiques en mémoire rapide). Vigilance utile à haute charge (> 50%) "
                            "pour les tâches à lecture profonde de l'historique — préférer alors FLASH ou PRO."
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
                    if not tools: tools = [{"function_declarations": []}]
                    tools[0]["function_declarations"].append(escalation_tool)

            if tools:
                payload["tools"] = tools
                payload["tool_config"] = {"function_calling_config": {"mode": "AUTO"}}

            if orch.logger: orch.logger.log("google_request", payload, metadata={"cascade_attempt": cascade_attempt, "model": target_model})
            proc = StreamProcessor(orch.user_data_manager, chat_id, events, logger=orch.logger)

            # Tentative d'appel au moteur Gemini
            try:
                # Appel au client factorisé (Agnostique)
                async for chunk in EchoGeminiClient.stream(
                    target_model=target_model,
                    payload=payload,
                    user_id=__user__["id"],
                    threshold=self.valves.KEY_SWITCH_THRESHOLD,
                    max_retries=self.valves.MAX_RETRIES,
                    events=events,
                    process_callback=proc.process,
                    timeout=self.valves.HTTP_CLIENT_TIMEOUT,
                    chat_id=chat_id
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

            # --- [NOUVEAU] ACCUMULATION DES TOKENS (SOUVERAINETÉ) ---
            if proc.usage_stats:
                for k in cumulative_usage_stats:
                    cumulative_usage_stats[k] += proc.usage_stats.get(k, 0)

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
                        "parts": [{"functionCall": {"name": "new_cognitive_level", "args": req}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "error", "message": f"ERREUR : Niveau '{target_req}' inconnu. Choisissez parmi MODEL_LITE, MODEL_FLASH ou MODEL_PRO."}}}]
                    })
                    continue
                
                # Vérification des droits (Valve)
                if user_valves.MODEL_SELECTION == "AUTO" and new_target == MODEL_PRO:
                    await events.status(f"⚠️ Transfert vers MODEL_PRO refusé (Valve AUTO).")
                    # Signalement de refus au modèle actuel
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "new_cognitive_level", "args": req}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "denied", "message": "ÉCHEC : Le transfert vers MODEL_PRO est refusé par la configuration utilisateur (Valve AUTO). Veuillez traiter la demande immédiatement avec vos capacités actuelles."}}}]
                    })
                    continue # On reboucle avec le MÊME target_model
                
                if new_target == target_model:
                    break

                await events.status(f"🚀 Transfert cognitif vers {new_target}...")
                
                if proc.captured_sig:
                    orch.user_data_manager.save_call_bridge(f"esc-{secrets.token_hex(4)}", proc.captured_sig, "new_cognitive_level", req)
                
                plan_md = req.get("plan_de_transfert", "Exécution du relais.")
                
                # 1. Mutation Chirurgicale de l'identité dans le contexte via Regex
                identity_format = orch._build_identity(new_target)
                origin_format = orch._build_identity(target_model)

                for part in context[-1]["parts"]:
                    if "text" in part and "modèle_actuel" in part["text"]:
                        # Remplacement de l'identité actuelle (Support hybride JSON/YAML)
                        part["text"] = re.sub(r'("?modèle_actuel"?\s*:\s*"?)([^"\n]+)("?)', rf'\g<1>{identity_format}\g<3>', part["text"])
                        # Mise à jour de l'origine pour le modèle suivant (Support hybride JSON/YAML)
                        part["text"] = re.sub(r'("?modèle_origine"?\s*:\s*"?)([^"\n]+)("?)', rf'\g<1>{origin_format}\g<3>', part["text"])
                
                # Mise à jour de l'état de l'orchestrateur pour les placeholders système
                orch.model_origin = target_model
                
                # 2. Suture Sémantique (Relais Protocolé avec réinjection signée du texte précédent)
                sig_to_apply = proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION
                model_parts = []
                if proc.accumulated_text:
                    model_parts.append({"text": proc.accumulated_text, "thoughtSignature": sig_to_apply})

                model_parts.append({"functionCall": {"name": "new_cognitive_level", "args": req}, "thoughtSignature": sig_to_apply})

                # [NOUVEAU] INDEXATION INTERMÉDIAIRE (SUTURE)
                model_msg = {"role": "model", "parts": model_parts}
                inv = orch.user_data_manager.calculate_invariant("model", model_parts)
                new_cumul = orch.user_data_manager.calculate_cumulative(inv, current_cumul)
                orch.user_data_manager.index_suture(new_cumul, chat_id, inv, current_cumul, user_msg_id)
                orch.user_data_manager.save_cognitive(new_cumul, sig_to_apply, proc.accumulated_text, None, user_msg_id, target_model)
                cascade_history.append(model_msg)
                current_cumul = new_cumul

                user_resp_parts = [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "success", "message": f"Relais vers {new_target} activé. Exécutez le plan maintenant.", "plan": plan_md}}}]
                user_msg = {"role": "user", "parts": user_resp_parts}
                inv_u = orch.user_data_manager.calculate_invariant("user", user_resp_parts)
                new_cumul_u = orch.user_data_manager.calculate_cumulative(inv_u, current_cumul)
                orch.user_data_manager.index_suture(new_cumul_u, chat_id, inv_u, current_cumul, user_msg_id)
                cascade_history.append(user_msg)
                current_cumul = new_cumul_u

                context.append(model_msg)
                context.append(user_msg)
                
                target_model = new_target
                continue
            else:
                # [NOUVEAU] INDEXATION FINALE (SUTURE)
                sig_to_apply = proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION
                tool_io = {"calls": [{"name": c["name"], "args": c["args"]} for c in proc.accumulated_calls]} if proc.accumulated_calls else None
                model_parts = []
                if proc.accumulated_text:
                    model_parts.append({"text": proc.accumulated_text, "thoughtSignature": sig_to_apply})
                if proc.accumulated_calls:
                    for c in proc.accumulated_calls:
                        model_parts.append({"functionCall": {"name": c["name"], "args": c["args"]}, "thoughtSignature": sig_to_apply})
                
                if model_parts:
                    model_msg = {"role": "model", "parts": model_parts}
                    inv = orch.user_data_manager.calculate_invariant("model", model_parts, tool_io=tool_io)
                    new_cumul = orch.user_data_manager.calculate_cumulative(inv, current_cumul)
                    orch.user_data_manager.index_suture(new_cumul, chat_id, inv, current_cumul, user_msg_id)
                    orch.user_data_manager.save_cognitive(new_cumul, sig_to_apply, proc.accumulated_text, tool_io, user_msg_id, target_model)
                    cascade_history.append(model_msg)
                    current_cumul = new_cumul

                # Pas d'escalade demandée, on sort de la boucle de cascade
                break

        # --- SCELLEMENT FINAL (SUTURE DÉFINITIVE) ---

        if chat_id:
            meta = __metadata__ or body.get("metadata", {})
            
            # 1. Scellement message Utilisateur (Le Draft complet)
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
                    if f.get("type") == "rag_ephemeral": content_to_save = f.get("content")
                    elif f.get("type") == "transmitted" and f.get("sub_type") == "text": content_to_save = f.get("content")
                    orch.user_data_manager.state_manager.mark_processed(chat_id, f['fid'], f['name'], f['mime'], f['type'], content_to_save, user_msg_id)
                    orch.user_data_manager.state_manager.move_to_vault(f['fid'], f['name'])

            # 3. Scellement Ombre de l'Assistant (Multi-Messages Cascade)
            # On utilise le message_id de l'assistant (qui sera créé par Open WebUI au retour)
            # Note: OWUI ne fournit pas l'ID de l'assistant à l'avance, on utilise une heuristique de suture
            # Mais ECHO stocke l'historique de cascade dans l'ombre du message_id s'il est dispo,
            # ou laisse prepare_context reconstruire via le cumulative_hash final.
            # ACTION : Sauvegarder l'historique complet dans le Shadow du message assistant si on a un ID.
            # En l'absence d'ID (courant pour la réponse en cours), la suture indexée via current_cumul suffit.
            # Si on a un ID dans kwargs (ex: retry), on scelle.
            asst_msg_id = kwargs.get("__message_id__")
            if asst_msg_id and cascade_history:
                orch.user_data_manager.save_shadow(asst_msg_id, int(time.time()), cascade_history, chat_id, "assistant")
            
            # Sauvegarde des ponts d'outils pour la navigation future
            for c in proc.accumulated_calls: 
                orch.user_data_manager.save_call_bridge(c["id"], proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION, c["name"], c["args"])


        # --- HUD METRICS ---
        if user_valves.SHOW_CONTEXT_METRICS:
            # Rafraîchissement intelligent des quotas (OAuth2 uniquement) - ASYNCHRONE NON BLOQUANT
            asyncio.create_task(auth.refresh_quota_if_needed())

            p_t = cumulative_usage_stats.get("promptTokenCount", 0)
            c_t = cumulative_usage_stats.get("cachedContentTokenCount", 0)
            g_t = cumulative_usage_stats.get("candidatesTokenCount", 0)
            
            max_t = self.valves.MAX_CONTEXT_SIZE
            
            # Nom de la source active (via le registre des fournisseurs d'accès)
            source_label = auth_providers[0]['type'].replace('_', ' ').title() if auth_providers else "ECHO"
            plan_name = f"Accès {source_label}"
            credits_val = "∞"
            quota_str = ""
            
            # Métadonnées d'identité pour l'infobulle (INFO GEMINI CODE ASSIST)
            from echo_constants import AUTH_DATA_USER_EMAIL, AUTH_DATA_USER_TIER, AUTH_DATA_PROJECT_ID
            email = echo_auth.get_auth_data(AUTH_DATA_USER_EMAIL)
            tier = echo_auth.get_auth_data(AUTH_DATA_USER_TIER)
            proj = echo_auth.get_auth_data(AUTH_DATA_PROJECT_ID)
            
            # Quota spécifique au modèle CA courant
            ca_model_id = get_ca_model_id(target_model)
            model_quota = echo_auth.get_model_quota(ca_model_id)

            q_fraction = float(model_quota.get("remainingFraction",
                               float(echo_auth.get_auth_data("google_quota_fraction") or 1.0)))
            q_reset_raw = str(model_quota.get("resetTime",
                              echo_auth.get_auth_data("google_quota_reset") or "N/A"))
            q_amount  = echo_auth.get_auth_data("google_quota_amount") or "N/A"
            q_type    = echo_auth.get_auth_data("google_quota_type") or "CODE_ASSIST"

            # Crédits AI (source : loadCodeAssist HEALTH_CHECK)
            credits_raw = echo_auth.get_auth_data("google_credits_total") or echo_auth.get_auth_data("google_g1_credits")
            credits_val = credits_raw if (credits_raw and credits_raw != "0") else "∞"

            # Formatage du reset : ISO → HH:MM + minutes restantes
            q_reset = q_reset_raw
            if "T" in q_reset_raw:
                try:
                    q_reset = q_reset_raw.split("T")[1][:5]
                    from datetime import datetime, timezone
                    reset_dt = datetime.fromisoformat(q_reset_raw.replace("Z", "+00:00"))
                    diff_min = int((reset_dt - datetime.now(timezone.utc)).total_seconds() / 60)
                    if diff_min > 0:
                        q_reset = f"{q_reset} ({diff_min}´)"
                except: pass

            # Champs détaillés du quota modèle (RPD / RPM)
            q_rpd_rem = str(model_quota.get("requestsPerDayRemaining", "N/A"))
            q_rpd_lim = str(model_quota.get("requestsPerDayLimit",      "N/A"))
            q_rpm_rem = str(model_quota.get("requestsPerMinuteRemaining", "N/A"))
            q_rpm_lim = str(model_quota.get("requestsPerMinuteLimit",     "N/A"))
            q_model_label = ca_model_id or "—"

            # Liste des fournisseurs d'accès résolus pour le HUD
            sources = [s['type'].replace('google_', '').replace('_', ' ').upper() for s in auth_providers] if auth_providers else []

            active_p_t = max(0, p_t - c_t)
            cache_pct = min(100, (c_t / max_t) * 100)
            prompt_pct = min(100, (active_p_t / max_t) * 100)
            gen_pct = min(100, (g_t / max_t) * 100)

            await EchoUI.deploy_context_gauge(
                events=events, plan_name=plan_name, credits_val=credits_val, quota_str=quota_str,
                c_t=c_t, active_p_t=active_p_t, g_t=g_t, max_t=max_t,
                cache_pct=cache_pct, prompt_pct=prompt_pct, gen_pct=gen_pct,
                user_email=email, user_tier=tier, project_id=proj,
                auth_sources=sources,
                quota_amount=q_amount, quota_fraction=q_fraction, quota_reset=q_reset, quota_type=q_type,
                quota_model=q_model_label,
                quota_rpd_rem=q_rpd_rem, quota_rpd_lim=q_rpd_lim,
                quota_rpm_rem=q_rpm_rem, quota_rpm_lim=q_rpm_lim,
            )
        
        yield ""
