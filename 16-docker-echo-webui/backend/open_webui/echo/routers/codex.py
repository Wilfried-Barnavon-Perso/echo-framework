"""
title: ECHO V6 Codex Router
author: Wilfried BARNAVON
version: 6.12.0.1
description: Routeur FastAPI pour dépôts Codex et conteneurs de session (/echo/v1/codex).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.12.0.1: Factorisation vers echo_codex_git/echo_paths (suppression helpers/validations dupliqués), confinement symlinks _resolve_safe_files_path, support delete récursif, move_entry et sync SQLite atomique sous get_codex_lock.
# 6.6.13.1: Ajout de l'endpoint GET /{chat_id}/chats (get_codex_chats) exposant CodexRepo.get_user_codex_chats(..., include_files=True).
# 6.6.9.1: Déportation asyncio.to_thread sur les opérations FS et Git Dulwich synchrones (tree, file, log, diff, download, mkdir, rename).
# 6.6.6.1: Ajout de POST /{chat_id}/ai-edit (édition LLM one-shot), verrous get_codex_lock sur save/delete et synchro SQLite echo_resources.
# 6.6.4.1: Normalisation canonique GET /{chat_id}/tree (name, path, filename, type, mime) et POST /{chat_id}/upload.

from __future__ import annotations

import asyncio
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
    from echo_codex_git import (  # type: ignore
        CodexDirectoryNotEmptyError,
        CodexPathConflictError,
        CodexRepo,
        sync_codex_registry_delete,
        sync_codex_registry_relocate,
        sync_codex_registry_save,
    )
    from echo_constants import (  # type: ignore
        CODEX_QUICK_ACTIONS,
        ECHO_CODEX_EDIT_TIMEOUT,
        ECHO_CODEX_WORKSPACES,
        ECHO_MODELS_REGISTRY,
        get_generation_config,
        is_codex_workspace_generic_writable,
        is_codex_workspace_versioned,
    )
    from echo_gemini_client import EchoGeminiClient  # type: ignore
    from echo_paths import (  # type: ignore
        get_codex_lock,
        get_echo_global_path,
        get_echo_session_path,
        resolve_confined_codex_path,
        sanitize_echo_id,
    )
    from echo_prompts import SYS_CODEX_EDIT  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_codex_git import (  # type: ignore
        CodexDirectoryNotEmptyError,
        CodexPathConflictError,
        CodexRepo,
        sync_codex_registry_delete,
        sync_codex_registry_relocate,
        sync_codex_registry_save,
    )
    from echo_constants import (  # type: ignore
        CODEX_QUICK_ACTIONS,
        ECHO_CODEX_EDIT_TIMEOUT,
        ECHO_CODEX_WORKSPACES,
        ECHO_MODELS_REGISTRY,
        get_generation_config,
        is_codex_workspace_generic_writable,
        is_codex_workspace_versioned,
    )
    from echo_gemini_client import EchoGeminiClient  # type: ignore
    from echo_paths import (  # type: ignore
        get_codex_lock,
        get_echo_global_path,
        get_echo_session_path,
        resolve_confined_codex_path,
        sanitize_echo_id,
    )
    from echo_prompts import SYS_CODEX_EDIT  # type: ignore

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


class AiEditPayload(BaseModel):
    workspace: str = "main"
    path: str
    instruction: str = ""
    quick_action: Optional[str] = None
    content: str
    selection: Optional[str] = None
    language: Optional[str] = None
    model: str = "MODEL_APOLLO"


_ALLOWED_AI_EDIT_MODELS = ("MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA", "MODEL_ZEPHYR")


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


def _resolve_safe_files_path(
    safe_uid: str,
    files_dir: str,
    rel_path: str,
    allow_empty: bool = False,
) -> str:
    """Résout et confine un chemin dans 'files' (autorisant uniquement les symlinks Vault de l'utilisateur)."""
    glob_files = get_echo_global_path(safe_uid, "files")
    try:
        _, abs_target = resolve_confined_codex_path(
            files_dir,
            rel_path or "",
            allow_empty=allow_empty,
            allowed_symlink_roots=(files_dir, glob_files),
        )
        return abs_target
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _list_files_entries_sync(
    files_dir: str,
    target_dir: str,
    safe_uid: str = "",
) -> list[dict]:
    """Liste de manière synchrone les entrées du conteneur 'files' pour asyncio.to_thread."""
    entries: list[dict] = []
    if not os.path.isdir(target_dir):
        return entries

    raw_roots = [files_dir]
    if safe_uid:
        raw_roots.append(get_echo_global_path(safe_uid, "files"))
    allowed_roots = tuple(os.path.realpath(r) for r in raw_roots if r)

    def _is_in_roots(real_p: str) -> bool:
        for root_r in allowed_roots:
            try:
                if os.path.commonpath([root_r, real_p]) == root_r:
                    return True
            except ValueError:
                continue
        return False

    def _sort_key(e: os.DirEntry) -> tuple[bool, str]:
        try:
            return (not e.is_dir(), e.name.lower())
        except OSError:
            return (True, e.name.lower())

    for item in sorted(os.scandir(target_dir), key=_sort_key):
        if item.is_symlink() and not _is_in_roots(os.path.realpath(item.path)):
            continue
        try:
            is_dir = item.is_dir()
            st = item.stat()
        except OSError:
            continue
        rel_item_path = os.path.relpath(item.path, files_dir).replace("\\", "/")
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
    return entries


def _read_files_entry_sync(target_file: str, path: str) -> dict:
    """Lit un fichier du conteneur 'files' avec fallback aperçu hexadécimal."""
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
    return payload_res


def _list_repo_dir_sync(safe_uid: str, safe_cid: str, ws: str, path: str) -> list:
    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    return repo.list_directory(path)


def _read_repo_file_sync(
    safe_uid: str, safe_cid: str, ws: str, path: str, commit: Optional[str]
) -> Optional[dict]:
    repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
    if commit:
        return repo.read_file_at_commit(path, commit)
    return repo.read_file(path)


@router.get("/{chat_id}/chats")
async def get_codex_chats(
    chat_id: str,
    user=Depends(get_verified_user),
):
    """Retourne la conversation courante et les autres conversations de l'utilisateur disposant d'un Codex ou de fichiers."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    chats_data = await asyncio.to_thread(
        CodexRepo.get_user_codex_chats, safe_uid, safe_cid, True
    )
    return JSONResponse(content=chats_data)


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
        target_dir = _resolve_safe_files_path(safe_uid, files_dir, path, allow_empty=True)
        entries = await asyncio.to_thread(_list_files_entries_sync, files_dir, target_dir, safe_uid)
        return JSONResponse(content={
            "workspace": "files",
            "writable": False,
            "workspaces": ECHO_CODEX_WORKSPACES,
            "entries": entries,
            "path": path,
        })

    try:
        raw_entries = await asyncio.to_thread(_list_repo_dir_sync, safe_uid, safe_cid, ws, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
        target_file = _resolve_safe_files_path(safe_uid, files_dir, path, allow_empty=False)
        if not os.path.isfile(target_file):
            raise HTTPException(status_code=404, detail="Fichier introuvable.")
        payload_res = await asyncio.to_thread(_read_files_entry_sync, target_file, path)
        return JSONResponse(content=payload_res)

    if ws not in ECHO_CODEX_WORKSPACES:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

    try:
        res = await asyncio.to_thread(_read_repo_file_sync, safe_uid, safe_cid, ws, path, commit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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

    author = getattr(user, "name", None) or getattr(user, "email", None) or "ECHO Codex UI"

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            safe_rel = repo._secure_path(clean_path)
            msg = payload.message or f"Update {safe_rel} via Codex UI"
            commit_hash = await asyncio.to_thread(
                repo.commit_file, safe_rel, payload.content, msg, author=author
            )
        except CodexPathConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not commit_hash:
            raise HTTPException(status_code=500, detail="Échec de l'enregistrement ou du commit dans le dépôt Codex.")

        await sync_codex_registry_save(
            safe_uid,
            safe_cid,
            ws,
            safe_rel,
            commit_hash,
            msg,
            repo_base_dir=repo.repo_path,
        )

    return JSONResponse(content={
        "success": True,
        "commit": commit_hash,
        "path": safe_rel,
        "workspace": ws,
    })


@router.delete("/{chat_id}/file")
async def delete_codex_file(
    chat_id: str,
    workspace: str = Query(default="main"),
    path: str = Query(default=""),
    recursive: bool = Query(default=True),
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

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            safe_rel = repo._secure_path(clean_path)
            is_dir = os.path.isdir(os.path.join(repo.repo_path, *safe_rel.split("/")))
            res = await asyncio.to_thread(
                repo.delete_file, safe_rel, f"Delete {safe_rel} via Codex UI", recursive
            )
        except CodexDirectoryNotEmptyError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if res is None:
            raise HTTPException(status_code=404, detail="Fichier introuvable ou erreur de suppression dans le Codex.")

        await sync_codex_registry_delete(
            safe_uid,
            safe_cid,
            ws,
            res[1],
            dir_rel_path=safe_rel if is_dir else None,
        )

    return JSONResponse(content={
        "success": True,
        "commit": res[0],
        "deleted": res[1],
        "path": safe_rel,
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

    def _get_log_sync() -> list:
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        return repo.get_log(filename=path if path else None, limit=limit)

    logs = await asyncio.to_thread(_get_log_sync)
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

    if ws == "files":
        return JSONResponse(content={"diff": "", "workspace": "files"})

    if ws not in ECHO_CODEX_WORKSPACES:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

    def _get_diff_sync() -> str:
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        return repo.get_diff(commit_a, commit_b if commit_b else None)

    diff_text = await asyncio.to_thread(_get_diff_sync)
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
        target_path = _resolve_safe_files_path(safe_uid, files_dir, path, allow_empty=False)
        if not os.path.isfile(target_path):
            raise HTTPException(status_code=404, detail="Fichier introuvable sur le disque.")
    elif ws in ECHO_CODEX_WORKSPACES:
        def _resolve_repo_download_path() -> str:
            repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
            _, abs_target = resolve_confined_codex_path(
                repo.repo_path, path, workspace=ws, allow_empty=False
            )
            return abs_target

        try:
            target_path = await asyncio.to_thread(_resolve_repo_download_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if os.path.islink(target_path) or not os.path.isfile(target_path):
            raise HTTPException(status_code=404, detail="Fichier introuvable sur le disque.")
    else:
        raise HTTPException(status_code=400, detail=f"Espace de travail invalide : '{ws}'")

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
            safe_rel, created = await asyncio.to_thread(repo.create_directory, payload.path)
        except (CodexPathConflictError, FileExistsError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse(content={"status": "created", "workspace": ws, "path": safe_rel, "created": created})


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

    rename_msg = f"Renommage UI : {payload.old_path} -> {payload.new_path}"
    author = getattr(user, "name", None) or "Utilisateur"

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            move_res = await asyncio.to_thread(
                repo.move_entry,
                payload.old_path,
                payload.new_path,
                rename_msg,
                author,
                False,
            )
        except CodexPathConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if move_res is None:
            raise HTTPException(
                status_code=400,
                detail="Impossible de renommer (source introuvable ou cible déjà existante).",
            )

        await sync_codex_registry_relocate(
            safe_uid,
            safe_cid,
            ws,
            move_res["moved_pairs"],
            move_res["commit_hash"],
            rename_msg,
            overwrite=False,
            repo_base_dir=repo.repo_path,
        )

    return JSONResponse(
        content={
            "status": "renamed",
            "workspace": ws,
            "old_path": move_res["old_path"],
            "new_path": move_res["new_path"],
            "commit_hash": move_res["commit_hash"],
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

    if not is_codex_workspace_versioned(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace '{ws}' n'est pas versionné par Git.",
        )

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            safe_rel = repo._secure_path(payload.path)
            restore_msg = f"Restauration UI de {safe_rel} au commit {payload.commit_hash[:12]}"
            hist = await asyncio.to_thread(repo.read_file_at_commit, safe_rel, payload.commit_hash)
            if hist is None:
                raise HTTPException(
                    status_code=404,
                    detail="Fichier ou commit introuvable pour restauration.",
                )
            commit_hash = await asyncio.to_thread(
                repo.commit_file,
                safe_rel,
                hist.get("content", ""),
                message=restore_msg,
                author=getattr(user, "name", None) or "Utilisateur",
                is_binary=hist.get("is_binary"),
            )
        except CodexPathConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        await sync_codex_registry_save(
            safe_uid,
            safe_cid,
            ws,
            safe_rel,
            commit_hash or "",
            restore_msg,
            repo_base_dir=repo.repo_path,
        )

    return JSONResponse(
        content={
            "status": "restored",
            "workspace": ws,
            "path": safe_rel,
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
            safe_rel = repo._secure_path(normalized_rel_path)
            upload_msg = f"Import UI : {safe_rel}"
            commit_hash = await asyncio.to_thread(
                repo.commit_file,
                safe_rel,
                raw_bytes,
                upload_msg,
                author=author,
            )
        except CodexPathConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        await sync_codex_registry_save(
            safe_uid,
            safe_cid,
            ws,
            safe_rel,
            commit_hash or "",
            upload_msg,
            repo_base_dir=repo.repo_path,
        )

    return JSONResponse(
        content={
            "status": "uploaded",
            "workspace": ws,
            "path": safe_rel,
            "size_bytes": len(raw_bytes),
            "commit_hash": commit_hash,
        }
    )


@router.post("/{chat_id}/ai-edit")
async def ai_edit_codex_file(
    chat_id: str,
    payload: AiEditPayload,
    user=Depends(get_verified_user),
):
    """Exécute une édition LLM one-shot sur un fichier Codex (sans commit automatique)."""
    safe_uid = sanitize_echo_id(user.id)
    safe_cid = sanitize_echo_id(chat_id)
    ws = (payload.workspace or "main").strip()

    if ws not in ECHO_CODEX_WORKSPACES or not is_codex_workspace_generic_writable(ws):
        raise HTTPException(
            status_code=403,
            detail=f"L'espace '{ws}' est en lecture seule.",
        )

    clean_path = payload.path.strip().lstrip("/\\")
    parts = [p for p in clean_path.replace("\\", "/").split("/") if p]
    if not clean_path or not parts or any(p in (".", "..") for p in parts):
        raise HTTPException(status_code=400, detail="Chemin de fichier invalide.")

    raw_instruction = (payload.instruction or "").strip()
    if payload.quick_action:
        qa_key = payload.quick_action.strip()
        if qa_key not in CODEX_QUICK_ACTIONS:
            raise HTTPException(status_code=400, detail=f"Action rapide Codex inconnue : '{qa_key}'")
        qa_text = CODEX_QUICK_ACTIONS[qa_key]
        effective_instruction = f"{qa_text}\n\n{raw_instruction}" if raw_instruction else qa_text
    else:
        effective_instruction = raw_instruction

    if not effective_instruction:
        raise HTTPException(status_code=400, detail="Une instruction ou une action rapide est obligatoire.")

    target_model = (payload.model or "MODEL_APOLLO").strip() or "MODEL_APOLLO"
    if target_model not in _ALLOWED_AI_EDIT_MODELS or target_model not in ECHO_MODELS_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Modèle Codex AI non autorisé : '{target_model}'")

    lang = (payload.language or "").strip() or CodexRepo.detect_language(clean_path)

    user_prompt = f"## Document complet\n```{lang}\n{payload.content}\n```\n"
    if payload.selection and payload.selection.strip():
        user_prompt += f"\n## Sélection ciblée\n```{lang}\n{payload.selection}\n```\n"
    user_prompt += f"\n## Instruction\n{effective_instruction}"

    payload_llm = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": get_generation_config(target_model, override_thinking="medium"),
        "systemInstruction": {
            "parts": [{"text": SYS_CODEX_EDIT.format(filename=clean_path, language=lang)}]
        },
    }

    data, model_key, _reason = await EchoGeminiClient.call_cascade(
        target_model_key=target_model,
        payload=payload_llm,
        user_id=safe_uid,
        metadata=None,
        events=None,
        timeout=ECHO_CODEX_EDIT_TIMEOUT,
        chat_id=safe_cid,
        include_thoughts=False,
    )

    if not data:
        raise HTTPException(status_code=502, detail="L'éditeur Codex AI n'a produit aucun résultat.")

    candidates = data.get("candidates", [])
    if not candidates or not candidates[0].get("content"):
        raise HTTPException(status_code=502, detail="Réponse LLM invalide pour l'édition Codex AI.")

    parts_llm = candidates[0]["content"].get("parts", [])
    text_parts = [p.get("text", "") for p in parts_llm if not p.get("thought")]
    modified_content = "".join(text_parts).strip()

    if modified_content.startswith("```"):
        lines = modified_content.split("\n")
        if len(lines) >= 3 and lines[-1].strip() == "```":
            modified_content = "\n".join(lines[1:-1])

    if not modified_content:
        raise HTTPException(status_code=502, detail="L'éditeur Codex AI a retourné un contenu vide.")

    return JSONResponse(
        content={
            "status": "ok",
            "modified_content": modified_content,
            "model_used": model_key or target_model,
            "path": clean_path,
            "workspace": ws,
        }
    )
