"""
title: ECHO V6 Cognition Router
author: Wilfried BARNAVON
version: 6.15.1.1
description: Routeur FastAPI pour la visualisation cognitive et des sous-agents (/echo/v1/cognition).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.15.1.1: Alignement du délai de grâce dans GET /{chat_id}/browser-frames sur BROWSER_TRANSIENT_GRACE_SEC (1800.0s).
# 6.15.0.1: Exposition de http_status dans _read_browser_frames_sync et grâce passive min_age_sec=300.0 dans GET /{chat_id}/browser-frames.
# 6.13.2.1: Ajout de GET /{chat_id}/browser-frames/{file_id}/clip, POST /{chat_id}/browser-frames/{file_id}/retain, du flux SSE GET /{chat_id}/browser-live et exposition des métadonnées clip_* dans _read_browser_frames_sync.
# 6.7.7.1: Normalisation des métriques de tranche par tours (slice_turns_used, slice_turns_limit, slice_timeout_sec, total_turns_used) avec miroirs rétrocompatibles slice_calls_* et alignement sur ECHO_SUBAGENT_CHECKPOINT_TURNS/ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC.
# 6.6.30.2: Suppression troncature [:8] des groupes, tri déterministe des membres par role_order, alignement ECHO_SUBAGENT_ENGINES_REGISTRY et dispatch multi-cibles sur POST /message.

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import orjson as json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from open_webui.echo.http_utils import (
    create_echo_sse_response,
    get_echo_sse_user,
    get_echo_verified_user,
)
from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.models.chats import Chats

ensure_echo_sys_path()
try:
    from echo_browser_lib import cleanup_expired_browserviews  # type: ignore
    from echo_constants import (  # type: ignore
        BROWSER_CLIP_EXT,
        BROWSER_CLIP_MIME,
        BROWSER_TRANSIENT_GRACE_SEC,
        BROWSER_VIEW_STATUS,
        BROWSER_VIEWS_DIR_NAME,
        ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
        ECHO_SUBAGENT_CHECKPOINT_TURNS,
        ECHO_SUBAGENT_ENGINES_REGISTRY,
    )
    from echo_db_schema import open_db  # type: ignore
    from echo_paths import (  # type: ignore
        get_echo_session_path,
        sanitize_echo_id,
    )
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_subagent_hub import (  # type: ignore
        SUBAGENT_META_PREFIX,
        SUBAGENT_QUEUE_PREFIX,
        EchoSubagentController,
        extract_turn_origin,
        get_cognition_seq,
        is_subagent_running,
        resolve_target_sub_sids,
    )
    from echo_window_manager import get_window_manager  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    try:
        from echo_browser_lib import cleanup_expired_browserviews  # type: ignore
    except ImportError:
        def cleanup_expired_browserviews(*args: Any, **kwargs: Any) -> int:
            return 0
    try:
        from echo_constants import (  # type: ignore
            BROWSER_CLIP_EXT,
            BROWSER_CLIP_MIME,
            BROWSER_TRANSIENT_GRACE_SEC,
            BROWSER_VIEW_STATUS,
            BROWSER_VIEWS_DIR_NAME,
            ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
            ECHO_SUBAGENT_CHECKPOINT_TURNS,
            ECHO_SUBAGENT_ENGINES_REGISTRY,
        )
    except ImportError:
        BROWSER_CLIP_EXT = "_clip.mp4"
        BROWSER_CLIP_MIME = "video/mp4"
        BROWSER_TRANSIENT_GRACE_SEC = 1800.0
        BROWSER_VIEW_STATUS = {"TRANSIENT": "transient", "RETAINED": "retained"}
        BROWSER_VIEWS_DIR_NAME = "browserviews"
        ECHO_SUBAGENT_CHECKPOINT_TURNS = 8
        ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC = 120
        ECHO_SUBAGENT_ENGINES_REGISTRY = {}
    from echo_db_schema import open_db  # type: ignore
    from echo_paths import (  # type: ignore
        get_echo_session_path,
        sanitize_echo_id,
    )
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_subagent_hub import (  # type: ignore
        SUBAGENT_META_PREFIX,
        SUBAGENT_QUEUE_PREFIX,
        EchoSubagentController,
        extract_turn_origin,
        get_cognition_seq,
        is_subagent_running,
        resolve_target_sub_sids,
    )
    try:
        from echo_window_manager import get_window_manager  # type: ignore
    except ImportError:
        get_window_manager = None  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()

_BG_SUBAGENT_TASKS: set[asyncio.Task] = set()


def _launch_bg_task(coro: Any, label: str) -> asyncio.Task:
    """Lance et trace une tâche asynchrone de sous-agent en arrière-plan."""
    async def _runner() -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[Cognition] Échec tâche arrière-plan (%s)", label)

    task = asyncio.create_task(_runner())
    _BG_SUBAGENT_TASKS.add(task)
    task.add_done_callback(_BG_SUBAGENT_TASKS.discard)
    return task


class SubagentMessagePayload(BaseModel):
    content: str
    target_sids: Optional[List[str]] = None


class SubagentControlRequest(BaseModel):
    action: Literal["suspend", "resume", "kill", "close"]
    directive: Optional[str] = None
    target_model_key: Optional[
        Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"]
    ] = None


class BrowserFrameRetainRequest(BaseModel):
    retain: bool = True


_DOM_MAP_PREFIX = "Voici les éléments interactifs actuels (Carte DOM) :"
_NAV_OBJ_RE = re.compile(r"<objective>(.*?)</objective>", re.DOTALL)
_COUNCIL_SID_RE = re.compile(r"^thread_council_(.+?)_(synthesis|[^_]+(?:_\d+)?)$")
_SUPERVISOR_SID_RE = re.compile(r"^thread_supervisor_(sup_[a-f0-9]+|.+?)_(critic|consolidator|[^_]+)$")
_QUESTION_RE = re.compile(r"QUESTION:\s*(.+?)$", re.MULTILINE)


def _sanitize_cognition_content(
    content: Any, role: str, role_id: str, step_index: int
) -> Any:
    """Allège les payloads DOM/système volumineux pour l'observabilité UI."""
    if not isinstance(content, list):
        return content

    sanitized: list[Any] = []
    for part in content:
        if not isinstance(part, dict):
            sanitized.append(part)
            continue

        if role == "user" and isinstance(part.get("text"), str):
            txt = part["text"]
            if step_index == 0 and (
                ("<objective>" in txt and "<rules>" in txt)
                or (role_id == "navigator" and "<objective>" in txt)
            ):
                m_obj = _NAV_OBJ_RE.search(txt)
                obj_txt = m_obj.group(1).strip() if m_obj else txt[:300].strip()
                new_part = dict(part)
                new_part["text"] = f"[ Consigne Système Navigateur ]\nObjectif : {obj_txt}"
                sanitized.append(new_part)
                continue

            if _DOM_MAP_PREFIX in txt:
                idx = txt.find(_DOM_MAP_PREFIX)
                prefix_head = txt[:idx].rstrip()
                tail = txt[idx + len(_DOM_MAP_PREFIX):]
                lines = tail.splitlines()
                vp_lines = [ln for ln in lines[:3] if "Dimensions de l'écran" in ln]
                json_tail = "\n".join(
                    ln for ln in lines if "Dimensions de l'écran" not in ln
                ).strip()
                count = 0
                if json_tail:
                    try:
                        parsed_dom = json.loads(json_tail)
                        if isinstance(parsed_dom, list):
                            count = len(parsed_dom)
                        else:
                            count = len(re.findall(r'"id"\s*:', json_tail))
                    except Exception:
                        count = len(re.findall(r'"id"\s*:', json_tail))
                summary_parts = []
                if prefix_head:
                    summary_parts.append(prefix_head)
                if vp_lines:
                    summary_parts.append(vp_lines[0].strip())
                summary_parts.append(f"[Carte DOM : {count} éléments interactifs]")
                new_part = dict(part)
                new_part["text"] = "\n".join(summary_parts)
                sanitized.append(new_part)
                continue

        if "functionResponse" in part and isinstance(part["functionResponse"], dict):
            fr = dict(part["functionResponse"])
            resp = fr.get("response")
            if isinstance(resp, dict) and ("dom_map" in resp or "a11y_tree" in resp):
                new_resp = dict(resp)
                for k in ("dom_map", "a11y_tree"):
                    if isinstance(new_resp.get(k), list):
                        new_resp[k] = f"[Carte DOM : {len(new_resp[k])} éléments]"
                fr["response"] = new_resp
                new_part = dict(part)
                new_part["functionResponse"] = fr
                sanitized.append(new_part)
                continue

        sanitized.append(part)
    return sanitized


