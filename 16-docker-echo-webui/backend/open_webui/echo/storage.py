"""
title: ECHO V6 Unified Vault Storage Provider
author: Wilfried BARNAVON
version: 6.7.9.1
description: Écriture directe des uploads dans l'arborescence ECHO (/users/{uid}/chats/{cid}/files
             ou /users/{uid}/files) et indexation immédiate dans echo_resources.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.7.9.1: Ajout de EchoStagedMultiPartParser / parse_staged_multipart (staging nommé tamponné 4 MiB) et adoption os.replace() O(1) avec fallback EXDEV et préservation ENAMETOOLONG dans upload_file_streamed.
# 6.7.8.1: Streaming par blocs de 4 MiB et calcul SHA-256 optionnel (compute_hash=False par défaut) dans upload_file_streamed.
# 6.5.3.1: Ajout de upload_file_streamed (streaming zéro-RAM par blocs de 1 MiB jusqu'à 4 Go avec hachage SHA-256 à la volée).
# 6.1.0.1: Création de EchoStorageProvider unifiant l'écriture dans les Vaults ECHO et echo_resources.

from __future__ import annotations

import asyncio
import codecs
import errno
import hashlib
import logging
import mimetypes
import os
import shutil
import uuid
from typing import TYPE_CHECKING, Any, BinaryIO, Dict, Tuple

import python_multipart as multipart
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import parse_options_header
from starlette.datastructures import FormData, Headers
from starlette.formparsers import MultiPartException, MultiPartParser, _user_safe_decode

try:
    from fastapi import UploadFile
except ImportError:
    from starlette.datastructures import UploadFile

from open_webui.config import UPLOAD_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.echo.identity_bridge import ensure_user_identity_db
from open_webui.echo.runtime import ensure_echo_sys_path

if TYPE_CHECKING:
    from python_multipart.multipart import MultipartCallbacks

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


def get_echo_staging_dir() -> str:
    """Retourne et crée le répertoire de staging multipart sur le volume persistant."""
    tmp_dir = os.environ.get("TMPDIR", "").strip()
    if tmp_dir:
        staging_dir = tmp_dir
    else:
        staging_dir = os.path.join(os.path.dirname(os.path.normpath(UPLOAD_DIR)), ".tmp_uploads")
    os.makedirs(staging_dir, exist_ok=True)
    return staging_dir


def _flush_and_rewind(fh: BinaryIO) -> None:
    """Force l'écriture disque et rembobine le descripteur de fichier."""
    fh.flush()
    fh.seek(0)


