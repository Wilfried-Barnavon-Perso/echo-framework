"""
title: ECHO V6 Codex Router
author: Wilfried BARNAVON
version: 6.6.6.1
description: Routeur FastAPI pour dépôts Codex et conteneurs de session (/echo/v1/codex).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.6.1: Ajout de POST /{chat_id}/ai-edit (édition LLM one-shot), verrous get_codex_lock sur save/delete et synchro SQLite echo_resources.
# 6.6.4.1: Normalisation canonique GET /{chat_id}/tree (name, path, filename, type, mime) et POST /{chat_id}/upload.
# 6.6.2.1: Ajout de _format_hex_preview, mime_type, et des endpoints POST /{chat_id}/mkdir, /rename et /restore.
# 6.3.0.1: Création du routeur /echo/v1/codex (tree, file, log, diff, download et persistance Git Dulwich).

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
    from echo_codex_git import CodexRepo  # type: ignore
    from echo_constants import (  # type: ignore
        CODEX_DEFAULT_LANG,
        CODEX_LANG_MAP,
        CODEX_QUICK_ACTIONS,
        ECHO_CODEX_EDIT_TIMEOUT,
        ECHO_CODEX_WORKSPACES,
        ECHO_MODELS_REGISTRY,
        FILE_INGESTION_STATUS,
        get_codex_workspace_resource_type,
        get_generation_config,
        is_codex_workspace_generic_writable,
        is_codex_workspace_indexed,
    )
    from echo_gemini_client import EchoGeminiClient  # type: ignore
    from echo_paths import (  # type: ignore
        format_codex_storage_path,
        get_codex_lock,
        get_echo_session_path,
        sanitize_echo_id,
    )
    from echo_prompts import SYS_CODEX_EDIT  # type: ignore
    from echo_state_manager import EchoStateManager  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_codex_git import CodexRepo  # type: ignore
    from echo_constants import (  # type: ignore
        CODEX_DEFAULT_LANG,
        CODEX_LANG_MAP,
        CODEX_QUICK_ACTIONS,
        ECHO_CODEX_EDIT_TIMEOUT,
        ECHO_CODEX_WORKSPACES,
        ECHO_MODELS_REGISTRY,
        FILE_INGESTION_STATUS,
        get_codex_workspace_resource_type,
        get_generation_config,
        is_codex_workspace_generic_writable,
        is_codex_workspace_indexed,
    )
    from echo_gemini_client import EchoGeminiClient  # type: ignore
    from echo_paths import (  # type: ignore
        format_codex_storage_path,
        get_codex_lock,
        get_echo_session_path,
        sanitize_echo_id,
    )
    from echo_prompts import SYS_CODEX_EDIT  # type: ignore
    from echo_state_manager import EchoStateManager  # type: ignore

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


def _detect_codex_lang(filename: str) -> str:
    """Détecte le langage Monaco depuis le nom ou l'extension du fichier."""
    basename = os.path.basename((filename or "").strip())
    if basename.lower() == "dockerfile":
        return "dockerfile"
    ext = os.path.splitext(basename)[1].lower()
    return CODEX_LANG_MAP.get(ext, CODEX_DEFAULT_LANG)


def _count_text_lines(content_or_bytes: object) -> int:
    """Calcule le nombre de lignes pour un contenu texte ou UTF-8 (0 si binaire)."""
    if isinstance(content_or_bytes, (bytes, bytearray)):
        try:
            text = content_or_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return 0
    else:
        text = str(content_or_bytes or "")
    return len(text.splitlines())


async def _sync_codex_registry_save(
    safe_uid: str,
    safe_cid: str,
    ws: str,
    rel_path: str,
    content_or_bytes: object,
    commit_hash: str,
    msg: str,
) -> None:
    """Synchronise une ressource Codex dans SQLite echo_resources si l'espace est indexé."""
    if not is_codex_workspace_indexed(ws):
        return
    try:
        state = EchoStateManager(user_id=safe_uid, chat_id=safe_cid)
        res_type = get_codex_workspace_resource_type(ws) or "codex"
        await asyncio.to_thread(
            state.save_resource,
            id=rel_path,
            name=rel_path,
            resource_type=res_type,
            status=FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
            git_tracked=True,
            language=_detect_codex_lang(rel_path),
            lines=_count_text_lines(content_or_bytes),
            last_commit=(commit_hash or "")[:12],
            commit_msg=msg,
            storage_path=format_codex_storage_path(rel_path, ws),
        )
    except Exception as exc:
        log.warning("Codex registry save sync skipped for %s/%s: %s", ws, rel_path, exc)


