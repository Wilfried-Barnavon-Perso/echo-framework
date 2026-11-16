"""
title: ECHO V6 Codex Router
author: Wilfried BARNAVON
version: 6.6.4.1
description: Routeur FastAPI pour dépôts Codex et conteneurs de session (/echo/v1/codex).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.4.1: Normalisation canonique GET /{chat_id}/tree (name, path, filename, type, mime) et POST /{chat_id}/upload.
# 6.6.2.1: Ajout de _format_hex_preview, mime_type, et des endpoints POST /{chat_id}/mkdir, /rename et /restore.
# 6.3.0.1: Création du routeur /echo/v1/codex (tree, file, log, diff, download et persistance Git Dulwich).

from __future__ import annotations

import logging
import mimetypes
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
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
    from echo_paths import get_codex_lock, get_echo_session_path, sanitize_echo_id  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_codex_git import CodexRepo  # type: ignore
    from echo_constants import (  # type: ignore
        ECHO_CODEX_WORKSPACES,
        is_codex_workspace_generic_writable,
    )
    from echo_paths import get_codex_lock, get_echo_session_path, sanitize_echo_id  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


class FileSavePayload(BaseModel):
    path: str
    content: str
    workspace: str = "main"
    message: Optional[str] = None


class MkdirPayload(BaseModel):
    workspace: str = "main"
    path: str


class RenamePayload(BaseModel):
    workspace: str = "main"
    old_path: str
    new_path: str


class RestorePayload(BaseModel):
    workspace: str = "main"
    path: str
    commit_hash: str


def _format_hex_preview(raw_bytes: bytes, limit: int = 4096) -> str:
    """Formate un aperçu hexadécimal (16 octets par ligne + colonne ASCII)."""
    chunk = raw_bytes[:limit]
    lines = []
    for offset in range(0, len(chunk), 16):
        row = chunk[offset:offset + 16]
        hex_left = " ".join(f"{b:02x}" for b in row[:8])
        hex_right = " ".join(f"{b:02x}" for b in row[8:])
        hex_part = f"{hex_left:<23}  {hex_right:<23}".rstrip()
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"{offset:08x}  {hex_part:<48}  |{ascii_part}|")
    if len(raw_bytes) > limit:
        lines.append(f"... ({len(raw_bytes) - limit} octets supplémentaires tronqués)")
    return "\n".join(lines)


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
                    "filename": rel_item_path,
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
    raw_entries = repo.list_directory(path)
    entries = []
    for item in raw_entries:
        if isinstance(item, dict):
            rel_p = str(
                item.get("path") or item.get("filename") or item.get("name") or ""
            ).replace("\\", "/").strip("/")
            base_name = item.get("name") or (rel_p.rsplit("/", 1)[-1] if rel_p else "")
            raw_type = str(item.get("type") or "file").lower()
            norm_type = "directory" if raw_type in ("directory", "dir", "folder") else "file"
            mime, _ = mimetypes.guess_type(base_name)
            entries.append({
                **item,
                "name": base_name,
                "path": rel_p,
                "filename": rel_p,
                "type": norm_type,
                "mime": item.get("mime") or (mime or "application/octet-stream"),
            })
        elif isinstance(item, str):
            rel_p = item.replace("\\", "/").strip("/")
            base_name = rel_p.rsplit("/", 1)[-1] if rel_p else rel_p
            mime, _ = mimetypes.guess_type(base_name)
            entries.append({
                "name": base_name,
                "path": rel_p,
                "filename": rel_p,
                "type": "file",
                "size": 0,
                "mime": mime or "application/octet-stream",
            })
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
        mime_type = mime or "application/octet-stream"
        is_binary = False
        content = ""
        hex_preview = None
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            is_binary = True
            with open(target_file, "rb") as f:
                raw_bytes = f.read(4096)
            hex_preview = _format_hex_preview(raw_bytes)

        payload_res = {
            "path": path,
            "content": content if not is_binary else None,
            "is_binary": is_binary,
            "mime": mime_type,
            "mime_type": mime_type,
            "size_bytes": st.st_size,
            "total_lines": len(content.splitlines()) if not is_binary else 0,
        }
        if hex_preview is not None:
            payload_res["hex_preview"] = hex_preview
        return JSONResponse(content=payload_res)

    if ws not in ECHO_CODEX_WORKSPACES:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    if commit:
        res = repo.read_file_at_commit(path, commit)
    else:
        res = repo.read_file(path)

    if res is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable dans le dépôt Codex.")

    mime_guessed, _ = mimetypes.guess_type(path)
    if res.get("is_binary"):
        raw_content = res.get("content") or ""
        if isinstance(raw_content, bytes):
            raw_bytes = raw_content[:4096]
        else:
            raw_bytes = str(raw_content).encode("latin-1", errors="replace")[:4096]
        res["hex_preview"] = _format_hex_preview(raw_bytes)
        res["content"] = None
        res["mime_type"] = mime_guessed or "application/octet-stream"
    else:
        res.setdefault("mime_type", mime_guessed or "text/plain")

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


@router.post("/{chat_id}/mkdir")
async def mkdir_codex(
    chat_id: str,
    payload: MkdirPayload,
    user=Depends(get_verified_user),
):
    """Crée un dossier dans un espace de travail Codex inscriptible."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (payload.workspace or "main").strip()

    if ws not in ECHO_CODEX_WORKSPACES or not is_codex_workspace_generic_writable(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace '{ws}' est en lecture seule.",
        )

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            repo.create_directory(payload.path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileExistsError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return JSONResponse(content={"status": "created", "workspace": ws, "path": payload.path})


@router.post("/{chat_id}/rename")
async def rename_codex(
    chat_id: str,
    payload: RenamePayload,
    user=Depends(get_verified_user),
):
    """Renomme ou déplace un fichier/dossier dans un espace Codex inscriptible."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (payload.workspace or "main").strip()

    if ws not in ECHO_CODEX_WORKSPACES or not is_codex_workspace_generic_writable(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace '{ws}' est en lecture seule.",
        )

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            commit_hash = repo.rename_file(
                payload.old_path,
                payload.new_path,
                message=f"Renommage UI : {payload.old_path} -> {payload.new_path}",
                author=getattr(user, "name", None) or "Utilisateur",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if commit_hash is None:
            raise HTTPException(
                status_code=400,
                detail="Impossible de renommer (source introuvable ou cible déjà existante).",
            )

    return JSONResponse(
        content={
            "status": "renamed",
            "workspace": ws,
            "old_path": payload.old_path,
            "new_path": payload.new_path,
            "commit_hash": commit_hash,
        }
    )


@router.post("/{chat_id}/restore")
async def restore_codex_file(
    chat_id: str,
    payload: RestorePayload,
    user=Depends(get_verified_user),
):
    """Restaure un fichier à un commit historique dans un espace Codex inscriptible."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (payload.workspace or "main").strip()

    if ws not in ECHO_CODEX_WORKSPACES or not is_codex_workspace_generic_writable(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace '{ws}' est en lecture seule.",
        )

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            hist = repo.read_file_at_commit(payload.path, payload.commit_hash)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if hist is None:
            raise HTTPException(
                status_code=404,
                detail="Fichier ou commit introuvable pour restauration.",
            )
        commit_hash = repo.commit_file(
            payload.path,
            hist.get("content", ""),
            message=f"Restauration UI de {payload.path} au commit {payload.commit_hash[:12]}",
            author=getattr(user, "name", None) or "Utilisateur",
            is_binary=hist.get("is_binary"),
        )

    return JSONResponse(
        content={
            "status": "restored",
            "workspace": ws,
            "path": payload.path,
            "restored_from": payload.commit_hash,
            "commit_hash": commit_hash,
        }
    )


@router.post("/{chat_id}/upload")
async def upload_codex_file(
    chat_id: str,
    file: UploadFile = File(...),
    workspace: str = Form(default="main"),
    path: str = Form(default=""),
    user=Depends(get_verified_user),
):
    """Importe un fichier (texte ou binaire) dans un espace Codex inscriptible avec commit Git Dulwich."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (workspace or "main").strip()

    if ws not in ECHO_CODEX_WORKSPACES or not is_codex_workspace_generic_writable(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace '{ws}' est en lecture seule.",
        )

    raw_filename = os.path.basename((file.filename or "").replace("\\", "/").strip())
    if not raw_filename or raw_filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")

    clean_dir = (path or "").replace("\\", "/").strip("/")
    target_rel_path = f"{clean_dir}/{raw_filename}" if clean_dir else raw_filename
    parts = [p for p in target_rel_path.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise HTTPException(status_code=400, detail="Chemin cible invalide.")
    normalized_rel_path = "/".join(parts)

    raw_bytes = await file.read()
    author = getattr(user, "name", None) or getattr(user, "email", None) or "Utilisateur"

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            commit_hash = repo.commit_file(
                normalized_rel_path,
                raw_bytes,
                f"Import UI : {normalized_rel_path}",
                author=author,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse(
        content={
            "status": "uploaded",
            "workspace": ws,
            "path": normalized_rel_path,
            "size_bytes": len(raw_bytes),
            "commit_hash": commit_hash,
        }
    )
