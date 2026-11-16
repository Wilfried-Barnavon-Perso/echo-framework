"""
title: ECHO V6 Cognition Router
author: Wilfried BARNAVON
version: 6.6.5.1
description: Routeur FastAPI pour la visualisation cognitive et des sous-agents (/echo/v1/cognition).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.5.1: Résolution prioritaire des captures navigateur dans browserviews/ avec GC éphémère à l'entrée et exposition de status/retained.
# 6.6.2.2: Ajout des endpoints GET /{chat_id}/browser-frames et /{chat_id}/browser-frames/{file_id}/image et alignement niveau_requis/is_question/pending_tool_calls.
# 6.6.2.1: Sanitization DOM/prompt, regroupement grouped_threads, enrichissement des steps et réactivité SSE via get_cognition_seq.
# 6.5.3.1: Ajout de POST /{chat_id}/threads/{sub_sid}/message (tours humains directs ou file FIFO) et enrichissement d'état des threads.
# 6.3.0.1: Création du routeur /echo/v1/cognition (snapshot et streaming SSE des cognitive_threads et call_bridge).

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Dict

import orjson as json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from open_webui.echo.runtime import (
    ensure_echo_sys_path,
    load_echo_tool_from_disk,
)
from open_webui.models.chats import Chats
from open_webui.utils.auth import get_verified_user

ensure_echo_sys_path()
try:
    from echo_browser_lib import cleanup_expired_browserviews  # type: ignore
    from echo_constants import BROWSER_VIEWS_DIR_NAME  # type: ignore
    from echo_db_schema import open_db  # type: ignore
    from echo_paths import (  # type: ignore
        get_echo_session_path,
        sanitize_echo_id,
    )
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_subagent_hub import (  # type: ignore
        SUBAGENT_META_PREFIX,
        SUBAGENT_QUEUE_PREFIX,
        enqueue_human_message,
        extract_turn_origin,
        get_cognition_seq,
        is_subagent_running,
    )
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_db_schema import open_db  # type: ignore
    from echo_paths import (  # type: ignore
        get_echo_session_path,
        sanitize_echo_id,
    )
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_subagent_hub import (  # type: ignore
        SUBAGENT_META_PREFIX,
        SUBAGENT_QUEUE_PREFIX,
        enqueue_human_message,
        extract_turn_origin,
        get_cognition_seq,
        is_subagent_running,
    )

log = logging.getLogger(__name__)

router = APIRouter()

_BG_SUBAGENT_TASKS: set[asyncio.Task] = set()


class SubagentMessagePayload(BaseModel):
    content: str


async def _load_agent_engine_module():
    """Charge le module agent_engine_tool depuis /opt/ECHO ou le dépôt."""
    loaded = await load_echo_tool_from_disk(
        "agent_engine_tool", sync_db_specs_on_reload=False
    )
    if loaded is not None:
        return loaded[2]
    local_path = (
        Path(__file__).resolve().parents[5]
        / "12-owui-tools"
        / "agent_engine_tool.py"
    )
    spec = importlib.util.spec_from_file_location(
        "tool_agent_engine_tool", str(local_path)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("agent_engine_tool introuvable")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool_agent_engine_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


_DOM_MAP_PREFIX = "Voici les éléments interactifs actuels (Carte DOM) :"
_NAV_OBJ_RE = re.compile(r"<objective>(.*?)</objective>", re.DOTALL)
_COUNCIL_SID_RE = re.compile(r"^thread_council_([^_]+)_(.+)$")
_SUPERVISOR_SID_RE = re.compile(r"^thread_supervisor_(sup_[a-f0-9]+|[^_]+)_(.+)$")
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


def _classify_thread(sid: str, role_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    """Retourne les attributs typés d'affichage d'un thread sous-agent."""
    is_running = meta.get("status") == "running"
    model_key = meta.get("model_key")
    custom_label = meta.get("role_name") or meta.get("role")

    if sid.startswith("thread_web_") or role_id == "navigator":
        return {
            "thread_type": "web",
            "icon": "🌐",
            "color": "#38bdf8",
            "display_label": custom_label or "Navigateur Web",
            "model_key": model_key,
            "is_running": is_running,
        }
    if sid.startswith("thread_council_"):
        m = _COUNCIL_SID_RE.match(sid)
        suffix = m.group(2) if m else sid
        return {
            "thread_type": "council",
            "icon": "🏛️",
            "color": "#a855f7",
            "display_label": custom_label or f"Expert ({suffix})",
            "model_key": model_key,
            "is_running": is_running,
        }
    if sid.startswith("thread_supervisor_"):
        m = _SUPERVISOR_SID_RE.match(sid)
        suffix = m.group(2) if m else sid
        return {
            "thread_type": "supervisor",
            "icon": "📋",
            "color": "#f59e0b",
            "display_label": custom_label or f"Supervision ({suffix})",
            "model_key": model_key,
            "is_running": is_running,
        }
    if sid.startswith("thread_"):
        parts = sid.split("_")
        role_guess = "_".join(parts[1:-1]) if len(parts) >= 3 else sid
        return {
            "thread_type": "expert",
            "icon": "🎓",
            "color": "#10b981",
            "display_label": custom_label or f"Expert ({role_guess})",
            "model_key": model_key,
            "is_running": is_running,
        }
    return {
        "thread_type": "agent",
        "icon": "🤖",
        "color": "#6366f1",
        "display_label": custom_label or f"Agent ({sid})",
        "model_key": model_key,
        "is_running": is_running,
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
        if is_subagent_running(chat_id, sid):
            entry["status"] = "running"
        steps_for_sid = threads.get(sid, [])
        role_id_hint = entry.get("role_id") or (
            steps_for_sid[0].get("role_id", "") if steps_for_sid else ""
        )
        entry.update(_classify_thread(sid, role_id_hint, entry))

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

        m_ccl = _COUNCIL_SID_RE.match(sid)
        m_sup = _SUPERVISOR_SID_RE.match(sid)
        if m_ccl:
            gid = f"ccl_{m_ccl.group(1)}"
            gtype = "council"
            glabel = f"Table Ronde ({m_ccl.group(1)[:8]})"
            gicon = "🏛️"
            gcolor = "#a855f7"
        elif m_sup:
            gid = f"sup_{m_sup.group(1)}"
            gtype = "supervisor"
            glabel = f"Supervision ({m_sup.group(1)[:8]})"
            gicon = "📋"
            gcolor = "#f59e0b"
        else:
            gid = sid
            gtype = meta_entry.get("thread_type", "agent")
            glabel = meta_entry.get("display_label") or sid
            gicon = meta_entry.get("icon", "🤖")
            gcolor = meta_entry.get("color", "#10b981")

        grp = groups_map.setdefault(
            gid,
            {
                "group_id": gid,
                "group_type": gtype,
                "label": glabel,
                "icon": gicon,
                "color": gcolor,
                "is_running": False,
                "updated_at": 0.0,
                "threads": [],
            },
        )
        grp["threads"].append(sid)
        if sid_running:
            grp["is_running"] = True
        if sid_updated > grp["updated_at"]:
            grp["updated_at"] = sid_updated

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
    total_count = len(threads)

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
    user=Depends(get_verified_user),
):
    """Retourne l'instantané complet de l'activité cognitive et des outils pour le chat demandé."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")
    snapshot = _read_cognition_snapshot(db_path, chat_id)
    return JSONResponse(content=snapshot)


