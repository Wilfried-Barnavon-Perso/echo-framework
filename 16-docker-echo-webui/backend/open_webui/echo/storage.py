"""
title: ECHO V6 Unified Vault Storage Provider
author: Wilfried BARNAVON
version: 6.7.8.1
description: Écriture directe des uploads dans l'arborescence ECHO (/users/{uid}/chats/{cid}/files
             ou /users/{uid}/files) et indexation immédiate dans echo_resources.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.7.8.1: Streaming par blocs de 4 MiB et calcul SHA-256 optionnel (compute_hash=False par défaut) dans upload_file_streamed.
# 6.5.3.1: Ajout de upload_file_streamed (streaming zéro-RAM par blocs de 1 MiB jusqu'à 4 Go avec hachage SHA-256 à la volée).
# 6.1.0.1: Création de EchoStorageProvider unifiant l'écriture dans les Vaults ECHO et echo_resources.

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import shutil
from typing import Any, BinaryIO, Dict, Tuple

from open_webui.config import UPLOAD_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.echo.identity_bridge import ensure_user_identity_db
from open_webui.echo.runtime import ensure_echo_sys_path

log = logging.getLogger(__name__)


def _link_or_copy(src_path: str, dst_path: str) -> None:
    """
    Crée un lien symbolique dst_path -> src_path, avec repli sur copie physique (ex: Windows sans privilège symlink).
    """
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        return
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(dst_path) or os.path.islink(dst_path):
        return
    try:
        os.symlink(src_path, dst_path)
    except OSError:
        shutil.copy2(src_path, dst_path)


def _infer_resource_type(mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith(("image/", "audio/", "video/")) or m == "application/pdf":
        return "media"
    return "binary"


def _unlink_silent(path: str) -> None:
    """Supprime un fichier ou lien symbolique sans propager d'erreur OS."""
    try:
        if path and (os.path.islink(path) or os.path.isfile(path)):
            os.unlink(path)
    except OSError:
        pass


def _resolve_vault_context(
    filename: str, tags: Dict[str, str]
) -> Dict[str, Any]:
    """Résout le contexte et les chemins Vault pour un upload ECHO."""
    ensure_echo_sys_path()
    from echo_paths import (
        get_echo_db_path,
        get_echo_global_path,
        get_echo_session_path,
        sanitize_echo_id,
    )

    safe_tags = tags or {}
    raw_uid = safe_tags.get("OpenWebUI-User-Id") or ""
    raw_cid = safe_tags.get("Echo-Chat-Id") or ""
    raw_mid = safe_tags.get("Echo-Message-Id") or None
    file_id = (
        safe_tags.get("OpenWebUI-File-Id") or filename.split("_", 1)[0]
    )
    orig_name = safe_tags.get("Echo-Original-Name") or (
        filename.split("_", 1)[1] if "_" in filename else filename
    )
    mime = (
        safe_tags.get("Echo-Mime-Type")
        or mimetypes.guess_type(orig_name)[0]
        or "application/octet-stream"
    )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    upload_mirror_path = os.path.join(UPLOAD_DIR, filename)

    if not raw_uid:
        return {
            "raw_uid": "",
            "safe_uid": "",
            "raw_cid": "",
            "safe_cid": "",
            "raw_mid": raw_mid,
            "file_id": file_id,
            "orig_name": orig_name,
            "mime": mime,
            "primary_path": upload_mirror_path,
            "global_file_path": None,
            "upload_mirror_path": upload_mirror_path,
            "session_db_path": None,
        }

    safe_uid = sanitize_echo_id(raw_uid)
    ensure_user_identity_db(
        safe_uid,
        email=safe_tags.get("OpenWebUI-User-Email", ""),
        name=safe_tags.get("OpenWebUI-User-Name", ""),
    )

    global_files_dir = get_echo_global_path(safe_uid, "files")
    os.makedirs(global_files_dir, exist_ok=True)
    global_file_path = os.path.join(global_files_dir, filename)

    if raw_cid:
        safe_cid = sanitize_echo_id(raw_cid)
        session_files_dir = get_echo_session_path(
            safe_uid, safe_cid, "files"
        )
        os.makedirs(session_files_dir, exist_ok=True)
        primary_path = os.path.join(session_files_dir, filename)
        session_db_path = get_echo_db_path(safe_uid, safe_cid)
    else:
        safe_cid = ""
        primary_path = global_file_path
        session_db_path = None

    return {
        "raw_uid": raw_uid,
        "safe_uid": safe_uid,
        "raw_cid": raw_cid,
        "safe_cid": safe_cid,
        "raw_mid": raw_mid,
        "file_id": file_id,
        "orig_name": orig_name,
        "mime": mime,
        "primary_path": primary_path,
        "global_file_path": global_file_path,
        "upload_mirror_path": upload_mirror_path,
        "session_db_path": session_db_path,
    }


