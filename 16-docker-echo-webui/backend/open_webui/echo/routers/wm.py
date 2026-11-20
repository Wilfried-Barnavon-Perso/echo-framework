"""
title: ECHO V6 Window Manager Router
author: Wilfried BARNAVON
version: 6.7.20.1
description: Routeur souverain FastAPI pour le gestionnaire de fenêtres et événements temps réel (/echo/v1/wm).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.7.20.1: Exposition de l'endpoint authentifié GET /echo/v1/wm/commands servant le catalogue déclaratif system-commands.xml.
# 6.6.9.1: Passage sur get_echo_sse_user/get_echo_verified_user et create_echo_sse_response depuis echo.http_utils.
# 6.6.8.1: Support du paramètre refresh sur /chat/{chat_id} via AuthService.refresh_quota_if_needed et préservation de context_stats enrichi par EchoWindowManager.
# 6.3.0.1: Création du routeur natif FastAPI /echo/v1/wm (SSE, snapshot enrichi context_stats, RPC).

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from open_webui.echo.http_utils import (
    create_echo_sse_response,
    get_echo_sse_user,
    get_echo_verified_user,
)
from open_webui.echo.runtime import ensure_echo_sys_path

ensure_echo_sys_path()
try:
    from echo_auth import AuthService  # type: ignore
    from echo_window_manager import get_window_manager  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_auth import AuthService  # type: ignore
    from echo_window_manager import get_window_manager  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stream")
async def wm_sse_stream(
    request: Request,
    client_id: str = "",
    user=Depends(get_echo_sse_user),
):
    """Abonne le client au flux SSE temps réel du Window Manager."""
    user_id = user.id
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

    return create_echo_sse_response(event_generator())


@router.get("/chat/{chat_id}")
async def wm_get_chat_snapshot(
    chat_id: str,
    refresh: bool = False,
    user=Depends(get_echo_verified_user),
):
    """Retourne l'état complet du chat enrichi des métriques de contexte et quotas."""
    user_id = user.id
    wm = get_window_manager()
    if refresh:
        try:
            uid = wm._resolve_owner_uid(user_id, chat_id)
            await AuthService(user_id=uid).refresh_quota_if_needed(force=True)
        except Exception as e:
            log.debug("Erreur refresh_quota_if_needed pour snapshot WM: %s", e)

    snapshot = await asyncio.to_thread(wm.get_chat_snapshot, user_id, chat_id)
    return JSONResponse(content=snapshot)


@router.post("/rpc")
async def wm_post_rpc(
    request: Request,
    user=Depends(get_echo_verified_user),
):
    """Traite les retours RPC et synchronisations d'état du Window Manager."""
    try:
        payload = await request.json()
    except Exception:
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}

    user_id = user.id
    wm = get_window_manager()
    res = await wm.handle_rpc(user_id, payload)
    return JSONResponse(content=res)


@router.get("/commands")
async def get_echo_authorized_commands(user=Depends(get_echo_verified_user)):
    """Expose le registre des commandes '/' ECHO depuis system-commands.xml."""
    from echo_core import load_echo_commands_registry
    ui_commands, _, _ = await asyncio.to_thread(load_echo_commands_registry)
    return JSONResponse(content={"commands": ui_commands})
