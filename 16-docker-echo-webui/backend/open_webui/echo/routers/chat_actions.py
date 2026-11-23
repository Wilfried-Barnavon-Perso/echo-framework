# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO V6 Chat Actions Router
author: Wilfried BARNAVON
version: 6.18.23.1
description: Routeur FastAPI pour les actions souveraines sur les conversations (/echo/v1/chats).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.23.1: Création du routeur /echo/v1/chats exposant POST /{chat_id}/resume.

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from open_webui.echo.http_utils import get_echo_verified_user
from open_webui.echo.runtime import ensure_echo_sys_path

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