async def _sync_codex_registry_delete(
    safe_uid: str,
    safe_cid: str,
    ws: str,
    paths_to_rm: list[str],
) -> None:
    """Supprime les ressources Codex de SQLite echo_resources si l'espace est indexé."""
    if not is_codex_workspace_indexed(ws) or not paths_to_rm:
        return
    try:
        state = EchoStateManager(user_id=safe_uid, chat_id=safe_cid)
        for rel_path in paths_to_rm:
            await asyncio.to_thread(state.delete_codex_resource, ws, rel_path)
    except Exception as exc:
        log.warning("Codex registry delete sync skipped for %s: %s", ws, exc)


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

    author = getattr(user, "name", None) or getattr(user, "email", None) or "ECHO Codex UI"
    msg = payload.message or f"Update {clean_path} via Codex UI"

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        commit_hash = await asyncio.to_thread(
            repo.commit_file, clean_path, payload.content, msg, author=author
        )
    if not commit_hash:
        raise HTTPException(status_code=500, detail="Échec de l'enregistrement ou du commit dans le dépôt Codex.")

    await _sync_codex_registry_save(
        safe_uid, safe_cid, ws, clean_path, payload.content, commit_hash, msg
    )

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

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        res = await asyncio.to_thread(
            repo.delete_file, clean_path, f"Delete {clean_path} via Codex UI"
        )
    if res is None:
        raise HTTPException(status_code=404, detail="Fichier introuvable ou erreur de suppression dans le Codex.")

    await _sync_codex_registry_delete(safe_uid, safe_cid, ws, res[1])

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

    if ws == "files":
        return JSONResponse(content={"diff": "", "workspace": "files"})

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

    rename_msg = f"Renommage UI : {payload.old_path} -> {payload.new_path}"
    author = getattr(user, "name", None) or "Utilisateur"
    synced_entries: list[tuple[str, object]] = []
    deleted_old_paths: list[str] = []

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            commit_hash = await asyncio.to_thread(
                repo.rename_file,
                payload.old_path,
                payload.new_path,
                message=rename_msg,
                author=author,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if commit_hash is None:
            raise HTTPException(
                status_code=400,
                detail="Impossible de renommer (source introuvable ou cible déjà existante).",
            )

        if is_codex_workspace_indexed(ws):
            clean_old = repo._secure_path(payload.old_path)
            clean_new = repo._secure_path(payload.new_path)
            new_abs = _validate_safe_path(repo.repo_path, clean_new)
            if os.path.isdir(new_abs):
                for root, _, files_in_dir in os.walk(new_abs):
                    for fname in files_in_dir:
                        f_abs = os.path.join(root, fname)
                        sub_rel = os.path.relpath(f_abs, new_abs).replace("\\", "/")
                        old_rel = f"{clean_old}/{sub_rel}" if clean_old else sub_rel
                        new_rel = f"{clean_new}/{sub_rel}" if clean_new else sub_rel
                        deleted_old_paths.append(old_rel)
                        read_res = await asyncio.to_thread(repo.read_file, new_rel)
                        synced_entries.append((new_rel, read_res.get("content", "") if read_res else ""))
            else:
                deleted_old_paths.append(clean_old)
                read_res = await asyncio.to_thread(repo.read_file, clean_new)
                synced_entries.append((clean_new, read_res.get("content", "") if read_res else ""))

    if deleted_old_paths:
        await _sync_codex_registry_delete(safe_uid, safe_cid, ws, deleted_old_paths)
    for new_rel, content_val in synced_entries:
        await _sync_codex_registry_save(
            safe_uid, safe_cid, ws, new_rel, content_val, commit_hash, rename_msg
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

    restore_msg = f"Restauration UI de {payload.path} au commit {payload.commit_hash[:12]}"
    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            hist = await asyncio.to_thread(repo.read_file_at_commit, payload.path, payload.commit_hash)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if hist is None:
            raise HTTPException(
                status_code=404,
                detail="Fichier ou commit introuvable pour restauration.",
            )
        commit_hash = await asyncio.to_thread(
            repo.commit_file,
            payload.path,
            hist.get("content", ""),
            message=restore_msg,
            author=getattr(user, "name", None) or "Utilisateur",
            is_binary=hist.get("is_binary"),
        )

    await _sync_codex_registry_save(
        safe_uid, safe_cid, ws, payload.path, hist.get("content", ""), commit_hash or "", restore_msg
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
    upload_msg = f"Import UI : {normalized_rel_path}"

    async with get_codex_lock(safe_uid, safe_cid):
        repo = CodexRepo(user_id=safe_uid, chat_id=safe_cid, workspace=ws)
        try:
            commit_hash = await asyncio.to_thread(
                repo.commit_file,
                normalized_rel_path,
                raw_bytes,
                upload_msg,
                author=author,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    await _sync_codex_registry_save(
        safe_uid, safe_cid, ws, normalized_rel_path, raw_bytes, commit_hash or "", upload_msg
    )

    return JSONResponse(
        content={
            "status": "uploaded",
            "workspace": ws,
            "path": normalized_rel_path,
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

    lang = (payload.language or "").strip() or _detect_codex_lang(clean_path)

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
