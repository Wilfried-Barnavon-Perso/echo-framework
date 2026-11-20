"""
title: ECHO V6 Embeds Router
author: Wilfried BARNAVON
version: 6.14.2.1
description: Routeur FastAPI pour les documents HTML d'embeds et leurs assets binaires (/echo/v1/embeds).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.14.2.1: Création du routeur /echo/v1/embeds (service confiné des embeds HTML et assets binaires par session).

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from open_webui.echo.http_utils import get_echo_verified_user
from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.models.chats import Chats

ensure_echo_sys_path()
try:
    from echo_paths import get_echo_embeds_dir, sanitize_echo_id  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_paths import get_echo_embeds_dir, sanitize_echo_id  # type: ignore

router = APIRouter()

_EMBED_ID_RE = re.compile(r"^[a-f0-9]{16,64}$")
_ASSET_NAME_RE = re.compile(r"^[a-f0-9]{16,64}\.[a-z0-9]{2,5}$")


async def _resolve_authorized_embeds_dir(chat_id: str, user) -> str:
    """Vérifie l'accès en lecture au chat et retourne le dossier embeds du propriétaire."""
    safe_cid = sanitize_echo_id(chat_id, fallback="")
    if not safe_cid:
        raise HTTPException(status_code=400, detail="Identifiant de chat invalide.")
    chat = await Chats.get_accessible_chat_by_id(chat_id, user, permission="read")
    if not chat:
        raise HTTPException(status_code=403, detail="Accès refusé au chat.")
    return await asyncio.to_thread(get_echo_embeds_dir, chat.user_id, safe_cid)


def _resolve_confined_file(base_dir: str, filename: str) -> Optional[str]:
    """Résout un fichier strictement confiné dans base_dir en rejetant tout symlink."""
    if not filename or os.path.sep in filename or (os.path.altsep and os.path.altsep in filename):
        return None
    raw_target = os.path.join(base_dir, filename)
    if os.path.islink(base_dir) or os.path.islink(raw_target):
        return None
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(os.path.join(real_base, filename))
    try:
        if os.path.commonpath([real_base, real_target]) != real_base:
            return None
    except ValueError:
        return None
    if os.path.islink(real_target) or not os.path.isfile(real_target):
        return None
    return real_target


def _find_confined_embed_file_sync(
    primary_dir: str,
    user_id: str,
    chat_id: str,
    filename: str,
) -> Optional[str]:
    """Cherche un fichier confiné dans le dossier propriétaire, puis dans celui de l'appelant."""
    target = _resolve_confined_file(primary_dir, filename)
    if target is not None:
        return target
    safe_cid = sanitize_echo_id(chat_id, fallback="")
    if not safe_cid:
        return None
    user_dir = get_echo_embeds_dir(user_id, safe_cid)
    if os.path.realpath(user_dir) != os.path.realpath(primary_dir):
        return _resolve_confined_file(user_dir, filename)
    return None


@router.get("/{chat_id}/{embed_id}")
async def get_embed_html(
    chat_id: str,
    embed_id: str,
    user=Depends(get_echo_verified_user),
):
    """Retourne le document HTML d'un embed externalisé de la session."""
    if not _EMBED_ID_RE.fullmatch(embed_id or ""):
        raise HTTPException(status_code=400, detail="Identifiant d'embed invalide.")

    primary_dir = await _resolve_authorized_embeds_dir(chat_id, user)

    def _read_embed_sync() -> Optional[bytes]:
        target_path = _find_confined_embed_file_sync(
            primary_dir, user.id, chat_id, f"{embed_id}.html"
        )
        if not target_path:
            return None
        with open(target_path, "rb") as f:
            return f.read()

    html_bytes = await asyncio.to_thread(_read_embed_sync)
    if html_bytes is None:
        raise HTTPException(status_code=404, detail="Embed introuvable.")

    return Response(
        content=html_bytes,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/{chat_id}/assets/{asset_name}")
async def get_embed_asset(
    chat_id: str,
    asset_name: str,
    user=Depends(get_echo_verified_user),
):
    """Retourne un asset binaire associé aux embeds d'une session."""
    if not _ASSET_NAME_RE.fullmatch(asset_name or ""):
        raise HTTPException(status_code=400, detail="Nom d'asset invalide.")

    primary_dir = await _resolve_authorized_embeds_dir(chat_id, user)

    def _resolve_asset_sync() -> tuple[Optional[str], str]:
        target_path = _find_confined_embed_file_sync(
            primary_dir, user.id, chat_id, asset_name
        )
        if not target_path:
            return None, "application/octet-stream"

        mime = ""
        meta_path = f"{target_path}.meta.json"
        if os.path.isfile(meta_path) and not os.path.islink(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                if isinstance(meta, dict):
                    mime = str(meta.get("mime") or "").strip()
            except Exception:
                mime = ""

        if not mime:
            guessed, _ = mimetypes.guess_type(target_path)
            mime = guessed or "application/octet-stream"
        return target_path, mime

    target_path, mime = await asyncio.to_thread(_resolve_asset_sync)
    if not target_path:
        raise HTTPException(status_code=404, detail="Asset introuvable.")

    return FileResponse(
        path=target_path,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=86400"},
    )
