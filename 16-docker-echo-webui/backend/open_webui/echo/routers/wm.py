"""
title: ECHO V6 Window Manager Router
author: Wilfried BARNAVON
version: 6.3.0.1
description: Routeur souverain FastAPI pour le gestionnaire de fenêtres et événements temps réel (/echo/v1/wm).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.3.0.1: Création du routeur natif FastAPI /echo/v1/wm (SSE, snapshot enrichi context_stats, RPC).

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from open_webui.echo.runtime import ensure_echo_sys_path

ensure_echo_sys_path()
try:
    from echo_window_manager import get_window_manager, resolve_wm_user_id  # type: ignore
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_paths import get_echo_session_path, sanitize_echo_id  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_window_manager import get_window_manager, resolve_wm_user_id  # type: ignore
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_paths import get_echo_session_path, sanitize_echo_id  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stream")
async def wm_sse_stream(request: Request, client_id: str = ""):
    """Abonne le client au flux SSE temps réel du Window Manager."""
    user_id = resolve_wm_user_id(request)
    cid = client_id or uuid.uuid4().hex
    wm = get_window_manager()
    queue = wm.subscribe(user_id, cid)

    async def event_generator():
        try:
            init_payload = json.dumps({"type": "wm_connected", "client_id": cid, "user_id": user_id})
            yield f"data: {init_payload}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    payload = json.dumps(msg) if not isinstance(msg, str) else msg
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            wm.unsubscribe(user_id, cid)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/{chat_id}")
async def wm_get_chat_snapshot(chat_id: str, request: Request):
    """Retourne l'état complet du chat enrichi des métriques de contexte si session.db existe."""
    user_id = resolve_wm_user_id(request, chat_id=chat_id)
    wm = get_window_manager()
    snapshot = dict(wm.get_chat_snapshot(user_id, chat_id))

    # Enrichir avec context_stats de session.db uniquement si la base existe déjà sur disque
    safe_uid = sanitize_echo_id(user_id)
    safe_cid = sanitize_echo_id(chat_id)
    try:
        db_path = get_echo_session_path(safe_uid, safe_cid, "db")
        if os.path.isfile(db_path):
            sm = EchoStateManager(user_id=safe_uid, chat_id=safe_cid)
            stats = sm.get_last_context_stats()
            if stats:
                snapshot["context_stats"] = stats
    except Exception as e:
        log.debug("Erreur lecture context_stats pour snapshot WM: %s", e)

    return JSONResponse(content=snapshot)


@router.post("/rpc")
async def wm_post_rpc(request: Request):
    """Traite les retours RPC et synchronisations d'état du Window Manager."""
    try:
        payload = await request.json()
    except Exception:
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}

    chat_id = str(payload.get("chat_id") or "") if isinstance(payload, dict) else ""
    user_id = resolve_wm_user_id(request, chat_id=chat_id)
    wm = get_window_manager()
    res = await wm.handle_rpc(user_id, payload)
    return JSONResponse(content=res)
