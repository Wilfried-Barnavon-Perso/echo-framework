"""
title: ECHO V6 Cognition Router
author: Wilfried BARNAVON
version: 6.3.0.1
description: Routeur FastAPI pour la visualisation cognitive et des sous-agents (/echo/v1/cognition).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.3.0.1: Création du routeur /echo/v1/cognition (snapshot et streaming SSE des cognitive_threads et call_bridge).

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.utils.auth import get_verified_user

ensure_echo_sys_path()
try:
    from echo_db_schema import open_db  # type: ignore
    from echo_paths import get_echo_session_path, sanitize_echo_id  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_db_schema import open_db  # type: ignore
    from echo_paths import get_echo_session_path, sanitize_echo_id  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


def _read_cognition_snapshot(db_path: str, chat_id: str) -> Dict[str, Any]:
    """Lit cognitive_threads et call_bridge en mode lecture seule sans jamais altérer la base."""
    if not os.path.isfile(db_path):
        return {
            "chat_id": chat_id,
            "threads": {},
            "tool_calls": [],
            "active_count": 0,
            "updated_at": 0,
        }

    threads: Dict[str, list[Dict[str, Any]]] = {}
    tool_calls: list[Dict[str, Any]] = []
    max_ts = 0

    try:
        with closing(open_db(db_path, readonly=True, timeout=5.0)) as conn:
            # Lecture des étapes de sous-agents groupées par sous-session
            t_rows = conn.execute(
                "SELECT sub_sid, step_index, role_id, role, content_json, thought_signature, updated_at "
                "FROM cognitive_threads ORDER BY updated_at ASC, step_index ASC"
            ).fetchall()
            for r in t_rows:
                sub_sid = r[0]
                content = None
                if r[4]:
                    try:
                        content = json.loads(r[4])
                    except Exception:
                        content = r[4]
                item = {
                    "sub_sid": sub_sid,
                    "step_index": r[1],
                    "role_id": r[2],
                    "role": r[3],
                    "content": content,
                    "thought_signature": r[5],
                    "updated_at": r[6],
                }
                threads.setdefault(sub_sid, []).append(item)
                if r[6] and r[6] > max_ts:
                    max_ts = r[6]

            # Lecture du journal des appels d'outils
            c_rows = conn.execute(
                "SELECT call_id, signature, function_name, args_json, message_id, timestamp "
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

    return {
        "chat_id": chat_id,
        "threads": threads,
        "tool_calls": tool_calls,
        "active_count": len(threads),
        "updated_at": max_ts,
    }


def _get_cognition_max_ts(db_path: str) -> int:
    """Retourne l'horodatage maximum entre threads et call_bridge via index en lecture seule."""
    if not os.path.isfile(db_path):
        return 0
    try:
        with closing(open_db(db_path, readonly=True, timeout=2.0)) as conn:
            t_max = conn.execute("SELECT MAX(updated_at) FROM cognitive_threads").fetchone()[0] or 0
            c_max = conn.execute("SELECT MAX(timestamp) FROM call_bridge").fetchone()[0] or 0
            return max(int(t_max), int(c_max))
    except Exception:
        return 0


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
    """Diffuse les mises à jour cognitives en SSE en surveillant les évolutions d'horodatage."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    db_path = get_echo_session_path(safe_uid, safe_cid, "db")

    async def event_generator():
        # Envoi initial de l'état
        current_snapshot = _read_cognition_snapshot(db_path, chat_id)
        last_seen_ts = current_snapshot.get("updated_at", 0)
        yield f"data: {json.dumps(current_snapshot)}\n\n"

        keepalive_counter = 0
        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(1.5)
            keepalive_counter += 1

            current_max_ts = _get_cognition_max_ts(db_path)
            if current_max_ts > last_seen_ts:
                current_snapshot = _read_cognition_snapshot(db_path, chat_id)
                last_seen_ts = current_max_ts
                yield f"data: {json.dumps(current_snapshot)}\n\n"
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
