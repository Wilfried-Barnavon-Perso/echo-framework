"""
title: ECHO V6 HTTP & SSE Utilities
author: Wilfried BARNAVON
version: 6.6.9.1
description: Helpers centralisés pour l'authentification SSE/HTTP et les réponses StreamingResponse ECHO.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.9.1: Création du module centralisé d'authentification HTTP/SSE (get_echo_verified_user, get_echo_sse_user) et des réponses StreamingResponse SSE.

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import (
    VERIFIED_USER_ROLES,
    bearer_security,
    get_current_user,
    get_verified_user,
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
