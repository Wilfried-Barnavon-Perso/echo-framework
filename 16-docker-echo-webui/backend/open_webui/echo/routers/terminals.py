# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO V6 Terminals Router
author: Wilfried BARNAVON
version: 6.25.3.1
description: Routeur FastAPI souverain pour la gestion des sessions terminales PTY et cibles SSH (/echo/v1/terminals).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.25.3.1: Propagation de l'erreur terminal_quota_exceeded en HTTP 429 Too Many Requests dans _raise_worker_http_error.
# 6.24.6.1: Création du routeur souverain /echo/v1/terminals (SSH targets, liste, ouverture, verrouillage agent_locked exclusif et fermeture).

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.echo.http_utils import get_echo_verified_user
from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.internal.db import get_async_session
from open_webui.models.chats import Chats

ensure_echo_sys_path()
try:
    from echo_terminals import (  # type: ignore
        list_user_ssh_vault_targets_sync,
        worker_close_terminal,
        worker_list_terminals,
        worker_open_terminal,
        worker_set_terminal_lock,
    )
    from echo_window_manager import get_window_manager  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_terminals import (  # type: ignore
        list_user_ssh_vault_targets_sync,
        worker_close_terminal,
        worker_list_terminals,
        worker_open_terminal,
        worker_set_terminal_lock,
    )
    from echo_window_manager import get_window_manager  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


class OpenTerminalRequest(BaseModel):
    mode: Literal["local", "ssh"] = "local"
    account_id: Optional[str] = None
    name: Optional[str] = None


class SetTerminalLockRequest(BaseModel):
    agent_locked: bool


async def _verify_chat_access(
    chat_id: str,
    user: Any,
    db: AsyncSession,
    write: bool = False,
) -> str:
    """Valide l'identifiant de conversation et vérifie les droits d'accès read/write de l'utilisateur."""
    clean_chat_id = (chat_id or "").strip()
    if not clean_chat_id or clean_chat_id.startswith("local:"):
        raise HTTPException(
            status_code=400,
            detail="Identifiant de conversation invalide ou temporaire.",
        )

    permission: Literal["read", "write"] = "write" if write else "read"
    has_access = await Chats.has_chat_access(
        clean_chat_id,
        user,
        db=db,
        permission=permission,
    )
    if not has_access:
        raise HTTPException(
            status_code=404,
            detail="Conversation introuvable ou accès refusé.",
        )
    return clean_chat_id


def _raise_worker_http_error(res: dict[str, Any], default_msg: str) -> None:
    """Convertit une réponse d'erreur de echo_terminals en HTTPException adaptée."""
    err_code = str(res.get("error_code") or "").strip()
    err_msg = str(res.get("error") or default_msg)
    if err_code == "not_found":
        raise HTTPException(status_code=404, detail=err_msg)
    if err_code == "terminal_quota_exceeded":
        raise HTTPException(status_code=429, detail=err_msg)
    status_code = 502 if "injoignable" in err_msg.lower() else 400
    raise HTTPException(status_code=status_code, detail=err_msg)


@router.get("/ssh-targets")
async def get_ssh_targets(
    user=Depends(get_echo_verified_user),
) -> dict[str, Any]:
    """Liste les cibles SSH configurées dans l'Identity Vault sans exposer aucun secret."""
    items = await asyncio.to_thread(list_user_ssh_vault_targets_sync, user.id)
    return {"items": items, "count": len(items)}


@router.get("/{chat_id}")
async def get_chat_terminals(
    chat_id: str,
    user=Depends(get_echo_verified_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Liste les sessions terminales actives pour la conversation donnée."""
    clean_chat_id = await _verify_chat_access(chat_id, user, db=db, write=False)
    res = await worker_list_terminals(user.id, clean_chat_id)
    if res.get("status") == "error":
        _raise_worker_http_error(res, "Erreur lors de la récupération des terminaux.")
    items = res.get("items") or []
    return {"items": items, "count": len(items)}


@router.post("/{chat_id}/open")
async def post_open_terminal(
    chat_id: str,
    payload: OpenTerminalRequest,
    user=Depends(get_echo_verified_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Ouvre une session terminale locale ou SSH et diffuse son état via Window Manager."""
    clean_chat_id = await _verify_chat_access(chat_id, user, db=db, write=True)
    res = await worker_open_terminal(
        user.id,
        clean_chat_id,
        mode=payload.mode,
        account_id=payload.account_id or "",
        name=payload.name or "",
    )
    if res.get("status") == "error":
        _raise_worker_http_error(res, "Erreur lors de l'ouverture du terminal.")

    session = res.get("session") or {}
    await get_window_manager().broadcast_terminal_state(
        user.id,
        clean_chat_id,
        session,
        "opened",
    )
    return session


@router.patch("/{chat_id}/{session_id}/lock")
async def patch_terminal_lock(
    chat_id: str,
    session_id: str,
    payload: SetTerminalLockRequest,
    user=Depends(get_echo_verified_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Seul point d'entrée autorisé pour modifier le verrou agent_locked d'un terminal."""
    clean_chat_id = await _verify_chat_access(chat_id, user, db=db, write=True)
    res = await worker_set_terminal_lock(
        user.id,
        clean_chat_id,
        session_id,
        payload.agent_locked,
    )
    if res.get("status") == "error":
        _raise_worker_http_error(res, "Erreur lors de la modification du verrou terminal.")

    session = res.get("session") or {}
    await get_window_manager().broadcast_terminal_state(
        user.id,
        clean_chat_id,
        session,
        "lock_changed",
    )
    return session


@router.delete("/{chat_id}/{session_id}")
async def delete_terminal_session(
    chat_id: str,
    session_id: str,
    user=Depends(get_echo_verified_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    """Ferme une session terminale à la demande de l'utilisateur et diffuse la clôture."""
    clean_chat_id = await _verify_chat_access(chat_id, user, db=db, write=True)
    res = await worker_close_terminal(
        user.id,
        clean_chat_id,
        session_id,
        actor="user",
    )
    if res.get("status") == "error":
        _raise_worker_http_error(res, "Erreur lors de la fermeture du terminal.")

    await get_window_manager().broadcast_terminal_state(
        user.id,
        clean_chat_id,
        {"id": session_id},
        "closed",
    )
    return {"status": "terminated", "id": session_id}