def _finalize_vault_mirrors_and_index(ctx: Dict[str, Any]) -> None:
    """Crée les miroirs et indexe la ressource dans session/identity DB."""
    if not ctx.get("raw_uid"):
        return

    ensure_echo_sys_path()
    from echo_constants import FILE_INGESTION_STATUS
    from echo_db_schema import DB_KIND_SESSION, ensure_db
    from echo_state_manager import EchoStateManager

    pending_status = FILE_INGESTION_STATUS.get(
        "PENDING_INGESTION", "pending_ingestion"
    )
    res_type = _infer_resource_type(ctx["mime"])
    primary_path = ctx["primary_path"]

    if ctx.get("raw_cid"):
        _link_or_copy(primary_path, ctx["global_file_path"])
        _link_or_copy(primary_path, ctx["upload_mirror_path"])

        ensure_db(ctx["session_db_path"], DB_KIND_SESSION)
        sm_session = EchoStateManager(
            user_id=ctx["safe_uid"], chat_id=ctx["safe_cid"]
        )
        sm_session.save_resource(
            id=ctx["file_id"],
            name=ctx["orig_name"],
            resource_type=res_type,
            status=pending_status,
            mime=ctx["mime"],
            storage_path=primary_path,
            message_id=ctx["raw_mid"],
        )
    else:
        _link_or_copy(primary_path, ctx["upload_mirror_path"])

    # Indexation systématique dans le registre global (identity.db)
    try:
        sm_global = EchoStateManager(user_id=ctx["safe_uid"], chat_id=None)
        sm_global.save_resource(
            id=ctx["file_id"],
            name=ctx["orig_name"],
            resource_type=res_type,
            status=pending_status,
            mime=ctx["mime"],
            storage_path=primary_path,
            message_id=ctx["raw_mid"],
        )
    except Exception as exc:
        log.warning(
            "[ECHO-STORAGE] Indexation echo_resources global ignorée (%s): %s",
            ctx["file_id"],
            exc,
        )


