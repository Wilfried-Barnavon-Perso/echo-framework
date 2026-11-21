"""
title: ECHO V6 HTTP, SSE & WebSocket Utilities
author: Wilfried BARNAVON
version: 6.16.0.1
description: Helpers centralisés pour l'authentification HTTP/WebSocket/SSE et les flux temps réel ECHO.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.16.0.1: Ajout de authenticate_echo_websocket (authentification WebSocket First-Message sans JWT en URL, contrôle SSO X-Webui-User et liaison resolve_canonical_user).  # noqa: E501
# 6.6.9.1: Création du module centralisé d'authentification HTTP/SSE (get_echo_verified_user, get_echo_sse_user) et des réponses StreamingResponse SSE.  # noqa: E501

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import WEBUI_AUTH_TRUSTED_EMAIL_HEADER
from open_webui.utils.auth import (
    VERIFIED_USER_ROLES,
    bearer_security,
    get_current_user,
    get_verified_user,
    get_verified_user_by_token,
)

SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

get_echo_verified_user = get_verified_user


async def get_echo_sse_user(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    auth_token: HTTPAuthorizationCredentials | None = Depends(bearer_security),
    token: str | None = Query(default=None),
):
    """Résout et valide l'utilisateur courant pour un flux SSE (Bearer, cookie ou query ?token=)."""
    effective_auth_token = auth_token
    if effective_auth_token is None and token:
        clean_token = token.strip().strip('"')
        if clean_token:
            effective_auth_token = HTTPAuthorizationCredentials(
                scheme="Bearer",
                credentials=clean_token,
            )

    user = await get_current_user(request, response, background_tasks, effective_auth_token)
    if user.role not in VERIFIED_USER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user


def create_echo_sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    """Construit une StreamingResponse SSE normalisée avec en-têtes anti-buffering."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def authenticate_echo_websocket(
    websocket: WebSocket,
    timeout_sec: float = 10.0,
) -> tuple[Any, str] | None:
    """Authentifie une connexion WebSocket ECHO via handshake First-Message sans JWT en URL."""
    async def _reject(reason: str | None = None) -> None:
        with contextlib.suppress(Exception):
            if reason:
                await websocket.close(code=1008, reason=reason)
            else:
                await websocket.close(code=1008)

    await websocket.accept()
    try:
        raw_msg = await asyncio.wait_for(websocket.receive_text(), timeout=timeout_sec)
        auth_data = json.loads(raw_msg)
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError, RuntimeError, TypeError):
        await _reject()
        return None

    if not (isinstance(auth_data, dict) and auth_data.get("type") == "auth"):
        await _reject()
        return None

    raw_token = str(auth_data.get("token") or websocket.cookies.get("token") or "").strip().strip('"')
    client_id = str(auth_data.get("client_id") or "").strip()
    if not raw_token:
        await _reject()
        return None

    state = getattr(getattr(websocket, "app", None), "state", None)
    redis_client = getattr(state, "redis", None) if state is not None else None
    user = await get_verified_user_by_token(raw_token, redis_client)
    if user is None:
        await _reject()
        return None

    if WEBUI_AUTH_TRUSTED_EMAIL_HEADER:
        trusted_email = (websocket.headers.get(WEBUI_AUTH_TRUSTED_EMAIL_HEADER) or "").strip().lower()
        if trusted_email and str(getattr(user, "email", "")).strip().lower() != trusted_email:
            await _reject(reason="User mismatch")
            return None

    from open_webui.echo.identity_bridge import resolve_canonical_user

    await resolve_canonical_user(websocket, user)
    return (user, client_id)
