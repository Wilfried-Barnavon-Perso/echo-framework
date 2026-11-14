# -*- coding: utf-8 -*-
"""
title: ECHO Echo Core
author: Wilfried BARNAVON
version: 5.239.17.1
description: Fonctions cognitives et utilitaires pures.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.17.1: Délestage automatique des sorties d'outils > 32 Ko dans tools_output/{output_id}.txt avec aperçu Head+Tail aligné sur les lignes et métadonnées JSON (truncate_and_spill_tool_output, skip_truncation).
# 5.239.11.1: Parsing JSON-first (`std_json.loads`) dans `unbox_tool_output` pour accepter la sérialisation Open WebUI (`null`/`true`/`false`), avec repli `ast.literal_eval` et garde `unstructured_data`.
# 5.238.1.1: Support orthogonal COGNITIVE_MODE:COGNITIVE_MODEL dans resolve_model_policy et convert_owui_tools (copie profonde sans mutation in-place) et surcharge thinking_override="low" étendue à MODEL_ATHENA en compaction.
# 5.237.20.2: Ancrage head_last_msg_id dans le checkpoint de compaction pour isoler les branches sœurs antérieures au pivot.
# 5.237.20.1: Division entière sur CHARS_PER_TOKEN=3.2, exclusion de thoughtSignature, pesée max(shadow, output) et résolution résiliente du pivot (resolve_pivot_turn_index, tail_anchor_ids, break_on_ids).
import os
import re
import uuid
import time
import inspect
import functools
import contextvars
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Set, Tuple
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
                    if k in ("inlineData", "inline_data", "thoughtSignature"):
                        continue
                    tokens += int(len(str(k)) / CHARS_PER_TOKEN)
                    tokens += _calc_size(v)
                return tokens
            elif "image_url" in item:
                tokens = 258
                for k, v in item.items():
                    if k not in ("image_url", "thoughtSignature"):
                        tokens += int(len(str(k)) / CHARS_PER_TOKEN)
                        tokens += _calc_size(v)
                return tokens
            else:
                tokens = 0
                for k, v in item.items():
                    if k == "thoughtSignature":
                        continue
                    tokens += int(len(str(k)) / CHARS_PER_TOKEN)
                    tokens += _calc_size(v)
                return tokens
        elif isinstance(item, list):
            return sum(_calc_size(i) for i in item)
        elif isinstance(item, str):
            return int(len(item) / CHARS_PER_TOKEN)
        else:
            return int(len(str(item)) / CHARS_PER_TOKEN)

    if isinstance(content, (dict, list)):
        try:
            return _calc_size(content)
        except Exception:
            try:
                return int(len(std_json.dumps(content)) / CHARS_PER_TOKEN)
            except Exception:
                pass

    return int(len(str(content)) / CHARS_PER_TOKEN)

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
    if not isinstance(text, str):
        return text, None
    thoughts_list: List[str] = []
    clean_text = text
    for pattern in (
        r'<details\b[^>]*\btype=["\']reasoning["\'][^>]*>(.*?)</details>',
        r"<(?:think|thought|thinking)>(.*?)</(?:think|thought|thinking)>",
    ):
        for match in re.finditer(pattern, clean_text, flags=re.DOTALL | re.IGNORECASE):
            if match.group(1).strip():
                thoughts_list.append(match.group(1).strip())
        clean_text = re.sub(pattern, "", clean_text, flags=re.DOTALL | re.IGNORECASE)
    if thoughts_list:
        return clean_text.strip(), "\n\n".join(thoughts_list)
    return text, None

_ECHO_IN_TOOL_CALL: contextvars.ContextVar[bool] = contextvars.ContextVar("_ECHO_IN_TOOL_CALL", default=False)


def _drain_tool_aec_events(user_id: str = None, chat_id: str = None, metadata: dict = None) -> Optional[str]:
    """
    Draine et formate les événements système AEC en attente pour un outil.
    """
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
                                print(f"[_drain_tool_aec_events] Erreur purge AEC {r.get('id')}: {e}")
            except Exception as e:
                print(f"[_drain_tool_aec_events] Erreur Delta SQLite: {e}")

    return aec_events


def truncate_and_spill_tool_output(
    text: str,
    uid: Optional[str] = None,
    cid: Optional[str] = None,
    tool_name: str = "tool",
    call_id: Optional[str] = None,
    max_chars: Optional[int] = None,
) -> Tuple[str, Optional[dict]]:
    """
    Sauvegarde l'intégralité d'une sortie d'outil dépassant `max_chars` dans `tools_output/{output_id}.txt`
    et retourne un aperçu Head (50%) + Tail (50%) aligné sur les lignes ainsi que les métadonnées JSON de troncature.
    """
    from echo_constants import (
        ECHO_CODEX_MAX_READ_LINES,
        ECHO_SUBAGENT_CONTEXT,
        ECHO_TOOL_OUTPUT_PREVIEW_CHARS,
    )
    from echo_paths import get_echo_session_path, sanitize_echo_id

    if max_chars is None:
        max_chars = ECHO_TOOL_OUTPUT_PREVIEW_CHARS
    if not isinstance(text, str) or len(text) <= max_chars:
        return text, None
    stripped = text.lstrip()
    if stripped.startswith("--- CONTENU BASE64 ---") or stripped.startswith("⚠️ [SORTIE D'OUTIL TRONQUÉE :"):
        return text, None

    sub_ctx = ECHO_SUBAGENT_CONTEXT.get() or {}
    sub_sid = sub_ctx.get("sub_sid") if sub_ctx.get("is_subagent") else None
    safe_tool = sanitize_echo_id(tool_name or "tool", fallback="tool")
    if call_id:
        raw_cid = str(call_id).strip()
        prefix = "" if raw_cid.startswith("to_") else "to_"
        output_id = f"{prefix}{sanitize_echo_id(raw_cid, fallback=uuid.uuid4().hex[:8])}"
    elif sub_sid:
        output_id = f"to_{sanitize_echo_id(sub_sid)}_{safe_tool}_{uuid.uuid4().hex[:8]}"
    else:
        output_id = f"to_{safe_tool}_{uuid.uuid4().hex[:8]}"

    saved_on_disk = False
    if uid and cid:
        try:
            out_dir = get_echo_session_path(uid, cid, "tools_output")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{output_id}.txt")
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            saved_on_disk = True
        except Exception as e:
            logger.warning(f"[truncate_and_spill_tool_output] Échec sauvegarde tools_output/{output_id}.txt: {e}")

    total_chars = len(text)
    total_lines = text.count("\n") + 1
    half = max_chars // 2

    # Alignement Head sur une fin de ligne
    head_cut = text.rfind("\n", 0, half)
    if head_cut <= half // 2:
        head_cut = half
    head_text = text[:head_cut]

    # Alignement Tail sur un début de ligne
    tail_start_idx = total_chars - half
    tail_nl = text.find("\n", tail_start_idx)
    if tail_nl != -1 and tail_nl < total_chars - (half // 2):
        tail_start_idx = tail_nl + 1
    tail_text = text[tail_start_idx:]

    head_end_line = head_text.count("\n") + 1
    tail_start_line = text.count("\n", 0, tail_start_idx) + 1
    omitted_start_line = head_end_line + 1 if text[head_cut:head_cut + 1] == "\n" else head_end_line
    omitted_end_line = (tail_start_line - 1) if (tail_start_idx > 0 and text[tail_start_idx - 1] == "\n") else tail_start_line
    omitted_end_line = max(omitted_start_line, omitted_end_line)
    omitted_chars = max(0, tail_start_idx - head_cut)
    omitted_lines = max(0, omitted_end_line - omitted_start_line + 1)
    suggested_end = min(omitted_end_line, omitted_start_line + (ECHO_CODEX_MAX_READ_LINES // 2) - 1)

    if saved_on_disk:
        header = (
            f"⚠️ [SORTIE D'OUTIL TRONQUÉE : {total_chars} caractères ({total_lines} lignes) > plafond inline de {max_chars} caractères]\n"
            f"L'intégralité de la sortie brute a été sauvegardée dans `tools_output/{output_id}.txt` (output_id: `{output_id}`).\n"
            f"- DOIT utiliser `read_tool_output(output_id=\"{output_id}\", start_line={omitted_start_line}, end_line={suggested_end})` "
            f"(ou `search_pattern=\"...\"`) pour inspecter les lignes manquantes si nécessaire.\n"
            f"- DOIT appeler `delete_tool_output(output_id=\"{output_id}\")` dès que l'exploitation de cette sortie est terminée.\n\n"
        )
        mid_marker = (
            f"\n\n... [TRONQUÉ : {omitted_chars} caractères ({omitted_lines} lignes) omis — "
            f"Lignes {omitted_start_line} à {omitted_end_line} disponibles via "
            f"read_tool_output(output_id=\"{output_id}\", start_line={omitted_start_line}, end_line={suggested_end})] ...\n\n"
        )
    else:
        header = f"⚠️ [SORTIE D'OUTIL TRONQUÉE : {total_chars} caractères ({total_lines} lignes) > plafond inline de {max_chars} caractères]\n\n"
        mid_marker = f"\n\n... [TRONQUÉ : {omitted_chars} caractères ({omitted_lines} lignes) omis au milieu] ...\n\n"

    preview_text = (
        f"{header}"
        f"--- DÉBUT DE LA SORTIE (Lignes 1 à {head_end_line}) ---\n"
        f"{head_text}"
        f"{mid_marker}"
        f"--- FIN DE LA SORTIE (Lignes {tail_start_line} à {total_lines}) ---\n"
        f"{tail_text}"
    )
    spill_meta = {
        "truncated": True,
        "output_id": output_id if saved_on_disk else None,
        "total_chars": total_chars,
        "total_lines": total_lines,
        "omitted_lines_range": [omitted_start_line, omitted_end_line],
        "next_tools": {"read": "read_tool_output", "delete": "delete_tool_output"} if saved_on_disk else None,
    }
    return preview_text, spill_meta


def wrap_tool_output(
    text: str,
    status: dict = None,
    echo_tool_multiparts: List[dict] = None,
    user_id: str = None,
    chat_id: str = None,
    metadata: dict = None,
    model_requested: str = None,
    model_used: str = None,
    reason: str = None,
    skip_truncation: bool = False,
) -> dict:
    """
    Enveloppe le résultat d'un outil ECHO avec métadonnées de statut, multiparts,
    drainage automatique des événements AEC, et traçabilité de cascade de modèles.
    """
    s = dict(status) if status else {"status": "success"}
    if model_requested or model_used:
        if model_requested:
            s["model_requested"] = model_requested
        if model_used:
            s["model_used"] = model_used
        if model_requested and model_used and model_requested != model_used:
            s["status"] = "warning"
            s["warning"] = reason or f"{model_requested} unavailable"
            text = f"[Modèle effectif : {model_used} (demandé : {model_requested})]\n\n{text}"

    aec_events = _drain_tool_aec_events(user_id=user_id, chat_id=chat_id, metadata=metadata)

    out = {
        "text": text,
        "aec_events": aec_events,
        "status": s,
        "echo_tool_multiparts": echo_tool_multiparts or []
    }
    if user_id and chat_id and metadata is not None:
        out["_aec_drained"] = True
    if skip_truncation:
        out["_skip_truncation"] = True
    return out


def _infer_string_status(text_str: str) -> str:
    """
    Infère un statut succès / warning / erreur depuis une chaîne brute de retour d'outil.
    """
    if not isinstance(text_str, str):
        return "success"
    t = text_str.lower().strip()
    if t.startswith(("❌", "[erreur]", "erreur", "error", "[error]", "exception")):
        return "error"
    if t.startswith(("⚠️", "[attention]", "attention", "warning", "[warning]")):
        return "warning"
    return "success"


def _apply_final_spillover(out: dict, uid: Optional[str], cid: Optional[str], is_nested: bool, tool_name: str) -> dict:
    skip_trunc = out.pop("_skip_truncation", False)
    if is_nested or skip_trunc or not isinstance(out.get("text"), str):
        return out
    st = out.get("status")
    if isinstance(st, dict) and st.get("status") == "error":
        return out
    preview_text, spill_meta = truncate_and_spill_tool_output(
        out["text"], uid=uid, cid=cid, tool_name=tool_name
    )
    if spill_meta:
        out["text"] = preview_text
        if not isinstance(out.get("status"), dict):
            out["status"] = {"status": str(out.get("status") or "success")}
        out["status"].update(spill_meta)
    return out


def _finalize_tool_result(
    res: Any,
    uid: Optional[str] = None,
    cid: Optional[str] = None,
    meta_dict: Optional[dict] = None,
    is_nested: bool = False,
    tool_name: str = "tool",
) -> Any:
    """
    Finalise le résultat d'un outil en gérant la sérialisation Pydantic,
    le drainage AEC unifié et la purge absolue de la clé interne '_aec_drained'.
    """
    if hasattr(res, "model_dump") and callable(getattr(res, "model_dump")):
        res = res.model_dump()
    elif hasattr(res, "dict") and callable(getattr(res, "dict")):
        res = res.dict()

    if isinstance(res, tuple):
        return tuple(
            _finalize_tool_result(item, uid=uid, cid=cid, meta_dict=meta_dict, is_nested=is_nested, tool_name=tool_name)
            if isinstance(item, (dict, str, list)) or hasattr(item, "model_dump") or hasattr(item, "dict")
            else item
            for item in res
        )

    if isinstance(res, dict):
        already_drained = res.pop("_aec_drained", False)
        if not already_drained and not is_nested and uid and meta_dict is not None:
            aec_events = _drain_tool_aec_events(user_id=uid, chat_id=cid, metadata=meta_dict)
            if aec_events:
                if "text" in res:
                    if not res.get("aec_events"):
                        res["aec_events"] = aec_events
                else:
                    res["aec_events"] = aec_events
        res.pop("_aec_drained", False)
        return _apply_final_spillover(res, uid, cid, is_nested, tool_name)

    if isinstance(res, str):
        status_val = _infer_string_status(res)
        out = wrap_tool_output(
            text=res,
            status={"status": status_val},
            user_id=uid if not is_nested else None,
            chat_id=cid if not is_nested else None,
            metadata=meta_dict if not is_nested else None
        )
        out.pop("_aec_drained", False)
        return _apply_final_spillover(out, uid, cid, is_nested, tool_name)

    text_val = "" if res is None else (std_json.dumps(res).decode("utf-8") if isinstance(res, list) else str(res))
    out = wrap_tool_output(
        text=text_val,
        status={"status": "success"},
        user_id=uid if not is_nested else None,
        chat_id=cid if not is_nested else None,
        metadata=meta_dict if not is_nested else None
    )
    out.pop("_aec_drained", False)
    return _apply_final_spillover(out, uid, cid, is_nested, tool_name)


def echo_tool(func):
    """
    Décorateur universel pour les outils ECHO (asynchrones ou synchrones).

    Fonctionnalités :
    1. Introspection de signature et exposition transparente de __user__ et __metadata__
       pour permettre l'injection automatique par Open WebUI sans contraindre l'outil.
    2. Extraction du schéma de retour Pydantic (responseSchema) vers __echo_response_schema__.
    3. Drainage automatique et dédupliqué des événements AEC via le contexte d'appel (_ECHO_IN_TOOL_CALL).
    4. Normalisation automatique du retour (dict wrap_tool_output, Pydantic model_dump, str).
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    func_params = sig.parameters
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    # Introspection du type de retour pour responseSchema
    response_schema = None
    return_annotation = sig.return_annotation
    if return_annotation is not inspect.Signature.empty and return_annotation is not None:
        try:
            from echo_protocol import normalize_response_schema
            response_schema = normalize_response_schema(return_annotation)
        except Exception as e:
            logger.debug(f"[echo_tool] Impossible de normaliser le schéma de retour: {e}")

    # Construction de la signature exposée à Open WebUI (avec __user__ et __metadata__)
    extra_params = []
    if "__user__" not in func_params:
        extra_params.append(
            inspect.Parameter("__user__", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=dict)
        )
    if "__metadata__" not in func_params:
        extra_params.append(
            inspect.Parameter("__metadata__", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=dict)
        )

    if extra_params:
        var_kw_idx = next((i for i, p in enumerate(params) if p.kind == inspect.Parameter.VAR_KEYWORD), None)
        if var_kw_idx is not None:
            exposed_params = params[:var_kw_idx] + extra_params + params[var_kw_idx:]
        else:
            exposed_params = params + extra_params
        exposed_sig = sig.replace(parameters=exposed_params)
    else:
        exposed_sig = sig

    def _prepare_call_kwargs(kwargs: dict) -> Tuple[dict, Optional[str], Optional[str], Optional[dict]]:
        user_arg = kwargs.get("__user__")
        meta_arg = kwargs.get("__metadata__")
        uid = user_arg.get("id") if isinstance(user_arg, dict) else None
        cid = meta_arg.get("chat_id") if isinstance(meta_arg, dict) else None
        meta_dict = meta_arg if isinstance(meta_arg, dict) else None

        if not has_var_kw:
            call_kwargs = {k: v for k, v in kwargs.items() if k in func_params}
        else:
            call_kwargs = dict(kwargs)
            if "__user__" not in func_params:
                call_kwargs.pop("__user__", None)
            if "__metadata__" not in func_params:
                call_kwargs.pop("__metadata__", None)

        return call_kwargs, uid, cid, meta_dict

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            call_kwargs, uid, cid, meta_dict = _prepare_call_kwargs(kwargs)
            is_nested = _ECHO_IN_TOOL_CALL.get()
            token = _ECHO_IN_TOOL_CALL.set(True)
            try:
                res = await func(*args, **call_kwargs)
            finally:
                _ECHO_IN_TOOL_CALL.reset(token)
            return _finalize_tool_result(res, uid=uid, cid=cid, meta_dict=meta_dict, is_nested=is_nested, tool_name=func.__name__)
    else:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            call_kwargs, uid, cid, meta_dict = _prepare_call_kwargs(kwargs)
            is_nested = _ECHO_IN_TOOL_CALL.get()
            token = _ECHO_IN_TOOL_CALL.set(True)
            try:
                res = func(*args, **call_kwargs)
            finally:
                _ECHO_IN_TOOL_CALL.reset(token)
            return _finalize_tool_result(res, uid=uid, cid=cid, meta_dict=meta_dict, is_nested=is_nested, tool_name=func.__name__)

    wrapper.__signature__ = exposed_sig
    wrapper.__echo_response_schema__ = response_schema
    return wrapper