_CANONICAL_SUBAGENT_STATUSES = frozenset({
    "running",
    "idle",
    "completed",
    "pending_question",
    "suspended",
    "suspended_checkpoint",
    "suspended_pare_alert",
    "killed",
    "error",
})

_GROUP_STATUS_PRIORITY = {
    "running": 9,
    "suspended_pare_alert": 8,
    "pending_question": 7,
    "suspended_checkpoint": 6,
    "suspended": 5,
    "error": 4,
    "killed": 3,
    "completed": 2,
    "idle": 1,
}


def _normalize_subagent_status(raw_status: Any, running_override: bool = False) -> str:
    """Normalise un statut de sous-agent vers l'un des 9 statuts canoniques."""
    if running_override:
        return "running"
    val = str(raw_status or "").strip()
    if val in _CANONICAL_SUBAGENT_STATUSES:
        return val
    if val in ("active", "in_progress"):
        return "idle"
    if val in ("done", "finished"):
        return "completed"
    return "idle"


def _infer_engine_key(sid: str, role_id: str, meta: Dict[str, Any]) -> str:
    """Résout la clé canonique de moteur dans ECHO_SUBAGENT_ENGINES_REGISTRY."""
    explicit = str(meta.get("engine") or "").strip()
    if explicit and (not ECHO_SUBAGENT_ENGINES_REGISTRY or explicit in ECHO_SUBAGENT_ENGINES_REGISTRY):
        return explicit
    if sid.startswith("thread_web_") or role_id == "navigator":
        return "navigator"
    if sid.startswith("thread_council_"):
        return "council_synthesis" if sid.endswith("_synthesis") else "council"
    if sid.startswith("thread_supervisor_"):
        if sid.endswith("_critic"):
            return "supervisor_critic"
        if sid.endswith("_consolidator"):
            return "supervisor_consolidator"
        return "supervisor"
    if sid.startswith("dlg_img_") or role_id == "image_gen":
        return "image_gen"
    if "deep_research" in sid or role_id == "deep_research":
        return "deep_research"
    if sid.startswith("thread_n8n_") or "n8n" in sid or role_id == "n8n_grapher":
        return "n8n_grapher"
    if sid.startswith("thread_databroker_") or "databroker" in sid or role_id == "data_broker":
        return "data_broker"
    return explicit or "delegate"