@router.get("/{chat_id}/stream")
async def get_cognition_stream(
    chat_id: str,
    request: Request,
    user=Depends(get_verified_user),
):
    """Diffuse les mises à jour cognitives en SSE via signature composite."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")

    async def event_generator():
        current_snapshot = _read_cognition_snapshot(db_path, chat_id)
        last_seen_sig = _get_cognition_state_sig(db_path, chat_id)
        yield f"data: {json.dumps(current_snapshot).decode('utf-8')}\n\n"

        keepalive_counter = 0
        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(0.25)
            keepalive_counter += 1

            current_sig = _get_cognition_state_sig(db_path, chat_id)
            if current_sig != last_seen_sig:
                current_snapshot = _read_cognition_snapshot(db_path, chat_id)
                last_seen_sig = current_sig
                out_sse = json.dumps(current_snapshot).decode("utf-8")
                yield f"data: {out_sse}\n\n"
            elif keepalive_counter >= 60:
                keepalive_counter = 0
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{chat_id}/threads/{sub_sid}/message")
async def post_subagent_human_message(
    chat_id: str,
    sub_sid: str,
    payload: SubagentMessagePayload,
    user=Depends(get_verified_user),
):
    """Envoie ou met en file une requête humaine directe vers un sous-agent."""
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(
            status_code=400, detail="Le message ne peut pas être vide."
        )

    chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")
    if not chat and not os.path.isfile(db_path):
        raise HTTPException(status_code=404, detail="Session introuvable.")

    if is_subagent_running(chat_id, sub_sid):
        sm = EchoStateManager(user_id=safe_uid, chat_id=safe_cid)
        queue = enqueue_human_message(sm, sub_sid, content, user.id)
        return JSONResponse(
            content={
                "status": "queued",
                "sub_sid": sub_sid,
                "queue_length": len(queue),
            },
            status_code=202,
        )

    mod = await _load_agent_engine_module()
    task = asyncio.create_task(
        mod.run_human_subagent_turn(user.id, chat_id, sub_sid, content)
    )
    _BG_SUBAGENT_TASKS.add(task)
    task.add_done_callback(_BG_SUBAGENT_TASKS.discard)
    return JSONResponse(
        content={"status": "started", "sub_sid": sub_sid},
        status_code=200,
    )


@router.get("/{chat_id}/browser-frames")
async def get_browser_frames(chat_id: str, user=Depends(get_verified_user)):
    """Retourne les captures persistées du navigateur web pour la session."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")
    if not os.path.isfile(db_path):
        return JSONResponse(content={"frames": []})

    try:
        await cleanup_expired_browserviews(safe_uid, safe_cid)
    except Exception:
        pass

    browserviews_dir = os.path.realpath(
        get_echo_session_path(safe_uid, safe_cid, BROWSER_VIEWS_DIR_NAME)
    )
    files_dir = os.path.realpath(get_echo_session_path(safe_uid, safe_cid, "files"))
    frames = []
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
                for base_dir in (browserviews_dir, files_dir):
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

                frames.append(
                    {
                        "file_id": file_id,
                        "filename": filename,
                        "session_id": meta.get("session_id", ""),
                        "url": meta.get("url", ""),
                        "action": meta.get("action", ""),
                        "timestamp": meta.get("timestamp", r[4]),
                        "status": res_status,
                        "retained": bool(res_status == "retained" or meta.get("retained")),
                        "image_url": (
                            f"/echo/v1/cognition/{safe_cid}"
                            f"/browser-frames/{file_id}/image"
                        ),
                    }
                )
    except Exception as e:
        log.warning(f"[Cognition] Erreur lecture browser-frames: {e}")

    return JSONResponse(content={"frames": frames})


@router.get("/{chat_id}/browser-frames/{file_id}/image")
async def get_browser_frame_image(
    chat_id: str,
    file_id: str,
    user=Depends(get_verified_user),
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

    target_path = None
    for base_dir in (browserviews_dir, files_dir):
        default_cand = os.path.realpath(
            os.path.join(base_dir, f"{safe_fid}_frame.jpg")
        )
        if (
            default_cand.startswith(base_dir + os.sep)
            and os.path.isfile(default_cand)
        ):
            target_path = default_cand
            break

    if not target_path and os.path.isfile(db_path):
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
                                target_path = cand
                                break
                        if target_path:
                            break
        except Exception as e:
            log.warning(f"[Cognition] Erreur lookup image frame {file_id}: {e}")

    if not target_path:
        raise HTTPException(status_code=404, detail="Capture introuvable.")

    return FileResponse(target_path, media_type="image/jpeg")