def resolve_model_policy(metadata: dict, user_id: str = None) -> tuple:
    """
    Résout la politique modèle depuis __metadata__ (injecté par le Pipe).
    Fallback : lecture identity.db si absent de metadata (OWUI ne propage pas __metadata__ du pipe aux outils).
    Retourne (mode, plafond_key).
    - mode "fixed" + plafond = modèle forcé
    - mode "auto" + plafond = choix libre jusqu'au plafond
    """
    from echo_constants import parse_model_policy
    selection = (metadata or {}).get("_echo_model_policy")

    # Fallback SQLite (echo_settings) si absent de metadata
    if not selection and user_id:
        try:
            state = EchoStateManager(user_id=user_id)
            selection = state.get_setting("model_policy")
        except Exception:
            pass

    mode_norm, model_key = parse_model_policy(selection or "AUTO:MODEL_APOLLO")
    if mode_norm == "MANUAL":
        return ("fixed", model_key)
    return ("auto", model_key)

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
            # Priorité JSON standard (null/true/false) issu du stockage Open WebUI
            content = std_json.loads(content)
        except Exception:
            try:
                # Repli historique Python sécurisé (ast) pour gérer les guillemets simples du stockage SQL
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

def convert_owui_tools(tools: Optional[List[Dict]], model_policy: str = "AUTO:MODEL_APOLLO") -> Optional[List[Dict]]:
    """
    Convertit les specs OWUI → format Gemini.
    Filtre dynamiquement les enum des paramètres modèle selon la politique cognitive active.
    """
    if not tools: return None
    import copy
    from echo_constants import MODEL_ENUM_REFERENCE, get_allowed_models, parse_model_policy
    mode_norm, _ = parse_model_policy(model_policy)
    allowed_models = get_allowed_models(model_policy)

    funcs = []
    for t in tools:
        if t.get("type") == "function":
            f = t.get("function", {})
            params = copy.deepcopy(f.get("parameters", {"type": "object", "properties": {}}))

            # Filtrage dynamique : tout paramètre dont l'enum est un sous-ensemble de MODEL_ENUM_REFERENCE
            for prop_name, prop_val in params.get("properties", {}).items():
                if "enum" in prop_val:
                    enum_set = set(prop_val["enum"])
                    if enum_set.issubset(MODEL_ENUM_REFERENCE):
                        if mode_norm == "MANUAL":
                            prop_val["enum"] = list(allowed_models)
                        elif prop_name in ("critic_model", "synthesis_model"):
                            capped = [m for m in allowed_models if m != "MODEL_ATHENA" and m in enum_set]
                            prop_val["enum"] = capped or list(allowed_models)
                        else:
                            filtered = [m for m in allowed_models if m in enum_set]
                            prop_val["enum"] = filtered or list(allowed_models)

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


