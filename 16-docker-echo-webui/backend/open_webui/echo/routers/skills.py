"""
title: ECHO V6 Native Skills Router
author: Wilfried BARNAVON
version: 6.2.0.1
description: Routeur souverain FastAPI pour la gestion CRUD des Skills ECHO (/echo/v1/skills et /api/v1/skills).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.2.0.1: Routeur souverain /echo/v1/skills interfacé avec echo_skills.py sous verrou get_user_skill_lock.

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.utils.auth import get_verified_user

ensure_echo_sys_path()
try:
    from echo_skills import (  # type: ignore
        get_all_skills,
        get_skill_content,
        save_skill,
        delete_skill,
        parse_skill_metadata,
        _resolve_safe_skill_dir,
    )
    from echo_paths import get_user_skill_lock  # type: ignore
except ImportError:
    # Fallback d'import pour l'exécution hors conteneur (ex: tests locaux Windows)
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_skills import (  # type: ignore
        get_all_skills,
        get_skill_content,
        save_skill,
        delete_skill,
        parse_skill_metadata,
        _resolve_safe_skill_dir,
    )
    from echo_paths import get_user_skill_lock  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()

_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FRONTMATTER_BODY_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?(.*)$", re.DOTALL)


class SkillPayload(BaseModel):
    id: Optional[str] = None
    name: str = ""
    description: str = ""
    instructions: str = ""


@router.get("/")
@router.get("/list")
async def list_skills(user: Any = Depends(get_verified_user)) -> list[dict[str, Any]]:
    """Liste tous les skills disponibles pour l'utilisateur vérifié."""
    raw_skills = await asyncio.to_thread(get_all_skills, user.id)
    return [
        {
            "id": s.get("id"),
            "name": s.get("name", s.get("id")),
            "description": s.get("description", ""),
            "user_id": user.id,
            "is_active": True,
            "write_access": True,
        }
        for s in raw_skills
    ]


@router.get("/{skill_id}")
async def get_skill(skill_id: str, user: Any = Depends(get_verified_user)) -> dict[str, Any]:
    """Récupère les détails et les instructions Markdown d'un skill spécifique."""
    if not _SKILL_ID_RE.match(skill_id):
        raise HTTPException(status_code=400, detail="Identifiant de skill invalide")

    safe_dir = _resolve_safe_skill_dir(user.id, skill_id)
    if not safe_dir:
        raise HTTPException(status_code=400, detail="Identifiant de skill invalide")

    raw_content = await asyncio.to_thread(get_skill_content, user.id, skill_id)
    if raw_content is None:
        raise HTTPException(status_code=404, detail="Skill introuvable")

    meta = parse_skill_metadata(raw_content)
    body_match = _FRONTMATTER_BODY_RE.match(raw_content)
    body = body_match.group(1).strip() if body_match else raw_content.strip()

    return {
        "id": skill_id,
        "name": meta.get("name", skill_id),
        "description": meta.get("description", ""),
        "instructions": body,
        "content": raw_content,
        "user_id": user.id,
        "is_active": True,
        "write_access": True,
    }


@router.post("/create")
async def create_skill(payload: SkillPayload, user: Any = Depends(get_verified_user)) -> dict[str, Any]:
    """Crée un nouveau skill pour l'utilisateur vérifié."""
    raw_id = (payload.id or payload.name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "_", raw_id).strip("_")
    if not slug or not _SKILL_ID_RE.match(slug):
        slug = f"skill_{int(asyncio.get_event_loop().time() * 1000)}"

    name = payload.name.strip() or slug
    desc = payload.description.strip()
    instructions = payload.instructions

    async with get_user_skill_lock(user.id):
        await asyncio.to_thread(save_skill, user.id, slug, name, desc, instructions)

    return {
        "id": slug,
        "name": name,
        "description": desc,
        "instructions": instructions,
        "user_id": user.id,
        "is_active": True,
        "write_access": True,
    }


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str,
    payload: SkillPayload,
    user: Any = Depends(get_verified_user),
) -> dict[str, Any]:
    """Met à jour un skill existant."""
    if not _SKILL_ID_RE.match(skill_id):
        raise HTTPException(status_code=400, detail="Identifiant de skill invalide")

    safe_dir = _resolve_safe_skill_dir(user.id, skill_id)
    if not safe_dir:
        raise HTTPException(status_code=400, detail="Identifiant de skill invalide")

    name = (payload.name or skill_id).strip()
    desc = payload.description.strip()
    instructions = payload.instructions

    async with get_user_skill_lock(user.id):
        await asyncio.to_thread(save_skill, user.id, skill_id, name, desc, instructions)

    return {
        "id": skill_id,
        "name": name,
        "description": desc,
        "instructions": instructions,
        "user_id": user.id,
        "is_active": True,
        "write_access": True,
    }


@router.delete("/{skill_id}")
async def remove_skill(skill_id: str, user: Any = Depends(get_verified_user)) -> dict[str, Any]:
    """Supprime définitivement un skill de l'utilisateur."""
    if not _SKILL_ID_RE.match(skill_id):
        raise HTTPException(status_code=400, detail="Identifiant de skill invalide")

    safe_dir = _resolve_safe_skill_dir(user.id, skill_id)
    if not safe_dir:
        raise HTTPException(status_code=400, detail="Identifiant de skill invalide")

    async with get_user_skill_lock(user.id):
        deleted = await asyncio.to_thread(delete_skill, user.id, skill_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Skill introuvable ou échec de suppression")

    return {"deleted": True, "id": skill_id}
