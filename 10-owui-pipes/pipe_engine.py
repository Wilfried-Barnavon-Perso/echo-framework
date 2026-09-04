"""
title: ECHO Engine
author: Wilfried BARNAVON
version: 192.39
requirements: asyncssh
description: Composant système interne : ECHO Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 192.39: Correction de Mojibakes ciblés (émojis et prime) liés à des corruptions d'encodage antérieures.
# 192.38: Purge des appels orphelins restants vers AuthService (auth.refresh_quota, PKCE) provoquant des NameError en fin de génération.
# 192.37: Remplacement global des caractères Mojibake (corrompus en CP1252) par leurs émojis UTF-8 natifs.
# 192.36: Retrait de l'import critique AuthService (non défini) pour restaurer le chargement OWUI.
# 192.35: Scission de echo_utils.py en 8 librairies dédiées (SRP) et bascule sur de nouveaux imports modulaires.


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
from datetime import datetime
from typing import List, Dict, Optional, AsyncGenerator, Literal, Any, Union

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")

# --- IMPORTATIONS ECHO STRICTES (Consolidées) ---
from echo_events import EchoEvents
from echo_state_manager import EchoStateManager
from echo_paths import get_echo_version
from echo_core import (
    split_thought_process,
    estimate_token_size,
    smart_truncate_history,
    build_model_identity,
    resolve_placeholders,
    ensure_gemini_parts,
    unbox_tool_output,
    convert_owui_tools
)
from echo_gemini_client import EchoGeminiClient
from echo_logger import DebugLogger
from echo_protocol import get_ca_model_id
from echo_ui import EchoUI
from echo_constants import (
    MODEL_LITE, MODEL_FLASH, MODEL_PRO,
    FILE_INGESTION_STATUS,
    CONTEXT_WARNING_THRESHOLD, CONTEXT_TRUNCATE_THRESHOLD, ECHO_MAX_CONTEXT_SIZE
)


# --- IMPORTATIONS TIERCES CRITIQUES ---
try:
    import httpx  # noqa: F401
    from pydantic import BaseModel, Field
    # Protocole HTTP/2 obligatoire (h2)
    import h2  # noqa: F401
    import logging
    log = logging.getLogger("echo.pipe_engine")
except ImportError as e:
    missing_module = e.name or "inconnu"
    raise ImportError(f"❌ Module critique manquant : '{missing_module}'. ECHO exige httpx, orjson, pybase64 et h2 (HTTP/2).") from e

MAGIC_KEY_SKIP_VALIDATION = "context_engineering_is_the_way_to_go"

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

    def _mutate_context_identity(self, context: List[Dict], new_model: str, old_model: str):
        """
        Mutation chirurgicale de l'AEC dans le contexte.
        Patche modèle_actuel et modèle_origine via regex (support JSON/YAML hybride).
        Met à jour model_origin pour les prochains resolve_placeholders.
        """
        new_identity = build_model_identity(new_model)
        old_identity = build_model_identity(old_model)
        for msg in context:
            for part in msg.get("parts", []):
                if "text" in part and "<AEC_environnement_contexte>" in part["text"]:
                    part["text"] = re.sub(
                        r'("?modèle_actuel"?\s*:\s*"?)([^"\n]+)("?)',
                        rf'\g<1>{new_identity}\g<3>', part["text"]
                    )
                    part["text"] = re.sub(
                        r'("?modèle_origine"?\s*:\s*"?)([^"\n]+)("?)',
                        rf'\g<1>{old_identity}\g<3>', part["text"]
                    )
        self.model_origin = old_model

    async def prepare_context(self, body: Dict, chat_id: str, target_model: str, __metadata__: Optional[Dict] = None, events: Optional[EchoEvents] = None) -> List[Dict]:
        """RESTAURATION Bit-Perfect avec Contrôle Temporel Strict (Anti-Ghosting)."""
        messages = body.get("messages", [])
        meta = {**(__metadata__ or {}), **body.get("metadata", {})}
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
                        
                        if role == "user":
                            try:
                                cascade_str = self.user_data_manager.state_manager.get_auth_data(f"cascade_{msg_id}")
                                if cascade_str:
                                    cascade_history = std_json.loads(cascade_str)
                                    for cm_msg in cascade_history:
                                        final_contents.append(cm_msg)
                                        cm_inv_hash = self.user_data_manager.calculate_invariant(cm_msg["role"], cm_msg["parts"])
                                        last_cumul = self.user_data_manager.calculate_cumulative(cm_inv_hash, last_cumul)
                            except Exception:
                                pass
                        
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
                    rich_tool_parts = unbox_tool_output(func_name, content_tool, model_id, self.model_origin)
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
                        restored_parts.extend(ensure_gemini_parts(draft_parts, model_id, self.model_origin))
                        user_text = content if isinstance(content, str) else ""
                        # Si content est une liste (multipart OWUI : texte + images inline),
                        # le texte est déjà dans le draft via le filtre (ordered_user_parts).
                        if user_text.strip(): restored_parts.append({"text": resolve_placeholders(user_text, model_id, self.model_origin)})
                    else:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        restored_parts = self.user_data_manager.get_rich_payload(inv_hash) or ensure_gemini_parts(content, model_id, self.model_origin)
                else:
                    # Assistant
                    sig = self.user_data_manager.get_signature_by_id(msg_id) if msg_id else None
                    if not sig:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        current_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                        sig = self.user_data_manager.get_signature(current_cumul)
                    
                    restored_parts = ensure_gemini_parts(content, model_id, self.model_origin)
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

                restored_parts = ensure_gemini_parts(restored_parts, model_id, self.model_origin)
                i += 1

            # --- RÉPARATION DE LA SUTURE (Scellement immédiat pour le tour suivant) ---
            final_contents.append({"role": role_gemini, "parts": restored_parts})
            if msg_id and updated_at and restored_parts:
                self.user_data_manager.save_shadow(msg_id, updated_at, restored_parts, chat_id, role)

            inv_hash = self.user_data_manager.calculate_invariant(role, restored_parts)
            last_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)

            if role == "user" and msg_id:
                try:
                    cascade_str = self.user_data_manager.state_manager.get_auth_data(f"cascade_{msg_id}")
                    if cascade_str:
                        cascade_history = std_json.loads(cascade_str)
                        for cm_msg in cascade_history:
                            final_contents.append(cm_msg)
                            cm_inv_hash = self.user_data_manager.calculate_invariant(cm_msg["role"], cm_msg["parts"])
                            last_cumul = self.user_data_manager.calculate_cumulative(cm_inv_hash, last_cumul)
                except Exception:
                    pass

        # --- SATURATION ET TRONCATURE ---
        if events:
            size = estimate_token_size(final_contents)
            max_tokens = getattr(self.valves, "MAX_CONTEXT_SIZE", ECHO_MAX_CONTEXT_SIZE)
            
            if size > max_tokens * CONTEXT_WARNING_THRESHOLD:
                try:
                    await events.toast("⚠️ï¸ Approche de la limite contextuelle. Migration recommandée (Action 'Resume in New Chat').", "warning")
                except AttributeError:
                    await events.emit({"type": "toast", "data": {"title": "ECHO V5", "message": "⚠️ï¸ Approche de la limite contextuelle. Migration recommandée (Action 'Resume in New Chat').", "type": "warning"}})
            
            if size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD:
                system_parts = final_contents[0] if final_contents and final_contents[0].get("role") == "system" else None
                while size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD and len(final_contents) > 3:
                    idx = 1 if (system_parts and final_contents[0].get("role") == "system") else 0
                    removed = smart_truncate_history(final_contents, idx)
                    if not removed:
                        break
                    size -= removed
                try:
                    await events.toast("🚨 Troncature active : les messages les plus anciens sont ignorés pour éviter le crash.", "error")
                except AttributeError:
                    await events.emit({"type": "toast", "data": {"title": "ECHO V5", "message": "🚨 Troncature active : les messages les plus anciens sont ignorés pour éviter le crash.", "type": "error"}})

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
        self.escalation_requested = None; self.hit_max_tokens = False

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
                        if cand:
                            finish_reason = cand[0].get("finishReason")
                            content = cand[0].get("content")
                            if finish_reason == "MAX_TOKENS":
                                self.hit_max_tokens = True
                            elif not content and finish_reason and finish_reason != "STOP":
                                yield f"\n\n> ⚠️ï¸ **Interruption de génération par Google API** (Motif : `{finish_reason}`)\n"
                                return
                            if content:
                                for part in content["parts"]:
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
                    except Exception as e:
                        if self.logger: self.logger.log("stream_decode_error", {"error": str(e), "chunk": full_json_str})
                        log.error(f"[StreamProcessor] Erreur de décodage du flux: {e} - Chunk: {full_json_str[:200]}")
                        if in_think: yield "\n</think>\n"; in_think = False
                        yield f"\n\n> ❌ **Erreur critique de décodage du flux API** : {str(e)}\n"
        if in_think: yield "\n</think>\n"
        if self.logger: self.logger.log("api_response", self.full_raw_accumulator)

# ==============================================================================
# SECTION 8 : LE PIPE
# ==============================================================================

# =============================================================================
# BRIDGE TOOLS : _TOOLS_CACHE
# =============================================================================
# Problème fondamental OWUI :
#   OWUI injecte __tools__ (dict {fn_name: {callable, spec, tool_id}}) dans le
#   Pipe lorsque des outils sont associés au modèle. Ce dict contient les
#   callables Python réels prêts à être appelés.
#
#   En revanche, OWUI NE propage PAS ces callables aux tool callables (ex:
#   delegate_to_agent). Chaque outil reçoit un __metadata__ vierge, déconnecté
#   des modifications faites par le Pipe.
#
# Solution : le Pipe stocke __tools__[chat_id] dans ce dict module-level.
#   Comme le Pipe et tous les outils s'exécutent dans le même processus Python,
#   agent_engine_tool peut accéder à ce cache via sys.modules["function_pipe_engine"].
#
# Durée de vie : le cache persiste tant que le processus uvicorn tourne.
#   En cas de redémarrage, il est reconstitué au premier message de chaque chat.
# =============================================================================
_TOOLS_CACHE: dict = {}  # {chat_id: dict[fn_name, {callable, spec, tool_id, metadata}]}

class Pipe:
    class Valves(BaseModel):
        DEBUG_MODE: bool = Field(default=False, description="Active le mode de débogage avancé (logs détaillés dans data_dir).")
        MAX_CONTEXT_SIZE: int = Field(default=1048576, description="Taille maximale du contexte absorbable en tokens.")
    class UserValves(BaseModel):
        SHOW_CONTEXT_METRICS: bool = Field(default=True)
        MODEL_SELECTION: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO", "AUTO", "AUTO_PRO"] = Field(default="AUTO")
        # Thinking, temperature, topP et max_tokens sont des constantes ECHO (echo_constants.py v4.8).
        # Plus de valves pour ces paramètres.
        ENABLE_PAID_CREDITS: bool = Field(default=False, description="Activer l'utilisation des crédits Google One AI pour les requêtes OAuth2. Désactivé par défaut.")
        MAX_CASCADE_ATTEMPTS: int = Field(default=5, ge=3, le=10, description="Nombre max de transferts de modèles autorisés par tour.")
        AUTO_CONTINUE_MAX: int = Field(default=1, ge=0, le=5, description="Nombre de relances automatiques si le flux s'arrête (MAX_TOKENS). 0 = Désactivé.")

    def __init__(self): self.valves, self.data_dir = self.Valves(), "/app/backend/data"

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __event_emitter__: Optional[any] = None, __request__: Optional[Any] = None, __tools__: list = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:
        events = EchoEvents(__event_emitter__)
        if not __user__: yield "❌ Identité manquante."; return
        user_valves = __user__.get("valves") or self.UserValves()
        chat_id = kwargs.get("__chat_id__") or body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        orch = Orchestrator(self.valves, user_valves, self.data_dir, __user__["id"], chat_id)
        # auth = AuthService(user_id=__user__["id"])  # [192.36] DÉSACTIVÉ : AuthService n'existe plus.
        from echo_auth import EchoAuth
        echo_auth = EchoAuth(user_id=__user__["id"])

        # Injection politiques Pipe → __metadata__ (non propagé aux outils par OWUI, conservé pour usage interne pipe)
        if __metadata__ is None:
            __metadata__ = {}
        __metadata__["_echo_model_policy"] = user_valves.MODEL_SELECTION
        __metadata__["_echo_enable_paid_credits"] = user_valves.ENABLE_PAID_CREDITS

        # -----------------------------------------------------------------------
        # INJECTION DES OUTILS (bridge __tools__ → _TOOLS_CACHE)
        # -----------------------------------------------------------------------
        # OWUI injecte __tools__ dans le Pipe sous deux formes selon la version :
        #   1. dict {fn_name: {tool_id, callable, spec, metadata}}  ← observé en production
        #   2. list[ToolUserModel] avec attribut .specs             ← doc officielle
        #      Contient les specs OpenAI mais PAS les callables.
        #
        # Dans le cas 1 (dict), on stocke dans _TOOLS_CACHE[chat_id] pour que
        # agent_engine_tool puisse y accéder sans passer par __metadata__.
        # Dans le cas 2 (list), on extrait les specs pour construire _echo_body_tools,
        # mais les callables devront être reconstruits via sys.modules dans agent_engine_tool.
        import logging as _plog
        _log_pipe = _plog.getLogger("echo.pipe")

        if isinstance(__tools__, dict) and __tools__:
            # Cas 1 — dict avec callables (production ECHO)
            __metadata__["_echo_tools_dict"] = __tools__          # Conservé pour compatibilité
            __metadata__["_echo_body_tools"] = [                  # Specs format OpenAI
                {"type": "function", "function": v["spec"]}
                for v in __tools__.values()
                if isinstance(v, dict) and "spec" in v
            ]
            # Bridge principal : stockage dans le cache module-level
            if chat_id:
                _TOOLS_CACHE[chat_id] = __tools__
                _log_pipe.debug("_TOOLS_CACHE[%s] = %d outils", chat_id, len(__tools__))

        elif isinstance(__tools__, list) and __tools__:
            # Cas 2 — list[ToolUserModel] sans callables
            __metadata__["_echo_tools_dict"] = {}
            _specs: list = []
            for _tm in __tools__:
                for _sp in (getattr(_tm, "specs", None) or (_tm.get("specs") if isinstance(_tm, dict) else []) or []):
                    _specs.append({"type": "function", "function": _sp})
            __metadata__["_echo_body_tools"] = _specs

        else:
            # Aucun outil injecté (modèle sans outils associés ou fallback OpenAI)
            __metadata__["_echo_tools_dict"] = {}
            __metadata__["_echo_body_tools"] = body.get("tools", [])

        # Log diagnostic (DEBUG uniquement, ne pas laisser en WARNING en production)
        if isinstance(__tools__, dict):
            _tools_info = f"dict({len(__tools__)} keys) — sample: {list(__tools__.keys())[:3]}"
        elif isinstance(__tools__, list):
            _first_type = type(__tools__[0]).__name__ if __tools__ else "empty"
            _tools_info = f"list({len(__tools__)} items, item[0]={_first_type})"
        else:
            _tools_info = f"{type(__tools__).__name__}: {__tools__}"
        _log_pipe.debug(
            "pipe [tools]: %s | _body_tools=%s | cache=%s",
            _tools_info,
            len(__metadata__.get("_echo_body_tools", [])),
            len(_TOOLS_CACHE.get(chat_id, {})),
        )

        # Persistance identity.db → lu par clamp_model() côté outils (fallback SQLite)
        from echo_state_manager import EchoStateManager
        _settings = EchoStateManager(user_id=__user__["id"])
        _settings.save_setting("model_policy", user_valves.MODEL_SELECTION)
        _settings.save_setting("enable_paid_credits", str(user_valves.ENABLE_PAID_CREDITS))

        # --- [NOUVEAU] DETECTION ET INTERCEPTION DE CLÉ API ---
        api_key_from_filter = body.get("_api_key")

        # Résolution du Registre des Fournisseurs d'Accès (Cache local pour ce tour de pipe)
        auth_providers = await echo_auth.get_ordered_auth_providers(__user__["id"])

        if api_key_from_filter:
            await events.status("🔒 Validation de l'authentification Google...")
            success, msg = False, 'Désactivé'
            if success:
                yield (
                    "✅ **Configuration d'accès ECHO Configurée avec Succès**\n\n"
                    f"{msg}\n\n"
                    "Vos accès Google ont été validés et enregistrés de manière sécurisée dans votre Espace Personnel ECHO.\n\n"
                    "Vous pouvez maintenant poser votre question."
                )
                return
            else:
                yield f"❌ **Échec de validation**\n\n{msg}\n\n" + ''
                return

        # --- AUTHENTIFICATION PKCE (Authorization Code + PKCE RFC 7636) ---
        # Tunnel SSH ephemere asyncssh - ports dynamiques - multi-user natif.
        if not auth_providers:
            from echo_auth import EchoAuth as _EchoAuth
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
                        f"*Ou collez directement une cl\u00e9 `AIza\u2026` / `AQ.\u2026` pour utiliser AI Studio.*"
                    )
                    return

                await events.status("\U0001f510 Lancement authentification PKCE...")
                ok, auth_url, server_ip, ssh_port, cb_port, temp_pwd = False, '', '', '', '', ''
                if not ok:
                    yield f"\u274c Impossible de lancer le flow PKCE.\n\n" + ''
                    return

                # Persister l'URL pour les messages suivants
                _ea.save_api_key("pkce_auth_url", auth_url)

                # Lancer le serveur callback en background (non bloquant)
                # PKCE désactivé

                yield ''

            except Exception as e:
                yield f"\u274c Erreur PKCE : {str(e)}\n\n" + ''
            return

        # --- [NOUVEAU] ROUTAGE DYNAMIQUE (Fluctuation Continue) ---
        model_selection = user_valves.MODEL_SELECTION
        last_model = orch.user_data_manager.get_last_active_model()
        from echo_constants import ECHO_MODELS_REGISTRY
        
        # Reverse-lookup (Auto-heal SQLite)
        if last_model and last_model != "aucun" and last_model not in ECHO_MODELS_REGISTRY:
            _found_model = False
            for logical_key, config in ECHO_MODELS_REGISTRY.items():
                if last_model in [config.get("ai_studio_id"), config.get("ca_model_id")]:
                    last_model = logical_key
                    _found_model = True
                    break
            if not _found_model:
                last_model = MODEL_LITE  # Fallback sécurisé pour les orphelins
                
        def _get_ui_display(model_key: str) -> str:
            config = ECHO_MODELS_REGISTRY.get(model_key, {})
            ai_id = config.get("ai_studio_id")
            ca_id = config.get("ca_model_id")
            if ai_id and ca_id and ai_id != ca_id:
                tech_str = f"{ai_id} | {ca_id}"
            else:
                tech_str = ai_id or ca_id or ""
            return f"{model_key} [{tech_str}]" if tech_str else model_key

        if model_selection in ["AUTO", "AUTO_PRO"]:
            # Plafond : AUTO → FLASH max, AUTO_PRO → PRO max
            ceiling = MODEL_PRO if model_selection == "AUTO_PRO" else MODEL_FLASH
            if last_model and last_model != "aucun":
                # Clamping dynamique via la hiérarchie du SSOT
                last_hierarchy = ECHO_MODELS_REGISTRY.get(last_model, {}).get("hierarchy")
                ceiling_hierarchy = ECHO_MODELS_REGISTRY.get(ceiling, {}).get("hierarchy")
                last_hierarchy = last_hierarchy if last_hierarchy is not None else -1
                ceiling_hierarchy = ceiling_hierarchy if ceiling_hierarchy is not None else -1
                if last_hierarchy > ceiling_hierarchy:
                    target_model = ceiling
                else:
                    target_model = last_model
                origine_model = last_model
                await events.status(f"🧠  Reprise du contexte ({_get_ui_display(target_model)})...")
            else:
                target_model = MODEL_LITE
                origine_model = "aucun"
                await events.status(f"🧠  Initialisation de session ({_get_ui_display(MODEL_LITE)})...")
        else:
            target_model = model_selection
            origine_model = last_model if last_model else "aucun"
            await events.status(f"Model Fixé : {_get_ui_display(target_model)}")

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

        auto_continue_count = 0

        while cascade_attempt < max_cascade_attempts:
            cascade_attempt += 1
            
            # --- [NOUVEAU] RÉSOLUTION DYNAMIQUE DES INSTRUCTIONS SYSTÈME ---
            sys_instr_raw = "\n".join([m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"]) or "Tu es ECHO."
            resolved_sys = resolve_placeholders(sys_instr_raw, target_model, orch.model_origin)
            sys_instr = {"parts": [{"text": resolved_sys}]}

            import copy
            gen_config = copy.deepcopy(ECHO_MODELS_REGISTRY.get(target_model, ECHO_MODELS_REGISTRY.get("MODEL_LITE", {})).get("generationConfig", {}))
            if "thinkingConfig" in gen_config:
                gen_config["thinkingConfig"]["includeThoughts"] = True

            payload = {
                "contents": context,
                "systemInstruction": sys_instr,
                "generationConfig": gen_config
            }

            tools = convert_owui_tools(body.get("tools"), user_valves.MODEL_SELECTION)
            
            # --- [NOUVEAU] INJECTION OUTIL CHANGEMENT COGNITIF (BIDIRECTIONNEL) ---
            if is_auto:
                # Construction simplifiée : tous les modèles dispos, moins le modèle actif
                menu_escalade = ["MODEL_LITE", "MODEL_FLASH"]
                if user_valves.MODEL_SELECTION == "AUTO_PRO":
                    menu_escalade.append("MODEL_PRO")
                from echo_constants import get_model_identity
                target_identity = get_model_identity(target_model)
                if target_identity in menu_escalade:
                    menu_escalade.remove(target_identity)
                
                if menu_escalade:
                    escalation_tool = {
                        "name": "new_cognitive_level",
                        "description": (
                            "Outil d'ajustement cognitif. Le Modèle est tenu d'appeler cet outil AVANT "
                            "toute tâche non-triviale pour garantir la qualité de la réponse.\n\n"
                            "## Règles de sélection\n"
                            "- **MODEL_LITE** (Réflexe — défaut) : Salutations, remerciements, extractions simples, "
                            "traduction courte, questions factuelles basiques.\n"
                            "- **MODEL_FLASH** (Exécution — moteur agentique) : Escalade requise pour toute "
                            "tâche non-triviale. Recherche web, écriture de code, analyse sémantique, "
                            "synthèse de documents, orchestration d'outils, planification, réponses "
                            "structurées, raisonnement multi-étapes.\n"
                            "  \u2192 Le Modèle escalade vers FLASH systématiquement dès que la tâche "
                            "dépasse le simple réflexe. L'inertie en LITE est proscrite.\n"
                            "- **MODEL_PRO** (Expertise) : Pour les tâches de haute complexité où "
                            "FLASH a échoué ou serait insuffisant. Architectures systèmes complexes, "
                            "refactoring multi-fichiers avec contraintes imbriquées, logique formelle.\n"
                            "  → Le Modèle justifie le besoin de PRO et redescend vers FLASH ou LITE "
                            "une fois la tâche complexe accomplie. (Note : Ne sont présentés dans l'enum que les modèles "
                            "vers lesquels une transition est possible, le modèle en cours d'utilisation en est exclu).\n\n"
                            "## Corrélation contextuelle\n"
                            "La saturation contextuelle est atténuée par la Mémoire Vectorisée de Session (save_memory "
                            "et save_session_context stockent les éléments critiques). Vigilance "
                            "accrue à haute charge (> 50%) — préférer alors FLASH ou PRO."
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
                    events=events,
                    process_callback=proc.process,
                    chat_id=chat_id,
                    enable_paid_credits=user_valves.ENABLE_PAID_CREDITS,
                ):
                    # On ne yield le texte que si aucune escalade n'est en cours (Gateway Pattern)
                    if isinstance(chunk, dict) or not proc.escalation_requested:
                        yield chunk
            except Exception as e:
                log.error(f"[PipeEngine] Erreur API lors de l'appel Gemini: {e}")
                # GESTION DES ÉCHECS TECHNIQUES — CASCADE DESCENDANTE
                if is_auto and cascade_attempt < max_cascade_attempts:
                    from echo_constants import ECHO_MODELS_REGISTRY, get_model_identity
                    target_identity = get_model_identity(target_model)
                    if target_identity == 'UNKNOWN': target_identity = 'MODEL_FLASH'
                    target_hierarchy = ECHO_MODELS_REGISTRY.get(target_identity, {}).get("hierarchy")
                    target_hierarchy = target_hierarchy if target_hierarchy is not None else 0
                    cascade_order_keys = sorted(
                        [k for k in ECHO_MODELS_REGISTRY if ECHO_MODELS_REGISTRY[k].get("hierarchy") is not None and ECHO_MODELS_REGISTRY[k].get("hierarchy") < target_hierarchy],
                        key=lambda k: ECHO_MODELS_REGISTRY[k].get("hierarchy"), reverse=True
                    )
                    cascade_order = cascade_order_keys
                    if cascade_order:
                        prev_model = target_model
                        target_model = cascade_order[0]
                        await events.status(f"⚡ {prev_model} indisponible → cascade vers {target_model}")
                        await events.toast(f"⚡ Cascade : {prev_model} → {target_model} (erreur: {str(e)[:80]})", "warning")
                        orch._mutate_context_identity(context, target_model, prev_model)
                    else:
                        # Plus de modèle inférieur → échec terminal
                        from echo_constants import ECHO_ENDPOINT_LOCK_TIMEOUT_MIN
                        from datetime import datetime, timedelta
                        resume_time = datetime.now().astimezone() + timedelta(minutes=ECHO_ENDPOINT_LOCK_TIMEOUT_MIN)
                        time_str = resume_time.strftime("%H:%M:%S")
                        yield f"❌ Cascade épuisée : tous les modèles sont indisponibles. Reprise estimée dans {ECHO_ENDPOINT_LOCK_TIMEOUT_MIN} min (vers {time_str}). ({str(e)})"
                        break
                    continue
                else:
                    yield f"❌ Erreur critique lors de la communication API : {str(e)}"
                    break

            # --- [NOUVEAU] ACCUMULATION DES TOKENS (SOUVERAINETÉ) ---
            if proc.usage_stats:
                for k in cumulative_usage_stats:
                    cumulative_usage_stats[k] += proc.usage_stats.get(k, 0)

            # --- [NOUVEAU] GESTION DE L'AUTO-CONTINUE (MAX_TOKENS) ---
            if proc.hit_max_tokens:
                if auto_continue_count < user_valves.AUTO_CONTINUE_MAX:
                    auto_continue_count += 1
                    await events.status(f"⚡ Limite MAX_TOKENS atteinte. Auto-continuation ({auto_continue_count}/{user_valves.AUTO_CONTINUE_MAX})...")
                    if proc.accumulated_text:
                        context.append({"role": "model", "parts": [{"text": proc.accumulated_text}]})
                        context.append({"role": "user", "parts": [{"text": "<AEC_evenement_systeme>\n- type: SYSTEM_AUTO_CONTINUE\n  message: Le plafond de tokens de sortie a été atteint. Le Modèle doit reprendre la génération EXACTEMENT au caractère près où il s'est arrêté (sans reprendre la phrase du début si elle est coupée). Le Modèle ne doit produire aucune formule de politesse, ni introduction. Il doit produire uniquement la suite absolue de la chaîne de caractères.\n</AEC_evenement_systeme>"}]})
                    else:
                        context.append({"role": "model", "parts": [{"text": "[Erreur Système Interne ECHO : L'appel d'outil précédent du Modèle était trop long et a été détruit par l'API. Le Modèle doit obligatoirement fragmenter son action et ne pas l'envoyer d'un seul coup.]"}]})
                        context.append({"role": "user", "parts": [{"text": "<AEC_evenement_systeme>\n- type: TOOL_CALL_DROPPED_MAX_TOKENS\n  message: Le Modèle doit recommencer l'action en cours en la fragmentant obligatoirement.\n</AEC_evenement_systeme>"}]})
                    cascade_attempt = 0
                    continue
                else:
                    yield f"\n\n> ⚠️ï¸ **Auto-Continue épuisé** ({user_valves.AUTO_CONTINUE_MAX} relances). Génération tronquée.\n"
                    break

            # --- [NOUVEAU] GESTION DE LA CASCADE ---
            if is_auto and proc.escalation_requested:
                req = proc.escalation_requested
                target_req = req.get("niveau_requis")
                
                # Mapping explicite pour gérer la montée ET la redescente
                from echo_constants import get_model_identity
                new_target = get_model_identity(target_req)
                
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
                    await events.status(f"⚠️ï¸ Transfert vers MODEL_PRO refusé (Valve AUTO).")
                    # Signalement de refus au modèle actuel
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "new_cognitive_level", "args": req}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "error", "model_requested": target_req, "model_used": target_model, "warning": f"{target_req} unavailable (policy)", "message": f"Transfert vers {target_req} refusé. Traitez avec {target_model}."}}}]
                    })
                    continue # On reboucle avec le MÊME target_model
                
                if new_target == target_model:
                    await events.status(f"⚠️ï¸ Auto-transfert annulé ({target_req}).")
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "new_cognitive_level", "args": req}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "error", "model_requested": target_req, "model_used": target_model, "warning": "Déjà sur le modèle", "message": f"ERREUR : Vous êtes déjà sur le modèle {target_req}. Poursuivez votre tâche."}}}]
                    })
                    continue

                await events.status(f"🚀 Transfert cognitif vers {new_target}...")
                
                if proc.captured_sig:
                    orch.user_data_manager.save_call_bridge(f"esc-{secrets.token_hex(4)}", proc.captured_sig, "new_cognitive_level", req)
                
                plan_md = req.get("plan_de_transfert", "Exécution du relais.")
                
                # 1. Mutation Chirurgicale de l'identité dans le contexte
                orch._mutate_context_identity(context, new_target, target_model)
                
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

                # Structure status alignée sur wrap_cascade_output (KEYs uniquement)
                escalation_status = {
                    "status": "success",
                    "model_requested": target_req,
                    "model_used": target_req,  # À ce stade l'escalade est approuvée (clamping fait en amont)
                }
                msg = f"Transfert effectué vers {target_req}."
                user_resp_parts = [{"functionResponse": {"name": "new_cognitive_level", "response": {**escalation_status, "message": msg, "plan": plan_md}}}]
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
            meta = {**(__metadata__ or {}), **body.get("metadata", {})}
            
            # 1. Scellement message Utilisateur (Le Draft complet)
            user_updated_at = meta.get("_echo_user_msg_updated_at")
            user_draft = meta.get("_echo_user_parts_draft")
            if user_msg_id and user_draft:
                user_text = body['messages'][-1].get('content', "")
                full_user_parts = ensure_gemini_parts(user_draft, target_model, orch.model_origin)
                # Guard : si content est une liste (multipart OWUI), le texte est déjà dans user_draft.
                # Sans ce guard, la liste entière serait passée à resolve_placeholders, corrompant le shadow.
                if isinstance(user_text, str) and user_text.strip():
                    full_user_parts.append({"text": resolve_placeholders(user_text, target_model, orch.model_origin)})
                orch.user_data_manager.save_shadow(user_msg_id, user_updated_at, full_user_parts, chat_id, "user")

            # 2. Scellement du Registre Unifié et Rangement
            files_to_seal = meta.get("_echo_files_to_seal", [])
            for f in files_to_seal:
                if f.get("status") == "success":
                    content_to_save = None
                    if f.get("type") == FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"]: content_to_save = f.get("content")
                    elif f.get("type") == FILE_INGESTION_STATUS["PUT_IN_CONTEXT"] and f.get("sub_type") == "text": content_to_save = f.get("content")
                    # Résolution du resource_type depuis le MIME et le type de traitement
                    mime = f.get('mime', '')
                    if f.get('sub_type') == 'text' or 'text/' in mime or 'json' in mime:
                        res_type = 'codex'
                    elif f.get('type') == 'indexed':
                        res_type = 'binary'
                    else:
                        res_type = 'media'
                    orch.user_data_manager.state_manager.save_resource(
                        id=f['fid'], name=f['name'], resource_type=res_type,
                        status=f['type'], mime=f['mime'], summary=content_to_save,
                        storage_path=f.get('storage_path'), git_tracked=(res_type == 'codex'),
                        message_id=user_msg_id
                    )
                    # Déplacement dans le Vault uniquement pour les non-Codex
                    if res_type != 'codex':
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
            
            # --- [NOUVEAU] RÉCUPÉRATION CHIRURGICALE ---
            # Si OWUI a omis l'ID dans le metadata, on le récupère du payload HTTP brut.
            if not asst_msg_id:
                request = kwargs.get("__request__")
                if request:
                    try:
                        raw_payload = await request.json()
                        if "message_ids" in raw_payload and raw_payload["message_ids"]:
                            asst_msg_id = raw_payload["message_ids"][0].get("message_id")
                        elif "id" in raw_payload:
                            asst_msg_id = raw_payload.get("id")
                    except Exception:
                        pass
            
            if asst_msg_id and cascade_history:
                orch.user_data_manager.save_shadow(asst_msg_id, int(time.time()), cascade_history, chat_id, "assistant")
            user_msg_id = __metadata__.get("_echo_user_msg_id") if __metadata__ else None
            if cascade_history and user_msg_id:
                try:
                    orch.user_data_manager.state_manager.save_auth_data(f"cascade_{user_msg_id}", std_json.dumps(cascade_history).decode('utf-8'))
                except Exception as e:
                    log.error(f"[PipeEngine] Erreur sauvegarde cascade KV: {e}")
            
            # Sauvegarde des ponts d'outils pour la navigation future
            for c in proc.accumulated_calls: 
                orch.user_data_manager.save_call_bridge(c["id"], proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION, c["name"], c["args"])


        # --- HUD METRICS ---
        if user_valves.SHOW_CONTEXT_METRICS:
            # Rafraîchissement intelligent des quotas (OAuth2 uniquement) - ASYNCHRONE NON BLOQUANT
            # # asyncio.create_task(auth.refresh_quota_if_needed())  # [192.38]  # [192.38] DÉSACTIVÉ : AuthService n'existe plus

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
                        q_reset = f"{q_reset} ({diff_min}')"
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
                quota_fraction=q_fraction, quota_reset=q_reset, quota_type=q_type,
                quota_model=q_model_label,
                quota_rpd_rem=q_rpd_rem, quota_rpd_lim=q_rpd_lim,
                quota_rpm_rem=q_rpm_rem, quota_rpm_lim=q_rpm_lim,
            )
        
        if cumulative_usage_stats.get("totalTokenCount", 0) > 0:
            yield {"usage": {"prompt_tokens": cumulative_usage_stats.get("promptTokenCount", 0), "completion_tokens": cumulative_usage_stats.get("candidatesTokenCount", 0), "total_tokens": cumulative_usage_stats.get("totalTokenCount", 0)}}
        yield ""
