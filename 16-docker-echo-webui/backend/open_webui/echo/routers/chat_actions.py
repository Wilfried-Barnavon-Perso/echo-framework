# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO V6 Chat Actions Router
author: Wilfried BARNAVON
version: 6.22.0.1
description: Routeur FastAPI pour les actions souveraines sur les conversations (/echo/v1/chats).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.22.0.1: Exécution to_thread sur window et requête O(k) directe ChatMessage sur messages/details.
# 6.21.12.1: Ajout des endpoints GET /{chat_id}/window et POST /{chat_id}/messages/details pour le fenêtrage UI.
# 6.18.23.1: Création du routeur /echo/v1/chats exposant POST /{chat_id}/resume.

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.echo.chat_windowing import (
    extract_full_message_details,
    extract_older_window_slice,
)
from open_webui.echo.http_utils import get_echo_verified_user
from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.internal.db import get_async_session
from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import Chats

ensure_echo_sys_path()
try:
    from echo_session_resume import ResumeSessionError, resume_chat_in_new_session  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_session_resume import ResumeSessionError, resume_chat_in_new_session  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{chat_id}/resume")
async def post_resume_chat(
    chat_id: str,
    user=Depends(get_echo_verified_user),
) -> dict[str, Any]:
    try:
        return await resume_chat_in_new_session(
            user_id=user.id,
            old_chat_id=chat_id,
            user_name=getattr(user, "name", "") or "",
            user_email=getattr(user, "email", "") or "",
        )
    except ResumeSessionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        log.error(f"[ECHO-CHAT-ACTIONS] Erreur inattendue resume ({chat_id}): {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erreur interne de migration : {exc}") from exc


class MessageDetailsRequest(BaseModel):
    message_ids: list[str]


@router.get("/{chat_id}/window")
async def get_chat_window_slice(
    chat_id: str,
    before_id: str | None = None,
    to_checkpoint_id: str | None = None,
    target_id: str | None = None,
    limit: int | None = None,
    user=Depends(get_echo_verified_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    chat = await Chats.get_accessible_chat_by_id(chat_id, user, db=db)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return await asyncio.to_thread(
        extract_older_window_slice,
        chat.chat or {},
        user_id=chat.user_id,
        chat_id=chat_id,
        before_id=before_id,
        to_checkpoint_id=to_checkpoint_id,
        target_id=target_id,
        limit=limit,
    )


@router.post("/{chat_id}/messages/details")
async def post_chat_message_details(
    chat_id: str,
    form_data: MessageDetailsRequest,
    user=Depends(get_echo_verified_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    if not await Chats.has_chat_access(chat_id, user, db=db):
        raise HTTPException(status_code=401, detail="Unauthorized")

    clean_ids = list(dict.fromkeys(str(mid) for mid in (form_data.message_ids or [])[:50] if mid))
    if not clean_ids:
        return {"messages": {}}

    composite_ids = [f"{chat_id}-{mid}" for mid in clean_ids]
    rows = (
        await db.execute(
            select(ChatMessage).where(
                ChatMessage.chat_id == chat_id,
                ChatMessage.id.in_(composite_ids),
            )
        )
    ).scalars().all()

    messages_out: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_dict = ChatMessages.row_to_message_dict(chat_id, row)
        mid = str(row_dict.get("id") or "")
        if mid:
            messages_out[mid] = {k: v for k, v in row_dict.items() if k != "_echoLazyDetails"}

    missing_ids = [mid for mid in clean_ids if mid not in messages_out]
    if missing_ids:
        chat = await Chats.get_chat_by_id(chat_id, db=db)
        if chat and chat.chat:
            fallback = await asyncio.to_thread(
                extract_full_message_details,
                chat.chat,
                missing_ids,
            )
            messages_out.update(fallback)

    return {"messages": messages_out}