class EchoStagedMultiPartParser(MultiPartParser):
    """Parseur multipart streamé directement sur disque sans bufferisation RAM."""

    def __init__(
        self,
        headers: Headers,
        stream: Any,
        *,
        staging_dir: str,
        max_bytes: int | None = None,
        write_buffer_bytes: int = 4 * 1024 * 1024,
        **kwargs: Any,
    ) -> None:
        super().__init__(headers, stream, **kwargs)
        self.staging_dir = staging_dir
        self.max_bytes = max_bytes
        self.write_buffer_bytes = max(65536, int(write_buffer_bytes))
        self._staged_paths: list[str] = []
        self._part_buffers: dict[int, bytearray] = {}

    def on_headers_finished(self) -> None:
        _, options = parse_options_header(self._current_part.content_disposition)
        try:
            self._current_part.field_name = _user_safe_decode(options[b"name"], self._charset)
        except KeyError:
            raise MultiPartException('The Content-Disposition header field "name" must be provided.')
        if b"filename" in options:
            self._current_files += 1
            if self._current_files > self.max_files:
                raise MultiPartException(f"Too many files. Maximum number of files is {self.max_files}.")
            filename = _user_safe_decode(options[b"filename"], self._charset)
            stage_path = os.path.join(self.staging_dir, f"upload_{uuid.uuid4().hex}.part")
            fh = open(stage_path, "w+b", buffering=self.write_buffer_bytes)
            self._files_to_close_on_error.append(fh)  # type: ignore[arg-type]
            self._staged_paths.append(stage_path)
            upload = UploadFile(
                file=fh,  # type: ignore[arg-type]
                size=0,
                filename=filename,
                headers=Headers(raw=self._current_part.item_headers),
            )
            setattr(upload, "_echo_staged_path", stage_path)
            self._part_buffers[id(upload)] = bytearray()
            self._current_part.file = upload
        else:
            self._current_fields += 1
            if self._current_fields > self.max_fields:
                raise MultiPartException(f"Too many fields. Maximum number of fields is {self.max_fields}.")
            self._current_part.file = None

    async def parse(self) -> FormData:
        if "content-type" not in self.headers:
            raise MultiPartException("Missing Content-Type header.")

        _, params = parse_options_header(self.headers["Content-Type"])
        charset = params.get(b"charset", "utf-8")
        if isinstance(charset, bytes):
            charset = charset.decode("latin-1")
        try:
            self._charset = codecs.lookup(charset).name
        except LookupError:
            self._charset = "latin-1"
        try:
            boundary = params[b"boundary"]
        except KeyError:
            raise MultiPartException("Missing boundary in multipart.")

        callbacks: MultipartCallbacks = {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_end": self.on_end,
        }

        async def _drain_pending() -> None:
            for part, data in self._file_parts_to_write:
                uf = part.file
                assert uf is not None
                buf = self._part_buffers[id(uf)]
                buf.extend(data)
                uf.size = (uf.size or 0) + len(data)
                if self.max_bytes is not None and uf.size > self.max_bytes:
                    raise ValueError("FILE_TOO_LARGE")
                if len(buf) >= self.write_buffer_bytes:
                    payload = bytes(buf)
                    buf.clear()
                    await asyncio.to_thread(uf.file.write, payload)
            for part in self._file_parts_to_finish:
                uf = part.file
                assert uf is not None
                buf = self._part_buffers.pop(id(uf), None)
                if buf:
                    payload = bytes(buf)
                    buf.clear()
                    await asyncio.to_thread(uf.file.write, payload)
                await asyncio.to_thread(_flush_and_rewind, uf.file)
            self._file_parts_to_write.clear()
            self._file_parts_to_finish.clear()

        try:
            parser = multipart.MultipartParser(boundary, callbacks)
            async for chunk in self.stream:
                parser.write(chunk)
                await _drain_pending()
            parser.finalize()
            await _drain_pending()
        except BaseException as exc:
            for fh in self._files_to_close_on_error:
                try:
                    fh.close()
                except Exception:
                    pass
            for path in self._staged_paths:
                _unlink_silent(path)
            if isinstance(exc, FormParserError):
                raise MultiPartException("Invalid multipart data.") from exc
            raise

        return FormData(self.items)


async def parse_staged_multipart(
    request: Any,
    max_bytes: int | None = None,
    write_buffer_bytes: int = 4 * 1024 * 1024,
) -> tuple[FormData, list[str]]:
    """Parse une requête multipart en écrivant les fichiers par blocs vers le staging disque."""
    if not str(request.headers.get("content-type", "")).lower().startswith("multipart/"):
        raise MultiPartException("Invalid Content-Type for multipart upload.")
    parser = EchoStagedMultiPartParser(
        request.headers,
        request.stream(),
        staging_dir=get_echo_staging_dir(),
        max_bytes=max_bytes,
        write_buffer_bytes=write_buffer_bytes,
    )
    return (await parser.parse(), list(parser._staged_paths))


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
        staged_path: str | None = None,
    ) -> Tuple[int, str, str]:
        """Écrit le flux par blocs de 4 MiB (ou adopte un fichier stagé) avec SHA-256 optionnel et plafond."""
        if staged_path and os.path.isfile(staged_path):
            if file is not None and not getattr(file, "closed", True):
                file.flush()
                file.close()

            total_bytes = os.path.getsize(staged_path)
            if total_bytes == 0:
                _unlink_silent(staged_path)
                raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)
            if total_bytes > max_bytes:
                _unlink_silent(staged_path)
                raise ValueError("FILE_TOO_LARGE")

            if compute_hash:
                hasher = hashlib.sha256()
                with open(staged_path, "rb") as f_src:
                    while True:
                        chunk = f_src.read(chunk_size)
                        if not chunk:
                            break
                        hasher.update(chunk)
                digest = hasher.hexdigest()
            else:
                digest = ""

            ctx = _resolve_vault_context(filename, tags)
            primary_path = ctx["primary_path"]
            try:
                os.replace(staged_path, primary_path)
            except OSError as exc:
                if exc.errno == errno.ENAMETOOLONG:
                    raise
                if exc.errno == errno.EXDEV:
                    try:
                        with open(staged_path, "rb") as f_src, open(primary_path, "wb") as f_dst:
                            while True:
                                chunk = f_src.read(chunk_size)
                                if not chunk:
                                    break
                                f_dst.write(chunk)
                    except Exception:
                        _unlink_silent(primary_path)
                        raise
                    _unlink_silent(staged_path)
                else:
                    _unlink_silent(primary_path)
                    _unlink_silent(staged_path)
                    raise

            _finalize_vault_mirrors_and_index(ctx)
            return total_bytes, primary_path, digest

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