def _classify_thread(sid: str, role_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Retourne les attributs typés d'affichage d'un thread sous-agent."""
    is_running = _normalize_subagent_status(meta.get("status")) == "running"
    model_key = meta.get("model_key")
    custom_label = meta.get("role_name") or meta.get("role")
    engine_key = _infer_engine_key(sid, role_id, meta)
    spec = ECHO_SUBAGENT_ENGINES_REGISTRY.get(engine_key, {})

    default_label = spec.get("default_label")
    if engine_key == "navigator":
        fallback_label = default_label or "Navigateur Web"
        ttype, icon, color = spec.get("thread_type", "web"), spec.get("icon", "🌐"), spec.get("color", "cyan")
    elif engine_key in ("council", "council_synthesis"):
        m = _COUNCIL_SID_RE.match(sid)
        suffix = m.group(2) if m else sid
        fallback_label = (default_label or "Synthèse du Conseil") if engine_key == "council_synthesis" else f"Expert ({suffix})"
        ttype, icon, color = spec.get("thread_type", "council"), spec.get("icon", "🏛️"), spec.get("color", "purple")
    elif engine_key in ("supervisor", "supervisor_critic", "supervisor_consolidator"):
        m = _SUPERVISOR_SID_RE.match(sid)
        suffix = m.group(2) if m else sid
        fallback_label = (
            (default_label or "Critique") if engine_key == "supervisor_critic"
            else (default_label or "Consolidation") if engine_key == "supervisor_consolidator"
            else f"Ouvrier ({suffix})"
        )
        ttype, icon, color = spec.get("thread_type", "supervisor"), spec.get("icon", "📋"), spec.get("color", "amber")
    elif engine_key == "deep_research":
        fallback_label = default_label or "Recherche Profonde"
        ttype, icon, color = spec.get("thread_type", "deep_research"), spec.get("icon", "🕵️"), spec.get("color", "cyan")
    elif engine_key == "n8n_grapher":
        fallback_label = default_label or "Architecte N8N"
        ttype, icon, color = spec.get("thread_type", "n8n_grapher"), spec.get("icon", "⚡"), spec.get("color", "amber")
    elif engine_key == "data_broker":
        fallback_label = default_label or "Data Broker"
        ttype, icon, color = spec.get("thread_type", "data_broker"), spec.get("icon", "🔌"), spec.get("color", "cyan")
    elif engine_key == "image_gen":
        fallback_label = default_label or f"Studio Visuel IA ({sid})"
        ttype, icon, color = spec.get("thread_type", "image_gen"), spec.get("icon", "🎨"), spec.get("color", "purple")
    elif sid.startswith("thread_"):
        parts = sid.split("_")
        role_guess = "_".join(parts[1:-1]) if len(parts) >= 3 else sid
        fallback_label = f"Expert ({role_guess})"
        ttype, icon, color = spec.get("thread_type", "expert"), spec.get("icon", "🎓"), spec.get("color", "emerald")
    else:
        fallback_label = default_label or f"Agent ({sid})"
        ttype, icon, color = spec.get("thread_type", "agent"), spec.get("icon", "🤖"), spec.get("color", "indigo")

    has_tool_loop = (
        bool(meta["has_tool_loop"])
        if "has_tool_loop" in meta
        else bool(spec.get("has_tool_loop", True))
    )
    is_addressable = (
        bool(meta["is_addressable"])
        if "is_addressable" in meta
        else bool(spec.get("is_addressable", True))
    )
    role_order = int(meta.get("role_order") or spec.get("role_order") or 100)

    return {
        "engine": engine_key,
        "thread_type": ttype,
        "icon": icon,
        "color": color,
        "display_label": custom_label or fallback_label,
        "model_key": model_key,
        "is_running": is_running,
        "member_role": meta.get("member_role") or spec.get("member_role", "agent"),
        "role_order": role_order,
        "has_tool_loop": has_tool_loop,
        "is_addressable": is_addressable,
    }


def _read_cognition_snapshot(db_path: str, chat_id: str) -> Dict[str, Any]:
    """Lit threads, métadonnées, file FIFO et call_bridge en lecture seule."""
    if not os.path.isfile(db_path):
        return {
            "chat_id": chat_id,
            "threads": {},
            "thread_meta": {},
            "grouped_threads": [],
            "queued_messages": {},
            "tool_calls": [],
            "active_count": 0,
            "running_count": 0,
            "total_count": 0,
            "updated_at": 0,
        }

    threads: Dict[str, list[Dict[str, Any]]] = {}
    thread_meta: Dict[str, Dict[str, Any]] = {}
    queued_messages: Dict[str, list[Dict[str, Any]]] = {}
    tool_calls: list[Dict[str, Any]] = []
    max_ts = 0

    try:
        with closing(open_db(db_path, readonly=True, timeout=5.0)) as conn:
            # Lecture des étapes de sous-agents groupées par sous-session
            t_rows = conn.execute(
                "SELECT sub_sid, step_index, role_id, role, content_json, "
                "thought_signature, updated_at FROM cognitive_threads "
                "ORDER BY updated_at ASC, step_index ASC"
            ).fetchall()
            for r in t_rows:
                sub_sid = r[0]
                content = None
                if r[4]:
                    try:
                        content = json.loads(r[4])
                    except Exception:
                        content = r[4]
                content = _sanitize_cognition_content(
                    content, r[3], r[2] or "", r[1]
                )
                item: Dict[str, Any] = {
                    "sub_sid": sub_sid,
                    "step_index": r[1],
                    "role_id": r[2],
                    "role": r[3],
                    "content": content,
                    "thought_signature": r[5],
                    "updated_at": r[6],
                }
                if r[3] == "user":
                    text_parts = (
                        [
                            p.get("text", "")
                            for p in content
                            if isinstance(p, dict) and "text" in p
                        ]
                        if isinstance(content, list)
                        else []
                    )
                    if text_parts:
                        origin, clean_text = extract_turn_origin(
                            "".join(text_parts)
                        )
                        item["origin"] = origin
                        item["clean_text"] = clean_text
                    else:
                        item["origin"] = "tool"
                        item["clean_text"] = ""
                threads.setdefault(sub_sid, []).append(item)
                if r[6] and r[6] > max_ts:
                    max_ts = r[6]

            # Lecture des métadonnées et files FIFO des sous-agents
            s_rows = conn.execute(
                "SELECT key, value, updated_at FROM echo_settings "
                "WHERE key LIKE 'subagent_%'"
            ).fetchall()
            for key, val, upd_ts in s_rows:
                if upd_ts and upd_ts > max_ts:
                    max_ts = upd_ts
                if not val:
                    continue
                try:
                    parsed = json.loads(val)
                except Exception:
                    continue
                if key.startswith(SUBAGENT_META_PREFIX) and isinstance(
                    parsed, dict
                ):
                    sid = key[len(SUBAGENT_META_PREFIX):]
                    thread_meta[sid] = parsed
                elif key.startswith(SUBAGENT_QUEUE_PREFIX) and isinstance(
                    parsed, list
                ):
                    sid = key[len(SUBAGENT_QUEUE_PREFIX):]
                    queued_messages[sid] = parsed

            # Lecture du journal des appels d'outils
            c_rows = conn.execute(
                "SELECT call_id, signature, function_name, args_json, "
                "message_id, timestamp "
                "FROM call_bridge ORDER BY timestamp DESC LIMIT 200"
            ).fetchall()
            for r in c_rows:
                args = None
                if r[3]:
                    try:
                        args = json.loads(r[3])
                    except Exception:
                        args = r[3]
                tool_calls.append({
                    "call_id": r[0],
                    "signature": r[1],
                    "function_name": r[2],
                    "args": args,
                    "message_id": r[4],
                    "timestamp": r[5],
                })
                if r[5] and r[5] > max_ts:
                    max_ts = r[5]
    except Exception as e:
        log.warning("Erreur lecture cognition snapshot pour chat %s: %s", chat_id, e)

    for sid in set(threads.keys()) | set(thread_meta.keys()):
        entry = thread_meta.setdefault(sid, {"sub_sid": sid})
        running_now = is_subagent_running(chat_id, sid)
        entry["status"] = _normalize_subagent_status(
            entry.get("status"), running_override=running_now
        )
        steps_for_sid = threads.get(sid, [])
        role_id_hint = entry.get("role_id") or (
            steps_for_sid[0].get("role_id", "") if steps_for_sid else ""
        )
        entry.update(_classify_thread(sid, role_id_hint, entry))
        has_loop = bool(entry.get("has_tool_loop", True))
        turns_used_raw = (
            entry.get("slice_turns_used")
            if entry.get("slice_turns_used") is not None
            else entry.get("slice_calls_used")
        )
        entry["slice_turns_used"] = int(turns_used_raw or 0)
        turns_limit_raw = (
            entry.get("slice_turns_limit")
            if entry.get("slice_turns_limit") is not None
            else entry.get("slice_calls_limit")
        )
        entry["slice_turns_limit"] = (
            int(turns_limit_raw)
            if turns_limit_raw is not None
            else (0 if not has_loop else ECHO_SUBAGENT_CHECKPOINT_TURNS)
        )
        timeout_raw = entry.get("slice_timeout_sec")
        entry["slice_timeout_sec"] = (
            int(timeout_raw)
            if timeout_raw is not None
            else (0 if not has_loop else ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC)
        )
        entry["total_turns_used"] = int(entry.get("total_turns_used") or 0)
        entry["total_calls_used"] = int(entry.get("total_calls_used") or 0)
        entry["slice_calls_used"] = entry["slice_turns_used"]
        entry["slice_calls_limit"] = entry["slice_turns_limit"]
        entry["consecutive_errors"] = int(entry.get("consecutive_errors") or 0)
        if "checkpoint_reason" not in entry:
            entry["checkpoint_reason"] = None

    for sid, steps_list in threads.items():
        meta_entry = thread_meta.get(sid, {})
        model_key = meta_entry.get("model_key")
        last_idx = len(steps_list) - 1
        for idx, step in enumerate(steps_list):
            step["model_key"] = model_key
            step_content = step.get("content")
            esc_target = None
            fn_calls = []
            text_chunks = []
            if isinstance(step_content, list):
                for part in step_content:
                    if not isinstance(part, dict):
                        continue
                    if isinstance(part.get("text"), str):
                        text_chunks.append(part["text"])
                    fc = part.get("functionCall")
                    if isinstance(fc, dict):
                        fc_name = fc.get("name", "")
                        fc_args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                        fn_calls.append({"name": fc_name, "args": fc_args})
                        if fc_name == "new_cognitive_level":
                            esc_target = (
                                fc_args.get("niveau_requis")
                                or fc_args.get("target_level")
                                or fc_args.get("level")
                                or fc_args.get("cognitive_level")
                            )
            step["escalation_target"] = esc_target
            joined_text = "".join(text_chunks).strip()
            has_question_fc = any(
                c["name"] in ("ask_user", "ask_human", "request_clarification")
                for c in fn_calls
            )
            step["is_question"] = bool(
                step.get("role") == "model"
                and (
                    has_question_fc
                    or bool(
                        re.search(
                            r"(?:^|\n)\s*QUESTION\s*:", joined_text, re.IGNORECASE
                        )
                    )
                )
            )
            step["pending_tool_calls"] = (
                fn_calls
                if (
                    idx == last_idx
                    and step.get("role") == "model"
                    and fn_calls
                    and bool(meta_entry.get("is_running"))
                )
                else []
            )

    groups_map: Dict[str, Dict[str, Any]] = {}
    for sid in set(threads.keys()) | set(thread_meta.keys()):
        meta_entry = thread_meta.get(sid, {})
        steps_list = threads.get(sid, [])
        sid_updated = max(
            [s.get("updated_at") or 0 for s in steps_list]
            + [meta_entry.get("updated_at") or 0],
            default=0,
        )
        sid_running = bool(meta_entry.get("is_running"))

        raw_group_id = str(meta_entry.get("group_id") or "").strip()
        engine_val = str(meta_entry.get("engine") or "")
        m_ccl = _COUNCIL_SID_RE.match(sid)
        m_sup = _SUPERVISOR_SID_RE.match(sid)

        if engine_val in ("council", "council_synthesis") or m_ccl:
            raw_gid = raw_group_id or (m_ccl.group(1) if m_ccl else sid)
            gid = raw_gid if raw_gid.startswith("ccl_") else f"ccl_{raw_gid}"
            display_gid = raw_gid[4:] if raw_gid.startswith("ccl_") else raw_gid
            gid_display = display_gid if len(display_gid) <= 28 else f"{display_gid[:25]}..."
            gtype = "council"
            glabel = f"Table Ronde ({gid_display})"
            gicon = "🏛️"
            gcolor = "purple"
        elif engine_val in ("supervisor", "supervisor_critic", "supervisor_consolidator") or m_sup:
            raw_gid = raw_group_id or (m_sup.group(1) if m_sup else sid)
            gid = raw_gid if raw_gid.startswith("sup_") else f"sup_{raw_gid}"
            display_gid = raw_gid[4:] if raw_gid.startswith("sup_") else raw_gid
            gid_display = display_gid if len(display_gid) <= 28 else f"{display_gid[:25]}..."
            gtype = "supervisor"
            glabel = f"Supervision ({gid_display})"
            gicon = "📋"
            gcolor = "amber"
        else:
            gid = sid
            gtype = meta_entry.get("thread_type", "agent")
            glabel = meta_entry.get("display_label") or sid
            gicon = meta_entry.get("icon", "🤖")
            gcolor = meta_entry.get("color", "emerald")

        grp = groups_map.setdefault(
            gid,
            {
                "group_id": gid,
                "group_type": gtype,
                "label": glabel,
                "icon": gicon,
                "color": gcolor,
                "group_status": "idle",
                "is_running": False,
                "updated_at": 0.0,
                "threads": [],
            },
        )
        grp["threads"].append(sid)
        sid_status = str(meta_entry.get("status") or "idle")
        if _GROUP_STATUS_PRIORITY.get(sid_status, 0) > _GROUP_STATUS_PRIORITY.get(
            grp["group_status"], 0
        ):
            grp["group_status"] = sid_status
        if sid_running:
            grp["is_running"] = True
        if sid_updated > grp["updated_at"]:
            grp["updated_at"] = sid_updated

    for grp in groups_map.values():
        grp["threads"].sort(
            key=lambda s: (
                int(thread_meta.get(s, {}).get("role_order") or 100),
                str(thread_meta.get(s, {}).get("display_label") or s).lower(),
                s,
            )
        )

    grouped_threads = sorted(
        groups_map.values(),
        key=lambda g: (1 if g["is_running"] else 0, g["updated_at"]),
        reverse=True,
    )

    running_count = sum(
        1
        for m in thread_meta.values()
        if isinstance(m, dict) and m.get("status") == "running"
    )
    total_count = len(thread_meta)

    return {
        "chat_id": chat_id,
        "threads": threads,
        "thread_meta": thread_meta,
        "grouped_threads": grouped_threads,
        "queued_messages": queued_messages,
        "tool_calls": tool_calls,
        "active_count": total_count,
        "running_count": running_count,
        "total_count": total_count,
        "updated_at": max_ts,
    }


def _get_cognition_state_sig(db_path: str, chat_id: str) -> str:
    """Signature composite lecture seule (cognition_seq, threads, call_bridge, subagent_*)."""
    seq = get_cognition_seq(chat_id)
    if not os.path.isfile(db_path):
        return f"{seq}|"
    try:
        with closing(open_db(db_path, readonly=True, timeout=2.0)) as conn:
            t_row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at), 0) "
                "FROM cognitive_threads"
            ).fetchone()
            c_row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(timestamp), 0) "
                "FROM call_bridge"
            ).fetchone()
            s_row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at), 0), "
                "COALESCE(SUM(LENGTH(value)), 0) "
                "FROM echo_settings WHERE key LIKE 'subagent_%'"
            ).fetchone()
            return (
                f"{seq}|"
                f"{t_row[0]}:{t_row[1]}|"
                f"{c_row[0]}:{c_row[1]}|"
                f"{s_row[0]}:{s_row[1]}:{s_row[2]}"
            )
    except Exception:
        return f"{seq}|"


@router.get("/{chat_id}/snapshot")
async def get_cognition_snapshot(
    chat_id: str,
    user=Depends(get_echo_verified_user),
):
    """Retourne l'instantané complet de l'activité cognitive et des outils pour le chat demandé."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")
    snapshot = await asyncio.to_thread(_read_cognition_snapshot, db_path, chat_id)
    return JSONResponse(content=snapshot)