class TurnDict(dict):
    """Tour conversationnel compatible dict et itérable sur ses messages."""
    def __iter__(self):
        return iter(self.get("messages", []))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.get("messages", [])[key]
        return super().__getitem__(key)


class EchoCompactionEngine:
    """
    Moteur de Compaction Contextuelle In-Place piloté en pourcentages de ECHO_MAX_CONTEXT_SIZE.
    Décompose l'enveloppe cible (ex: 20% de CONTEXT_MAX = 209 715 tokens) en :
    - Part compressée HEAD : ex. 31.25% de 20% = 6.25% de CONTEXT_MAX (65 535 tokens max budget, plafonné à maxOutputTokens=65535 API)
    - Part gardée intacte TAIL : 1 - COMPACTION_COMPRESSED_SHARE_PCT (ex. 68.75% de 20% = 13.75% de CONTEXT_MAX, ~144 179 tokens max budget)
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
                    if (
                        node.get("role") == "assistant"
                        and not node.get("content")
                        and not node.get("output")
                    ):
                        curr = node.get("parentId")
                        continue
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
    async def load_active_branch_ids(cls, chat_id: str) -> List[str]:
        """Retourne la liste ordonnée des message_id de la branche active dans Open WebUI."""
        if not chat_id:
            return []
        branch = await cls.load_active_branch_messages(chat_id) or []
        return [
            str(m.get("id"))
            for m in branch
            if isinstance(m, dict) and m.get("id")
        ]

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
    def _estimate_message_tokens(
        cls, msg: Dict[str, Any], state_mgr: Optional[EchoStateManager] = None
    ) -> int:
        """Estime les tokens réels d'un message (via son Shadow SQLite ou hors pensées)."""
        msg_id = msg.get("id")
        shadow_toks = 0
        if state_mgr is not None and msg_id:
            shadow = state_mgr.get_shadow(str(msg_id))
            if shadow:
                shadow_toks = estimate_token_size(shadow)
        content = msg.get("content", "")
        if isinstance(content, str):
            clean_c, _ = split_thought_process(content)
            toks = estimate_token_size(clean_c)
        else:
            toks = estimate_token_size(content)
        if msg.get("tool_calls"):
            toks += estimate_token_size(msg["tool_calls"])
        output = msg.get("output")
        if isinstance(output, list):
            for item in output:
                if isinstance(item, dict) and item.get("type") in (
                    "function_call",
                    "function_call_output",
                ):
                    toks += estimate_token_size(item)
        return max(shadow_toks, toks)

    @classmethod
    def split_into_turns(
        cls,
        messages: List[Dict[str, Any]],
        state_mgr: Optional[EchoStateManager] = None,
        break_on_ids: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
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
                    if (
                        any(not cls.is_real_user_turn(m) for m in current_turn["messages"])
                        or (break_on_ids is not None and str(msg.get("id") or "") in break_on_ids)
                    ):
                        turns.append(current_turn)
                        current_turn = TurnDict({
                            "turn_idx": len(turns),
                            "start_msg_idx": idx,
                            "user_msg_id": msg.get("id"),
                            "messages": [msg],
                            "tokens_est": 0,
                        })
                    else:
                        current_turn["messages"].append(msg)
                else:
                    current_turn = TurnDict({
                        "turn_idx": len(turns),
                        "start_msg_idx": idx,
                        "user_msg_id": msg.get("id"),
                        "messages": [msg],
                        "tokens_est": 0,
                    })
            else:
                if current_turn is not None:
                    current_turn["messages"].append(msg)

        if current_turn is not None:
            turns.append(current_turn)

        for t in turns:
            t["tokens_est"] = sum(
                cls._estimate_message_tokens(m, state_mgr) for m in t["messages"]
            )
        return turns

    @classmethod
    def resolve_pivot_turn_index(
        cls,
        turns: List[Dict[str, Any]],
        checkpoint: Optional[Dict[str, Any]],
    ) -> int:
        """Résout l'indice du premier tour de TAIL par ancrage UUID (pivot_msg_id puis tail_anchor_ids)."""
        if not checkpoint or not isinstance(checkpoint, dict) or not turns:
            return 0
        pivot_msg_id = checkpoint.get("pivot_msg_id")
        if pivot_msg_id:
            s_piv = str(pivot_msg_id)
            for t_idx, t in enumerate(turns):
                if any(cls.is_real_user_turn(m) and str(m.get("id") or "") == s_piv for m in t["messages"]):
                    return t_idx
        tail_anchors = checkpoint.get("tail_anchor_ids")
        if isinstance(tail_anchors, list) and tail_anchors:
            for aid in tail_anchors:
                if not aid:
                    continue
                s_aid = str(aid)
                for t_idx, t in enumerate(turns):
                    if any(cls.is_real_user_turn(m) and str(m.get("id") or "") == s_aid for m in t["messages"]):
                        return t_idx

        # Lorsque pivot_msg_id et tail_anchor_ids ne correspondent à aucun tour dans turns
        head_last_id = checkpoint.get("head_last_msg_id")
        if head_last_id is not None:
            s_head = str(head_last_id)
            for t_idx, t in enumerate(turns):
                if any(cls.is_real_user_turn(m) and str(m.get("id") or "") == s_head for m in t["messages"]):
                    return t_idx + 1
            return 0

        if isinstance(tail_anchors, list) and tail_anchors:
            if len(turns) < int(checkpoint.get("pivot_turn_idx", 0)):
                return 0
            return len(turns)

        return min(max(0, int(checkpoint.get("pivot_turn_idx", 0))), len(turns))

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
        - Part compressée HEAD = compressed_share_pct (ex: 31.25% de 20% = 6.25% de CONTEXT_MAX = 65 535 tokens)
        - Part gardée intacte TAIL = 1 - compressed_share_pct (ex: 68.75% de 20% = 13.75% de CONTEXT_MAX = ~144 179 tokens)
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
        old_pivot_idx = cls.resolve_pivot_turn_index(turns, existing_checkpoint)
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

        uncompacted_tokens = sum(int(t.get("tokens_est", 0)) for t in turns[old_pivot_idx:])
        if existing_checkpoint and uncompacted_tokens <= kept_budget_tokens:
            return {
                "eligible": False,
                "reason": f"La fenêtre active ({uncompacted_tokens} tokens) respecte déjà le budget cible ({kept_budget_tokens} tokens).",
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
        head_last_msg_id = (
            str(delta_turns[-1]["user_msg_id"])
            if (delta_turns and delta_turns[-1].get("user_msg_id"))
            else (existing_checkpoint or {}).get("head_last_msg_id")
        )

        return {
            "eligible": len(delta_turns) > 0,
            "total_turns": total_turns,
            "old_pivot_turn_idx": old_pivot_idx,
            "new_pivot_turn_idx": new_pivot_idx,
            "pivot_msg_id": pivot_msg_id,
            "head_last_msg_id": head_last_msg_id,
            "tail_anchor_ids": [str(t["user_msg_id"]) for t in turns[new_pivot_idx:] if t.get("user_msg_id")],
            "keep_recent_turns": keep_turns,
            "tail_tokens_est": tail_tokens,
            "delta_turns": delta_turns,
            "delta_turns_count": len(delta_turns),
            "target_total_tokens": target_total_tokens,
            "compressed_budget_tokens": compressed_budget_tokens,
            "kept_budget_tokens": kept_budget_tokens,
        }

    @classmethod
    def format_turns_for_distillation(
        cls,
        turns_slice: List[Dict[str, Any]],
        state_mgr: Optional[EchoStateManager] = None,
    ) -> str:
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
                text_val = ""
                used_shadow = False
                if state_mgr is not None and m.get("role") == "user" and m.get("id"):
                    u_sh = state_mgr.get_shadow(str(m["id"]))
                    if isinstance(u_sh, list):
                        parts_txt = [
                            p.get("text", "")
                            for p in u_sh
                            if isinstance(p, dict) and "text" in p and not cls._is_perishable_aec_part(p)
                        ]
                        if parts_txt:
                            text_val = "\n".join(parts_txt)
                            used_shadow = True

                if not used_shadow:
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

                text_val, _ = split_thought_process(text_val)
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
        if not user_id or not chat_id:
            return ""
        lines: List[str] = []
        try:
            state_mgr = EchoStateManager(user_id=user_id, chat_id=chat_id)
            resources = state_mgr.get_resources()
            filtered = [
                r for r in resources
                if isinstance(r, dict) and r.get("resource_type") not in ("aec_event", "aec_directive")
            ][:30]
            for r in filtered:
                rid = r.get("id", "")
                rname = r.get("name", "")
                rtype = r.get("resource_type", "")
                rst = r.get("status", "")
                spath = r.get("storage_path")
                pgoal = r.get("plan_goal")
                if not pgoal and r.get("metadata"):
                    try:
                        meta = std_json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]
                        pgoal = meta.get("plan_goal")
                    except Exception:
                        pass
                loc = f" (`{spath}`)" if spath else ""
                goal_str = f" — Objectif: *{pgoal}*" if pgoal else ""
                lines.append(
                    f"- `{rid}` [{rtype}] : **{rname}**{loc} — Statut: `{rst}`{goal_str}"
                )
        except Exception as e:
            logger.warning(f"ECHO Compaction: Snapshot SQLite partiel: {e}")
        return "\n".join(lines)

    @classmethod
    def rehydrate_messages_metadata(
        cls,
        messages: List[Dict[str, Any]],
        branch_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Réhydrate par tour logique les clés 'id' et 'updated_at' depuis branch_messages sur messages, et restaure user_message_id."""
        if not messages:
            return
        if branch_messages:
            p_turns = cls.split_into_turns(messages)
            b_turns = cls.split_into_turns(branch_messages)
            if len(b_turns) == len(p_turns):
                turn_pairs = zip(p_turns, b_turns)
            elif len(b_turns) < len(p_turns):
                turn_pairs = zip(p_turns[: len(b_turns)], b_turns)
            else:
                turn_pairs = zip(p_turns, b_turns[-len(p_turns):])

            for pt, bt in turn_pairs:
                p_users = [m for m in pt["messages"] if cls.is_real_user_turn(m)]
                b_users = [bm for bm in bt["messages"] if cls.is_real_user_turn(bm)]
                for pm, bm in zip(p_users, b_users):
                    if not pm.get("id") and bm.get("id"):
                        pm["id"] = bm["id"]
                    ts = bm.get("updated_at") or bm.get("timestamp")
                    if not pm.get("updated_at") and ts:
                        pm["updated_at"] = ts

                b_assts = [bm for bm in bt["messages"] if bm.get("role") == "assistant"]
                p_non_users = [m for m in pt["messages"] if not cls.is_real_user_turn(m)]
                p_assts_all = [m for m in p_non_users if m.get("role") in ("assistant", "model")]
                p_assts_text = [m for m in p_assts_all if not m.get("tool_calls")]
                if b_assts:
                    bm = b_assts[-1]
                    b_id = bm.get("id")
                    ts = bm.get("updated_at") or bm.get("timestamp")
                    for m in p_non_users:
                        if b_id:
                            m["_echo_turn_asst_id"] = b_id
                        if ts:
                            m["_echo_turn_asst_ts"] = ts
                    pm = p_assts_text[-1] if p_assts_text else (p_assts_all[-1] if p_assts_all else None)
                    if pm is not None:
                        if not pm.get("id") and b_id:
                            pm["id"] = b_id
                        if not pm.get("updated_at") and ts:
                            pm["updated_at"] = ts

        for m in messages:
            if isinstance(m, dict) and m.get("id") and m.get("role") == "user":
                meta = m.setdefault("metadata", {})
                if isinstance(meta, dict) and "user_message_id" not in meta:
                    meta["user_message_id"] = m["id"]

    PERISHABLE_AEC_IDS = (
        "AEC_modele",
        "AEC_identite",
        "AEC_temporalite",
        "AEC_horloge",
        "AEC_localisation",
    )

    @classmethod
    def _is_perishable_aec_part(cls, part: Dict[str, Any]) -> bool:
        if not isinstance(part, dict) or "text" not in part or len(part) != 1:
            return False
        txt = str(part.get("text") or "").strip()
        if not txt.startswith("<artifact") or not txt.endswith("</artifact>"):
            return False
        return any(f'id="{aid}"' in txt for aid in cls.PERISHABLE_AEC_IDS)

    @classmethod
    def _merge_parts_sequences(cls, sequences: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Fusionne plusieurs listes de parts de messages user consécutifs."""
        if not sequences:
            return []
        if len(sequences) == 1:
            return list(sequences[0])

        last_seq = sequences[-1] or []
        fresh_headers = [
            p for p in last_seq
            if isinstance(p, dict) and cls._is_perishable_aec_part(p)
        ]

        re_req = re.compile(r"<REQUETE_UTILISATEUR>(.*?)</REQUETE_UTILISATEUR>", re.DOTALL)
        queries: List[str] = []
        substantial_parts: List[Dict[str, Any]] = []
        seen_keys: set = set()

        for seq in sequences:
            for p in (seq or []):
                if not isinstance(p, dict) or cls._is_perishable_aec_part(p):
                    continue
                txt = p.get("text")
                if isinstance(txt, str) and "<REQUETE_UTILISATEUR>" in txt:
                    for match in re_req.finditer(txt):
                        q_content = match.group(1).strip()
                        if q_content:
                            queries.append(q_content)
                    remainder = re_req.sub("", txt).strip()
                    if remainder:
                        p_rem = dict(p)
                        p_rem["text"] = remainder
                        try:
                            key = std_json.dumps(p_rem, option=std_json.OPT_SORT_KEYS)
                        except Exception:
                            key = repr(p_rem).encode("utf-8", errors="ignore")
                        if key not in seen_keys:
                            seen_keys.add(key)
                            substantial_parts.append(p_rem)
                else:
                    try:
                        key = std_json.dumps(p, option=std_json.OPT_SORT_KEYS)
                    except Exception:
                        key = repr(p).encode("utf-8", errors="ignore")
                    if key not in seen_keys:
                        seen_keys.add(key)
                        substantial_parts.append(p)

        merged: List[Dict[str, Any]] = list(fresh_headers) + list(substantial_parts)
        if queries:
            joined = "\n\n".join(queries)
            merged.append({"text": f"<REQUETE_UTILISATEUR>{joined}</REQUETE_UTILISATEUR>"})
        return merged

    @classmethod
    def merge_consecutive_user_parts(
        cls,
        prev_or_messages: Any,
        next_parts: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """
        Fusionne les tours user consécutifs.
        Supporte deux signatures :
        1. merge_consecutive_user_parts(messages: List[Dict]) -> List[Dict]
           Fusionne les messages Gemini/OWUI successifs ayant role == 'user'.
        2. merge_consecutive_user_parts(prev_parts: List[Dict], next_parts: List[Dict]) -> List[Dict]
           Fusionne directement deux listes de parts Gemini.
        """
        if next_parts is not None:
            return cls._merge_parts_sequences([prev_or_messages, next_parts])

        if not isinstance(prev_or_messages, list):
            return prev_or_messages

        is_msg_list = bool(prev_or_messages and isinstance(prev_or_messages[0], dict) and "role" in prev_or_messages[0])
        if not is_msg_list:
            return prev_or_messages

        merged_messages: List[Dict[str, Any]] = []
        user_group: List[Dict[str, Any]] = []

        def flush_user_group():
            nonlocal user_group
            if not user_group:
                return
            if len(user_group) == 1:
                merged_messages.append(user_group[0])
            else:
                all_ids = [m.get("_msg_id") or m.get("id") for m in user_group if m.get("_msg_id") or m.get("id")]
                last_id = all_ids[-1] if all_ids else None
                parts_seq = [m.get("parts", []) for m in user_group]
                merged_parts = cls._merge_parts_sequences(parts_seq)
                merged_msg = {
                    "role": "user",
                    "parts": merged_parts,
                }
                if last_id:
                    merged_msg["_msg_id"] = last_id
                if all_ids:
                    merged_msg["_merged_from"] = all_ids
                merged_messages.append(merged_msg)
            user_group = []

        for msg in prev_or_messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_group.append(msg)
            else:
                flush_user_group()
                merged_messages.append(msg)
        flush_user_group()
        return merged_messages

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
        break_ids: Set[str] = set()
        if pivot_msg_id:
            break_ids.add(str(pivot_msg_id))
        if checkpoint.get("head_last_msg_id"):
            break_ids.add(str(checkpoint["head_last_msg_id"]))
        for tid in checkpoint.get("tail_anchor_ids") or []:
            break_ids.add(str(tid))

        # Réhydratation O(1) des IDs et timestamps depuis branch_messages avant recherche d'ID
        if branch_messages:
            cls.rehydrate_messages_metadata(messages, branch_messages)

        p_turns = cls.split_into_turns(messages, break_on_ids=break_ids)
        if not p_turns:
            return [], 0

        pivot_idx = cls.resolve_pivot_turn_index(p_turns, checkpoint)
        cut_msg_idx = p_turns[pivot_idx]["start_msg_idx"] if pivot_idx < len(p_turns) else p_turns[-1]["start_msg_idx"]

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
        target_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Exécute la Compaction Contextuelle In-Place sur les tours passés complets (past_messages) :
        1. Découpe past_messages en tours et calcule le pivot selon la répartition (ex: 63% compressé / 37% gardé de 20%).
        2. Sérialise les tours de HEAD sans aucune troncature et avec leurs AEC.
        3. Résout le modèle le plus haut disponible par clamping et cascade descendante.
        4. Appelle EchoGeminiClient.call_distillation en surchargeant le thinking à "low" pour Prometheus et en préservant le thinking nominal en cascade.
        5. Scelle <artifact id="AEC_context_checkpoint"> dans echo_settings['context_checkpoint'].
        """
        from echo_constants import COMPACTION_MIN_TURNS_REQUIRED, get_descending_cascade, ECHO_MODELS_REGISTRY
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

        break_ids: Set[str] = set()
        if existing_ckpt:
            if existing_ckpt.get("pivot_msg_id"):
                break_ids.add(str(existing_ckpt["pivot_msg_id"]))
            if existing_ckpt.get("head_last_msg_id"):
                break_ids.add(str(existing_ckpt["head_last_msg_id"]))
            for tid in existing_ckpt.get("tail_anchor_ids") or []:
                break_ids.add(str(tid))

        turns = cls.split_into_turns(past_messages, state_mgr=state_mgr, break_on_ids=break_ids)
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

        serialized_delta = cls.format_turns_for_distillation(plan["delta_turns"], state_mgr=state_mgr)
        sqlite_snapshot = cls.build_sqlite_resources_snapshot(user_id, chat_id)

        compressed_budget = int(plan["compressed_budget_tokens"])
        api_max_output_tokens = min(compressed_budget, cls.GEMINI_MAX_OUTPUT_TOKENS)

        prompt_parts: List[str] = [
            f"<COMPRESSION_BUDGET>\n"
            f"Budget cible maximal pour ce Checkpoint : {compressed_budget} tokens "
            f"(capacité de sortie maximale : {api_max_output_tokens} tokens).\n"
            f"INJONCTION D'EXHAUSTIVITÉ & EXPLOITATION CAPACITAIRE : Le Modèle DOIT exploiter pleinement la capacité disponible pour fournir une archive technique hautement détaillée et approfondie, restituant l'intégralité du code critique, des données extraites, des raisonnements complets et de la chronologie des échanges.\n"
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

        # Résolution du modèle le plus haut disponible par clamping
        start_model = target_model or clamp_model("MODEL_PROMETHEUS", metadata, user_id=user_id)
        if start_model not in ECHO_MODELS_REGISTRY:
            start_model = "MODEL_APOLLO"
        candidate_models = get_descending_cascade(start_model, include_start=True)
        if not candidate_models:
            candidate_models = [start_model]

        summary_md = ""
        model_used = None
        for cand_model in candidate_models:
            try:
                thinking_override = "low" if cand_model in ("MODEL_PROMETHEUS", "MODEL_ATHENA") else None
                res = await EchoGeminiClient.call_distillation(
                    prompt=user_prompt,
                    __user__={"id": user_id},
                    __metadata__={**(metadata or {}), "chat_id": chat_id},
                    is_json=False,
                    max_tokens=api_max_output_tokens,
                    target_model=cand_model,
                    system_instruction=SYS_COMPACTION_DISTILL,
                    timeout=240,
                    override_thinking=thinking_override,
                )
                if res and str(res).strip():
                    summary_md = str(res).strip()
                    model_used = cand_model
                    break
                logger.warning(f"ECHO Compaction: Distillation vide avec {cand_model}, tentative suivante...")
            except Exception as e:
                logger.warning(f"ECHO Compaction: Échec {cand_model} ({e}), repli cascade...")

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
            "head_last_msg_id": plan.get("head_last_msg_id"),
            "keep_recent_turns": plan["keep_recent_turns"],
            "tail_anchor_ids": plan.get("tail_anchor_ids", []),
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
        new_est_prompt = (
            new_ckpt["checkpoint_tokens"] + plan["tail_tokens_est"] + incoming_tokens
        )
        state_mgr.save_context_stats(
            {
                "promptTokenCount": new_est_prompt,
                "totalTokenCount": new_est_prompt,
                "compaction_count": compaction_count,
                "compacted_turns": plan["new_pivot_turn_idx"],
            }
        )

        EchoAEC.record_event(
            state_manager=state_mgr,
            event_name="context_compaction",
            status="completed",
            summary=(
                f"Compaction contextuelle #{compaction_count} exécutée via {model_used or 'LLM'} : "
                f"tours 1 à {plan['new_pivot_turn_idx']} consolidés dans <artifact id=\"AEC_context_checkpoint\">, "
                f"{plan['keep_recent_turns']} tour(s) récent(s) maintenu(s) intact(s)."
            ),
            resource_type="aec_event",
        )

        return {
            "ok": True,
            "compaction_count": compaction_count,
            "compacted_turns": plan["new_pivot_turn_idx"],
            "model_used": model_used,
            "delta_turns_count": plan["delta_turns_count"],
            "keep_recent_turns": plan["keep_recent_turns"],
            "checkpoint_tokens": estimate_token_size(summary_xml),
            "tail_tokens_est": plan["tail_tokens_est"],
        }

