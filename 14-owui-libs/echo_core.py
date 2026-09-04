# -*- coding: utf-8 -*-
"""
title: ECHO Echo Core
author: Wilfried BARNAVON
version: 1.0
description: Fonctions cognitives et utilitaires pures.
"""
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import orjson as std_json
from typing import Any, Dict, List, Optional, Tuple
from echo_state_manager import EchoStateManager
from echo_paths import get_echo_version
from echo_constants import CHARS_PER_TOKEN

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
    is_response = any(isinstance(p, dict) and "functionResponse" in p for p in parts)
    
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

def _dict_to_yaml_aec(d: Any, indent: int = 0) -> str:
    """Helper local pour la sérialisation YAML de l'AEC (factorisé depuis new_context_filter)."""
    lines = []
    space = "  " * indent
    if isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                lines.append(f"{space}-")
                lines.append(_dict_to_yaml_aec(item, indent + 1))
            else:
                lines.append(f"{space}- {item}")
    elif isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                if not v: lines.append(f"{space}{k}: {{}}")
                else:
                    lines.append(f"{space}{k}:")
                    lines.append(_dict_to_yaml_aec(v, indent + 1))
            elif isinstance(v, list):
                if not v: lines.append(f"{space}{k}: []")
                else:
                    lines.append(f"{space}{k}:")
                    for item in v:
                        if isinstance(item, dict):
                            lines.append(f"{space}  -")
                            lines.append(_dict_to_yaml_aec(item, indent + 2))
                        else:
                            lines.append(f"{space}  - {item}")
            else:
                val = str(v).replace("\n", " ") if v is not None else ""
                lines.append(f"{space}{k}: {val}")
    return "\n".join(lines)

def build_aec_system_events(sys_events: list = None, error_events: list = None) -> str:
    """Génère la balise <AEC_evenement_systeme> standardisée dont le contenu est au format YAML pour l'AEC"""
    if not sys_events and not error_events:
        return ""
        
    events_text = "<AEC_evenement_systeme>\n"
    if sys_events:
        events_text += _dict_to_yaml_aec(sys_events) + "\n"
        events_text += "> Utilisez `query_registry` pour consulter l'état complet des ressources.\n"
    if error_events:
        events_text += "\n[ERREURS D'INGESTION]\n" + _dict_to_yaml_aec(error_events) + "\n"
        events_text += "> Ces fichiers ont échoué et ne sont pas exploitables.\n"
    events_text += "</AEC_evenement_systeme>\n\n"
    return events_text

def wrap_tool_output(text: str, status: dict = None, echo_tool_multiparts: List[dict] = None, user_id: str = None, chat_id: str = None, metadata: dict = None) -> dict:
    if user_id and chat_id and metadata is not None:
        last_check = metadata.get("_echo_last_event_check_at")
        if last_check:
            try:
                state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
                delta = state_manager.get_resources(created_after=float(last_check))
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
                        "source": "outil/HUD"
                    } for r in delta]
                    
                    # Appel de la fonction factorisée
                    events_text = build_aec_system_events(sys_events=events)
                    if events_text:
                        text += f"\n\n{events_text}"
                        metadata["_echo_last_event_check_at"] = time.time()
            except Exception as e:
                print(f"[wrap_tool_output] Erreur Delta SQLite: {e}")
                
    return {"text": text, "status": status or {"status": "success"}, "echo_tool_multiparts": echo_tool_multiparts or []}

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
        return ("auto", "MODEL_FLASH")
    elif selection == "AUTO_PRO":
        return ("auto_pro", "MODEL_PRO")
    elif selection in ECHO_MODELS_REGISTRY:
        return ("fixed", selection)
    return ("auto", "MODEL_FLASH")

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

def unbox_tool_output(name: str, content: Any, model_id: str, model_origin: str = "unknown") -> List[Dict]:
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

    response_dict = status_meta.copy()
    if text_body:
        response_dict["result"] = resolve_placeholders(text_body, model_id, model_origin)

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
    return [{"function_declarations": funcs}] if funcs else None

