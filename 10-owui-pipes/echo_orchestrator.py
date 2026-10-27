"""
title: Écho
author: Wilfried BARNAVON
version: 5.231.17.1
requirements: asyncssh
description: ECHO - Orchestrateur
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.15.1: Sanctuarisation de promptTokenCount en cascade cognitive pour immuniser la persistance contre les faux deltas négatifs.
# 5.231.13.1: Suppression de la double écriture SSE de save_context_stats et correction du cumul de promptTokenCount/cachedContentTokenCount en cascade.
# 5.231.10.2: Déport du HUD contextuel vers context_gauge_filter, transmission de (user_id, chat_id) à EchoEvents et persistance auto-archivée.
# 5.231.10.1: Suppression de detect_verbal_escalation et déduplication de la docstring de new_cognitive_level vis-à-vis du PCD.
# 5.231.8.1: Reformulation haut SNR de la description et des paramètres de new_cognitive_level (style PAS, conformité Gemini 3/4).


# ==============================================================================
# SECTION 0 : IMPORTS & CONFIGURATION
# ==============================================================================
import sys
import secrets
import re
import time
import codecs
import json
import orjson as std_json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, AsyncGenerator, Literal, Any, Union

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")

# --- IMPORTATIONS ECHO STRICTES (Consolidées) ---
from echo_events import EchoEvents
from echo_state_manager import EchoStateManager
from echo_core import (
    split_thought_process,
    estimate_token_size,
    smart_truncate_history,
    build_model_identity,
    resolve_placeholders,
    ensure_gemini_parts,
    unbox_tool_output,
    convert_owui_tools,
    attach_thought_signature,
)
from echo_gemini_client import EchoGeminiClient
from echo_http import FatalAPIError
from echo_logger import DebugLogger
try:
    from echo_audio import register_echo_audio_routes
except ImportError:
    register_echo_audio_routes = None
try:
    from echo_window_manager import register_echo_wm_routes
except ImportError:
    register_echo_wm_routes = None
from echo_constants import (
    MODEL_ZEPHYR, MODEL_APOLLO, MODEL_PROMETHEUS,
    FILE_INGESTION_STATUS,
    CONTEXT_WARNING_THRESHOLD, CONTEXT_TRUNCATE_THRESHOLD, ECHO_MAX_CONTEXT_SIZE,
    ECHO_TOOLS_CACHE_MAX_SIZE,
    ECHO_MODELS_REGISTRY, get_model_identity, get_generation_config,
    get_descending_cascade, ECHO_ENDPOINT_LOCK_TIMEOUT_MIN,
    AEC_REMINDERS
)
from echo_auth import EchoAuth


# --- IMPORTATIONS TIERCES CRITIQUES ---
try:
    import httpx  # noqa: F401
    from pydantic import BaseModel, Field
    # Protocole HTTP/2 obligatoire (h2)
    import h2  # noqa: F401
    import logging
    log = logging.getLogger("echo.echo_orchestrator")
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

    def get_auth_data(self, key: str) -> Optional[str]:
        return self.state_manager.get_auth_data(key)

    def save_session_setting(self, key: str, value: str):
        self.state_manager.save_session_setting(key, value)

    def get_session_setting(self, key: str) -> Optional[str]:
        return self.state_manager.get_session_setting(key)

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
        Mutation chirurgicale de l'AEC XML dans le contexte (<artifact id="AEC_modele">).
        Patche exclusivement la dernière occurrence (tour courant) afin de préserver
        le cache des préfixes de l'API Gemini sur les tours historiques.
        Met à jour self.model_origin pour les prochains resolve_placeholders.
        """
        new_identity = build_model_identity(new_model)
        old_identity = build_model_identity(old_model)

        found = False
        for msg in reversed(context):
            for part in msg.get("parts", []):
                if "text" in part and '<artifact id="AEC_modele">' in part["text"]:
                    part["text"] = re.sub(
                        r'(<modele_actuel>)(.*?)(</modele_actuel>)',
                        rf'\g<1>{new_identity}\g<3>',
                        part["text"]
                    )
                    part["text"] = re.sub(
                        r'(<modele_origine>)(.*?)(</modele_origine>)',
                        rf'\g<1>{old_identity}\g<3>',
                        part["text"]
                    )
                    found = True
                    break
            if found:
                break

        self.model_origin = old_model

    async def prepare_context(self, body: Dict, chat_id: str, target_model: str, __metadata__: Optional[Dict] = None, events: Optional[EchoEvents] = None) -> List[Dict]:
        """RESTAURATION Bit-Perfect avec Contrôle Temporel Strict (Anti-Ghosting)."""
        messages = body.get("messages", [])
        meta = {**(__metadata__ or {}), **body.get("metadata", {})}
        model_id = target_model

        # Nettoyage des AEC orphelins postérieurs au dernier tour reçu (gestion des retouches de messages)
        if messages:
            t_pivot = max([m.get("updated_at") or 0 for m in messages] + [0])
            if t_pivot > 0:
                from echo_aec import EchoAEC
                EchoAEC.purge_orphaned_events(self.user_data_manager.state_manager, t_pivot)

        # --- [NOUVEAU] Rappels Cognitifs Multi-Axes par Charge (Tokens Exacts) ---
        # Évaluation avant la boucle pour intégration native dans la Suture Bit-Perfect.
        last_stats = self.user_data_manager.get_last_context_stats()
        if last_stats and messages and messages[-1].get("role") == "user":
            current_tokens = last_stats.get("promptTokenCount", 0)
            sys_events_to_inject = []

            from echo_aec import EchoAEC
            for reminder in AEC_REMINDERS:
                threshold = reminder.get("token_threshold", 0)
                if threshold > 0:
                    current_tier = current_tokens // threshold
                    if current_tier > 0:
                        setting_key = f"aec_tier_{reminder['id']}"
                        last_tier_str = self.user_data_manager.get_session_setting(setting_key)
                        last_tier = int(last_tier_str) if last_tier_str else 0

                        if current_tier > last_tier:
                            event_id = EchoAEC.record_event(
                                state_manager=self.user_data_manager.state_manager,
                                event_name=reminder["id"],
                                status="active",
                                summary=reminder["message"],
                                resource_type="aec_directive"
                            )
                            sys_events_to_inject.append({
                                "id": event_id,
                                "type": "rappel_cognitif",
                                "message": reminder["message"],
                                "tier": current_tier
                            })
                            self.user_data_manager.save_session_setting(setting_key, str(current_tier))

                            if events:
                                await events.status(f"🛤️ Alignement du Modèle : {reminder['id']}", done=True)

            if sys_events_to_inject:
                aec_text = EchoAEC.render_system_events(sys_events=sys_events_to_inject)
                messages[-1]["content"] = aec_text + str(messages[-1].get("content", ""))

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
                    rich_tool_parts = unbox_tool_output(func_name, content_tool, model_id, self.model_origin, call_id)
                    aggregated_tool_parts.extend(rich_tool_parts)
                    i += 1
                restored_parts = aggregated_tool_parts
                role_gemini = "user"  # Les réponses d'outils sont toujours 'user' pour Gemini

            else:
                # USER / ASSISTANT
                if role in ["assistant", "model"]:
                    content, _ = split_thought_process(content if isinstance(content, str) else str(content))

                draft_parts = meta.get("_echo_user_parts_draft") if (role == "user" and i == len(messages) - 1) else None

                if role == "user":
                    if draft_parts is not None:
                        restored_parts = []
                        restored_parts.extend(ensure_gemini_parts(draft_parts, model_id, self.model_origin))
                    else:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        restored_parts = self.user_data_manager.get_rich_payload(inv_hash) or ensure_gemini_parts(content, model_id, self.model_origin)
                        for p in restored_parts:
                            if "text" in p:
                                p["text"] = f"<REQUETE_UTILISATEUR>\n{p['text']}\n</REQUETE_UTILISATEUR>"
                else:
                    # Assistant
                    tool_calls = m.get("tool_calls", [])
                    sig = self.user_data_manager.get_signature_by_id(msg_id) if msg_id else None
                    if not sig and tool_calls:
                        for tc in tool_calls:
                            cb = self.user_data_manager.get_call_bridge(tc.get("id"))
                            if cb and cb.get("signature"):
                                sig = cb["signature"]
                                break
                    if not sig:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        current_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                        sig = self.user_data_manager.get_signature(current_cumul)

                    restored_parts = ensure_gemini_parts(content, model_id, self.model_origin)
                    if tool_calls:
                        parsed_tc = []
                        for tc in tool_calls:
                            raw_args = tc["function"].get("arguments", "{}")
                            if not raw_args:
                                raw_args = "{}"
                            try:
                                args = std_json.loads(raw_args)
                            except Exception:
                                args = {}
                            call_part = {"name": tc["function"]["name"], "args": args}
                            if "id" in tc:
                                call_part["id"] = tc["id"]
                            parsed_tc.append({"functionCall": call_part})
                        restored_parts = parsed_tc + restored_parts
                    else:
                        inv_hash = self.user_data_manager.calculate_invariant(role, content)
                        current_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)
                        tool_io = self.user_data_manager.state_manager.get_tool_io(current_cumul)
                        if tool_io:
                            restored_parts = [{"functionCall": {"name": tc["name"], "args": tc["args"], **({"id": tc["id"]} if "id" in tc else {})}} for tc in tool_io.get("calls", [])] + restored_parts

                    attach_thought_signature(
                        restored_parts,
                        sig,
                        fallback_magic_key=MAGIC_KEY_SKIP_VALIDATION if tool_calls else None,
                    )

                restored_parts = ensure_gemini_parts(restored_parts, model_id, self.model_origin)
                i += 1

            # --- RÉPARATION DE LA SUTURE (Scellement immédiat pour le tour suivant) ---
            final_contents.append({"role": role_gemini, "parts": restored_parts})
            if msg_id and updated_at and restored_parts:
                self.user_data_manager.save_shadow(msg_id, updated_at, restored_parts, chat_id, role)

            inv_hash = self.user_data_manager.calculate_invariant(role, restored_parts)
            last_cumul = self.user_data_manager.calculate_cumulative(inv_hash, last_cumul)

            if role == "user" and msg_id:
                pass

        # --- SATURATION ET TRONCATURE ---
        if events:
            size = estimate_token_size(final_contents)
            max_tokens = getattr(self.valves, "MAX_CONTEXT_SIZE", ECHO_MAX_CONTEXT_SIZE)

            if size > max_tokens * CONTEXT_WARNING_THRESHOLD:
                await events.toast("⚠️ Approche de la limite contextuelle. Migration de session recommandée (Action 'Poursuivre dans une nouvelle conversation').", "warning", "ECHO V5")

            if size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD:
                system_parts = final_contents[0] if final_contents and final_contents[0].get("role") == "system" else None
                while size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD and len(final_contents) > 3:
                    idx = 1 if (system_parts and final_contents[0].get("role") == "system") else 0
                    removed = smart_truncate_history(final_contents, idx)
                    if not removed:
                        break
                    size -= removed
                await events.toast("🚨 Troncature active : les messages les plus anciens sont ignorés pour éviter le crash.", "error", "ECHO V5")

        body["_echo_last_cumul"] = last_cumul
        if self.logger:
            self.logger.log("context_reconstructed", final_contents)
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
        # [AUTO-CONTINUE] Suivi granulaire de l'état du flux pour la détection des troncatures
        self.last_finish_reason = None; self.is_generating_tool = False

    def _create_tool_call_part(self, func_call: dict, tool_index: int) -> Optional[dict]:
        name = func_call["name"]
        args = func_call.get("args", {})
        if name == "new_cognitive_level":
            self.escalation_requested = args
            return None
        tc_id = f"echo-{secrets.token_hex(8)}"
        self.accumulated_calls.append({"id": tc_id, "name": name, "args": args})
        return self._build_openai_tool_call(tool_index, tc_id, name, args)

    def _build_openai_tool_call(self, tool_index: int, tc_id: str, name: str, args: dict) -> dict:
        return {"index": tool_index, "id": tc_id, "type": "function", "function": {"name": name, "arguments": std_json.dumps(args).decode('utf-8')}}

    def _filter_cascade_for_shadow(self, cascade_history: List[Dict]) -> List[Dict]:
        """
        Analyse l'historique de la cascade pour la sauvegarde du shadow.
        - Retourne la cascade entière si erreurs complexes (Tool Error, Cascade Cognitive).
        - Retourne les parties fusionnées du modèle si uniquement auto-continue (MAX_TOKENS).
        """
        is_complex = False
        for msg in cascade_history:
            if msg["role"] == "user":
                part_text = msg.get("parts", [{}])[0].get("text", "") if msg.get("parts") else ""
                if "MAX_TOKENS" not in part_text:
                    is_complex = True
                    break

        if is_complex:
            return cascade_history

        fused_parts = []
        for msg in cascade_history:
            if msg["role"] in ["model", "assistant"]:
                fused_parts.extend(msg.get("parts", []))
        return fused_parts

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False; buffer = ""; decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        buffered_lines = []
        pending_tool_deltas = []
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
                            if self.usage_stats is None:
                                self.usage_stats = {}
                            self.usage_stats.update(target["usageMetadata"])

                        cand = data.get("candidates", []) or data.get("response", {}).get("candidates", [])
                        if cand:
                            finish_reason = cand[0].get("finishReason")
                            content = cand[0].get("content")
                            if finish_reason:
                                # [AUTO-CONTINUE] Capture systématique de la raison d'arrêt pour déclenchement ultérieur
                                self.last_finish_reason = finish_reason
                                if finish_reason == "STOP":
                                    # Libération immédiate de l'interface Open WebUI pendant que l'API Google calcule l'usageMetadata
                                    yield ""
                                elif not content and finish_reason != "MAX_TOKENS":
                                    yield f"\n\n> ⚠️ï¸  **Interruption de génération par Google API** (Motif : `{finish_reason}`)\n"
                                    return
                            if content:
                                for part in content.get("parts", []):
                                    if "thoughtSignature" in part:
                                        self.captured_sig = part["thoughtSignature"]
                                    if part.get("thought"):
                                        if not in_think:
                                            in_think = True
                                        chunk_text = part.get("text", "").replace("<think>", "").replace("</think>", "")
                                        if chunk_text:
                                            yield {"choices": [{"index": 0, "delta": {"reasoning_content": chunk_text}}]}
                                    elif part.get("functionCall"):
                                        # [AUTO-CONTINUE] Verrouillage d'état lors de la construction d'un appel d'outil
                                        self.is_generating_tool = True
                                        if in_think:
                                            in_think = False
                                        if self.escalation_requested:
                                            self.is_generating_tool = False
                                            continue
                                        tool_call = self._create_tool_call_part(part["functionCall"], len(self.accumulated_calls))
                                        if tool_call:
                                            pending_tool_deltas.append({"choices": [{"index": 0, "delta": {"tool_calls": [tool_call]}}]})
                                        # [AUTO-CONTINUE] Libération du verrou après complétion de l'appel d'outil
                                        self.is_generating_tool = False
                                    elif "text" in part:
                                        if self.escalation_requested:
                                            continue
                                        if in_think:
                                            in_think = False
                                        raw_t = part["text"].replace("<think>", "").replace("</think>", "")
                                        if "<EPHEMERAL_MESSAGE>" in raw_t or "CRITICAL INSTRUCTION" in raw_t:
                                            continue
                                        self.accumulated_text += raw_t; yield raw_t
                    except std_json.JSONDecodeError:
                        # Trame réseau probablement fragmentée, réintégration dans le tampon d'attente
                        buffered_lines = [full_json_str]
                        continue
                    except Exception as e:
                        if self.logger:
                            self.logger.log("stream_decode_error", {"error": str(e), "chunk": full_json_str})
                        log.error(f"[StreamProcessor] Erreur de décodage du flux: {e} - Chunk: {full_json_str[:200]}")
                        if in_think:
                            in_think = False
                        yield f"\n\n> ❌ **Erreur critique de décodage du flux API** : {str(e)}\n"
        if in_think:
            in_think = False
        if not self.escalation_requested and pending_tool_deltas:
            for delta_chunk in pending_tool_deltas:
                yield delta_chunk
        if self.logger:
            self.logger.log("api_response", self.full_raw_accumulator)

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
#   agent_engine_tool peut accéder à ce cache via sys.modules["function_echo_orchestrator"].
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
        MODEL_SELECTION: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "AUTO", "AUTO_PROM"] = Field(default="AUTO")
        # Thinking, temperature, topP et max_tokens sont des constantes ECHO (echo_constants.py v4.8).
        # Plus de valves pour ces paramètres.
        ENABLE_PAID_CREDITS: bool = Field(default=False, description="Activer l'utilisation des crédits Google One AI pour les requêtes OAuth2. Désactivé par défaut.")
        MAX_CASCADE_ATTEMPTS: int = Field(default=5, ge=3, le=10, description="Nombre max de transferts de modèles autorisés par tour.")
        # [AUTO-CONTINUE] Valve contrôlant le nombre maximal de boucles de relance automatique autorisées en cas de troncature API (MAX_TOKENS).
        ECHO_AUTO_CONTINUE_MAX: int = Field(default=2, ge=1, le=10, description="Nombre max de relances si le modèle est interrompu (MAX_TOKENS).")

    def __init__(self):
        self.valves, self.data_dir = self.Valves(), "/app/backend/data"
        if register_echo_audio_routes is not None:
            try:
                register_echo_audio_routes()
            except Exception as e:
                print(f"[Écho] ⚠️ Échec enregistrement routes audio : {e}")
        if register_echo_wm_routes is not None:
            try:
                register_echo_wm_routes()
            except Exception as e:
                print(f"[Écho] ⚠️ Échec enregistrement routes Window Manager : {e}")

    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __event_emitter__: Optional[any] = None, __request__: Optional[Any] = None, __tools__: list = None, **kwargs) -> AsyncGenerator[Union[str, Dict], None]:

        # ==============================================================================
        # COUPE-CIRCUIT (FAST-TRACK) : Tâches d'arrière-plan Open WebUI
        # ==============================================================================
        safe_metadata = __metadata__ or {}
        if safe_metadata.get("task") is not None:
            gemini_contents = []
            for msg in body.get("messages", []):
                if msg.get("role") == "system":
                    continue
                role = "user" if msg.get("role") == "user" else "model"
                parts = ensure_gemini_parts(msg.get("content", ""))

                if gemini_contents and gemini_contents[-1]["role"] == role:
                    # Fusion des rôles consécutifs (Gemini API l'exige)
                    gemini_contents[-1]["parts"].extend([{"text": "\n\n"}] + parts)
                else:
                    gemini_contents.append({
                        "role": role,
                        "parts": parts
                    })

            generation_config = {
                "maxOutputTokens": 8192,
                "thinkingConfig": {"thinkingLevel": "low"},
            }
            task_id = str(safe_metadata.get("task", ""))
            if "." in task_id:
                task_id = task_id.split(".")[-1].lower()

            # Force la sortie JSON pour les Titres, Tags et Follow-ups
            # Indispensable car Open WebUI parse la sortie via JSONCodec.loads()
            if task_id in ["title_generation", "tags_generation", "follow_up_generation"]:
                generation_config["responseMimeType"] = "application/json"

            if task_id == "tags_generation":
                user_id = __user__.get("id") if __user__ else None
                chat_id = safe_metadata.get("chat_id")
                known_user_tags = []
                current_chat_tags = []
                try:
                    from open_webui.models.tags import Tags
                    from open_webui.models.chats import Chats
                    if user_id:
                        user_tag_objs = await Tags.get_tags_by_user_id(user_id) if hasattr(Tags, "get_tags_by_user_id") else []
                        known_user_tags = [t.name for t in user_tag_objs]
                    if chat_id and user_id:
                        chat_obj = await Chats.get_chat_by_id_and_user_id(chat_id, user_id) if hasattr(Chats, "get_chat_by_id_and_user_id") else None
                        current_chat_tags = (chat_obj.meta or {}).get("tags", []) if chat_obj else []
                except Exception as tag_ctx_err:
                    log.warning(f"[ECHO-ORCHESTRATOR] Impossible de charger le contexte tags: {tag_ctx_err}")

                reconcile_directive = (
                    "\n\n### Réconciliation et Cohérence des Étiquettes\n"
                    f"- Catalogue des étiquettes existantes de l'Utilisateur : {json.dumps(known_user_tags, ensure_ascii=False)}\n"
                    f"- Étiquettes actuelles de cette discussion : {json.dumps(current_chat_tags, ensure_ascii=False)}\n"
                    "Directives strictes :\n"
                    "1. Rédiger TOUTES les étiquettes EXCLUSIVEMENT en anglais (minuscules, tiret du bas '_' au singulier).\n"
                    "2. Granularité : Privilégier des étiquettes informatives et distinctives (technologies, méthodes, outils, concepts métiers, objets d'étude).\n"
                    "3. Le catalogue sert de référence d'harmonisation pour réutiliser les termes exacts et éviter les quasi-synonymes, mais DOIT laisser place à de NOUVELLES étiquettes distinctives dès qu'un concept clé distinct le justifie.\n"
                    "4. CONSERVER les étiquettes actuelles de cette discussion si les thèmes correspondants demeurent pertinents dans l'échange.\n"
                    "5. Quota : retourner un objet JSON contenant entre 2 et 16 étiquettes au total."
                )
                if gemini_contents and gemini_contents[-1]["parts"]:
                    gemini_contents[-1]["parts"].append({"text": reconcile_directive})

            payload = {
                "contents": gemini_contents,
                "generationConfig": generation_config
            }

            try:
                # Appel direct au modèle ZEPHYR, by-pass total de l'agent ECHO (sans streaming)
                res = await EchoGeminiClient.call(
                    target_model=MODEL_ZEPHYR,
                    payload=payload,
                    user_id=__user__["id"] if __user__ else "system",
                    events=None,
                    chat_id="stateless_bypass",
                    enable_paid_credits=False
                )
                try:
                    parts_out = res.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    raw_text = "".join(
                        p.get("text", "") for p in parts_out
                        if isinstance(p, dict) and not p.get("thought")
                    )
                    text, _ = split_thought_process(raw_text)
                    if text:
                        yield text
                except (KeyError, IndexError):
                    pass
            except Exception as e:
                yield f"ECHO Fast-Track Error: {str(e)}"
            return

        # ==============================================================================
        # SUITE NORMALE DU PIPE ECHO (Agentivité, RAG, Outils...)
        # ==============================================================================
        if not __user__:
            yield "❌ Identité manquante."; return
        user_valves = __user__.get("valves") or self.UserValves()
        chat_id = kwargs.get("__chat_id__") or body.get("chat_id") or (__metadata__.get("chat_id") if __metadata__ else None)
        events = EchoEvents(__event_emitter__, user_id=__user__["id"], chat_id=chat_id)
        orch = Orchestrator(self.valves, user_valves, self.data_dir, __user__["id"], chat_id)
        # auth = AuthService(user_id=__user__["id"])  # [192.36] DÉSACTIVÉ : AuthService n'existe plus.
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
                # Mécanisme de fenêtre glissante (Garbage Collection FIFO)
                # Préserve les ECHO_TOOLS_CACHE_MAX_SIZE dernières sessions actives.
                # Protège le processus Uvicorn d'une fuite de mémoire à long terme.
                if len(_TOOLS_CACHE) > ECHO_TOOLS_CACHE_MAX_SIZE:
                    _TOOLS_CACHE.pop(next(iter(_TOOLS_CACHE)))
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
        _settings = EchoStateManager(user_id=__user__["id"])
        _settings.save_setting("model_policy", user_valves.MODEL_SELECTION)
        _settings.save_setting("enable_paid_credits", str(user_valves.ENABLE_PAID_CREDITS))

        # --- [NOUVEAU] DETECTION ET INTERCEPTION DE CLÉ API ---
        api_key_from_filter = body.get("_api_key")

        # auth = AuthService(user_id=__user__["id"])  # [192.36] DÉSACTIVÉ : AuthService n'existe plus.
        from echo_auth import AuthService
        auth = AuthService(user_id=__user__["id"])
        echo_auth = EchoAuth(user_id=__user__["id"])

        if api_key_from_filter:
            await events.status("🔒  Validation de l'authentification Google...")
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

        # Résolution du Registre des Fournisseurs d'Accès (Cache local pour ce tour de pipe)
        auth_providers = await echo_auth.get_ordered_auth_providers(__user__["id"])

        # --- AUTHENTIFICATION PKCE (Authorization Code + PKCE RFC 7636) ---
        # Tunnel SSH ephemere asyncssh - ports dynamiques - multi-user natif.
        if not auth_providers:
            _ea = EchoAuth(user_id=__user__["id"])
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
                ok, auth_url, server_ip, ssh_port, cb_port, temp_pwd = \
                    await auth.initiate_pkce_flow(request=__request__)
                if not ok:
                    yield "\u274c Impossible de lancer le flow PKCE.\n\n" + auth.get_auth_prompt()
                    return

                # Persister l'URL pour les messages suivants
                _ea.save_api_key("pkce_auth_url", auth_url)

                # Lancer le serveur callback en background (non bloquant)
                # Préservation de la référence pour éviter le Garbage Collection de la tâche
                import asyncio
                global _BACKGROUND_TASKS
                if '_BACKGROUND_TASKS' not in globals():
                    _BACKGROUND_TASKS = set()

                pkce_task = asyncio.create_task(auth.await_pkce_callback())
                _BACKGROUND_TASKS.add(pkce_task)
                pkce_task.add_done_callback(_BACKGROUND_TASKS.discard)

                yield auth.get_auth_prompt(
                    auth_url=auth_url,
                    server_ip=server_ip,
                    ssh_port=ssh_port,
                    cb_port=cb_port,
                    temp_pwd=temp_pwd
                )

            except Exception as e:
                yield f"\u274c Erreur PKCE : {str(e)}\n\n" + auth.get_auth_prompt()
            return

        # --- [NOUVEAU] ROUTAGE DYNAMIQUE (Fluctuation Continue) ---
        model_selection = user_valves.MODEL_SELECTION
        last_model = orch.user_data_manager.get_last_active_model()

        # Reverse-lookup (Auto-heal SQLite)
        if last_model and last_model != "aucun" and last_model not in ECHO_MODELS_REGISTRY:
            _found_model = False
            for logical_key, config in ECHO_MODELS_REGISTRY.items():
                if last_model in [config.get("ai_studio_id"), config.get("ca_model_id")]:
                    last_model = logical_key
                    _found_model = True
                    break
            if not _found_model:
                last_model = MODEL_ZEPHYR  # Fallback sécurisé pour les orphelins

        def _get_ui_display(model_key: str) -> str:
            config = ECHO_MODELS_REGISTRY.get(model_key, {})
            ai_id = config.get("ai_studio_id")
            ca_id = config.get("ca_model_id")
            if ai_id and ca_id and ai_id != ca_id:
                tech_str = f"{ai_id} | {ca_id}"
            else:
                tech_str = ai_id or ca_id or ""
            return f"{model_key} [{tech_str}]" if tech_str else model_key

        incoming_msgs = body.get("messages", [])
        is_tool_continuation = bool(incoming_msgs and incoming_msgs[-1].get("role") == "tool")

        if is_tool_continuation:
            # Sous-tour d'outil : Le modèle actif poursuit le traitement de son propre outil.
            # L'origine du tour utilisateur est préservée via le Verrou de Tour (interdiction d'écraser avec last_model).
            target_model = last_model or MODEL_ZEPHYR
            saved_turn_origin = orch.user_data_manager.state_manager.get_session_setting("turn_origin_model")
            origine_model = saved_turn_origin if saved_turn_origin else (last_model or "aucun")
        elif model_selection in ["AUTO", "AUTO_PROM"]:
            # Nouveau tour utilisateur : Calcul normal de la cible et fixation du Verrou de Tour
            ceiling = MODEL_PROMETHEUS if model_selection == "AUTO_PROM" else MODEL_APOLLO
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
                await events.status(f"🧠  Reprise du contexte ({_get_ui_display(target_model)})...")
            else:
                target_model = MODEL_ZEPHYR
                origine_model = "aucun"
                await events.status(f"🧠  Initialisation de session ({_get_ui_display(MODEL_ZEPHYR)})...")
            # Enregistrement du Verrou de Tour pour les sous-tours d'outils subséquents
            orch.user_data_manager.state_manager.save_session_setting("turn_origin_model", origine_model)
        else:
            target_model = model_selection
            origine_model = last_model if last_model else "aucun"
            orch.user_data_manager.state_manager.save_session_setting("turn_origin_model", origine_model)
            await events.status(f"Model Fixé : {_get_ui_display(target_model)}")

        # L'origine est passée à l'Orchestrateur pour la résolution des placeholders
        orch.model_origin = origine_model

        # Reconstruction contexte (Bit-Perfect)
        context = await orch.prepare_context(body, chat_id, target_model, __metadata__, events)

        if target_model and origine_model and target_model != origine_model and origine_model != "aucun":
            orch._mutate_context_identity(context, target_model, origine_model)

        # --- [NOUVEAU] CONFIGURATION CASCADE ---
        is_auto = user_valves.MODEL_SELECTION in ["AUTO", "AUTO_PROM"]
        # Détermination des niveaux autorisés pour le schéma de l'outil (Approach: Clean Prompt)
        niveaux_autorises = ["MODEL_APOLLO"]
        if user_valves.MODEL_SELECTION == "AUTO_PROM":
            niveaux_autorises.append("MODEL_PROMETHEUS")

        max_cascade_attempts = user_valves.MAX_CASCADE_ATTEMPTS
        cascade_attempt = 0
        # [AUTO-CONTINUE] Initialisation des compteurs de relance pour sécuriser la boucle de génération
        max_auto_continue = user_valves.ECHO_AUTO_CONTINUE_MAX
        auto_continue_attempts = 0
        cumulative_usage_stats = {"promptTokenCount": 0, "cachedContentTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0}

        # [NOUVEAU] HISTORIQUE DE CASCADE POUR SUTURE & SHADOW
        cascade_history = []
        current_cumul = body.get("_echo_last_cumul")
        meta_init = {**(__metadata__ or {}), **body.get("metadata", {})}
        user_msg_id = meta_init.get("_echo_user_msg_id") or meta_init.get("user_message_id")
        asst_msg_id = kwargs.get("__message_id__") or meta_init.get("message_id")
        failed_models_this_turn = set()
        applied_model = target_model
        is_fallback_cascade = False

        while cascade_attempt < max_cascade_attempts:
            cascade_attempt += 1

            # 1. Synchronisation de l'AEC d'identité si le modèle actif a divergé
            if target_model != applied_model:
                orch._mutate_context_identity(context, target_model, applied_model)
                applied_model = target_model

            # 2. Drainage des directives et événements système en attente
            from echo_aec import EchoAEC
            aec_directive_xml, drained_raw = EchoAEC.drain_pending_directives(orch.user_data_manager.state_manager)
            if aec_directive_xml:
                directive_part = [{"text": aec_directive_xml}]
                directive_msg = {"role": "user", "parts": directive_part}
                context.append(directive_msg)

                # Suture sémantique pour capture bit-perfect dans le Shadow
                inv_d = orch.user_data_manager.calculate_invariant("user", directive_part)
                current_cumul = orch.user_data_manager.calculate_cumulative(inv_d, current_cumul)
                orch.user_data_manager.index_suture(current_cumul, chat_id, inv_d, current_cumul, user_msg_id)
                cascade_history.append(directive_msg)

            # --- [NOUVEAU] RÉSOLUTION DYNAMIQUE DES INSTRUCTIONS SYSTÈME ---
            sys_instr_raw = "\n".join([m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"]) or "Le Modèle est ECHO."
            resolved_sys = resolve_placeholders(sys_instr_raw, target_model, orch.model_origin)
            sys_instr = {"parts": [{"text": resolved_sys}]}

            gen_config = get_generation_config(
                target_model,
                is_fallback_cascade=is_fallback_cascade,
                include_thoughts=True,
            )

            payload = {
                "contents": context,
                "systemInstruction": sys_instr,
                "generationConfig": gen_config
            }

            tools = convert_owui_tools(body.get("tools"), user_valves.MODEL_SELECTION)

            # --- [NOUVEAU] INJECTION OUTIL CHANGEMENT COGNITIF (BIDIRECTIONNEL) ---
            if is_auto:
                # Construction simplifiée : tous les modèles dispos, moins le modèle actif
                menu_escalade = ["MODEL_ZEPHYR", "MODEL_APOLLO"]
                if user_valves.MODEL_SELECTION == "AUTO_PROM":
                    menu_escalade.append("MODEL_PROMETHEUS")
                target_identity = get_model_identity(target_model)
                if target_identity in menu_escalade:
                    menu_escalade.remove(target_identity)

                # Exclusion formelle des modèles ayant échoué lors de ce tour (Anti-boucle)
                for failed_m in failed_models_this_turn:
                    failed_id = get_model_identity(failed_m)
                    if failed_id in menu_escalade:
                        menu_escalade.remove(failed_id)

                if menu_escalade:
                    escalation_tool = {
                        "name": "new_cognitive_level",
                        "description": (
                            "Outil de transition cognitive dynamique exécutant le Protocole PCD (le niveau actuel est exclu de l'enum `niveau_requis`).\n\n"
                            "**Exclusivité d'appel d'outil** : Le Modèle doit circonscrire l'appel à `new_cognitive_level` au sein d'un appel isolé de tout autre appel parallèle à un autre outil."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "niveau_requis": {
                                    "type": "string",
                                    "enum": menu_escalade,
                                    "description": "Identifiant du modèle cognitif cible requis dans le contexte actuel.",
                                },
                                "plan_de_transfert": {
                                    "type": "string",
                                    "description": (
                                        "Plan de relais Markdown à haut SNR transmis au niveau cible. DOIT structurer : "
                                        "1) Objectif, 2) Analyse de l'état courant, 3) Amorce de stratégie, 4) Contraintes repérées."
                                    ),
                                },
                                "raison": {
                                    "type": "string",
                                    "description": "Justification synthétique du changement de niveau cognitif.",
                                },
                            },
                            "required": ["niveau_requis", "plan_de_transfert"],
                        },
                    }
                    if not tools:
                        tools = [{"functionDeclarations": []}]
                    decl_key = "functionDeclarations" if "functionDeclarations" in tools[0] else "function_declarations"
                    tools[0][decl_key].append(escalation_tool)

            if tools:
                payload["tools"] = tools
                payload["tool_config"] = {"functionCallingConfig": {"mode": "AUTO"}}

            if orch.logger:
                orch.logger.log("google_request", payload, metadata={"cascade_attempt": cascade_attempt, "model": target_model})
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
                # Payload rejeté (400) : le contexte est invalide pour TOUS les modèles.
                # Une cascade ne peut pas corriger l'erreur et masquerait la cause réelle.
                if isinstance(e, FatalAPIError):
                    yield f"❌ Requête rejetée par l'API (400, payload invalide), sans cascade. ({str(e)})"
                    break
                # GESTION DES ÉCHECS TECHNIQUES — CASCADE DESCENDANTE
                if is_auto and cascade_attempt < max_cascade_attempts:
                    cascade_order = get_descending_cascade(target_model, include_start=False)
                    if cascade_order:
                        prev_model = target_model
                        target_model = cascade_order[0]
                        failed_models_this_turn.add(prev_model)
                        is_fallback_cascade = True

                        await events.status(f"⚡ {prev_model} indisponible → cascade vers {target_model}")
                        await events.toast(f"⚡ Cascade : {prev_model} → {target_model} (erreur: {str(e)[:80]})", "warning")

                        # Inscription de la directive épurée (drainée au début du tour suivant)
                        from echo_aec import EchoAEC
                        err_summary = str(e)[:60]
                        msg_directive = f"Le modèle {prev_model} est indisponible ({err_summary}). Repli forcé vers {target_model}."
                        EchoAEC.record_event(
                            state_manager=orch.user_data_manager.state_manager,
                            event_name=f"cascade_{prev_model}_to_{target_model}",
                            summary=msg_directive,
                            resource_type="aec_directive"
                        )
                    else:
                        # Plus de modèle inférieur → échec terminal
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
                # Sanctuarisation du promptTokenCount conversationnel initial :
                # Ne pas écraser avec le prompt temporaire gonflé des cascades cognitives internes (relais, schémas).
                if cumulative_usage_stats["promptTokenCount"] == 0:
                    cumulative_usage_stats["promptTokenCount"] = proc.usage_stats.get("promptTokenCount", 0)
                cumulative_usage_stats["cachedContentTokenCount"] = proc.usage_stats.get("cachedContentTokenCount", cumulative_usage_stats["cachedContentTokenCount"])
                cumulative_usage_stats["candidatesTokenCount"] += proc.usage_stats.get("candidatesTokenCount", 0)
                cumulative_usage_stats["totalTokenCount"] = (
                    cumulative_usage_stats["promptTokenCount"]
                    + cumulative_usage_stats["candidatesTokenCount"]
                )

            # --- [NOUVEAU] GESTION DE LA CASCADE ---
            if is_auto and proc.escalation_requested:
                req = proc.escalation_requested
                target_req = req.get("niveau_requis")

                # Mapping explicite pour gérer la montée ET la redescente
                new_target = get_model_identity(target_req)

                if not new_target or new_target == "UNKNOWN":
                    sys_tc_id = f"echo-sys-{secrets.token_hex(4)}"
                    # Signalement d'erreur de paramètre au modèle actuel
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "new_cognitive_level", "args": req, "id": sys_tc_id}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "error", "message": f"ERREUR : Niveau '{target_req}' inconnu. Choisissez parmi MODEL_ZEPHYR, MODEL_APOLLO ou MODEL_PROMETHEUS."}, "id": sys_tc_id}}]
                    })
                    continue

                # Vérification des droits (Valve)
                if user_valves.MODEL_SELECTION == "AUTO" and new_target == MODEL_PROMETHEUS:
                    await events.status("⚠️  Transfert vers MODEL_PROMETHEUS refusé (Valve AUTO).")
                    sys_tc_id = f"echo-sys-{secrets.token_hex(4)}"
                    # Signalement de refus au modèle actuel
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "new_cognitive_level", "args": req, "id": sys_tc_id}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "error", "model_requested": target_req, "model_used": target_model, "warning": f"{target_req} unavailable (policy)", "message": f"Transfert vers {target_req} refusé. Traitez avec {target_model}."}, "id": sys_tc_id}}]
                    })
                    continue  # On reboucle avec le MÊME target_model

                if new_target == target_model:
                    sys_tc_id = f"echo-sys-{secrets.token_hex(4)}"
                    await events.status(f"⚠️ï¸  Auto-transfert annulé ({target_req}).")
                    context.append({
                        "role": "model",
                        "parts": [{"functionCall": {"name": "new_cognitive_level", "args": req, "id": sys_tc_id}, "thoughtSignature": proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION}]
                    })
                    context.append({
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "new_cognitive_level", "response": {"status": "error", "model_requested": target_req, "model_used": target_model, "warning": "Déjà sur le modèle", "message": f"ERREUR : Le système est déjà sur le modèle {target_req}. Le Modèle DOIT poursuivre sa tâche."}, "id": sys_tc_id}}]
                    })
                    continue

                await events.status(f"🚀 Transfert cognitif vers {_get_ui_display(new_target)}...")

                if proc.captured_sig:
                    orch.user_data_manager.save_call_bridge(f"esc-{secrets.token_hex(4)}", proc.captured_sig, "new_cognitive_level", req)

                plan_md = req.get("plan_de_transfert", "Exécution du relais.")

                # 1. Mutation Chirurgicale de l'identité dans le contexte
                orch._mutate_context_identity(context, new_target, target_model)
                # Mise à jour du Verrou de Tour : l'ancien modèle devient l'origine scellée pour la suite du tour
                orch.user_data_manager.state_manager.save_session_setting("turn_origin_model", target_model)

                # 2. Suture Sémantique (Relais Protocolé avec réinjection signée du texte précédent)
                sig_to_apply = proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION
                tool_io = {"calls": [{"id": c.get("id"), "name": c["name"], "args": c["args"]} for c in proc.accumulated_calls]} if proc.accumulated_calls else None
                model_parts = []
                if proc.accumulated_text:
                    model_parts.append({"text": proc.accumulated_text})

                sys_tc_id = f"echo-sys-{secrets.token_hex(4)}"
                model_parts.append({"functionCall": {"name": "new_cognitive_level", "args": req, "id": sys_tc_id}})

                # Récupération des appels parallèles orphelins
                if proc.accumulated_calls:
                    for c in proc.accumulated_calls:
                        call_part = {"name": c["name"], "args": c["args"]}
                        if "id" in c:
                            call_part["id"] = c["id"]
                        model_parts.append({"functionCall": call_part})

                # Injection de la thoughtSignature requise par l'API Gemini 3+
                # Selon la spec, pour des appels parallèles, seul le PREMIER functionCall doit recevoir la signature.
                # (https://ai.google.dev/gemini-api/docs/thinking#signatures)
                attach_thought_signature(model_parts, sig_to_apply)

                # [NOUVEAU] INDEXATION INTERMÉDIAIRE (SUTURE)
                model_msg = {"role": "model", "parts": model_parts}
                inv = orch.user_data_manager.calculate_invariant("model", model_parts, tool_io=tool_io)
                new_cumul = orch.user_data_manager.calculate_cumulative(inv, current_cumul)
                orch.user_data_manager.index_suture(new_cumul, chat_id, inv, current_cumul, user_msg_id)
                orch.user_data_manager.save_cognitive(new_cumul, sig_to_apply, proc.accumulated_text, tool_io, asst_msg_id, target_model)
                cascade_history.append(model_msg)
                current_cumul = new_cumul

                # Structure status alignée sur wrap_cascade_output (KEYs uniquement)
                escalation_status = {
                    "status": "success",
                    "model_requested": target_req,
                    "model_used": target_req,  # À ce stade l'escalade est approuvée (clamping fait en amont)
                }
                msg = f"Transfert effectué vers {target_req}."
                if proc.accumulated_text and proc.accumulated_text.strip():
                    msg += (
                        " Le préambule textuel précédent a déjà été affiché à l'Utilisateur : "
                        "poursuivez directement le traitement de la requête sans répéter les salutations ni l'annonce de transition."
                    )
                user_resp_parts = [{"functionResponse": {"name": "new_cognitive_level", "response": {**escalation_status, "message": msg, "plan": plan_md}, "id": sys_tc_id}}]

                # Annulation formelle des appels parallèles orphelins pour préserver le schéma strict
                if proc.accumulated_calls:
                    for c in proc.accumulated_calls:
                        cancel_resp = {
                            "name": c["name"],
                            "response": {"status": "error", "message": "Exécution annulée (Adaptation cognitive prioritaire). Le Modèle doit relancer cet outil."},
                        }
                        if "id" in c:
                            cancel_resp["id"] = c["id"]
                        user_resp_parts.append({"functionResponse": cancel_resp})

                user_msg = {"role": "user", "parts": user_resp_parts}
                inv_u = orch.user_data_manager.calculate_invariant("user", user_resp_parts)
                new_cumul_u = orch.user_data_manager.calculate_cumulative(inv_u, current_cumul)
                orch.user_data_manager.index_suture(new_cumul_u, chat_id, inv_u, current_cumul, user_msg_id)
                cascade_history.append(user_msg)
                current_cumul = new_cumul_u

                context.append(model_msg)
                context.append(user_msg)

                target_model = new_target
                applied_model = new_target
                is_fallback_cascade = False
                continue
            else:
                # [NOUVEAU] INDEXATION FINALE (SUTURE)
                sig_to_apply = proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION
                tool_io = {"calls": [{"id": c.get("id"), "name": c["name"], "args": c["args"]} for c in proc.accumulated_calls]} if proc.accumulated_calls else None
                model_parts = []
                if proc.accumulated_text:
                    model_parts.append({"text": proc.accumulated_text})
                if proc.accumulated_calls:
                    for c in proc.accumulated_calls:
                        call_part = {"name": c["name"], "args": c["args"]}
                        if "id" in c:
                            call_part["id"] = c["id"]
                        model_parts.append({"functionCall": call_part})

                if model_parts:
                    attach_thought_signature(model_parts, sig_to_apply)
                    model_msg = {"role": "model", "parts": model_parts}
                    inv = orch.user_data_manager.calculate_invariant("model", model_parts, tool_io=tool_io)
                    new_cumul = orch.user_data_manager.calculate_cumulative(inv, current_cumul)
                    orch.user_data_manager.index_suture(new_cumul, chat_id, inv, current_cumul, user_msg_id)
                    orch.user_data_manager.save_cognitive(new_cumul, sig_to_apply, proc.accumulated_text, tool_io, asst_msg_id, target_model)
                    cascade_history.append(model_msg)
                    current_cumul = new_cumul

                # [NOUVEAU] GESTION DE L'AUTO-CONTINUE (MAX_TOKENS)
                # Mécanisme de relance encapsulée empêchant l'arrêt prématuré de la génération.
                if proc.last_finish_reason == "MAX_TOKENS" and auto_continue_attempts < max_auto_continue:
                    auto_continue_attempts += 1
                    if proc.is_generating_tool:
                        # Cas 1 : L'interruption a eu lieu durant la structuration JSON d'un appel de fonction.
                        # Le payload partiel est rejeté par le StreamProcessor. On injecte une directive punitive pour forcer la concision.
                        await events.status("⚠️ Appel d'outil tronqué (MAX_TOKENS). Reprise et correction...")
                        await events.toast("Appel d'outil trop volumineux : Reprise automatique de la génération.", "warning")
                        texte_outil = "Erreur : L'appel d'outil précédent a échoué car les arguments étaient trop volumineux (limite MAX_TOKENS atteinte). Le modèle doit relancer l'outil avec des paramètres strictement plus concis ou expliquer la situation."
                        xml_outil = f'<artifact id="AEC_directive" source="Système">\\n{texte_outil}\\n</artifact>'
                        user_resp_parts = [{"text": xml_outil}]
                    else:
                        # Cas 2 : L'interruption a eu lieu sur du texte brut.
                        # Le texte existant a déjà été indexé. On injecte une directive de continuation pure.
                        await events.status("🔄 Reprise automatique de la génération (MAX_TOKENS)...")
                        await events.toast("Limite de contexte (MAX_TOKENS) atteinte : Reprise automatique.", "info")
                        texte_gene = "Erreur : La génération a été interrompue car la limite de tokens (MAX_TOKENS) a été atteinte. Le modèle doit poursuivre la génération du texte à partir du point de troncature exact, sans introduction."
                        xml_gene = f'<artifact id="AEC_directive" source="Système">\\n{texte_gene}\\n</artifact>'
                        user_resp_parts = [{"text": xml_gene}]

                    # Suture sémantique de l'événement système pour maintenir l'invariant cognitif bit-perfect
                    user_msg = {"role": "user", "parts": user_resp_parts}
                    inv_u = orch.user_data_manager.calculate_invariant("user", user_resp_parts)
                    new_cumul_u = orch.user_data_manager.calculate_cumulative(inv_u, current_cumul)
                    orch.user_data_manager.index_suture(new_cumul_u, chat_id, inv_u, current_cumul, user_msg_id)
                    cascade_history.append(user_msg)
                    current_cumul = new_cumul_u

                    # Mise à jour du contexte pour la boucle suivante
                    context.append(model_msg)
                    context.append(user_msg)
                    continue

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
                has_req_tag = any(
                    isinstance(p, dict) and "<REQUETE_UTILISATEUR>" in str(p.get("text", ""))
                    for p in full_user_parts
                )
                if isinstance(user_text, str) and user_text.strip() and not has_req_tag:
                    full_user_parts.append({"text": resolve_placeholders(user_text, target_model, orch.model_origin)})
                orch.user_data_manager.save_shadow(user_msg_id, user_updated_at, full_user_parts, chat_id, "user")

            # 2. Scellement du Registre Unifié et Rangement
            files_to_seal = meta.get("_echo_files_to_seal", [])
            for f in files_to_seal:
                if f.get("status") == "success":
                    content_to_save = None
                    if f.get("type") == FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"]:
                        content_to_save = f.get("content")
                    elif f.get("type") == FILE_INGESTION_STATUS["PUT_IN_CONTEXT"] and f.get("sub_type") == "text":
                        content_to_save = f.get("content")
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
            if asst_msg_id and cascade_history:
                final_shadow_content = proc._filter_cascade_for_shadow(cascade_history)
                if final_shadow_content:
                    orch.user_data_manager.save_shadow(asst_msg_id, int(time.time()), final_shadow_content, chat_id, "assistant")
            user_msg_id = meta.get("_echo_user_msg_id") or meta.get("user_message_id")
            if cascade_history and user_msg_id:
                try:
                    orch.user_data_manager.state_manager.save_auth_data(f"cascade_{user_msg_id}", std_json.dumps(cascade_history).decode('utf-8'))
                except Exception as e:
                    log.error(f"[PipeEngine] Erreur sauvegarde cascade KV: {e}")

            # Sauvegarde des ponts d'outils pour la navigation future
            for c in proc.accumulated_calls:
                orch.user_data_manager.save_call_bridge(c["id"], proc.captured_sig or MAGIC_KEY_SKIP_VALIDATION, c["name"], c["args"])

        # --- PERSISTANCE DES MÉTRIQUES CONTEXTUELLES ---
        if cumulative_usage_stats.get("totalTokenCount", 0) > 0:
            orch.user_data_manager.save_context_stats(cumulative_usage_stats)
            yield {"usage": {"prompt_tokens": cumulative_usage_stats.get("promptTokenCount", 0), "completion_tokens": cumulative_usage_stats.get("candidatesTokenCount", 0), "total_tokens": cumulative_usage_stats.get("totalTokenCount", 0)}}
        yield ""
