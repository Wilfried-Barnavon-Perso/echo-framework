# -*- coding: utf-8 -*-
"""
title: ECHO Echo Core
author: Wilfried BARNAVON
version: 5.231.31.1
description: Fonctions cognitives et utilitaires pures.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.31.1: Prise en charge asynchrone de Chats.get_chat_by_id et correction de l'appel EchoGeminiClient.call_distillation (is_json=False, system_instruction).
# 5.231.28.2: Implémentation de EchoCompactionEngine en % de CONTEXT_MAX (63% HEAD / 37% TAIL de 20%), zéro troncature, conservation des balises AEC et fallback ZoneInfo.
# 5.231.10.1: Suppression de detect_verbal_escalation ; conservation de attach_thought_signature (conformité Gemini 3/4).
# 5.231.7.1: Factorisation de attach_thought_signature (conformité Gemini 3/4).
# 5.227.1.1: Suppression de EchoCompactionEngine suite au remplacement du compactage in-place par resume_in_new_chat_action.
import os
import re
import time
import sqlite3
import logging
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple
from echo_state_manager import EchoStateManager
from echo_aec import EchoAEC
import orjson as std_json
from echo_paths import get_echo_version
from echo_constants import CHARS_PER_TOKEN, AEC_REMINDERS

logger = logging.getLogger("ECHO_CORE")

def build_model_identity(m_id: str) -> str:
    if m_id == "aucun": return "aucun"
    from echo_constants import get_model_identity
    cat = get_model_identity(m_id)
    return cat

def resolve_placeholders(text: str, model_id: str, model_origin: str = "unknown") -> str:
    if not isinstance(text, str): return text
    version = get_echo_version() or "##VERSION_ERR##"
    resolved = text.replace("##ECHO_VERSION##", version)
    resolved = resolved.replace("##MODEL_ID##", build_model_identity(model_id))
    resolved = resolved.replace("##MODEL_ORIGIN##", build_model_identity(model_origin))
    return resolved

def ensure_gemini_parts(content: Any, model_id: str = "unknown", model_origin: str = "unknown") -> List[Dict]:
    parts = []
    if isinstance(content, str):
        if content.strip(): parts.append({"text": resolve_placeholders(content, model_id, model_origin)})
    elif isinstance(content, list):
        for p in content:
            if not isinstance(p, dict): continue
            new_part = {}
            if "text" in p: 
                new_part["text"] = resolve_placeholders(p["text"], model_id, model_origin)
            elif p.get("type") == "image_url" and "image_url" in p:
                url = p["image_url"].get("url", "")
                if url.startswith("data:"):
                    try:
                        mime, b64 = url.split(";", 1)[0].replace("data:", ""), url.split(",", 1)[1]
                        new_part["inlineData"] = {"mimeType": mime, "data": b64}
                    except Exception:
                        pass
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

def estimate_token_size(content: Any) -> int:
    """Estime le poids cognitif (tokens) via une heuristique rapide sur la longueur de la chaîne."""
    
    def _estimate_media_tokens(mime_type: str, b64_length: int) -> int:
        # Poids décodé estimé en Mo
        size_mb = (b64_length * 0.75) / (1024 * 1024)
        
        if mime_type.startswith("image/"):
            # 258 tokens de base + tuiles pour les hautes résolutions (approx. 1 tuile par 0.5 Mo)
            return 258 + int(size_mb / 0.5) * 258
        elif mime_type == "application/pdf":
            # Gemini facture 258 tokens par page. Moyenne d'environ 1 page / 100Ko
            return int(size_mb * 2500) or 258
        elif mime_type.startswith("audio/"):
            # ~32 tokens / sec. Un MP3 128kbps = ~1Mo / min -> ~1920 tokens / Mo
            return int(size_mb * 2000)
        elif mime_type.startswith("video/"):
            # ~263 tokens / sec. Vidéo compressée ~10Mo / min -> ~1578 tokens / Mo
            return int(size_mb * 1500)
        else:
            return 258 # Fallback générique

    def _calc_size(item: Any) -> int:
        if isinstance(item, dict):
            media_node = item.get("inlineData") or item.get("inline_data")
            if media_node and isinstance(media_node, dict):
                mime_type = media_node.get("mimeType", media_node.get("mime_type", "image/unknown"))
                b64_data = media_node.get("data", "")
                b64_length = len(b64_data) if isinstance(b64_data, str) else 0
                
                tokens = _estimate_media_tokens(mime_type, b64_length)
                
                for k, v in item.items():
                    if k not in ["inlineData", "inline_data"]:
                        tokens += len(str(k)) // CHARS_PER_TOKEN
                        tokens += _calc_size(v)
                return tokens
            elif "image_url" in item:
                tokens = 258
                for k, v in item.items():
                    if k != "image_url":
                        tokens += len(str(k)) // CHARS_PER_TOKEN
                        tokens += _calc_size(v)
                return tokens
            else:
                tokens = 0
                for k, v in item.items():
                    tokens += len(str(k)) // CHARS_PER_TOKEN
                    tokens += _calc_size(v)
                return tokens
        elif isinstance(item, list):
            return sum(_calc_size(i) for i in item)
        elif isinstance(item, str):
            return len(item) // CHARS_PER_TOKEN
        else:
            return len(str(item)) // CHARS_PER_TOKEN

    if isinstance(content, (dict, list)):
        try:
            return _calc_size(content)
        except Exception:
            try:
                return len(std_json.dumps(content)) // CHARS_PER_TOKEN
            except Exception:
                pass

    return len(str(content)) // CHARS_PER_TOKEN

def smart_truncate_history(history: list, start_index: int = 0) -> int:
    """
    Supprime le bloc le plus ancien de l'historique de manière intelligente.
    Garantit l'intégrité structurelle des appels d'outils (functionCall + functionResponse).
    Retourne la taille en tokens des éléments supprimés (0 si rien n'a été supprimé).
    """
    if len(history) <= start_index:
        return 0
        
    msg = history[start_index]
    parts = msg.get("parts", [])
    
    removed_size = estimate_token_size(msg)
    
    if not isinstance(parts, list):
        history.pop(start_index)
        return removed_size
        
    is_call = any(isinstance(p, dict) and "functionCall" in p for p in parts)
    
    history.pop(start_index)
    
    if is_call and start_index < len(history):
        next_parts = history[start_index].get("parts", [])
        if isinstance(next_parts, list) and any(isinstance(p, dict) and "functionResponse" in p for p in next_parts):
            removed_size += estimate_token_size(history[start_index])
            history.pop(start_index)
            
    return removed_size

def split_thought_process(text: str) -> Tuple[str, Optional[str]]:
    if not isinstance(text, str): return text, None
    for tag in ["think", "thought"]:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            thoughts = match.group(1).strip()
            clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
            return clean_text, thoughts
    return text, None

def wrap_tool_output(text: str, status: dict = None, echo_tool_multiparts: List[dict] = None, user_id: str = None, chat_id: str = None, metadata: dict = None) -> dict:
    aec_events = None
    if user_id and chat_id and metadata is not None:
        # [NOUVEAU] Rappels Cognitifs par Outil (AVANT le delta)
        call_count = metadata.get("_echo_tool_call_count", 0) + 1
        metadata["_echo_tool_call_count"] = call_count
        
        state_manager = None
        for reminder in AEC_REMINDERS:
            threshold = reminder.get("tool_calls_threshold", 0)
            if threshold > 0 and call_count % threshold == 0:
                if state_manager is None:
                    state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
                EchoAEC.record_event(
                    state_manager=state_manager, 
                    event_name=reminder["id"], 
                    status="avertissement", 
                    summary=reminder["message"],
                    resource_type="aec_directive"
                )

        last_check = metadata.get("_echo_last_event_check_at")
        from echo_constants import ECHO_SUBAGENT_CONTEXT
        is_subagent = ECHO_SUBAGENT_CONTEXT.get().get("is_subagent", False)
        if last_check and not is_subagent:
            try:
                state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
                delta = EchoAEC.get_pending_events(state_manager, float(last_check))
                if delta:
                    # Résolution du fuseau horaire de l'utilisateur depuis les variables OWUI
                    user_tz_str = metadata.get("variables", {}).get("{{CURRENT_TIMEZONE}}", "UTC")
                    try:
                        user_tz = ZoneInfo(user_tz_str)
                    except Exception:
                        user_tz = ZoneInfo("UTC")

                    events = [{
                        "type": r.get("status", "unknown"),
                        "name": r.get("name", "unnamed"),
                        "mime": r.get("mime"),
                        "date": datetime.fromtimestamp(r.get("created_at", time.time()), tz=user_tz).strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "Système" if r.get("resource_type") in ("aec_event", "aec_directive") else "outil/HUD",
                        "message": r.get("summary"),
                        "resource_type": r.get("resource_type")
                    } for r in delta]
                    
                    # Nettoyage des clés avec des valeurs None
                    events = [{k: v for k, v in e.items() if v is not None} for e in events]
                    
                    # Appel de la fonction factorisée
                    events_text = EchoAEC.render_system_events(sys_events=events)
                    if events_text:
                        aec_events = events_text
                        metadata["_echo_last_event_check_at"] = time.time()
                        
                    # FIFO destructif : Purge EXCLUSIVE des événements purement système (AEC).
                    # Les fichiers (codex, uploads) remontés dans ce delta sont conservés.
                    for r in delta:
                        if r.get("resource_type") in ("aec_event", "aec_directive"):
                            try:
                                state_manager.delete_resource(r.get("id"))
                            except Exception as e:
                                print(f"[wrap_tool_output] Erreur purge AEC {r.get('id')}: {e}")
            except Exception as e:
                print(f"[wrap_tool_output] Erreur Delta SQLite: {e}")
            
    return {"text": text, "aec_events": aec_events, "status": status or {"status": "success"}, "echo_tool_multiparts": echo_tool_multiparts or []}

def wrap_cascade_output(text: str, model_requested: str, model_used: str, status: dict = None, echo_tool_multiparts: List[dict] = None, reason: str = None, user_id: str = None, chat_id: str = None, metadata: dict = None) -> dict:
    """
    Enrichit wrap_tool_output avec les métadonnées de cascade.
    Le LLM orchestrateur voit model_used dans le status dict ET dans un préfixe texte si le modèle a changé.
    reason : cause du changement de modèle (ex: "policy", "503/429").
    """
    s = status or {"status": "success"}
    s["model_requested"] = model_requested
    s["model_used"] = model_used
    if model_requested != model_used:
        s["status"] = "warning"
        s["warning"] = reason or f"{model_requested} unavailable"
        text = f"[Modèle effectif : {model_used} (demandé : {model_requested})]\n\n{text}"
    return wrap_tool_output(text, status=s, echo_tool_multiparts=echo_tool_multiparts, user_id=user_id, chat_id=chat_id, metadata=metadata)

def resolve_model_policy(metadata: dict, user_id: str = None) -> tuple:
    """
    Résout la politique modèle depuis __metadata__ (injecté par le Pipe).
    Fallback : lecture identity.db si absent de metadata (OWUI ne propage pas __metadata__ du pipe aux outils).
    Retourne (mode, plafond_key).
    - mode "fixed" + plafond = modèle forcé
    - mode "auto"/"auto_pro" + plafond = choix libre jusqu'au plafond
    """
    from echo_constants import ECHO_MODELS_REGISTRY
    selection = (metadata or {}).get("_echo_model_policy")

    # Fallback SQLite (echo_settings) si absent de metadata
    if not selection and user_id:
        try:
            state = EchoStateManager(user_id=user_id)
            selection = state.get_setting("model_policy")
        except Exception:
            pass

    if not selection:
        selection = "AUTO"

    if selection == "AUTO":
        return ("auto", "MODEL_APOLLO")
    elif selection == "AUTO_PROM":
        return ("auto_pro", "MODEL_PROMETHEUS")
    elif selection in ECHO_MODELS_REGISTRY:
        return ("fixed", selection)
    return ("auto", "MODEL_APOLLO")

def clamp_model(requested: str, metadata: dict, user_id: str = None) -> str:
    """
    Applique la politique du Pipe sur un modèle demandé par un outil.
    Mode fixé → retourne le modèle fixé (ignore la demande).
    Mode auto → min(demandé, plafond).
    Fallback SQLite si la politique est absente de metadata.
    """
    from echo_constants import ECHO_MODELS_REGISTRY
    # user_id depuis metadata si non fourni
    uid = user_id or (metadata or {}).get("user_id")
    mode, ceiling = resolve_model_policy(metadata, user_id=uid)
    if mode == "fixed":
        return ceiling
    req_level = ECHO_MODELS_REGISTRY.get(requested, {}).get("hierarchy")
    ceil_level = ECHO_MODELS_REGISTRY.get(ceiling, {}).get("hierarchy")
    req_level = req_level if req_level is not None else -1
    ceil_level = ceil_level if ceil_level is not None else -1
    return ceiling if req_level > ceil_level else requested

def unbox_tool_output(name: str, content: Any, model_id: str, model_origin: str = "unknown", call_id: str = None) -> List[Dict]:
    import ast
    if isinstance(content, str):
        try:
            # Utilisation du lecteur Python sécurisé (ast) pour gérer les guillemets simples du stockage SQL
            content = ast.literal_eval(content)
        except Exception:
            # Échec total de lecture : marquage comme donnée non structurée
            content = {"text": str(content), "status": {"status": "unstructured_data"}}
    
    if not isinstance(content, dict):
        content = {"text": str(content), "status": {"status": "error_format"}}
    
    text_body = content.get("text", "")
    status_meta = content.get("status", {"status": "success"})
    rich_multiparts = content.get("echo_tool_multiparts", [])
    aec_events = content.get("aec_events")

    # Protection vitale contre les valeurs scalaires renvoyées par des outils/API externes
    if not isinstance(status_meta, dict):
        status_meta = {"status": status_meta}

    response_dict = status_meta.copy()
    
    # SÉGRÉGATION 100% SAFE POUR L'API : L'AEC est injecté dans une clé XML explicite
    if aec_events:
        response_dict["AEC_evenement_systeme"] = aec_events

    if text_body:
        response_dict["result"] = resolve_placeholders(text_body, model_id, model_origin)
    else:
        # Si aucun 'text' explicite n'existe, on s'assure que le Modèle reçoive le payload brut (ex: "id" de l'automation)
        import json
        payload = {k: v for k, v in content.items() if k not in ["text", "status", "echo_tool_multiparts", "aec_events"]}
        if payload:
            response_dict["result"] = json.dumps(payload, ensure_ascii=False)

    func_resp_part = {
        "functionResponse": {
            "name": name,
            "response": response_dict
        }
    }

    if call_id:
        func_resp_part["functionResponse"]["id"] = call_id

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

def convert_owui_tools(tools: Optional[List[Dict]], model_policy: str = "AUTO") -> Optional[List[Dict]]:
    """
    Convertit les specs OWUI → format Gemini.
    Filtre dynamiquement les enum des paramètres modèle selon MODEL_SELECTION.
    """
    if not tools: return None
    from echo_constants import MODEL_ENUM_BY_POLICY, MODEL_ENUM_REFERENCE
    allowed_models = MODEL_ENUM_BY_POLICY.get(model_policy, list(MODEL_ENUM_REFERENCE))

    funcs = []
    for t in tools:
        if t.get("type") == "function":
            f = t.get("function", {})
            params = f.get("parameters", {"type": "object", "properties": {}})

            # Filtrage dynamique : tout paramètre dont l'enum est un sous-ensemble de MODEL_ENUM_REFERENCE
            for prop_name, prop_val in params.get("properties", {}).items():
                if "enum" in prop_val:
                    enum_set = set(prop_val["enum"])
                    if enum_set.issubset(MODEL_ENUM_REFERENCE):
                        prop_val["enum"] = allowed_models

            funcs.append({
                "name": f.get("name"),
                "description": f.get("description", ""),
                "parameters": params
            })
    return [{"functionDeclarations": funcs}] if funcs else None


def attach_thought_signature(
    parts: List[Dict[str, Any]],
    signature: Optional[str],
    fallback_magic_key: Optional[str] = None,
) -> None:
    """
    Attache la thoughtSignature sur les parts d'un message 'model' selon la norme stricte Gemini 3/4 :
    - Si au moins un 'functionCall' est présent, SEUL le premier 'functionCall' reçoit la signature.
    - Sinon (réponse purement textuelle), le premier bloc 'text' reçoit la signature si elle est fournie.
    """
    if not parts:
        return
    sig_to_use = signature or fallback_magic_key
    if not sig_to_use:
        return

    for p in parts:
        if isinstance(p, dict):
            p.pop("thoughtSignature", None)

    for p in parts:
        if isinstance(p, dict) and "functionCall" in p:
            p["thoughtSignature"] = sig_to_use
            return

    if signature:
        for p in parts:
            if isinstance(p, dict) and "text" in p:
                p["thoughtSignature"] = signature
                return


class EchoCompactionEngine:
    """
    Moteur de Compaction Contextuelle In-Place piloté en pourcentages de ECHO_MAX_CONTEXT_SIZE.
    Décompose l'enveloppe cible (ex: 20% de CONTEXT_MAX = 209 715 tokens) en :
    - Part compressée HEAD : ex. 63% de 20% = 12.6% de CONTEXT_MAX (~132 120 tokens max budget, plafonné à maxOutputTokens=65535 API)
    - Part gardée intacte TAIL : 1 - COMPACTION_COMPRESSED_SHARE_PCT (ex. 37% de 20% = 7.4% de CONTEXT_MAX, ~77 594 tokens max budget)
    Sans troncature des messages entrants ni retrait des balises <artifact id="AEC_...">.
    """

    SYNTHETIC_IMAGE_MARKER = "Here are the images from the tool results above. Please analyze them."
    GEMINI_MAX_OUTPUT_TOKENS = 65535

    @staticmethod
    def extract_active_branch(
        chat_dict: Any, current_message_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Reconstruit la branche active complète de messages jusqu'à history.currentId."""
        if not isinstance(chat_dict, dict):
            return []
        history = (
            chat_dict.get("history")
            if isinstance(chat_dict.get("history"), dict)
            else {}
        )
        messages_map = (
            history.get("messages")
            if isinstance(history.get("messages"), dict)
            else {}
        )
        curr_id = current_message_id or history.get("currentId")

        if messages_map and curr_id and curr_id in messages_map:
            chain = []
            visited = set()
            curr = curr_id
            while curr and curr in messages_map and curr not in visited:
                visited.add(curr)
                node = messages_map[curr]
                if isinstance(node, dict):
                    chain.append({**node, "id": node.get("id") or curr})
                    curr = node.get("parentId")
                else:
                    break
            chain.reverse()
            if chain:
                return chain

        db_msgs = chat_dict.get("messages")
        if isinstance(db_msgs, list) and db_msgs:
            return db_msgs
        return []

    @classmethod
    async def load_active_branch_messages(
        cls,
        chat_id: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Charge de manière asynchrone/hybride la branche active de messages depuis Open WebUI Chats."""
        try:
            import inspect
            from open_webui.models.chats import Chats

            res = Chats.get_chat_by_id(chat_id)
            chat_record = await res if inspect.isawaitable(res) else res
            if chat_record and getattr(chat_record, "chat", None):
                curr_id = getattr(chat_record, "current_message_id", None)
                branch = cls.extract_active_branch(chat_record.chat, curr_id)
                if branch:
                    return branch
        except Exception:
            pass
        return None

    @classmethod
    def is_real_user_turn(cls, msg: Dict[str, Any]) -> bool:
        """Détermine si un message 'user' est un vrai tour utilisateur (ignore les messages synthétiques d'images d'outils)."""
        if msg.get("role") != "user":
            return False
        content = msg.get("content", "")
        if isinstance(content, str) and cls.SYNTHETIC_IMAGE_MARKER in content:
            return False
        if isinstance(content, list):
            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and cls.SYNTHETIC_IMAGE_MARKER in item.get("text", "")
                ):
                    return False
        return True

    @classmethod
    def split_into_turns(cls, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Découpe une liste linéaire de messages (hors 'system') en tours logiques complets.
        Chaque tour débute par un vrai message 'user' et inclut toutes les réponses 'assistant'/'tool'
        subséquentes jusqu'au prochain vrai message 'user'.
        """
        turns: List[Dict[str, Any]] = []
        current_turn: Optional[Dict[str, Any]] = None

        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "system":
                continue
            if cls.is_real_user_turn(msg):
                if current_turn is not None:
                    turns.append(current_turn)
                current_turn = {
                    "turn_idx": len(turns),
                    "start_msg_idx": idx,
                    "user_msg_id": msg.get("id"),
                    "messages": [msg],
                    "tokens_est": 0,
                }
            else:
                if current_turn is not None:
                    current_turn["messages"].append(msg)

        if current_turn is not None:
            turns.append(current_turn)

        for t in turns:
            t["tokens_est"] = estimate_token_size(t["messages"])
        return turns

    @classmethod
    def compute_adaptive_pivot(
        cls,
        turns: List[Dict[str, Any]],
        existing_checkpoint: Optional[Dict[str, Any]] = None,
        target_context_pct: Optional[float] = None,
        compressed_share_pct: Optional[float] = None,
        kept_share_pct: Optional[float] = None,
        incoming_user_tokens: int = 0,
    ) -> Dict[str, Any]:
        """
        Calcule le pivot de compaction selon les pourcentages de ECHO_MAX_CONTEXT_SIZE :
        - Enveloppe cible totale = ECHO_MAX_CONTEXT_SIZE * target_context_pct (ex: 20% = 209 715 tokens)
        - Part compressée HEAD = compressed_share_pct (ex: 63% de 20% = 12.6% de CONTEXT_MAX = 132 120 tokens)
        - Part gardée intacte TAIL = 1 - compressed_share_pct (ex: 37% de 20% = 7.4% de CONTEXT_MAX = 77 594 tokens)
        Au moins 1 tour non-compressé (le plus ancien) est systématiquement intégré dans HEAD.
        """
        from echo_constants import (
            ECHO_MAX_CONTEXT_SIZE,
            COMPACTION_MIN_TURNS_REQUIRED,
            COMPACTION_TARGET_CONTEXT_PCT,
            COMPACTION_COMPRESSED_SHARE_PCT,
            get_compaction_shares,
        )

        t_pct = float(
            target_context_pct if target_context_pct is not None else COMPACTION_TARGET_CONTEXT_PCT
        )
        raw_c_share = float(
            compressed_share_pct
            if compressed_share_pct is not None
            else COMPACTION_COMPRESSED_SHARE_PCT
        )
        # Dérivation fonctionnelle stricte de kept_share via 1 - c_share (helper SSOT)
        c_share, k_share = get_compaction_shares(raw_c_share)

        target_total_tokens = int(ECHO_MAX_CONTEXT_SIZE * t_pct)
        compressed_budget_tokens = int(target_total_tokens * c_share)
        kept_budget_tokens = max(
            0, int(target_total_tokens * k_share) - max(0, int(incoming_user_tokens))
        )

        total_turns = len(turns)
        old_pivot_idx = int((existing_checkpoint or {}).get("pivot_turn_idx", 0))
        uncompacted_count = total_turns - old_pivot_idx

        if uncompacted_count < COMPACTION_MIN_TURNS_REQUIRED:
            return {
                "eligible": False,
                "reason": (
                    f"Aucun nouveau tour complet à compresser "
                    f"(tours totaux : {total_turns}, pivot actuel : {old_pivot_idx}, "
                    f"minimum requis : {COMPACTION_MIN_TURNS_REQUIRED})."
                ),
                "total_turns": total_turns,
            }

        # Remplissage de TAIL à rebours depuis le tour le plus récent,
        # en laissant obligatoirement au moins turns[old_pivot_idx] dans HEAD.
        tail_tokens = 0
        keep_turns = 0
        for idx in range(total_turns - 1, old_pivot_idx, -1):
            t_tok = int(turns[idx].get("tokens_est", 0))
            if tail_tokens + t_tok <= kept_budget_tokens:
                tail_tokens += t_tok
                keep_turns += 1
            else:
                break

        new_pivot_idx = total_turns - keep_turns
        delta_turns = turns[old_pivot_idx:new_pivot_idx]
        pivot_msg_id = (
            turns[new_pivot_idx]["user_msg_id"] if new_pivot_idx < total_turns else None
        )

        return {
            "eligible": len(delta_turns) > 0,
            "total_turns": total_turns,
            "old_pivot_turn_idx": old_pivot_idx,
            "new_pivot_turn_idx": new_pivot_idx,
            "pivot_msg_id": pivot_msg_id,
            "keep_recent_turns": keep_turns,
            "tail_tokens_est": tail_tokens,
            "delta_turns": delta_turns,
            "delta_turns_count": len(delta_turns),
            "target_total_tokens": target_total_tokens,
            "compressed_budget_tokens": compressed_budget_tokens,
            "kept_budget_tokens": kept_budget_tokens,
        }

    @classmethod
    def format_turns_for_distillation(cls, turns_slice: List[Dict[str, Any]]) -> str:
        """
        Sérialise les tours de HEAD pour le LLM de distillation :
        - ZÉRO troncature de caractères sur les messages ou résultats d'outils.
        - Conservation intégrale des balises <artifact id="AEC_...">.
        - Seuls les blobs binaires Base64 (data:...;base64,... / inline_data) sont remplacés par '[MÉDIA BINAIRE OMIS]'.
        """
        b64_pattern = re.compile(r"data:[^;]+;base64,[A-Za-z0-9+/=]+")
        blocks: List[str] = []

        for t in turns_slice:
            t_num = t["turn_idx"] + 1
            lines = [f"=== TOUR #{t_num} ==="]
            for m in t["messages"]:
                role = (m.get("role") or "unknown").upper()
                content = m.get("content", "")
                if isinstance(content, list):
                    parts_txt = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                parts_txt.append(item.get("text", ""))
                            elif item.get("type") == "image_url" or "inline_data" in item:
                                parts_txt.append("[MÉDIA BINAIRE OMIS]")
                    text_val = "\n".join(parts_txt)
                else:
                    text_val = str(content or "")

                text_val = b64_pattern.sub("[MÉDIA BINAIRE OMIS]", text_val).strip()

                tool_calls = m.get("tool_calls") or []
                if tool_calls:
                    tc_formatted = []
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            tc_formatted.append(
                                f"{fn.get('name', 'tool')}({fn.get('arguments', '')})"
                            )
                    if tc_formatted:
                        lines.append(f"[{role} -> TOOL_CALLS]: {', '.join(tc_formatted)}")

                output_items = m.get("output")
                if isinstance(output_items, list):
                    for out_item in output_items:
                        if not isinstance(out_item, dict):
                            continue
                        otype = out_item.get("type")
                        if otype == "function_call":
                            lines.append(
                                f"[{role} -> FUNCTION_CALL]: {out_item.get('name', 'tool')}({out_item.get('arguments', '')})"
                            )
                        elif otype == "function_call_output":
                            out_val = b64_pattern.sub(
                                "[MÉDIA BINAIRE OMIS]", str(out_item.get("output", ""))
                            ).strip()
                            if out_val:
                                lines.append(f"[TOOL_OUTPUT]: {out_val}")

                if text_val:
                    lines.append(f"[{role}]: {text_val}")
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)

    @staticmethod
    def build_sqlite_resources_snapshot(user_id: str, chat_id: str) -> str:
        """Extrait un instantané déterministe du Registre Unifié SQLite (table echo_resources uniquement)."""
        from echo_paths import get_echo_session_path

        session_db = get_echo_session_path(user_id, chat_id, "db")
        if not os.path.exists(session_db):
            return ""

        lines: List[str] = []
        try:
            with closing(sqlite3.connect(session_db, timeout=5.0)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='echo_resources'"
                )
                if cur.fetchone():
                    cur.execute(
                        "SELECT id, name, resource_type, status, storage_path, plan_goal "
                        "FROM echo_resources "
                        "WHERE resource_type NOT IN ('aec_event', 'aec_directive') "
                        "ORDER BY updated_at DESC LIMIT 30"
                    )
                    rows = cur.fetchall()
                    if rows:
                        for rid, rname, rtype, rst, spath, pgoal in rows:
                            loc = f" (`{spath}`)" if spath else ""
                            goal_str = f" — Objectif: *{pgoal}*" if pgoal else ""
                            lines.append(
                                f"- `{rid}` [{rtype}] : **{rname}**{loc} — Statut: `{rst}`{goal_str}"
                            )
        except Exception as e:
            logger.warning(f"ECHO Compaction: Snapshot SQLite partiel: {e}")

        return "\n".join(lines)

    @classmethod
    def resolve_active_window(
        cls,
        messages: List[Dict[str, Any]],
        checkpoint: Optional[Dict[str, Any]],
        branch_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Résout la fenêtre active de messages pour EchoOrchestrator.prepare_context().
        Retourne (prefix_contents, cut_msg_idx) et réhydrate les 'id' et 'updated_at'
        depuis branch_messages sur la tranche TAIL afin que get_signature_by_id fonctionne en O(1).
        """
        if not checkpoint or not isinstance(checkpoint, dict):
            return [], 0

        summary_xml = checkpoint.get("summary_xml", "")
        if not summary_xml:
            return [], 0

        from echo_prompts import CONTEXT_COMPACTION_ACK_MESSAGE

        pivot_msg_id = checkpoint.get("pivot_msg_id")
        pivot_turn_idx = int(checkpoint.get("pivot_turn_idx", 0))

        real_user_indices = [
            idx for idx, m in enumerate(messages) if cls.is_real_user_turn(m)
        ]
        if not real_user_indices:
            return [], 0

        # Réhydratation O(1) des IDs et timestamps depuis branch_messages avant recherche d'ID
        if branch_messages:
            non_sys_branch = [
                bm for bm in branch_messages if bm.get("role") != "system"
            ]
            non_sys_payload_indices = [
                idx
                for idx, m in enumerate(messages)
                if m.get("role") != "system"
                and cls.SYNTHETIC_IMAGE_MARKER not in str(m.get("content", ""))
            ]
            # Si messages contient le nouveau tour entrant non encore sauvé dans branch_messages, on aligne le préfixe
            if len(non_sys_branch) == len(non_sys_payload_indices):
                pairs = zip(non_sys_payload_indices, non_sys_branch)
            elif len(non_sys_branch) < len(non_sys_payload_indices):
                pairs = zip(non_sys_payload_indices[: len(non_sys_branch)], non_sys_branch)
            else:
                pairs = zip(
                    non_sys_payload_indices,
                    non_sys_branch[-len(non_sys_payload_indices) :],
                )

            for p_idx, bm in pairs:
                if bm.get("role") == messages[p_idx].get("role"):
                    if not messages[p_idx].get("id") and bm.get("id"):
                        messages[p_idx]["id"] = bm["id"]
                    if not messages[p_idx].get("updated_at") and (
                        bm.get("updated_at") or bm.get("timestamp")
                    ):
                        messages[p_idx]["updated_at"] = bm.get("updated_at") or bm.get(
                            "timestamp"
                        )

        cut_msg_idx: Optional[int] = None
        if pivot_msg_id:
            for idx in real_user_indices:
                if messages[idx].get("id") == pivot_msg_id:
                    cut_msg_idx = idx
                    break

        if cut_msg_idx is None:
            if pivot_turn_idx < len(real_user_indices):
                cut_msg_idx = real_user_indices[pivot_turn_idx]
            else:
                # Si tous les tours passés ont été compactés, on coupe au dernier tour utilisateur (le tour entrant)
                cut_msg_idx = real_user_indices[-1]

        if cut_msg_idx <= 0:
            return [], 0

        prefix_contents = [
            {"role": "user", "parts": [{"text": summary_xml}]},
            {"role": "model", "parts": [{"text": CONTEXT_COMPACTION_ACK_MESSAGE}]},
        ]
        return prefix_contents, cut_msg_idx

    @classmethod
    async def execute_compaction(
        cls,
        user_id: str,
        chat_id: str,
        past_messages: List[Dict[str, Any]],
        incoming_user_msg: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        events: Optional[Any] = None,
        target_context_pct: Optional[float] = None,
        compressed_share_pct: Optional[float] = None,
        kept_share_pct: Optional[float] = None,
        min_turns_required: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Exécute la Compaction Contextuelle In-Place sur les tours passés complets (past_messages) :
        1. Découpe past_messages en tours et calcule le pivot selon la répartition (ex: 63% compressé / 37% gardé de 20%).
        2. Sérialise les tours de HEAD sans aucune troncature et avec leurs AEC.
        3. Appelle EchoGeminiClient.call_distillation avec max_tokens = min(compressed_budget_tokens, 65535).
        4. Scelle <artifact id="AEC_context_checkpoint"> dans echo_settings['context_checkpoint'].
        """
        from echo_constants import COMPACTION_MIN_TURNS_REQUIRED
        from echo_prompts import SYS_COMPACTION_DISTILL
        from echo_gemini_client import EchoGeminiClient

        state_mgr = EchoStateManager(user_id=user_id, chat_id=chat_id)
        raw_ckpt = state_mgr.get_session_setting("context_checkpoint")
        existing_ckpt = None
        if raw_ckpt:
            try:
                existing_ckpt = std_json.loads(raw_ckpt)
            except Exception:
                existing_ckpt = None

        turns = cls.split_into_turns(past_messages)
        min_req = (
            min_turns_required
            if min_turns_required is not None
            else COMPACTION_MIN_TURNS_REQUIRED
        )
        if len(turns) < min_req:
            return {
                "ok": False,
                "reason": (
                    f"La session ne comporte que {len(turns)} tour(s) complet(s) "
                    f"(minimum requis : {min_req})."
                ),
            }

        incoming_tokens = (
            estimate_token_size(incoming_user_msg) if incoming_user_msg else 0
        )
        plan = cls.compute_adaptive_pivot(
            turns=turns,
            existing_checkpoint=existing_ckpt,
            target_context_pct=target_context_pct,
            compressed_share_pct=compressed_share_pct,
            kept_share_pct=kept_share_pct,
            incoming_user_tokens=incoming_tokens,
        )
        if not plan.get("eligible"):
            return {
                "ok": False,
                "reason": plan.get("reason", "Compaction non éligible."),
            }

        serialized_delta = cls.format_turns_for_distillation(plan["delta_turns"])
        sqlite_snapshot = cls.build_sqlite_resources_snapshot(user_id, chat_id)

        compressed_budget = int(plan["compressed_budget_tokens"])
        api_max_output_tokens = min(compressed_budget, cls.GEMINI_MAX_OUTPUT_TOKENS)

        prompt_parts: List[str] = [
            f"<COMPRESSION_BUDGET>\n"
            f"Budget cible maximal pour ce Checkpoint : {compressed_budget} tokens "
            f"(capacité de sortie maximale : {api_max_output_tokens} tokens).\n"
            f"Conserve le maximum de détails techniques pertinents, le code, les décisions et le sens complet sans dépasser ce budget.\n"
            f"</COMPRESSION_BUDGET>"
        ]
        if existing_ckpt and existing_ckpt.get("summary_md"):
            prompt_parts.append(
                f"<PREVIOUS_CHECKPOINT compacted_turns=\"{existing_ckpt.get('pivot_turn_idx', 0)}\">\n"
                f"{existing_ckpt['summary_md']}\n"
                f"</PREVIOUS_CHECKPOINT>"
            )
        prompt_parts.append(
            f"<NEW_CONVERSATION_TURNS from_turn=\"{plan['old_pivot_turn_idx'] + 1}\" to_turn=\"{plan['new_pivot_turn_idx']}\">\n"
            f"{serialized_delta}\n"
            f"</NEW_CONVERSATION_TURNS>"
        )
        if sqlite_snapshot:
            prompt_parts.append(
                f"<ACTIVE_SQLITE_RESOURCES>\n{sqlite_snapshot}\n</ACTIVE_SQLITE_RESOURCES>"
            )

        user_prompt = "\n\n".join(prompt_parts)
        summary_md = await EchoGeminiClient.call_distillation(
            prompt=user_prompt,
            __user__={"id": user_id},
            __metadata__={**(metadata or {}), "chat_id": chat_id},
            is_json=False,
            max_tokens=api_max_output_tokens,
            system_instruction=SYS_COMPACTION_DISTILL,
            timeout=240,
        )

        if not summary_md or not summary_md.strip():
            return {
                "ok": False,
                "reason": "Le moteur de distillation n'a retourné aucune synthèse.",
            }

        compaction_count = int((existing_ckpt or {}).get("compaction_count", 0)) + 1
        try:
            now_dt = datetime.now(ZoneInfo("Europe/Paris"))
        except Exception:
            now_dt = datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        summary_xml = EchoAEC.render_context_checkpoint(
            summary_md=summary_md,
            resources_snapshot=sqlite_snapshot,
            compacted_turns=plan["new_pivot_turn_idx"],
            compaction_count=compaction_count,
            timestamp_str=now_str,
        )

        new_ckpt = {
            "version": 1,
            "compaction_count": compaction_count,
            "pivot_turn_idx": plan["new_pivot_turn_idx"],
            "pivot_msg_id": plan["pivot_msg_id"],
            "keep_recent_turns": plan["keep_recent_turns"],
            "summary_md": summary_md.strip(),
            "summary_xml": summary_xml,
            "checkpoint_tokens": estimate_token_size(summary_xml),
            "tail_tokens_est": plan["tail_tokens_est"],
            "created_at": int(time.time()),
        }
        state_mgr.save_session_setting(
            "context_checkpoint", std_json.dumps(new_ckpt).decode("utf-8")
        )

        # Enregistre les métriques de compaction dans context_stats
        state_mgr.save_context_stats(
            {
                "compaction_count": compaction_count,
                "compacted_turns": plan["new_pivot_turn_idx"],
            }
        )

        EchoAEC.record_event(
            state_manager=state_mgr,
            event_name="context_compaction",
            status="completed",
            summary=(
                f"Compaction contextuelle #{compaction_count} exécutée : "
                f"tours 1 à {plan['new_pivot_turn_idx']} consolidés dans <artifact id=\"AEC_context_checkpoint\">, "
                f"{plan['keep_recent_turns']} tour(s) récent(s) maintenu(s) intact(s)."
            ),
            resource_type="aec_event",
        )

        return {
            "ok": True,
            "compaction_count": compaction_count,
            "compacted_turns": plan["new_pivot_turn_idx"],
            "delta_turns_count": plan["delta_turns_count"],
            "keep_recent_turns": plan["keep_recent_turns"],
            "checkpoint_tokens": estimate_token_size(summary_xml),
            "tail_tokens_est": plan["tail_tokens_est"],
        }


