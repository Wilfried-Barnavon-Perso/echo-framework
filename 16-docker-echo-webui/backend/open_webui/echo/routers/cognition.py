"""
title: ECHO V6 Cognition Router
author: Wilfried BARNAVON
version: 6.5.3.1
description: Routeur FastAPI pour la visualisation cognitive et des sous-agents (/echo/v1/cognition).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.5.3.1: Ajout de POST /{chat_id}/threads/{sub_sid}/message (tours humains directs ou file FIFO) et enrichissement d'état des threads.
# 6.3.0.1: Création du routeur /echo/v1/cognition (snapshot et streaming SSE des cognitive_threads et call_bridge).

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Dict

import orjson as json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from open_webui.echo.runtime import (
    ensure_echo_sys_path,
    load_echo_tool_from_disk,
)
from open_webui.models.chats import Chats
from open_webui.utils.auth import get_verified_user

ensure_echo_sys_path()
try:
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


def _read_cognition_snapshot(db_path: str, chat_id: str) -> Dict[str, Any]:
    """Lit threads, métadonnées, file FIFO et call_bridge en lecture seule."""
    if not os.path.isfile(db_path):
        return {
            "chat_id": chat_id,
            "threads": {},
            "thread_meta": {},
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
        if is_subagent_running(chat_id, sid):
            entry = thread_meta.setdefault(sid, {"sub_sid": sid})
            entry["status"] = "running"

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
        "queued_messages": queued_messages,
        "tool_calls": tool_calls,
        "active_count": total_count,
        "running_count": running_count,
        "total_count": total_count,
        "updated_at": max_ts,
    }


def _get_cognition_state_sig(db_path: str) -> str:
    """Signature composite lecture seule (threads, call_bridge, subagent_*)."""
    if not os.path.isfile(db_path):
        return ""
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
                f"{t_row[0]}:{t_row[1]}|"
                f"{c_row[0]}:{c_row[1]}|"
                f"{s_row[0]}:{s_row[1]}:{s_row[2]}"
            )
    except Exception:
        return ""


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
        last_seen_sig = _get_cognition_state_sig(db_path)
        yield f"data: {json.dumps(current_snapshot).decode('utf-8')}\n\n"

        keepalive_counter = 0
        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(1.5)
            keepalive_counter += 1

            current_sig = _get_cognition_state_sig(db_path)
            if current_sig != last_seen_sig:
                current_snapshot = _read_cognition_snapshot(db_path, chat_id)
                last_seen_sig = current_sig
                out_sse = json.dumps(current_snapshot).decode("utf-8")
                yield f"data: {out_sse}\n\n"
            elif keepalive_counter >= 10:
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
