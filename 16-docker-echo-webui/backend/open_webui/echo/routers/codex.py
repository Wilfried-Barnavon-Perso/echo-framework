"""
title: ECHO V6 Codex Router
author: Wilfried BARNAVON
version: 6.3.0.1
description: Routeur FastAPI pour dépôts Codex et conteneurs de session (/echo/v1/codex).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.3.0.1: Création du routeur /echo/v1/codex (tree, file, log, diff, download et persistance Git Dulwich).

from __future__ import annotations

import logging
import mimetypes
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.utils.auth import get_verified_user

ensure_echo_sys_path()
try:
    from echo_codex_git import CodexRepo  # type: ignore
    from echo_constants import (  # type: ignore
        ECHO_CODEX_WORKSPACES,
        is_codex_workspace_generic_writable,
    )
    from echo_paths import get_echo_session_path, sanitize_echo_id  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_codex_git import CodexRepo  # type: ignore
    from echo_constants import (  # type: ignore
        ECHO_CODEX_WORKSPACES,
        is_codex_workspace_generic_writable,
    )
    from echo_paths import get_echo_session_path, sanitize_echo_id  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


class FileSavePayload(BaseModel):
    path: str
    content: str
    workspace: str = "main"
    message: Optional[str] = None


def _validate_safe_path(base_dir: str, rel_path: str) -> str:
    """Valide et retourne le chemin absolu en prévenant toute tentative de traversée de répertoire."""
    abs_base = os.path.abspath(base_dir)
    target = os.path.abspath(os.path.join(abs_base, rel_path.lstrip("/\\")))
    if not (target == abs_base or target.startswith(abs_base + os.sep)):
        raise HTTPException(status_code=400, detail="Tentative de traversée de chemin non autorisée.")
    return target


@router.get("/{chat_id}/tree")
async def get_codex_tree(
    chat_id: str,
    workspace: str = Query(default="main"),
    path: str = Query(default=""),
    user=Depends(get_verified_user),
):
    """Retourne l'arborescence du répertoire pour l'espace de travail ou le conteneur de fichiers."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (workspace or "main").strip()

    if ws != "files" and ws not in ECHO_CODEX_WORKSPACES:
        raise HTTPException(status_code=400, detail=f"Espace de travail Codex invalide : '{ws}'")

    if ws == "files":
        files_dir = get_echo_session_path(safe_uid, safe_cid, "files")
        target_dir = _validate_safe_path(files_dir, path) if path else files_dir
        entries = []
        if os.path.isdir(target_dir):
            for item in sorted(os.scandir(target_dir), key=lambda e: (not e.is_dir(), e.name.lower())):
                rel_item_path = os.path.relpath(item.path, files_dir).replace("\\", "/")
                is_dir = item.is_dir()
                st = item.stat()
                mime, _ = mimetypes.guess_type(item.name)
                entries.append({
                    "name": item.name,
                    "path": rel_item_path,
                    "type": "directory" if is_dir else "file",
                    "size": st.st_size if not is_dir else 0,
                    "mime": mime or "application/octet-stream",
                    "updated_at": int(st.st_mtime),
                })
        return JSONResponse(content={
            "workspace": "files",
            "writable": False,
            "workspaces": ECHO_CODEX_WORKSPACES,
            "entries": entries,
            "path": path,
        })

    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    entries = repo.list_directory(path)
    writable = is_codex_workspace_generic_writable(ws)
    return JSONResponse(content={
        "workspace": ws,
        "writable": writable,
        "workspaces": ECHO_CODEX_WORKSPACES,
        "entries": entries,
        "path": path,
    })


