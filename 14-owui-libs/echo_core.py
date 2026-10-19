# -*- coding: utf-8 -*-
"""
title: ECHO Echo Core
author: Wilfried BARNAVON
version: 5.227.0.2
description: Fonctions cognitives et utilitaires pures.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.227.0.1: Fiabilisation de EchoCompactionEngine (regex ALL_AEC_PATTERN multi-attributs, désambiguïsation par index attendu dans resolve_active_window, mise à l'échelle télémétrique dans find_split_index et extraction des outils Open WebUI m['output']).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.11: Ajout de progress_callback dans EchoCompactionEngine.execute_compaction pour le pilotage temps-réel du HUD de progression.
# 1.10: Ajout de EchoCompactionEngine pour le compactage contextuel in-place (auto et manuel) via MODEL_APOLLO.
# 1.9: Renommage souverain des modèles cognitifs (MODEL_ZEPHYR, MODEL_APOLLO, MODEL_PROMETHEUS) et politique AUTO_PROM.
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple
from echo_state_manager import EchoStateManager
from echo_aec import EchoAEC
import orjson as std_json
from echo_paths import get_echo_version
from echo_constants import CHARS_PER_TOKEN, AEC_REMINDERS

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
        except:
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


class EchoCompactionEngine:
    """Moteur unifié de compactage contextuel In-Place (Filtre Automatique & Action Manuelle)."""

    SETTING_KEY = "compaction_state"
    B64_PATTERN = re.compile(r"data:[a-zA-Z0-9/+-.]+;base64,[a-zA-Z0-9+/]+={0,2}")
    AEC_CHECKPOINT_PATTERN = re.compile(
        r'<artifact id="AEC_context_checkpoint"[^>]*>.*?</artifact>\s*',
        re.DOTALL,
    )
    ALL_AEC_PATTERN = re.compile(r'<artifact id="AEC_[^"]*"[^>]*>.*?</artifact>\s*', re.DOTALL)

    @classmethod
    def strip_checkpoint_tag(cls, text: str) -> Tuple[Optional[str], str]:
        """Extrait (<artifact id="AEC_context_checkpoint">...</artifact>, texte_nettoyé)."""
        if not isinstance(text, str) or '<artifact id="AEC_context_checkpoint"' not in text:
            return None, text
        match = cls.AEC_CHECKPOINT_PATTERN.search(text)
        if not match:
            return None, text
        checkpoint_xml = match.group(0).strip()
        cleaned = cls.AEC_CHECKPOINT_PATTERN.sub("", text).strip()
        return checkpoint_xml, cleaned

    @classmethod
    def extract_text_from_message(cls, msg: Dict, strip_aec: bool = True) -> str:
        """Extrait le texte lisible d'un message (str ou list) en masquant les blobs Base64."""
        content = msg.get("content", "")
        if isinstance(content, list):
            parts_txt = []
            for p in content:
                if isinstance(p, dict):
                    if p.get("type") == "text" and p.get("text"):
                        parts_txt.append(str(p["text"]))
                    elif "text" in p and p["text"]:
                        parts_txt.append(str(p["text"]))
            text = "\n".join(parts_txt)
        else:
            text = str(content or "")

        if strip_aec:
            text = cls.ALL_AEC_PATTERN.sub("", text)
        text = cls.B64_PATTERN.sub("[MÉDIA BASE64 MASQUÉ]", text)
        return text.strip()

    @classmethod
    def group_messages_into_turns(cls, messages: List[Dict]) -> Tuple[List[Dict], List[List[Dict]]]:
        """
        Sépare les messages système et regroupe les échanges en tours atomiques.
        Chaque tour commence par un message de rôle 'user' authentique (et inclut les 'assistant'/'tool' suivants).
        """
        system_msgs = []
        turns = []
        current_turn = []
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                system_msgs.append(m)
                continue
            if role == "user":
                if current_turn:
                    turns.append(current_turn)
                current_turn = [m]
            else:
                if not current_turn:
                    current_turn = [m]
                else:
                    current_turn.append(m)
        if current_turn:
            turns.append(current_turn)
        return system_msgs, turns

    @classmethod
    def compute_turn_signature(cls, turn: List[Dict]) -> Tuple[str, str]:
        """Retourne (msg_id, content_hash) du message utilisateur ouvrant le tour."""
        import hashlib
        if not turn:
            return "", ""
        first_msg = turn[0]
        msg_id = str(first_msg.get("id") or "")
        clean_txt = cls.extract_text_from_message(first_msg, strip_aec=True)
        content_hash = hashlib.sha256(clean_txt.encode("utf-8")).hexdigest()[:32]
        return msg_id, content_hash

    @classmethod
    def get_compaction_state(cls, state_manager: EchoStateManager) -> Optional[Dict]:
        """Charge l'état de compactage actif depuis echo_settings."""
        raw = state_manager.get_session_setting(cls.SETTING_KEY)
        if not raw:
            return None
        try:
            data = std_json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @classmethod
    def resolve_active_window(
        cls, state_manager: EchoStateManager, turns: List[List[Dict]]
    ) -> Tuple[List[List[Dict]], Optional[Dict], int]:
        """
        Applique le Checkpoint existant sur la liste des tours.
        Retourne (active_turns, state_dict_if_valid, evicted_turns_count).
        Si l'utilisateur a supprimé/édité en amont du pivot (rewind), invalide le state.
        """
        state = cls.get_compaction_state(state_manager)
        if not state or not turns:
            return turns, None, 0

        target_id = state.get("pivot_msg_id")
        target_hash = state.get("pivot_hash")
        expected_idx = max(1, int(state.get("compacted_turns", 1)))
        pivot_turn_idx = -1

        signatures = [cls.compute_turn_signature(turn) for turn in turns]

        # 1. Priorité absolue à l'identifiant de message s'il est présent dans le payload
        if target_id:
            for idx, (m_id, _) in enumerate(signatures):
                if m_id and m_id == target_id:
                    pivot_turn_idx = idx
                    break

        # 2. Fallback sur le hash de contenu (cas Inlet où Open WebUI a purgé 'id'),
        # désambiguïsé par proximité avec l'index attendu `compacted_turns`.
        if pivot_turn_idx == -1 and target_hash:
            hash_matches = [
                idx for idx, (_, m_hash) in enumerate(signatures)
                if idx > 0 and m_hash == target_hash
            ]
            if hash_matches:
                pivot_turn_idx = min(hash_matches, key=lambda idx: abs(idx - expected_idx))
            elif signatures and signatures[0][1] == target_hash:
                pivot_turn_idx = 0

        if pivot_turn_idx <= 0:
            if pivot_turn_idx == -1:
                state_manager.save_session_setting(cls.SETTING_KEY, "")
            return turns, None, 0

        return turns[pivot_turn_idx:], state, pivot_turn_idx

    @classmethod
    def estimate_turn_tokens(cls, turn: List[Dict]) -> int:
        """
        Estime le poids en tokens d'un tour sur son seul contenu utile dédupliqué
        (sans double comptage entre m['content'] et m['output'], ni sérialisation des métadonnées webui.db m['files']).
        """
        total = 0
        for m in turn:
            content = m.get("content", "")
            if isinstance(content, list):
                txt_parts = []
                for p in content:
                    if not isinstance(p, dict):
                        continue
                    if "text" in p and p["text"]:
                        txt_parts.append(str(p["text"]))
                    elif p.get("type") == "image_url" or "inlineData" in p or "inline_data" in p:
                        total += estimate_token_size(p)
                raw_str = "\n".join(txt_parts)
            else:
                raw_str = str(content or "")

            b64_matches = cls.B64_PATTERN.findall(raw_str)
            total += len(b64_matches) * 258
            clean_str = cls.B64_PATTERN.sub("", raw_str)
            total += len(clean_str) // CHARS_PER_TOKEN

            has_inline_tools = '<details type="tool_calls"' in raw_str
            has_inline_reasoning = '<details type="reasoning"' in raw_str

            if not has_inline_tools:
                for tc in m.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        total += len(str(tc.get("function", {}))) // CHARS_PER_TOKEN

            for item in m.get("output") or []:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if not has_inline_tools and item_type == "function_call":
                    total += len(str(item.get("arguments", ""))) // CHARS_PER_TOKEN
                elif not has_inline_tools and item_type == "function_call_output":
                    for op in item.get("output") or []:
                        if isinstance(op, dict) and op.get("text"):
                            op_str = str(op["text"])
                            total += len(cls.B64_PATTERN.findall(op_str)) * 258
                            total += len(cls.B64_PATTERN.sub("", op_str)) // CHARS_PER_TOKEN
                elif not has_inline_reasoning and item_type == "reasoning":
                    r_txt = str(item.get("summary") or item.get("content") or "")
                    total += len(r_txt) // CHARS_PER_TOKEN

        return max(1, total)

    @classmethod
    def estimate_active_tokens(
        cls,
        active_turns: List[List[Dict]],
        active_state: Optional[Dict],
        state_manager: EchoStateManager,
    ) -> int:
        """
        Estime le nombre de tokens actifs en priorisant la télémétrie réelle Gemini (context_stats)
        alignée sur get_context_load et la jauge HUD, avec repli sur l'heuristique dédupliquée.
        """
        stats_raw = state_manager.get_session_setting("context_stats")
        telemetry_tokens = 0
        if stats_raw:
            try:
                stats = std_json.loads(stats_raw)
                telemetry_tokens = int(stats.get("totalTokenCount") or stats.get("promptTokenCount") or 0)
            except Exception:
                telemetry_tokens = 0

        if telemetry_tokens > 0:
            pending_user_tokens = 0
            if active_turns and len(active_turns[-1]) == 1 and active_turns[-1][0].get("role") == "user":
                pending_user_tokens = cls.estimate_turn_tokens(active_turns[-1])
            return telemetry_tokens + pending_user_tokens

        heuristic_tokens = sum(cls.estimate_turn_tokens(t) for t in active_turns)
        if active_state and active_state.get("summary_xml"):
            heuristic_tokens += estimate_token_size(active_state["summary_xml"])
        return heuristic_tokens

    @classmethod
    def find_split_index(
        cls,
        active_turns: List[List[Dict]],
        target_tokens: int,
        min_turns_to_keep: int,
        force_manual: bool = False,
        active_tokens: int = 0,
    ) -> int:
        """
        Détermine l'index de coupe `split_idx` dans `active_turns` :
        - `active_turns[:split_idx]` sera compacté dans le nouveau Checkpoint.
        - `active_turns[split_idx:]` restera intact dans la fenêtre active (avec `active_turns[split_idx][0]` comme nouveau pivot).
        """
        total_turns = len(active_turns)
        if total_turns <= min_turns_to_keep:
            return 0

        max_split = total_turns - min_turns_to_keep
        turn_sizes = [cls.estimate_turn_tokens(t) for t in active_turns]
        heuristic_total = sum(turn_sizes)

        if active_tokens > 0 and heuristic_total > 0:
            effective_target = max(1, int(heuristic_total * (target_tokens / active_tokens)))
        else:
            effective_target = target_tokens

        accumulated_tokens = 0
        split_idx = max_split

        for i in range(total_turns - 1, -1, -1):
            turn_tokens = turn_sizes[i]
            if accumulated_tokens + turn_tokens > effective_target:
                split_idx = min(i + 1, max_split)
                break
            accumulated_tokens += turn_tokens
        else:
            # Tout `active_turns` tient sous `effective_target`
            if force_manual or active_tokens > target_tokens:
                split_idx = max_split
            else:
                return 0

        return max(0, min(split_idx, max_split))

    @classmethod
    def format_turns_for_compaction(cls, turns_to_compact: List[List[Dict]]) -> str:
        """Formate intégralement les tours à compacter sans troncature des messages User/Assistant."""
        blocks = []
        for idx, turn in enumerate(turns_to_compact, start=1):
            turn_lines = [f"--- TOUR #{idx} ---"]
            for m in turn:
                role = m.get("role", "user").upper()
                txt = cls.extract_text_from_message(m, strip_aec=True)
                files = m.get("files") or []
                file_names = [
                    f.get("name") or f.get("file", {}).get("name", "fichier")
                    for f in files
                    if isinstance(f, dict)
                ]
                file_suffix = f" [Fichiers joints : {', '.join(file_names)}]" if file_names else ""

                tool_calls = m.get("tool_calls") or []
                tc_names = [
                    tc.get("function", {}).get("name", "unknown")
                    for tc in tool_calls
                    if isinstance(tc, dict)
                ]
                output_items = m.get("output") if isinstance(m.get("output"), list) else []
                extra_tool_outputs = []
                for item in output_items:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type == "function_call" and item.get("name"):
                        if item["name"] not in tc_names:
                            tc_names.append(item["name"])
                    elif item_type == "function_call_output":
                        out_parts = item.get("output") if isinstance(item.get("output"), list) else []
                        for op in out_parts:
                            if isinstance(op, dict) and op.get("type") == "input_text" and op.get("text"):
                                clean_op = cls.B64_PATTERN.sub("[MÉDIA BASE64 MASQUÉ]", str(op["text"])).strip()
                                if clean_op:
                                    extra_tool_outputs.append(clean_op)

                tc_summary = f"\n[Appels d'outils déclenchés : {', '.join(tc_names)}]" if tc_names else ""
                if extra_tool_outputs:
                    tc_summary += "\n[Résultats d'outils]:\n" + "\n".join(extra_tool_outputs)

                if txt or file_suffix or tc_summary:
                    turn_lines.append(f"[{role}]{file_suffix}:\n{txt}{tc_summary}".strip())
            blocks.append("\n\n".join(turn_lines))
        return "\n\n".join(blocks)

    @classmethod
    def inject_checkpoint_into_turn(cls, pivot_turn: List[Dict], summary_xml: str) -> None:
        """Préfixe le message utilisateur d'ouverture du tour pivot avec <artifact id="AEC_context_checkpoint">."""
        if not pivot_turn or not summary_xml:
            return
        pivot_msg = pivot_turn[0]
        content = pivot_msg.get("content", "")
        if isinstance(content, list):
            cleaned_parts = [
                p for p in content
                if not (isinstance(p, dict) and '<artifact id="AEC_context_checkpoint"' in str(p.get("text", "")))
            ]
            pivot_msg["content"] = [{"type": "text", "text": summary_xml.strip() + "\n\n"}] + cleaned_parts
        else:
            _, clean_str = cls.strip_checkpoint_tag(str(content or ""))
            pivot_msg["content"] = f"{summary_xml.strip()}\n\n{clean_str}"
        pivot_msg["_echo_is_compaction_pivot"] = True

    @classmethod
    async def execute_compaction(
        cls,
        messages: List[Dict],
        user_id: str,
        chat_id: str,
        __user__: Optional[Dict] = None,
        __metadata__: Optional[Dict] = None,
        force_manual: bool = False,
        events: Optional[Any] = None,
        progress_callback: Optional[Any] = None,
    ) -> Optional[Dict]:
        """
        Exécute un cycle complet de compactage cognitif via MODEL_APOLLO (Consolidation Téléscopique).
        Retourne le nouveau dictionnaire `compaction_state` scellé dans `session.db`, ou None si non applicable/échec.
        """
        from echo_constants import (
            ECHO_MAX_CONTEXT_SIZE,
            CONTEXT_COMPACTION_TARGET_RATIO,
            ECHO_COMPACTION_MIN_TURNS_TO_KEEP,
            ECHO_COMPACTION_MODEL,
            ECHO_COMPACTION_MAX_OUTPUT_TOKENS,
        )
        from echo_prompts import SYS_CONTEXT_COMPACTION, USR_CONTEXT_COMPACTION
        from echo_gemini_client import EchoGeminiClient

        state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
        _, all_turns = cls.group_messages_into_turns(messages)
        if len(all_turns) <= ECHO_COMPACTION_MIN_TURNS_TO_KEEP:
            return None

        active_turns, prev_state, already_evicted_count = cls.resolve_active_window(state_manager, all_turns)
        tokens_before = cls.estimate_active_tokens(active_turns, prev_state, state_manager)
        target_tokens = int(ECHO_MAX_CONTEXT_SIZE * CONTEXT_COMPACTION_TARGET_RATIO)

        split_idx = cls.find_split_index(
            active_turns=active_turns,
            target_tokens=target_tokens,
            min_turns_to_keep=ECHO_COMPACTION_MIN_TURNS_TO_KEEP,
            force_manual=force_manual,
            active_tokens=tokens_before,
        )
        if split_idx <= 0:
            return None

        turns_to_compact = active_turns[:split_idx]
        remaining_turns = active_turns[split_idx:]
        if not remaining_turns:
            return None

        if progress_callback:
            await progress_callback(20, f"🔍 Préparation de {len(turns_to_compact)} tour(s) à condenser...")

        prev_summary_raw = prev_state.get("summary_raw", "") if prev_state else ""
        prev_count = int(prev_state.get("compaction_count", 0)) if prev_state else 0
        new_compaction_count = prev_count + 1
        total_compacted_turns = already_evicted_count + len(turns_to_compact)

        transcript_text = cls.format_turns_for_compaction(turns_to_compact)
        user_prompt = USR_CONTEXT_COMPACTION.format(
            compaction_index=new_compaction_count,
            compacted_turns_total=total_compacted_turns,
            previous_checkpoint=prev_summary_raw if prev_summary_raw else "Aucun (Première compression de la session).",
            transcript_to_compact=transcript_text,
        )

        if progress_callback:
            await progress_callback(45, "🧠 Synthèse téléscopique via MODEL_APOLLO en cours...")

        summary_md = await EchoGeminiClient.call_distillation(
            prompt=user_prompt,
            __user__=__user__ or {"id": user_id},
            __metadata__=__metadata__ or {"chat_id": chat_id},
            is_json=False,
            max_tokens=ECHO_COMPACTION_MAX_OUTPUT_TOKENS,
            target_model=ECHO_COMPACTION_MODEL,
            system_instruction=SYS_CONTEXT_COMPACTION,
        )

        if not isinstance(summary_md, str) or not summary_md.strip():
            return None

        if progress_callback:
            await progress_callback(85, "💾 Scellement du Checkpoint Cognitif dans SQLite...")

        summary_md = summary_md.strip()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        summary_xml = EchoAEC.render_context_checkpoint(
            summary=summary_md,
            compacted_turns=total_compacted_turns,
            updated_at_str=now_str,
            compaction_count=new_compaction_count,
        )

        pivot_id, pivot_hash = cls.compute_turn_signature(remaining_turns[0])
        heuristic_before = sum(cls.estimate_turn_tokens(t) for t in active_turns)
        heuristic_remaining = sum(cls.estimate_turn_tokens(t) for t in remaining_turns)
        if tokens_before > 0 and heuristic_before > 0:
            scaled_remaining = int(tokens_before * (heuristic_remaining / heuristic_before))
            tokens_after = scaled_remaining + estimate_token_size(summary_xml)
        else:
            tokens_after = heuristic_remaining + estimate_token_size(summary_xml)

        new_state = {
            "pivot_msg_id": pivot_id,
            "pivot_hash": pivot_hash,
            "summary_raw": summary_md,
            "summary_xml": summary_xml,
            "compacted_turns": total_compacted_turns,
            "compaction_count": new_compaction_count,
            "updated_at": now_str,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
        }

        state_manager.save_session_setting(cls.SETTING_KEY, std_json.dumps(new_state).decode("utf-8"))
        # Réinitialise le compteur télémétrique pour éviter un re-déclenchement immédiat avant le retour API Gemini
        state_manager.save_session_setting(
            "context_stats",
            std_json.dumps({"promptTokenCount": tokens_after, "totalTokenCount": tokens_after}).decode("utf-8"),
        )

        EchoAEC.record_event(
            state_manager=state_manager,
            event_name="context_compaction",
            status="completed",
            summary=f"Checkpoint Cognitif #{new_compaction_count} généré ({total_compacted_turns} tours condensés, {tokens_before} -> {tokens_after} tokens).",
            resource_type="aec_event",
        )

        return new_state