class EchoStorageProvider:
    """
    Fournisseur de stockage souverain ECHO V6 :
    - Avec chat_id : écrit dans /app/backend/data/users/{uid}/chats/{cid}/files/{filename},
      lie dans /users/{uid}/files/{filename} et UPLOAD_DIR, et indexe dans session.db + identity.db (echo_resources).
    - Sans chat_id : écrit dans /app/backend/data/users/{uid}/files/{filename},
      lie dans UPLOAD_DIR, et indexe dans identity.db (echo_resources).
    """

    @staticmethod
    def upload_file_streamed(
        file: BinaryIO,
        filename: str,
        tags: Dict[str, str],
        max_bytes: int = 4096 * 1024 * 1024,
        chunk_size: int = 4 * 1024 * 1024,
        compute_hash: bool = False,
    ) -> Tuple[int, str, str]:
        """Écrit le flux par blocs de 4 MiB avec SHA-256 optionnel à la volée et plafond."""
        ctx = _resolve_vault_context(filename, tags)
        primary_path = ctx["primary_path"]
        hasher = hashlib.sha256() if compute_hash else None
        total_bytes = 0

        try:
            with open(primary_path, "wb") as f_out:
                while True:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break
                    if total_bytes + len(chunk) > max_bytes:
                        raise ValueError("FILE_TOO_LARGE")
                    f_out.write(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
                    total_bytes += len(chunk)
        except Exception:
            _unlink_silent(primary_path)
            raise

        if total_bytes == 0:
            _unlink_silent(primary_path)
            raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)

        _finalize_vault_mirrors_and_index(ctx)
        return total_bytes, primary_path, (hasher.hexdigest() if hasher is not None else "")

    @staticmethod
    def upload_file(
        file: BinaryIO, filename: str, tags: Dict[str, str]
    ) -> Tuple[bytes, str]:
        contents = file.read()
        if not contents:
            raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)

        ctx = _resolve_vault_context(filename, tags)
        primary_path = ctx["primary_path"]
        with open(primary_path, "wb") as f_out:
            f_out.write(contents)

        _finalize_vault_mirrors_and_index(ctx)
        return contents, primary_path

    @staticmethod
    def get_file(file_path: str) -> str:
        if file_path and os.path.isfile(file_path):
            return file_path
        basename = os.path.basename(file_path or "")
        candidate = os.path.join(UPLOAD_DIR, basename)
        if basename and os.path.isfile(candidate):
            return candidate
        return file_path

    @staticmethod
    def delete_file(file_path: str) -> None:
        if not file_path:
            return
        ensure_echo_sys_path()
        from echo_paths import ECHO_USERS_ROOT
        from echo_state_manager import EchoStateManager

        basename = os.path.basename(file_path)
        file_id = basename.split("_", 1)[0] if "_" in basename else basename

        # 1. Suppression du chemin fourni et du miroir UPLOAD_DIR
        candidates = {file_path, os.path.join(UPLOAD_DIR, basename)}
        norm_path = os.path.normpath(file_path)
        norm_users_root = os.path.normpath(ECHO_USERS_ROOT)

        detected_uid: str | None = None
        detected_cid: str | None = None
        if norm_path.startswith(norm_users_root):
            rel_parts = os.path.relpath(norm_path, norm_users_root).split(os.sep)
            if len(rel_parts) >= 2:
                detected_uid = rel_parts[0]
            if len(rel_parts) >= 4 and rel_parts[1] == "chats":
                detected_cid = rel_parts[2]

        if detected_uid:
            user_root = os.path.join(ECHO_USERS_ROOT, detected_uid)
            candidates.add(os.path.join(user_root, "files", basename))
            chats_dir = os.path.join(user_root, "chats")
            if os.path.isdir(chats_dir):
                for cid_entry in os.listdir(chats_dir):
                    candidates.add(os.path.join(chats_dir, cid_entry, "files", basename))

        for target in candidates:
            try:
                if os.path.islink(target) or os.path.isfile(target):
                    os.unlink(target)
            except OSError:
                pass

        # 2. Nettoyage dans echo_resources (session.db et identity.db)
        if detected_uid and file_id:
            try:
                EchoStateManager(user_id=detected_uid, chat_id=None).delete_resource(file_id)
            except Exception:
                pass
            if detected_cid:
                try:
                    EchoStateManager(user_id=detected_uid, chat_id=detected_cid).delete_resource(file_id)
                except Exception:
                    pass

    @staticmethod
    def delete_all_files() -> None:
        """Supprime tous les fichiers de transit du stockage local (UPLOAD_DIR)."""
        if os.path.exists(UPLOAD_DIR):
            for filename in os.listdir(UPLOAD_DIR):
                file_path = os.path.join(UPLOAD_DIR, filename)
                try:
                    if os.path.islink(file_path) or os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    log.error("[ECHO-STORAGE] Erreur suppression transit %s: %s", file_path, e)