@router.get("/{chat_id}/file")
async def get_codex_file(
    chat_id: str,
    workspace: str = Query(default="main"),
    path: str = Query(default=""),
    commit: Optional[str] = Query(default=None),
    user=Depends(get_verified_user),
):
    """Lit un fichier du dépôt Codex ou du conteneur de fichiers avec métadonnées."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (workspace or "main").strip()

    if not path:
        raise HTTPException(status_code=400, detail="Le paramètre 'path' est obligatoire.")

    if ws == "files":
        files_dir = get_echo_session_path(safe_uid, safe_cid, "files")
        target_file = _validate_safe_path(files_dir, path)
        if not os.path.isfile(target_file):
            raise HTTPException(status_code=404, detail="Fichier introuvable.")
        st = os.stat(target_file)
        mime, _ = mimetypes.guess_type(target_file)
        is_binary = False
        content = ""
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            is_binary = True

        return JSONResponse(content={
            "path": path,
            "content": content if not is_binary else None,
            "is_binary": is_binary,
            "mime": mime or "application/octet-stream",
            "size_bytes": st.st_size,
            "total_lines": len(content.splitlines()) if not is_binary else 0,
        })

    if ws not in ECHO_CODEX_WORKSPACES:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    if commit:
        res = repo.read_file_at_commit(path, commit)
    else:
        res = repo.read_file(path)

    if res is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable dans le dépôt Codex.")

    return JSONResponse(content=res)


@router.put("/{chat_id}/file")
async def save_codex_file(
    chat_id: str,
    payload: FileSavePayload,
    user=Depends(get_verified_user),
):
    """Crée ou met à jour un fichier dans un workspace modifiable (main, sandbox) avec commit Git Dulwich."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (payload.workspace or "main").strip()

    if not is_codex_workspace_generic_writable(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace de travail '{ws}' est en lecture seule ou réservé aux générateurs spécialisés.",
        )

    clean_path = payload.path.strip().lstrip("/\\")
    if not clean_path:
        raise HTTPException(status_code=400, detail="Chemin de fichier invalide.")

    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    author = getattr(user, "name", None) or getattr(user, "email", None) or "ECHO Codex UI"
    msg = payload.message or f"Update {clean_path} via Codex UI"

    commit_hash = repo.commit_file(clean_path, payload.content, msg, author=author)
    if not commit_hash:
        raise HTTPException(status_code=500, detail="Échec de l'enregistrement ou du commit dans le dépôt Codex.")

    return JSONResponse(content={
        "success": True,
        "commit": commit_hash,
        "path": clean_path,
        "workspace": ws,
    })


@router.delete("/{chat_id}/file")
async def delete_codex_file(
    chat_id: str,
    workspace: str = Query(default="main"),
    path: str = Query(default=""),
    user=Depends(get_verified_user),
):
    """Supprime un fichier dans un workspace modifiable (main, sandbox) avec commit Git Dulwich."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (workspace or "main").strip()

    if not is_codex_workspace_generic_writable(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace de travail '{ws}' est en lecture seule.",
        )

    clean_path = path.strip().lstrip("/\\")
    if not clean_path:
        raise HTTPException(status_code=400, detail="Chemin de fichier invalide.")

    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    res = repo.delete_file(clean_path, f"Delete {clean_path} via Codex UI")
    if res is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable ou erreur de suppression dans le Codex.")

    return JSONResponse(content={
        "success": True,
        "commit": res[0],
        "deleted": res[1],
        "path": clean_path,
        "workspace": ws,
    })


@router.get("/{chat_id}/log")
async def get_codex_log(
    chat_id: str,
    workspace: str = Query(default="main"),
    path: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=100),
    user=Depends(get_verified_user),
):
    """Retourne l'historique des commits Dulwich pour l'espace de travail ou un fichier spécifique."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (workspace or "main").strip()

    if ws == "files":
        return JSONResponse(content={"workspace": "files", "logs": []})

    if ws not in ECHO_CODEX_WORKSPACES:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    logs = repo.get_log(filename=path if path else None, limit=limit)
    return JSONResponse(content={
        "workspace": ws,
        "path": path,
        "logs": logs,
    })


@router.get("/{chat_id}/diff")
async def get_codex_diff(
    chat_id: str,
    commit_a: str = Query(...),
    commit_b: Optional[str] = Query(default=None),
    workspace: str = Query(default="main"),
    user=Depends(get_verified_user),
):
    """Retourne le diff textuel unifié entre deux commits Dulwich."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (workspace or "main").strip()

    if ws not in ECHO_CODEX_WORKSPACES:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    diff_text = repo.get_diff(commit_a, commit_b if commit_b else None)
    return JSONResponse(content={"diff": diff_text, "workspace": ws})


@router.get("/{chat_id}/download")
async def download_codex_file(
    chat_id: str,
    workspace: str = Query(default="main"),
    path: str = Query(default=""),
    user=Depends(get_verified_user),
):
    """Télécharge un fichier Codex ou de session avec protection anti-traversal."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (workspace or "main").strip()

    if not path:
        raise HTTPException(status_code=400, detail="Le paramètre 'path' est obligatoire.")

    if ws == "files":
        files_dir = get_echo_session_path(safe_uid, safe_cid, "files")
        target_path = _validate_safe_path(files_dir, path)
    elif ws in ECHO_CODEX_WORKSPACES:
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            safe_rel = repo._secure_path(path)
        except ValueError:
            raise HTTPException(status_code=400, detail="Tentative de traversée de chemin non autorisée.")
        target_path = _validate_safe_path(repo.repo_path, safe_rel)
    else:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

    if not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le disque.")

    return FileResponse(
        path=target_path,
        filename=os.path.basename(target_path),
        media_type="application/octet-stream",
    )