@router.get("/{chat_id}/stream")
async def get_cognition_stream(
    chat_id: str,
    request: Request,
    user=Depends(get_echo_sse_user),
):
    """Diffuse les mises à jour cognitives en SSE via signature composite."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")

    async def event_generator():
        current_snapshot = await asyncio.to_thread(_read_cognition_snapshot, db_path, chat_id)
        last_seen_sig = await asyncio.to_thread(_get_cognition_state_sig, db_path, chat_id)
        yield f"data: {json.dumps(current_snapshot).decode('utf-8')}\n\n"

        keepalive_counter = 0
        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(0.25)
            keepalive_counter += 1

            current_sig = await asyncio.to_thread(_get_cognition_state_sig, db_path, chat_id)
            if current_sig != last_seen_sig:
                current_snapshot = await asyncio.to_thread(_read_cognition_snapshot, db_path, chat_id)
                last_seen_sig = current_sig
                out_sse = json.dumps(current_snapshot).decode("utf-8")
                yield f"data: {out_sse}\n\n"
            elif keepalive_counter >= 60:
                keepalive_counter = 0
                yield ": keepalive\n\n"

    return create_echo_sse_response(event_generator())


def _normalize_control_target(raw_id: str) -> str:
    """Normalise un identifiant de thread ou groupe UI pour resolve_target_sub_sids."""
    tid = (raw_id or "").strip()
    if tid.startswith("ccl_"):
        return tid[4:]
    if tid.startswith("sup_sup_"):
        return tid[4:]
    return tid


async def _validate_cognition_session(chat_id: str, user_id: str) -> tuple[str, str]:
    """Vérifie l'accès à la session de chat et retourne (safe_uid, safe_cid)."""
    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user_id)
    safe_uid = sanitize_echo_id(user_id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")
    if not chat and not os.path.isfile(db_path):
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return safe_uid, safe_cid


@router.post("/{chat_id}/threads/{sub_sid}/message")
async def post_subagent_human_message(
    chat_id: str,
    sub_sid: str,
    payload: SubagentMessagePayload,
    user=Depends(get_echo_verified_user),
):
    """Envoie ou met en file une requête humaine directe vers un sous-agent."""
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(
            status_code=400, detail="Le message ne peut pas être vide."
        )

    safe_uid, safe_cid = await _validate_cognition_session(chat_id, user.id)
    _ = safe_uid, safe_cid

    dispatch_res = await asyncio.to_thread(
        EchoSubagentController.dispatch_message,
        user_id=user.id,
        chat_id=chat_id,
        target_ids=payload.target_sids or [sub_sid],
        directive=content,
        origin="human",
    )
    if dispatch_res.get("error"):
        raise HTTPException(
            status_code=int(dispatch_res.get("status_code") or 400),
            detail=dispatch_res["error"],
        )

    run_coro = dispatch_res.pop("run_coro", None)
    if run_coro is not None:
        _launch_bg_task(
            run_coro,
            label=f"human_message:{dispatch_res.get('sub_sid', sub_sid)}",
        )

    return JSONResponse(
        content=dispatch_res,
        status_code=202 if dispatch_res.get("status") == "queued" else 200,
    )


@router.post("/{chat_id}/threads/{sub_sid}/control")
async def post_subagent_control(
    chat_id: str,
    sub_sid: str,
    payload: SubagentControlRequest,
    user=Depends(get_echo_verified_user),
):
    """Pilote le cycle de vie d'un sous-agent ou groupe (suspend, resume, kill, close)."""
    safe_uid, safe_cid = await _validate_cognition_session(chat_id, user.id)
    sm = EchoStateManager(user_id=safe_uid, chat_id=safe_cid)
    target_key = _normalize_control_target(sub_sid)
    affected_sids = await asyncio.to_thread(
        resolve_target_sub_sids, sm, chat_id, [target_key]
    )
    if not affected_sids:
        raise HTTPException(status_code=404, detail="Sous-agent introuvable.")

    if payload.action == "resume":
        async def _resume_all() -> None:
            await asyncio.gather(
                *(
                    EchoSubagentController.run_or_resume(
                        user_id=user.id,
                        chat_id=chat_id,
                        sub_sid=sid,
                        directive=payload.directive,
                        origin="human",
                        target_model_key=payload.target_model_key,
                    )
                    for sid in affected_sids
                )
            )

        _launch_bg_task(_resume_all(), label=f"resume:{sub_sid}")
        return JSONResponse(
            content={
                "status": "started",
                "action": "resume",
                "sub_sid": sub_sid,
                "affected_sids": affected_sids,
            },
            status_code=200,
        )

    res = await EchoSubagentController.execute_control_action(
        action=payload.action,
        user_id=user.id,
        chat_id=chat_id,
        target_ids=affected_sids,
        directive=payload.directive,
        target_model_key=payload.target_model_key,
    )
    final_sids = (res or {}).get("affected_sids") or affected_sids
    return JSONResponse(
        content={
            "status": "ok",
            "action": payload.action,
            "sub_sid": sub_sid,
            "affected_sids": final_sids,
        },
        status_code=200,
    )


def _read_browser_frames_sync(
    db_path: str, safe_cid: str, browserviews_dir: str, files_dir: str
) -> list[Dict[str, Any]]:
    """Lit les captures navigateur persistées en lecture seule depuis SQLite et le disque."""
    frames: list[Dict[str, Any]] = []
    bv_root = os.path.realpath(browserviews_dir)
    files_root = os.path.realpath(files_dir)
    try:
        with closing(open_db(db_path, readonly=True, timeout=2.0)) as conn:
            rows = conn.execute(
                "SELECT id, name, storage_path, metadata, created_at, status "
                "FROM echo_resources "
                "WHERE resource_type = 'browserview' OR name LIKE '%_frame.jpg' "
                "ORDER BY created_at ASC, id ASC"
            ).fetchall()
            for r in rows:
                file_id = r[0]
                filename = os.path.basename(r[1] or "")
                storage_path = r[2] or ""
                meta_raw = r[3]
                res_status = r[5] or "transient"
                meta: Dict[str, Any] = {}
                if meta_raw:
                    try:
                        parsed = json.loads(meta_raw)
                        if isinstance(parsed, dict):
                            meta = parsed
                    except Exception:
                        meta = {}

                candidate_names = [
                    filename,
                    os.path.basename(storage_path),
                    f"{sanitize_echo_id(file_id)}_frame.jpg",
                ]
                resolved_path = None
                for base_dir in (bv_root, files_root):
                    for cand in candidate_names:
                        if not cand:
                            continue
                        cand_real = os.path.realpath(os.path.join(base_dir, cand))
                        if (
                            cand_real.startswith(base_dir + os.sep)
                            and os.path.isfile(cand_real)
                        ):
                            resolved_path = cand_real
                            if not filename:
                                filename = os.path.basename(cand_real)
                            break
                    if resolved_path:
                        break

                if not resolved_path:
                    continue

                clip_resolved = None
                clip_candidates = [
                    f"{sanitize_echo_id(file_id)}{BROWSER_CLIP_EXT}",
                    os.path.basename(str(meta.get("clip_filename") or "")),
                ]
                for ccand in clip_candidates:
                    if not ccand:
                        continue
                    ccand_real = os.path.realpath(os.path.join(bv_root, ccand))
                    if (
                        ccand_real.startswith(bv_root + os.sep)
                        and os.path.isfile(ccand_real)
                    ):
                        clip_resolved = ccand_real
                        break

                frames.append(
                    {
                        "file_id": file_id,
                        "filename": filename,
                        "session_id": meta.get("session_id", ""),
                        "url": meta.get("url", ""),
                        "action": meta.get("action", ""),
                        "http_status": meta.get("http_status"),
                        "timestamp": meta.get("timestamp", r[4]),
                        "status": res_status,
                        "retained": bool(res_status == "retained" or meta.get("retained")),
                        "image_url": (
                            f"/echo/v1/cognition/{safe_cid}"
                            f"/browser-frames/{file_id}/image"
                        ),
                        "clip_url": (
                            f"/echo/v1/cognition/{safe_cid}"
                            f"/browser-frames/{file_id}/clip"
                            if clip_resolved
                            else ""
                        ),
                        "clip_duration_ms": (
                            int(meta.get("clip_duration_ms") or 0)
                            if clip_resolved
                            else 0
                        ),
                        "clip_size_bytes": (
                            int(meta.get("clip_size_bytes") or 0)
                            if clip_resolved
                            else 0
                        ),
                    }
                )
    except Exception as e:
        log.warning(f"[Cognition] Erreur lecture browser-frames: {e}")
    return frames


def _resolve_browser_frame_image_sync(
    db_path: str, file_id: str, safe_fid: str, browserviews_dir: str, files_dir: str
) -> str | None:
    """Résout le chemin disque d'une capture navigateur avec repli SQLite lecture seule."""
    for base_dir in (browserviews_dir, files_dir):
        default_cand = os.path.realpath(
            os.path.join(base_dir, f"{safe_fid}_frame.jpg")
        )
        if (
            default_cand.startswith(base_dir + os.sep)
            and os.path.isfile(default_cand)
        ):
            return default_cand

    if os.path.isfile(db_path):
        try:
            with closing(open_db(db_path, readonly=True, timeout=2.0)) as conn:
                row = conn.execute(
                    "SELECT name, storage_path FROM echo_resources WHERE id = ?",
                    (file_id,),
                ).fetchone()
                if row:
                    for base_dir in (browserviews_dir, files_dir):
                        for raw_name in (row[1] or "", row[0] or ""):
                            base = os.path.basename(raw_name)
                            if not base:
                                continue
                            cand = os.path.realpath(os.path.join(base_dir, base))
                            if (
                                cand.startswith(base_dir + os.sep)
                                and os.path.isfile(cand)
                            ):
                                return cand
        except Exception as e:
            log.warning(f"[Cognition] Erreur lookup image frame {file_id}: {e}")
    return None


def _resolve_browser_frame_clip_sync(
    db_path: str, file_id: str, safe_fid: str, browserviews_dir: str
) -> str | None:
    """Résout le chemin disque du clip MP4 d'une capture navigateur avec repli SQLite lecture seule."""
    bv_root = os.path.realpath(browserviews_dir)
    default_cand = os.path.realpath(
        os.path.join(bv_root, f"{safe_fid}{BROWSER_CLIP_EXT}")
    )
    if (
        default_cand.startswith(bv_root + os.sep)
        and os.path.isfile(default_cand)
    ):
        return default_cand

    if os.path.isfile(db_path):
        try:
            with closing(open_db(db_path, readonly=True, timeout=2.0)) as conn:
                row = conn.execute(
                    "SELECT metadata FROM echo_resources WHERE id = ?",
                    (file_id,),
                ).fetchone()
                if row and row[0]:
                    parsed = json.loads(row[0])
                    if isinstance(parsed, dict):
                        clip_name = os.path.basename(
                            str(parsed.get("clip_filename") or "")
                        )
                        if clip_name:
                            cand = os.path.realpath(
                                os.path.join(bv_root, clip_name)
                            )
                            if (
                                cand.startswith(bv_root + os.sep)
                                and os.path.isfile(cand)
                            ):
                                return cand
        except Exception as e:
            log.warning(f"[Cognition] Erreur lookup clip frame {file_id}: {e}")
    return None


@router.get("/{chat_id}/browser-frames")
async def get_browser_frames(chat_id: str, user=Depends(get_echo_verified_user)):
    """Retourne les captures persistées du navigateur web pour la session."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")
    if not os.path.isfile(db_path):
        return JSONResponse(content={"frames": []})

    try:
        await cleanup_expired_browserviews(safe_uid, safe_cid, min_age_sec=BROWSER_TRANSIENT_GRACE_SEC)
    except Exception:
        pass

    browserviews_dir = os.path.realpath(
        get_echo_session_path(safe_uid, safe_cid, BROWSER_VIEWS_DIR_NAME)
    )
    files_dir = os.path.realpath(get_echo_session_path(safe_uid, safe_cid, "files"))
    frames = await asyncio.to_thread(
        _read_browser_frames_sync, db_path, safe_cid, browserviews_dir, files_dir
    )
    return JSONResponse(content={"frames": frames})


@router.get("/{chat_id}/browser-frames/{file_id}/image")
async def get_browser_frame_image(
    chat_id: str,
    file_id: str,
    user=Depends(get_echo_verified_user),
):
    """Sert l'image JPEG d'une capture navigateur persistée."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    safe_fid = sanitize_echo_id(file_id)
    browserviews_dir = os.path.realpath(
        get_echo_session_path(safe_uid, safe_cid, BROWSER_VIEWS_DIR_NAME)
    )
    files_dir = os.path.realpath(get_echo_session_path(safe_uid, safe_cid, "files"))
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")

    target_path = await asyncio.to_thread(
        _resolve_browser_frame_image_sync,
        db_path,
        file_id,
        safe_fid,
        browserviews_dir,
        files_dir,
    )
    if not target_path:
        raise HTTPException(status_code=404, detail="Capture introuvable.")

    return FileResponse(target_path, media_type="image/jpeg")


@router.get("/{chat_id}/browser-frames/{file_id}/clip")
async def get_browser_frame_clip(
    chat_id: str,
    file_id: str,
    user=Depends(get_echo_verified_user),
):
    """Sert le clip vidéo MP4 d'une capture navigateur persistée."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    safe_fid = sanitize_echo_id(file_id)
    browserviews_dir = os.path.realpath(
        get_echo_session_path(safe_uid, safe_cid, BROWSER_VIEWS_DIR_NAME)
    )
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")

    target_path = await asyncio.to_thread(
        _resolve_browser_frame_clip_sync,
        db_path,
        file_id,
        safe_fid,
        browserviews_dir,
    )
    if not target_path:
        raise HTTPException(status_code=404, detail="Clip introuvable.")

    return FileResponse(target_path, media_type=BROWSER_CLIP_MIME)


@router.post("/{chat_id}/browser-frames/{file_id}/retain")
async def retain_browser_frame(
    chat_id: str,
    file_id: str,
    payload: BrowserFrameRetainRequest,
    user=Depends(get_echo_verified_user),
):
    """Bascule la conservation durable d'une capture navigateur et notifie le client."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    safe_fid = sanitize_echo_id(file_id)
    state_manager = EchoStateManager(user_id=safe_uid, chat_id=safe_cid)
    res = await asyncio.to_thread(state_manager.get_resource, file_id)
    if not res or (
        res.get("resource_type") != "browserview"
        and not str(res.get("name") or "").endswith("_frame.jpg")
    ):
        raise HTTPException(status_code=404, detail="Capture introuvable.")

    raw_meta = res.get("metadata")
    meta: Dict[str, Any] = {}
    if isinstance(raw_meta, dict):
        meta = dict(raw_meta)
    elif isinstance(raw_meta, str) and raw_meta:
        try:
            parsed = json.loads(raw_meta)
            if isinstance(parsed, dict):
                meta = parsed
        except Exception:
            meta = {}

    retained = bool(payload.retain)
    meta["retained"] = retained
    new_status = (
        BROWSER_VIEW_STATUS["RETAINED"]
        if retained
        else BROWSER_VIEW_STATUS["TRANSIENT"]
    )
    await asyncio.to_thread(
        state_manager.update_resource_fields,
        file_id,
        status=new_status,
        metadata=json.dumps(meta).decode("utf-8"),
    )

    if not retained:
        try:
            await cleanup_expired_browserviews(safe_uid, safe_cid)
        except Exception:
            pass

    if get_window_manager:
        try:
            browserviews_dir = os.path.realpath(
                get_echo_session_path(safe_uid, safe_cid, BROWSER_VIEWS_DIR_NAME)
            )
            db_path = get_echo_session_path(safe_uid, safe_cid, "db")
            clip_resolved = await asyncio.to_thread(
                _resolve_browser_frame_clip_sync,
                db_path,
                file_id,
                safe_fid,
                browserviews_dir,
            )
            await get_window_manager().broadcast_browser_frame(
                user.id,
                chat_id,
                {
                    "file_id": file_id,
                    "status": new_status,
                    "retained": retained,
                    "image_url": (
                        f"/echo/v1/cognition/{safe_cid}"
                        f"/browser-frames/{file_id}/image"
                    ),
                    "clip_url": (
                        f"/echo/v1/cognition/{safe_cid}"
                        f"/browser-frames/{file_id}/clip"
                        if clip_resolved
                        else ""
                    ),
                    "clip_duration_ms": (
                        int(meta.get("clip_duration_ms") or 0)
                        if clip_resolved
                        else 0
                    ),
                    "clip_size_bytes": (
                        int(meta.get("clip_size_bytes") or 0)
                        if clip_resolved
                        else 0
                    ),
                },
            )
        except Exception:
            pass

    return JSONResponse(
        content={
            "status": "success",
            "file_id": file_id,
            "retained": retained,
            "resource_status": new_status,
        }
    )


@router.get("/{chat_id}/browser-live")
async def stream_browser_live(
    chat_id: str,
    request: Request,
    client_id: str = "",
    user=Depends(get_echo_sse_user),
):
    """Flux SSE temps réel (screencast live_frame, live_cursor, live_state) du navigateur."""
    if not get_window_manager:
        raise HTTPException(status_code=503, detail="WindowManager indisponible.")
    wm = get_window_manager()
    cid = client_id or uuid.uuid4().hex
    queue = wm.subscribe_browser_live(user.id, chat_id, cid)

    async def event_generator():
        try:
            init_ev = {
                "type": "live_connected",
                "chat_id": chat_id,
                "client_id": cid,
            }
            yield f"data: {json.dumps(init_ev).decode('utf-8')}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(ev).decode('utf-8')}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            wm.unsubscribe_browser_live(user.id, chat_id, cid)

    return create_echo_sse_response(event_generator())
