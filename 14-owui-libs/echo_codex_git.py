# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Codex Git Engine
author: Wilfried BARNAVON
version: 6.18.1.1
description: Composant système interne : ECHO Codex Git Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.1.1: Enrichissement de CodexRepo.detect_language (noms spéciaux Dockerfile/Containerfile/Makefile/CMakeLists/Jenkinsfile/.env, extensions CODEX_LANG_MAP et heuristique contenu shebang/HTML/XML/PHP/JSON/YAML).
# 6.13.1.1: Support de la résolution et du transfert des clips vidéo browserviews (clip_filename dans metadata).
# 6.10.0.1: Durcissement confinement/symlinks/O_NOFOLLOW (_open_regular_file_ro, _ensure_parent_dirs_safe), create_directory avec retour (rel, created), delete_file récursif sans commit vide, move_entry, list_directory récursif/borné + list_all_files, transfer_codex_entries (matrice, quotas, rollback, move Option A) et sync_codex_registry_save/delete/relocate.
# 6.6.13.1: Ajout du paramètre include_files: bool = False à get_user_codex_chats pour détecter également les sessions possédant des fichiers dans files/.
# 5.235.4.1: Exclusion systématique des flux binaires dans search_in_file et trace_history pour éliminer les faux positifs et ralentissements.
# Couche pure, testable, sans dépendance OWUI/LLM/events.
# desc.

import asyncio
import json
import os
import re
import shutil
import stat
import tempfile
from typing import Any, List, Optional

from dulwich.repo import Repo
from dulwich.objects import Tree, Commit
from dulwich import porcelain

import echo_constants
from echo_constants import (
    BROWSER_VIEWS_DIR_NAME,
    CODEX_DEFAULT_LANG,
    CODEX_DEFAULT_WORKSPACE,
    CODEX_DIR_NAME,
    CODEX_FILES_DOMAIN_NAME,
    CODEX_LANG_MAP,
    CODEX_TRANSFER_CHUNK_BYTES,
    CODEX_TRANSFER_MAX_FILES_MAIN,
    CODEX_TRANSFER_MAX_FILE_BYTES_MAIN,
    CODEX_TRANSFER_MAX_TOTAL_BYTES_MAIN,
    CODEX_TRANSFER_MAX_FILES_SANDBOX,
    CODEX_TRANSFER_MAX_FILE_BYTES_SANDBOX,
    CODEX_TRANSFER_MAX_TOTAL_BYTES_SANDBOX,
    CODEX_TRANSFER_SOURCES,
    CODEX_WORKSPACE_MAIN,
    CODEX_WORKSPACE_SANDBOX,
    CODEX_WRITABLE_WORKSPACES,
    ECHO_BASE_DATA_DIR,
    ECHO_CODEX_WORKSPACES,
    ECHO_SYNC_EXCLUDE_LIST,
    ECHO_USERS_ROOT,
    FILE_INGESTION_STATUS,
    get_codex_workspace_resource_type,
    is_binary_file,
    is_codex_workspace_indexed,
    is_codex_workspace_versioned,
)
from echo_paths import (
    format_codex_storage_path,
    get_echo_global_path,
    get_echo_session_path,
    is_sandbox_internal_segment,
    normalize_codex_rel_path,
    resolve_confined_codex_path,
    resolve_upload_file_path,
)


class CodexDirectoryNotEmptyError(ValueError):
    """Erreur levée lors de la suppression non récursive d'un dossier."""

    def __init__(self, path: str, entries_count: int):
        self.path = path
        self.entries_count = entries_count
        super().__init__(
            f"Le dossier '{path}' n'est pas vide ({entries_count} entrée(s))."
        )


class CodexPathConflictError(FileExistsError):
    """Erreur levée en cas de conflit de type ou cible existante Codex."""
    pass


class CodexQuotaExceededError(ValueError):
    """Erreur levée lors du dépassement d'un quota de transfert Codex."""
    pass


def _open_regular_file_ro(
    filepath: str,
    follow_symlinks: bool = False,
) -> tuple[int, os.stat_result]:
    """Ouvre un fichier régulier en lecture seule (contrôles anti-TOCTOU)."""
    st = os.stat(filepath) if follow_symlinks else os.lstat(filepath)
    if not follow_symlinks and stat.S_ISLNK(st.st_mode):
        raise ValueError("Lien symbolique non autorisé.")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(
            "Type de fichier spécial non supporté "
            "(FIFO/socket/périphérique/dossier)."
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if not follow_symlinks:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(filepath, flags)
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise ValueError(
                "Type de fichier spécial non supporté "
                "(FIFO/socket/périphérique/dossier)."
            )
    except Exception:
        os.close(fd)
        raise
    return (fd, fst)


class CodexRepo:
    """Gestionnaire Git (dulwich) pour un dépôt Codex user/chat.
    Un dépôt isolé par couple (user_id, chat_id, workspace).
    Toutes les opérations sont synchrones et sans effet de bord réseau."""

    def __init__(
        self,
        user_id: str,
        chat_id: str,
        workspace: str = CODEX_DEFAULT_WORKSPACE,
    ):
        if workspace not in ECHO_CODEX_WORKSPACES:
            workspace = CODEX_DEFAULT_WORKSPACE
        self.user_id = user_id
        self.chat_id = chat_id
        base_codex = get_echo_session_path(user_id, chat_id, CODEX_DIR_NAME)
        self.workspace = workspace
        self.is_versioned = is_codex_workspace_versioned(workspace)
        self.repo_path = os.path.join(base_codex, workspace)
        os.makedirs(self.repo_path, exist_ok=True)
        self.repo = self._ensure_repo()

    def _ensure_repo(self) -> Optional[Repo]:
        """Initialise le dépôt uniquement si le workspace est versionné, sinon retourne None."""
        if not self.is_versioned:
            os.makedirs(self.repo_path, exist_ok=True)
            return None
        if os.path.exists(os.path.join(self.repo_path, ".git")):
            repo = Repo(self.repo_path)
        else:
            os.makedirs(self.repo_path, exist_ok=True)
            repo = Repo.init(self.repo_path)
        try:
            cfg = repo.get_config()
            cfg.set((b"core",), b"autocrlf", b"false")
            cfg.write_to_path()
        except Exception:
            pass
        return repo

    # =========================================================================
    # CRUD FICHIERS
    # =========================================================================

    def _secure_path(self, path: str, allow_empty: bool = False) -> str:
        """Sécurise et confine un chemin relatif dans le workspace Codex."""
        clean_rel, _ = resolve_confined_codex_path(
            self.repo_path,
            path,
            workspace=getattr(self, "workspace", "") or "",
            allow_empty=allow_empty,
        )
        return clean_rel

    def _ensure_parent_dirs_safe(self, target_abs: str) -> None:
        """Vérifie qu'aucun parent n'est un symlink/fichier puis crée."""
        repo_abs = os.path.abspath(self.repo_path)
        parent_abs = os.path.abspath(os.path.dirname(target_abs))
        if parent_abs != repo_abs:
            rel_parent = os.path.relpath(parent_abs, repo_abs)
            if rel_parent != ".":
                if rel_parent.startswith(".."):
                    raise ValueError("Sortie de confinement parent détectée.")
                cur = repo_abs
                for seg in rel_parent.split(os.sep):
                    if not seg or seg == ".":
                        continue
                    cur = os.path.join(cur, seg)
                    if os.path.lexists(cur) and (
                        os.path.islink(cur) or not os.path.isdir(cur)
                    ):
                        raise CodexPathConflictError(
                            f"Conflit sur le parent '{seg}' : "
                            "fichier ou lien symbolique existant."
                        )
        os.makedirs(parent_abs, exist_ok=True)

    def create_directory(self, path: str) -> tuple[str, bool]:
        """Crée un répertoire vide (non tracké). Retourne (rel, created)."""
        safe_name = self._secure_path(path, allow_empty=False)
        dirpath = os.path.join(self.repo_path, *safe_name.split("/"))
        if os.path.lexists(dirpath):
            if os.path.islink(dirpath) or not os.path.isdir(dirpath):
                raise CodexPathConflictError(
                    f"Impossible de créer '{safe_name}' : "
                    "un fichier ou lien symbolique porte déjà ce nom."
                )
            return (safe_name, False)
        self._ensure_parent_dirs_safe(os.path.join(dirpath, "__probe__"))
        return (safe_name, True)

    def commit_file(
        self,
        filename: str,
        content: str,
        message: str,
        author: str = "ECHO Codex",
        is_binary: Optional[bool] = None,
    ) -> Optional[str]:
        """Crée ou met à jour un fichier et commit. Retourne le hash."""
        safe_name = self._secure_path(filename, allow_empty=False)
        filepath = os.path.join(self.repo_path, *safe_name.split("/"))
        self._ensure_parent_dirs_safe(filepath)
        if os.path.lexists(filepath) and (
            os.path.islink(filepath) or os.path.isdir(filepath)
        ):
            raise CodexPathConflictError(
                f"Impossible d'écrire '{safe_name}' : "
                "la cible est un dossier ou lien symbolique."
            )

        latin1_bytes = None
        if isinstance(content, str):
            try:
                latin1_bytes = content.encode("latin-1")
            except UnicodeEncodeError:
                latin1_bytes = None
                is_binary = False

            if is_binary is None:
                probe = b"\x00" if "\x00" in content else b""
                is_binary, _ = is_binary_file(safe_name, probe)
        elif isinstance(content, (bytes, bytearray)):
            if is_binary is None:
                is_binary, _ = is_binary_file(
                    safe_name, bytes(content[:8192])
                )

        if is_binary and latin1_bytes is not None:
            with open(filepath, "wb") as f:
                f.write(latin1_bytes)
        elif is_binary and isinstance(content, (bytes, bytearray)):
            with open(filepath, "wb") as f:
                f.write(content)
        else:
            with open(filepath, "w", encoding="utf-8", newline="") as f:
                f.write(
                    content
                    if isinstance(content, str)
                    else content.decode("utf-8", errors="replace")
                )

        if not self.is_versioned or self.repo is None:
            return "untracked"

        porcelain.add(self.repo_path, paths=[safe_name])
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=f"{author} <codex@echo.local>".encode("utf-8"),
            committer=f"{author} <codex@echo.local>".encode("utf-8"),
        )
        return (
            commit_sha.decode("ascii")
            if isinstance(commit_sha, bytes)
            else str(commit_sha)
        )

    def read_file(
        self,
        filename: str,
        start_line: int = None,
        end_line: int = None,
    ) -> Optional[dict]:
        """Lit un fichier régulier. Retourne métadonnées + contenu ou None."""
        safe_name = self._secure_path(filename, allow_empty=False)
        filepath = os.path.join(self.repo_path, *safe_name.split("/"))
        if not os.path.lexists(filepath):
            return None
        if not os.path.islink(filepath) and os.path.isdir(filepath):
            return None

        fd, _ = _open_regular_file_ro(filepath, follow_symlinks=False)
        with os.fdopen(fd, "rb") as f:
            raw_bytes = f.read()

        is_bin, mime = is_binary_file(filepath, raw_bytes)
        if is_bin:
            return {
                "content": raw_bytes.decode("latin-1"),
                "total_lines": (len(raw_bytes) + 15) // 16,
                "range": None,
                "is_binary": True,
                "mime": mime,
                "size_bytes": len(raw_bytes),
            }

        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_content = raw_bytes.decode("latin-1")
        lines = text_content.splitlines(True)
        total = len(lines)

        if start_line is not None and end_line is not None:
            s = max(1, start_line) - 1
            e = min(total, end_line)
            content = "".join(lines[s:e])
            return {
                "content": content,
                "total_lines": total,
                "range": [s + 1, e],
                "is_binary": False,
                "mime": mime,
                "size_bytes": len(raw_bytes),
            }

        return {
            "content": text_content,
            "total_lines": total,
            "range": None,
            "is_binary": False,
            "mime": mime,
            "size_bytes": len(raw_bytes),
        }

    def delete_file(
        self,
        path: str,
        message: str,
        recursive: bool = False,
    ) -> Optional[tuple[str, list[str]]]:
        """Supprime fichier ou dossier et commit. Retourne (hash, paths)."""
        safe_name = self._secure_path(path, allow_empty=False)
        target_path = os.path.join(self.repo_path, *safe_name.split("/"))
        if not os.path.lexists(target_path):
            return None
        if os.path.islink(target_path):
            raise ValueError("Lien symbolique non autorisé.")

        st = os.lstat(target_path)
        paths_to_rm: list[str] = []
        if stat.S_ISDIR(st.st_mode):
            entries_count = 0
            for root, dirs, files in os.walk(target_path, followlinks=False):
                for d in dirs:
                    if os.path.islink(os.path.join(root, d)):
                        raise ValueError(
                            "Lien symbolique non autorisé dans le répertoire."
                        )
                    entries_count += 1
                for f in files:
                    f_abs = os.path.join(root, f)
                    if os.path.islink(f_abs):
                        raise ValueError(
                            "Lien symbolique non autorisé dans le répertoire."
                        )
                    entries_count += 1
                    rel_p = os.path.relpath(f_abs, self.repo_path).replace(
                        "\\", "/"
                    )
                    paths_to_rm.append(rel_p)
            if entries_count > 0 and not recursive:
                raise CodexDirectoryNotEmptyError(safe_name, entries_count)
            if entries_count > 0:
                shutil.rmtree(target_path)
            else:
                os.rmdir(target_path)
        else:
            os.remove(target_path)
            paths_to_rm = [safe_name]

        if not self.is_versioned or self.repo is None:
            return "untracked", paths_to_rm
        if not paths_to_rm:
            return "no_commit", []

        try:
            index = self.repo.open_index()
            tracked = {f.decode("utf-8") for f in index}
        except Exception:
            tracked = set()
        tracked_to_rm = [p for p in paths_to_rm if p in tracked]
        if not tracked_to_rm:
            return "no_commit", paths_to_rm

        porcelain.rm(self.repo_path, paths=tracked_to_rm)
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=b"ECHO Codex <codex@echo.local>",
            committer=b"ECHO Codex <codex@echo.local>",
        )
        commit_str = (
            commit_sha.decode("ascii")
            if isinstance(commit_sha, bytes)
            else str(commit_sha)
        )
        return commit_str, paths_to_rm

    def move_entry(
        self,
        old_name: str,
        new_name: str,
        message: str,
        author: str = "ECHO Codex",
        overwrite: bool = False,
    ) -> Optional[dict]:
        """Déplace ou renomme un fichier ou dossier intra-workspace."""
        safe_old = self._secure_path(old_name, allow_empty=False)
        safe_new = self._secure_path(new_name, allow_empty=False)
        if safe_old == safe_new:
            return None
        if safe_new.startswith(safe_old + "/"):
            raise ValueError(
                "Impossible de déplacer un dossier dans "
                "l'un de ses propres sous-dossiers."
            )

        old_path = os.path.join(self.repo_path, *safe_old.split("/"))
        new_path = os.path.join(self.repo_path, *safe_new.split("/"))
        if not os.path.lexists(old_path):
            return None
        if os.path.islink(old_path) or os.path.islink(new_path):
            raise ValueError("Lien symbolique non autorisé.")

        old_st = os.lstat(old_path)
        src_is_dir = stat.S_ISDIR(old_st.st_mode)
        if not src_is_dir and not stat.S_ISREG(old_st.st_mode):
            raise ValueError("Type de fichier spécial non supporté.")
        target_type = "directory" if src_is_dir else "file"

        if os.path.lexists(new_path):
            if not overwrite:
                return None
            dst_st = os.lstat(new_path)
            if src_is_dir or stat.S_ISDIR(dst_st.st_mode):
                raise CodexPathConflictError(
                    f"Conflit de dossier sur la destination '{safe_new}'."
                )

        moved_pairs: list[tuple[str, str]] = []
        if src_is_dir:
            for root, dirs, files in os.walk(old_path, followlinks=False):
                for d in dirs:
                    if os.path.islink(os.path.join(root, d)):
                        raise ValueError(
                            "Lien symbolique non autorisé dans la source."
                        )
                for f in files:
                    f_abs = os.path.join(root, f)
                    if os.path.islink(f_abs):
                        raise ValueError(
                            "Lien symbolique non autorisé dans la source."
                        )
                    rel_in = os.path.relpath(f_abs, old_path).replace(
                        "\\", "/"
                    )
                    moved_pairs.append(
                        (f"{safe_old}/{rel_in}", f"{safe_new}/{rel_in}")
                    )
        else:
            moved_pairs = [(safe_old, safe_new)]

        self._ensure_parent_dirs_safe(new_path)
        os.replace(old_path, new_path)

        if not self.is_versioned or self.repo is None:
            return {
                "commit_hash": "untracked",
                "target_type": target_type,
                "old_path": safe_old,
                "new_path": safe_new,
                "moved_pairs": moved_pairs,
            }
        if not moved_pairs:
            return {
                "commit_hash": "no_commit",
                "target_type": target_type,
                "old_path": safe_old,
                "new_path": safe_new,
                "moved_pairs": [],
            }

        try:
            index = self.repo.open_index()
            tracked = {f.decode("utf-8") for f in index}
        except Exception:
            tracked = set()
        old_tracked = [old_p for old_p, _ in moved_pairs if old_p in tracked]
        new_paths = [new_p for _, new_p in moved_pairs]
        if old_tracked:
            porcelain.rm(self.repo_path, paths=old_tracked)
        if new_paths:
            porcelain.add(self.repo_path, paths=new_paths)
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=f"{author} <codex@echo.local>".encode("utf-8"),
            committer=f"{author} <codex@echo.local>".encode("utf-8"),
        )
        commit_str = (
            commit_sha.decode("ascii")
            if isinstance(commit_sha, bytes)
            else str(commit_sha)
        )
        return {
            "commit_hash": commit_str,
            "target_type": target_type,
            "old_path": safe_old,
            "new_path": safe_new,
            "moved_pairs": moved_pairs,
        }

    def rename_file(
        self,
        old_name: str,
        new_name: str,
        message: str,
        author: str = "ECHO Codex",
    ) -> Optional[str]:
        """Renomme un fichier ou un dossier dans le dépôt sans écrasement."""
        res = self.move_entry(
            old_name,
            new_name,
            message=message,
            author=author,
            overwrite=False,
        )
        return res["commit_hash"] if res else None

    def _should_skip_fs_name(self, name: str) -> bool:
        """Retourne True si l'entrée disque doit être filtrée au listing."""
        if not name or name.startswith(".") or name in ECHO_SYNC_EXCLUDE_LIST:
            return True
        if self.workspace == CODEX_WORKSPACE_SANDBOX:
            return is_sandbox_internal_segment(name)
        return False

    def _build_list_file_entry(
        self,
        rel_path: str,
        abs_path: str,
    ) -> Optional[dict]:
        """Construit un item listing sécurisé pour un fichier régulier."""
        if os.path.islink(abs_path):
            return None
        try:
            st = os.lstat(abs_path)
        except Exception:
            return None
        if not stat.S_ISREG(st.st_mode):
            return None
        size = st.st_size
        mtime = st.st_mtime
        line_count = 0
        is_bin = False
        try:
            fd, _ = _open_regular_file_ro(abs_path, follow_symlinks=False)
            with os.fdopen(fd, "rb") as fh:
                sample = fh.read(8192)
                is_bin, _ = is_binary_file(abs_path, sample)
                if is_bin:
                    line_count = (size + 15) // 16
                elif sample:
                    line_count = sample.count(b"\n")
                    last_byte = sample[-1]
                    while True:
                        chunk = fh.read(65536)
                        if not chunk:
                            break
                        line_count += chunk.count(b"\n")
                        last_byte = chunk[-1]
                    if last_byte != 10:
                        line_count += 1
        except Exception:
            pass
        return {
            "filename": rel_path,
            "type": "file",
            "lang": self.detect_language(os.path.basename(rel_path)),
            "lines": line_count,
            "size_bytes": size,
            "mtime": mtime,
            "is_binary": is_bin,
        }

    def _build_list_dir_entry(self, rel_path: str, abs_path: str) -> dict:
        """Construit un item listing pour un dossier non symlink."""
        try:
            mtime = os.path.getmtime(abs_path)
        except Exception:
            mtime = 0
        return {
            "filename": rel_path,
            "type": "directory",
            "lang": "folder",
            "lines": 0,
            "size_bytes": 0,
            "mtime": mtime,
        }

    def list_directory(
        self,
        target_rel_path: str = "",
        recursive: bool = False,
        max_entries: Optional[int] = None,
    ) -> List[dict]:
        """Liste un dossier Codex (profondeur 1 ou récursif sécurisé)."""
        safe_rel = self._secure_path(target_rel_path, allow_empty=True)
        target_abs_path = (
            os.path.join(self.repo_path, *safe_rel.split("/"))
            if safe_rel
            else self.repo_path
        )
        if (
            os.path.islink(target_abs_path)
            or not os.path.exists(target_abs_path)
            or not os.path.isdir(target_abs_path)
        ):
            return []

        files: List[dict] = []
        if not self.is_versioned or self.repo is None:
            if not recursive:
                for entry in os.scandir(target_abs_path):
                    if self._should_skip_fs_name(entry.name):
                        continue
                    if entry.is_symlink():
                        continue
                    rel_path = os.path.relpath(
                        entry.path, self.repo_path
                    ).replace("\\", "/")
                    if entry.is_dir(follow_symlinks=False):
                        item = self._build_list_dir_entry(rel_path, entry.path)
                        item["is_binary"] = False
                        files.append(item)
                    elif entry.is_file(follow_symlinks=False):
                        item = self._build_list_file_entry(
                            rel_path, entry.path
                        )
                        if item:
                            files.append(item)
            else:
                for root, dirs, fnames in os.walk(
                    target_abs_path, followlinks=False
                ):
                    dirs[:] = sorted(
                        d
                        for d in dirs
                        if not self._should_skip_fs_name(d)
                        and not os.path.islink(os.path.join(root, d))
                    )
                    for d in dirs:
                        d_abs = os.path.join(root, d)
                        rel_dir = os.path.relpath(
                            d_abs, self.repo_path
                        ).replace("\\", "/")
                        files.append(
                            self._build_list_dir_entry(rel_dir, d_abs)
                        )
                    for fname in sorted(fnames):
                        if self._should_skip_fs_name(fname):
                            continue
                        f_abs = os.path.join(root, fname)
                        rel_file = os.path.relpath(
                            f_abs, self.repo_path
                        ).replace("\\", "/")
                        item = self._build_list_file_entry(rel_file, f_abs)
                        if item:
                            files.append(item)
        else:
            try:
                index = self.repo.open_index()
                tracked_files = {f.decode("utf-8") for f in index}
            except Exception:
                tracked_files = set()

            prefix = "" if not safe_rel else safe_rel + "/"
            dirs_found: set[str] = set()

            if not recursive:
                for tracked_file in tracked_files:
                    if not tracked_file.startswith(prefix):
                        continue
                    remainder = tracked_file[len(prefix):]
                    if not remainder:
                        continue
                    parts = remainder.split("/")
                    if len(parts) == 1:
                        filepath = os.path.join(
                            self.repo_path, *tracked_file.split("/")
                        )
                        item = self._build_list_file_entry(
                            tracked_file, filepath
                        )
                        if item:
                            files.append(item)
                    else:
                        immediate_dir = prefix + parts[0]
                        if immediate_dir not in dirs_found:
                            dir_abs = os.path.join(
                                self.repo_path, *immediate_dir.split("/")
                            )
                            if (
                                not os.path.islink(dir_abs)
                                and os.path.isdir(dir_abs)
                            ):
                                dirs_found.add(immediate_dir)
                                files.append(
                                    self._build_list_dir_entry(
                                        immediate_dir, dir_abs
                                    )
                                )
                try:
                    for entry in os.scandir(target_abs_path):
                        if (
                            entry.is_symlink()
                            or not entry.is_dir(follow_symlinks=False)
                            or self._should_skip_fs_name(entry.name)
                        ):
                            continue
                        immediate_dir = prefix + entry.name
                        if immediate_dir not in dirs_found:
                            dirs_found.add(immediate_dir)
                            files.append(
                                self._build_list_dir_entry(
                                    immediate_dir, entry.path
                                )
                            )
                except Exception:
                    pass
            else:
                for tracked_file in sorted(tracked_files):
                    if not tracked_file.startswith(prefix):
                        continue
                    remainder = tracked_file[len(prefix):]
                    if not remainder:
                        continue
                    parts = remainder.split("/")
                    for i in range(len(parts) - 1):
                        parent_rel = prefix + "/".join(parts[:i + 1])
                        if parent_rel not in dirs_found:
                            p_abs = os.path.join(
                                self.repo_path, *parent_rel.split("/")
                            )
                            if not os.path.islink(p_abs) and os.path.isdir(
                                p_abs
                            ):
                                dirs_found.add(parent_rel)
                                files.append(
                                    self._build_list_dir_entry(
                                        parent_rel, p_abs
                                    )
                                )
                    filepath = os.path.join(
                        self.repo_path, *tracked_file.split("/")
                    )
                    item = self._build_list_file_entry(tracked_file, filepath)
                    if item:
                        files.append(item)

                try:
                    for root, dirs, _ in os.walk(
                        target_abs_path, followlinks=False
                    ):
                        dirs[:] = sorted(
                            d
                            for d in dirs
                            if not self._should_skip_fs_name(d)
                            and not os.path.islink(os.path.join(root, d))
                        )
                        for d in dirs:
                            d_abs = os.path.join(root, d)
                            rel_dir = os.path.relpath(
                                d_abs, self.repo_path
                            ).replace("\\", "/")
                            if rel_dir not in dirs_found:
                                dirs_found.add(rel_dir)
                                files.append(
                                    self._build_list_dir_entry(rel_dir, d_abs)
                                )
                except Exception:
                    pass

        if recursive:
            files.sort(key=lambda x: x["filename"])
        else:
            files.sort(key=lambda x: x["mtime"], reverse=True)

        if max_entries is not None:
            return files[: max(0, max_entries)]
        return files

    def list_files(self) -> List[dict]:
        """Amorce le chargement Lazy Loading en ne retournant que la racine."""
        return self.list_directory("")

    def list_all_files(self, target_rel_path: str = "") -> List[dict]:
        """Retourne tous les fichiers réguliers sous target_rel_path."""
        return [
            e
            for e in self.list_directory(
                target_rel_path=target_rel_path, recursive=True
            )
            if e.get("type") == "file"
        ]

    def _get_text_content(self, filename: str, commit_hash: str = None) -> str:
        """Helper interne : retourne le contenu d'un fichier (HEAD ou commit), ou une chaîne vide."""
        if commit_hash:
            return self.get_file_at_commit(filename, commit_hash) or ""
        else:
            result = self.read_file(filename)
            return result["content"] if result else ""

    def _find_lines(self, content: str, pattern: str, is_regex: bool = False) -> List[str]:
        """Helper interne : retourne uniquement les lignes contenant le pattern (ou matchant la regex)."""
        lines = content.splitlines()
        if not is_regex:
            return [l for l in lines if pattern in l]
        try:
            return [l for l in lines if re.search(pattern, l)]
        except re.error:
            # Fallback automatique si la regex est invalide
            return [l for l in lines if pattern in l]

    def search_in_file(self, filename: str, pattern: str,
                       is_regex: bool = False, commit_hash: str = None) -> List[dict]:
        """Permet de rechercher une occurrence textuelle ou une expression régulière au sein d'un fichier du Codex.
        Supporte l'investigation spatio-temporelle : si un identifiant de commit est fourni, la recherche s'opère sur la révision historique correspondante au lieu du fichier actif.
        """
        res = self.read_file_at_commit(filename, commit_hash) if commit_hash else self.read_file(filename)
        if not res or res.get("is_binary"):
            return []
        content = res.get("content", "")
        if not content:
            return []

        matches = []
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            try:
                match = re.search(pattern, line) if is_regex else (pattern in line)
            except re.error:
                match = (pattern in line)
                
            if match:
                matches.append({"line_number": i, "line_content": line})
                if len(matches) >= 50:
                    break
        return matches

    def trace_history(self, query: str, target_paths: List[str] = None, limit: int = 50) -> List[dict]:
        """Traverse l'historique de versionnement du Codex pour trouver les itérations où `query` a été ajouté ou supprimé."""
        entries = []
        blob_cache = {} 
        try:
            walker = self.repo.get_walker(max_entries=limit * 5)
            for walk_entry in walker:
                commit = walk_entry.commit
                c_id = commit.id.decode("ascii")
                p_id = commit.parents[0].decode("ascii") if commit.parents else None
                
                all_files = [
                    f["filename"]
                    for f in self.list_all_files()
                    if f["type"] == "file"
                ]
                if target_paths:
                    files_to_check = [f for f in all_files if any(f.startswith(p) for p in target_paths)]
                else:
                    files_to_check = all_files
                
                commit_diffs = []
                for f in files_to_check:
                    is_bin, _ = is_binary_file(f)
                    if is_bin:
                        continue
                    cache_key_curr = f"{c_id}:{f}"
                    cache_key_prev = f"{p_id}:{f}" if p_id else None
                    
                    curr_content = blob_cache.pop(cache_key_curr, None)
                    if curr_content is None:
                        curr_content = self._get_text_content(f, c_id)
                        
                    prev_content = self._get_text_content(f, p_id) if p_id else ""
                    if p_id:
                        blob_cache[cache_key_prev] = prev_content
                        
                    curr_lines = self._find_lines(curr_content, query, is_regex=False)
                    prev_lines = self._find_lines(prev_content, query, is_regex=False)
                    
                    if curr_lines != prev_lines:
                        added = [l for l in curr_lines if l not in prev_lines]
                        removed = [l for l in prev_lines if l not in curr_lines]
                        if added or removed:
                            commit_diffs.append({"file": f, "added": added, "removed": removed})
                        
                if commit_diffs:
                    msg = commit.message.decode("utf-8", errors="replace").strip()
                    entries.append({"hash": c_id[:12], "message": msg, "diffs": commit_diffs})
                    
                if len(entries) >= limit:
                    break
        except Exception:
            pass
        return entries

    # =========================================================================
    # HISTORIQUE GIT
    # =========================================================================

    def get_log(self, filename: str = None, limit: int = 20) -> List[dict]:
        """Retourne l'historique des commits (ordre du plus récent HEAD au plus ancien). Si filename, filtré pour ce fichier."""
        if not self.is_versioned or self.repo is None:
            return []
        entries = []
        try:
            head_sha = self.repo.head()
            visited = set()
            curr_sha = head_sha
            while curr_sha and curr_sha not in visited and len(entries) < limit:
                visited.add(curr_sha)
                commit = self.repo[curr_sha]
                if not isinstance(commit, Commit):
                    break
                msg = commit.message.decode("utf-8", errors="replace").strip()
                ts = commit.commit_time
                sha = commit.id.decode("ascii")

                include = True
                if filename:
                    safe_name = self._secure_path(filename)
                    include = self._file_in_commit(commit, safe_name)

                if include:
                    entries.append({
                        "hash": sha[:12],
                        "hash_full": sha,
                        "message": msg,
                        "author": commit.author.decode("utf-8", errors="replace"),
                        "timestamp": ts,
                    })

                curr_sha = commit.parents[0] if commit.parents else None
        except Exception:
            pass
        return entries

    def resolve_commit(self, commit_ref: str) -> Optional[Any]:
        """Résout une référence de commit (HEAD, SHA-1 complet 40 caractères, SHA court 7..39 caractères)."""
        if not commit_ref or not self.is_versioned or self.repo is None:
            return None
        ref_str = str(commit_ref).strip()
        if ref_str.upper() == "HEAD":
            try:
                head_sha = self.repo.head()
                obj = self.repo[head_sha]
                if isinstance(obj, Commit):
                    return obj
            except Exception:
                return None
        if len(ref_str) == 40:
            try:
                obj = self.repo[ref_str.encode("ascii")]
                if isinstance(obj, Commit):
                    return obj
            except KeyError:
                pass
        try:
            walker = self.repo.get_walker()
            for entry in walker:
                sha = entry.commit.id.decode("ascii")
                if sha.startswith(ref_str):
                    return entry.commit
        except Exception:
            pass
        return None

    def _file_in_commit(self, commit, filename: str) -> bool:
        """Vérifie si un fichier existe dans l'arbre d'un commit (y compris dans les sous-dossiers)."""
        try:
            safe_name = self._secure_path(filename)
            tree = self.repo[commit.tree]
            _, _ = tree.lookup_path(self.repo.get_object, safe_name.encode("utf-8"))
            return True
        except (KeyError, Exception):
            return False

    def get_diff(self, commit_a: str, commit_b: str = None) -> str:
        """Diff entre deux commits. Si commit_b=None, diff commit_a vs son parent."""
        try:
            from dulwich.patch import write_tree_diff
            import io

            obj_a = self.resolve_commit(commit_a)
            if not obj_a:
                return f"Erreur diff: commit '{commit_a}' introuvable."

            if commit_b:
                obj_b = self.resolve_commit(commit_b)
                if not obj_b:
                    return f"Erreur diff: commit '{commit_b}' introuvable."
                tree_b = obj_b.tree
            else:
                # Parent du commit_a
                if obj_a.parents:
                    parent = self.repo.object_store[obj_a.parents[0]]
                    tree_b = parent.tree
                else:
                    tree_b = None  # Premier commit, pas de parent

            buf = io.BytesIO()
            write_tree_diff(buf, self.repo.object_store,
                            tree_b if tree_b else Tree().id,
                            obj_a.tree)
            return buf.getvalue().decode("utf-8", errors="replace")
        except Exception as e:
            return f"Erreur diff: {e}"

    def read_file_at_commit(
            self,
            filename: str,
            commit_hash: str) -> Optional[dict]:
        """Lit un fichier à un commit donné avec métadonnées complètes {content, total_lines, range, is_binary, mime, size_bytes}."""
        try:
            commit = self.resolve_commit(commit_hash)
            if not commit:
                return None

            safe_name = self._secure_path(filename)
            root_tree = self.repo[commit.tree]

            try:
                _, sha = root_tree.lookup_path(self.repo.get_object, safe_name.encode("utf-8"))
            except KeyError:
                return None

            blob = self.repo[sha]
            raw_bytes = blob.data
            is_bin, mime = is_binary_file(safe_name, raw_bytes)
            if is_bin:
                return {
                    "content": raw_bytes.decode("latin-1"),
                    "total_lines": (len(raw_bytes) + 15) // 16,
                    "range": None,
                    "is_binary": True,
                    "mime": mime,
                    "size_bytes": len(raw_bytes),
                }

            try:
                text_content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text_content = raw_bytes.decode("latin-1")
            lines = text_content.splitlines(True)
            total = len(lines)
            return {
                "content": text_content,
                "total_lines": total,
                "range": None,
                "is_binary": False,
                "mime": mime,
                "size_bytes": len(raw_bytes),
            }
        except Exception:
            pass
        return None

    def get_file_at_commit(
            self,
            filename: str,
            commit_hash: str) -> Optional[str]:
        """Lit le contenu d'un fichier à un commit donné (checkout virtuel via SHA complet ou court)."""
        res = self.read_file_at_commit(filename, commit_hash)
        return res["content"] if res else None

    def get_file_history_index(self, filename: str) -> List[dict]:
        """Retourne la liste ordonnée des commits touchant un fichier (pour navigation ◀ ▶).
        Index 0 = commit le plus ancien, dernier index = HEAD."""
        log = self.get_log(filename=filename, limit=500)
        log.reverse()  # Ordre chronologique (ancien → récent)
        return log

    # =========================================================================
    # ADMINISTRATION
    # =========================================================================

    def restore_repo_to_timestamp(self, cutoff_ts: int) -> dict:
        """Restaure le dépôt Git 'main' (working tree, index et HEAD) au dernier commit dont commit_time <= cutoff_ts.
        Retourne {'restored_commit': sha_or_none, 'tracked_files': set_of_rel_paths}."""
        try:
            commits = self.get_log(limit=500)
            if not commits:
                return {"restored_commit": None, "tracked_files": set()}

            # Sélection du commit le plus récent antérieur ou égal à cutoff_ts
            target_commit = None
            for c in commits:
                if int(c.get("timestamp", 0)) <= int(cutoff_ts):
                    target_commit = c
                    break

            # Nettoyage préalable de l'arbre de travail (hors .git)
            for entry in os.listdir(self.repo_path):
                if entry == ".git":
                    continue
                full_p = os.path.join(self.repo_path, entry)
                if os.path.isdir(full_p) and not os.path.islink(full_p):
                    shutil.rmtree(full_p)
                else:
                    os.remove(full_p)

            if not target_commit:
                # Aucun commit antérieur ou égal au point de fork : réinitialisation de l'index et des refs
                idx_path = self.repo.index_path()
                if os.path.exists(idx_path):
                    os.remove(idx_path)
                for ref in list(self.repo.refs.keys()):
                    if ref.startswith(b"refs/heads/"):
                        del self.repo.refs[ref]
                return {"restored_commit": None, "tracked_files": set()}

            from dulwich.index import build_index_from_tree
            sha_bytes = target_commit["hash_full"].encode("ascii")
            commit_obj = self.repo.object_store[sha_bytes]

            # Reconstruction de l'index et du working tree depuis l'arbre du commit cible
            build_index_from_tree(
                self.repo_path,
                self.repo.index_path(),
                self.repo.object_store,
                commit_obj.tree,
            )

            # Réalignement des références Git sur le commit cible
            for ref in list(self.repo.refs.keys()):
                if ref.startswith(b"refs/heads/"):
                    self.repo.refs[ref] = sha_bytes
            self.repo.refs[b"HEAD"] = sha_bytes

            index = self.repo.open_index()
            tracked = {f.decode("utf-8") for f in index}
            return {"restored_commit": target_commit["hash_full"], "tracked_files": tracked}
        except Exception as e:
            return {"restored_commit": None, "tracked_files": set(), "error": str(e)}

    def reset_all(self) -> bool:
        """Supprime entièrement le dépôt. Irréversible."""
        try:
            if os.path.exists(self.repo_path):
                shutil.rmtree(self.repo_path)
                self.repo = None
            return True
        except Exception:
            return False

    def get_repo_stats(self) -> dict:
        """Statistiques du dépôt."""
        files = self.list_all_files()
        log = self.get_log(limit=1)
        total_size = sum(f["size_bytes"] for f in files)
        total_commits = 0
        try:
            total_commits = len(list(self.repo.get_walker()))
        except BaseException:
            pass

        return {
            "total_files": len(files),
            "total_commits": total_commits,
            "total_size_bytes": total_size,
            "last_commit_hash": log[0]["hash"] if log else None,
            "last_commit_message": log[0]["message"] if log else None,
        }

    def get_latest_state(self) -> str:
        """Retourne un identifiant d'état rapide (hash Git ou mtime) pour le ping."""
        if not self.is_versioned or self.repo is None:
            try:
                max_mtime = os.path.getmtime(self.repo_path)
                for root, dirs, files in os.walk(self.repo_path):
                    for name in dirs + files:
                        mtime = os.path.getmtime(os.path.join(root, name))
                        if mtime > max_mtime:
                            max_mtime = mtime
                return str(max_mtime)
            except Exception:
                return "0"
        else:
            log = self.get_log(limit=1)
            return log[0]["hash"] if log else "empty"

    # =========================================================================
    # UTILITAIRES
    # =========================================================================

    @staticmethod
    def detect_language(filename: str, content: Optional[str] = None) -> str:
        """Détecte le langage Monaco depuis le nom, l'extension ou le contenu."""
        base = os.path.basename(filename or "").lower()
        if (
            base == "dockerfile"
            or base.startswith("dockerfile.")
            or base == "containerfile"
            or base.startswith("containerfile.")
        ):
            return "dockerfile"
        if base in ("makefile", "gnumakefile"):
            return "makefile"
        if base == "cmakelists.txt":
            return "cmake"
        if base == "jenkinsfile":
            return "groovy"
        if base in (".env", ".gitignore", ".dockerignore") or base.startswith(".env."):
            return "ini"

        ext = os.path.splitext(base)[1]
        if ext in CODEX_LANG_MAP:
            return CODEX_LANG_MAP[ext]

        if isinstance(content, str) and content.strip():
            head = content.lstrip("\ufeff \t\r\n")[:512]
            lines = head.splitlines()
            first_line = lines[0].strip().lower() if lines else ""

            if first_line.startswith("#!"):
                if "python" in first_line:
                    return "python"
                if re.search(r"\b(pwsh|powershell)\b", first_line):
                    return "powershell"
                if re.search(r"\b(bash|sh|zsh|dash|ksh)\b", first_line):
                    return "shell"
                if re.search(r"\b(node|deno|bun)\b", first_line):
                    return "javascript"
                if re.search(r"\bruby\b", first_line):
                    return "ruby"
                if re.search(r"\bperl\b", first_line):
                    return "perl"
                if re.search(r"\bphp\b", first_line):
                    return "php"

            head_lower = head.lower()
            if head_lower.startswith("<!doctype html") or head_lower.startswith("<html"):
                return "html"
            if head_lower.startswith("<svg") or head_lower.startswith("<?xml"):
                return "xml"
            if head_lower.startswith("<?php"):
                return "php"

            if (head.startswith("{") or head.startswith("[")) and len(content) <= 262144:
                try:
                    json.loads(content)
                    return "json"
                except (ValueError, TypeError):
                    pass

            if first_line == "---" and len(lines) > 1:
                if any(re.match(r"^[a-zA-Z0-9_.-]+\s*:", ln.strip()) for ln in lines[1:]):
                    return "yaml"

        return CODEX_DEFAULT_LANG

    # =========================================================================
    # MULTI-CHAT & DÉCOUVERTE SOUVERAINE
    # =========================================================================

    @classmethod
    def validate_user_chat(cls, user_id: str, chat_id: str) -> bool:
        """Valide qu'un chat_id appartient bien à l'utilisateur et ne présente aucun risque de traversée."""
        if not user_id or not chat_id:
            return False
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
        if not safe_uid or not safe_cid or safe_cid != str(chat_id).strip():
            return False
        users_root = getattr(echo_constants, "ECHO_USERS_ROOT", ECHO_USERS_ROOT)
        chat_dir = os.path.join(users_root, safe_uid, "chats", safe_cid)
        return os.path.exists(chat_dir)

    @classmethod
    def get_user_codex_chats(
        cls,
        user_id: str,
        current_chat_id: Optional[str] = None,
        include_files: bool = False,
    ) -> dict:
        """Découvre et résout les conversations de l'utilisateur ayant un Codex
        (ou des fichiers dans files/ si include_files=True).
        Exclut strictement current_chat_id de la liste others.
        Retourne:
        {
            "current": {"id": current_chat_id, "title": <titre_actuel>},
            "others": [
                {"id": cid, "title": <titre>},  # Strictement != current_chat_id
            ]
        }
        """
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        users_root = getattr(echo_constants, "ECHO_USERS_ROOT", ECHO_USERS_ROOT)
        base_data_dir = getattr(echo_constants, "ECHO_BASE_DATA_DIR", ECHO_BASE_DATA_DIR)
        user_chats_dir = os.path.join(users_root, safe_uid, "chats")
        cur_cid = str(current_chat_id or "").strip()

        candidate_cids = set()
        if os.path.exists(user_chats_dir):
            with os.scandir(user_chats_dir) as entries:
                for entry in entries:
                    if entry.is_dir() and not entry.name.startswith("."):
                        has_content = False
                        codex_path = os.path.join(entry.path, CODEX_DIR_NAME)
                        if os.path.exists(codex_path):
                            try:
                                for ws in ECHO_CODEX_WORKSPACES:
                                    ws_dir = os.path.join(codex_path, ws)
                                    if os.path.exists(ws_dir):
                                        with os.scandir(ws_dir) as ws_entries:
                                            if any(e.name != ".git" for e in ws_entries):
                                                has_content = True
                                                break
                            except Exception:
                                has_content = True
                        if not has_content and include_files:
                            files_path = os.path.join(entry.path, "files")
                            if os.path.exists(files_path):
                                try:
                                    with os.scandir(files_path) as f_entries:
                                        if any(not e.name.startswith(".") for e in f_entries):
                                            has_content = True
                                except Exception:
                                    has_content = True
                        if has_content:
                            candidate_cids.add(entry.name)

        if cur_cid:
            candidate_cids.add(cur_cid)

        # Résolution atomique des titres depuis /app/backend/data/webui.db
        titles_map = {}
        valid_chat_ids = set()
        db_verified = False
        webui_db = os.path.join(base_data_dir, "webui.db")
        if os.path.exists(webui_db) and candidate_cids:
            import sqlite3
            try:
                conn = sqlite3.connect(f"file:{webui_db}?mode=ro", uri=True)
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in candidate_cids)
                cursor.execute(
                    f"SELECT id, title FROM chat WHERE user_id = ? AND id IN ({placeholders})",
                    [safe_uid] + list(candidate_cids)
                )
                for row in cursor.fetchall():
                    cid_db = str(row[0])
                    valid_chat_ids.add(cid_db)
                    title_db = (row[1] or "").strip()
                    titles_map[cid_db] = title_db or f"Chat {cid_db[:8]}"
                conn.close()
                db_verified = True
            except Exception:
                db_verified = False

        cur_title = titles_map.get(cur_cid, "Conversation Actuelle")
        others = []
        for cid in sorted(candidate_cids):
            if cid == cur_cid:
                continue  # EXCLUSION STRICTE DU CHAT ACTUEL

            # Si webui.db a été vérifié avec succès, exclure les conversations orphelines (supprimées de webui.db)
            if db_verified and cid not in valid_chat_ids:
                continue

            others.append({
                "id": cid,
                "title": titles_map.get(cid, f"Chat {cid[:8]}")
            })

        return {
            "current": {"id": cur_cid, "title": cur_title},
            "others": sorted(others, key=lambda x: x["title"].lower())
        }


def _is_under_real(real_p: str, real_root: str) -> bool:
    """Vérifie par commonpath que real_p réside sous real_root."""
    try:
        return os.path.commonpath([real_root, real_p]) == real_root
    except ValueError:
        return False


def _resolve_transfer_source(
    user_id: str,
    chat_id: str,
    source_path: str,
    src_ws: str,
) -> tuple[str, str, str, bool, tuple[str, ...]]:
    """Résout (clean_src_rel, src_abs, default_dst, follow_links, roots)."""
    if src_ws in ECHO_CODEX_WORKSPACES:
        src_repo = CodexRepo(user_id, chat_id, workspace=src_ws)
        clean_src_rel, src_abs = resolve_confined_codex_path(
            src_repo.repo_path,
            source_path,
            workspace=src_ws,
            allow_empty=False,
        )
        if not os.path.lexists(src_abs):
            raise FileNotFoundError(
                f"Source introuvable dans '{src_ws}' : {clean_src_rel}"
            )
        return (
            clean_src_rel,
            src_abs,
            clean_src_rel,
            False,
            (os.path.realpath(src_repo.repo_path),),
        )

    if src_ws == CODEX_FILES_DOMAIN_NAME:
        sess_files = get_echo_session_path(
            user_id, chat_id, CODEX_FILES_DOMAIN_NAME
        )
        glob_files = get_echo_global_path(
            user_id, CODEX_FILES_DOMAIN_NAME
        )
        allowed_roots = (sess_files, glob_files)
        real_roots = tuple(
            os.path.realpath(r) for r in allowed_roots if r
        )
        clean_src_rel, cand_abs = resolve_confined_codex_path(
            sess_files,
            source_path,
            workspace=CODEX_FILES_DOMAIN_NAME,
            allow_empty=False,
            allowed_symlink_roots=allowed_roots,
        )
        if os.path.exists(cand_abs):
            return (clean_src_rel, cand_abs, clean_src_rel, True, real_roots)

        from echo_state_manager import EchoStateManager

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        res = (
            state.get_resource(source_path)
            or state.get_resource(clean_src_rel)
        )
        if not res:
            base_q = os.path.basename(clean_src_rel)
            for c_res in state.get_resources(search=base_q) or []:
                c_id = c_res.get("id") or ""
                c_name = (
                    (c_res.get("name") or "").replace("\\", "/").strip("/")
                )
                if (
                    c_id in (source_path, clean_src_rel)
                    or c_name in (source_path, clean_src_rel)
                    or os.path.basename(c_name) == clean_src_rel
                ):
                    res = c_res
                    break

        if res:
            cand_found = None
            up = resolve_upload_file_path(
                user_id, res.get("id") or "", chat_id=chat_id
            )
            if up and os.path.exists(up):
                if os.path.islink(up) and not any(
                    _is_under_real(os.path.realpath(up), r)
                    for r in real_roots
                ):
                    raise ValueError(
                        "Lien symbolique source hors périmètre autorisé."
                    )
                cand_found = up
            else:
                sp = res.get("storage_path") or ""
                if sp and os.path.isabs(sp) and os.path.exists(sp):
                    if not any(
                        _is_under_real(os.path.realpath(sp), r)
                        for r in real_roots
                    ):
                        raise ValueError(
                            "Chemin ressource 'files' hors périmètre."
                        )
                    cand_found = sp
            if cand_found:
                raw_name = (
                    (res.get("name") or "").replace("\\", "/").strip("/")
                )
                clean_name = (
                    normalize_codex_rel_path(raw_name)
                    or normalize_codex_rel_path(os.path.basename(raw_name))
                )
                return (
                    clean_src_rel,
                    cand_found,
                    clean_name or clean_src_rel,
                    True,
                    real_roots,
                )

        up_direct = resolve_upload_file_path(
            user_id, clean_src_rel, chat_id=chat_id
        )
        if up_direct and os.path.exists(up_direct):
            if os.path.islink(up_direct) and not any(
                _is_under_real(os.path.realpath(up_direct), r)
                for r in real_roots
            ):
                raise ValueError(
                    "Lien symbolique source hors périmètre autorisé."
                )
            return (
                clean_src_rel, up_direct, clean_src_rel, True, real_roots
            )
        raise FileNotFoundError(
            f"Source introuvable dans 'files' : {source_path}"
        )

    bv_dir = get_echo_session_path(user_id, chat_id, BROWSER_VIEWS_DIR_NAME)
    real_bv = os.path.realpath(bv_dir)
    clean_src_rel, cand_abs = resolve_confined_codex_path(
        bv_dir,
        source_path,
        workspace=BROWSER_VIEWS_DIR_NAME,
        allow_empty=False,
    )
    if os.path.lexists(cand_abs):
        return (clean_src_rel, cand_abs, clean_src_rel, False, (real_bv,))

    from echo_state_manager import EchoStateManager

    state = EchoStateManager(user_id=user_id, chat_id=chat_id)
    res = (
        state.get_resource(source_path)
        or state.get_resource(clean_src_rel)
    )
    if res and res.get("resource_type") != "browserview":
        res = None
    def _extract_clip_filename(r_obj: dict) -> str:
        raw_meta = r_obj.get("metadata")
        meta = {}
        if isinstance(raw_meta, dict):
            meta = raw_meta
        elif isinstance(raw_meta, str) and raw_meta:
            try:
                parsed = json.loads(raw_meta)
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:
                meta = {}
        return str(meta.get("clip_filename") or "").replace("\\", "/").strip("/")

    if not res:
        for c_res in state.get_resources(resource_type="browserview") or []:
            c_id = c_res.get("id") or ""
            c_name = (c_res.get("name") or "").replace("\\", "/").strip("/")
            c_clip = _extract_clip_filename(c_res)
            if (
                c_id in (source_path, clean_src_rel)
                or c_name in (source_path, clean_src_rel)
                or os.path.basename(c_name) == clean_src_rel
                or (c_clip and (c_clip in (source_path, clean_src_rel) or os.path.basename(c_clip) == clean_src_rel))
            ):
                res = c_res
                break

    if res:
        clip_fn = _extract_clip_filename(res)
        for cand_val in (
            res.get("storage_path") or "",
            res.get("name") or "",
            clip_fn,
        ):
            if not cand_val:
                continue
            if os.path.isabs(cand_val):
                if (
                    os.path.lexists(cand_val)
                    and not os.path.islink(cand_val)
                    and _is_under_real(os.path.realpath(cand_val), real_bv)
                ):
                    rel_bv = os.path.relpath(
                        os.path.realpath(cand_val), real_bv
                    ).replace("\\", "/")
                    return (
                        clean_src_rel, cand_val, rel_bv, False, (real_bv,)
                    )
            else:
                try:
                    c_rel, c_abs = resolve_confined_codex_path(
                        bv_dir,
                        cand_val,
                        workspace=BROWSER_VIEWS_DIR_NAME,
                        allow_empty=False,
                    )
                    if os.path.lexists(c_abs):
                        return (
                            clean_src_rel, c_abs, c_rel, False, (real_bv,)
                        )
                except ValueError:
                    pass

    raise FileNotFoundError(
        f"Source introuvable dans 'browserviews' : {source_path}"
    )


def _check_dst_parents_readonly(repo_path: str, target_abs: str) -> None:
    """Vérifie sans écriture qu'aucun parent destination n'est en conflit."""
    repo_abs = os.path.abspath(repo_path)
    parent_abs = os.path.abspath(os.path.dirname(target_abs))
    if parent_abs == repo_abs:
        return
    rel_parent = os.path.relpath(parent_abs, repo_abs)
    if rel_parent == "." or rel_parent.startswith(".."):
        return
    cur = repo_abs
    for seg in rel_parent.split(os.sep):
        if not seg or seg == ".":
            continue
        cur = os.path.join(cur, seg)
        if os.path.lexists(cur) and (
            os.path.islink(cur) or not os.path.isdir(cur)
        ):
            raise CodexPathConflictError(
                f"Conflit sur le dossier parent destination '{seg}'."
            )


def _should_skip_transfer_name(name: str) -> bool:
    """Filtre les fichiers cachés, exclusions sync et segments IPC sandbox."""
    return (
        not name
        or name.startswith(".")
        or name in ECHO_SYNC_EXCLUDE_LIST
        or is_sandbox_internal_segment(name)
    )


def transfer_codex_entries(
    user_id: str,
    chat_id: str,
    source_path: str,
    source_workspace: str,
    destination_workspace: str,
    destination_path: Optional[str] = None,
    mode: str = "copy",
    overwrite: bool = False,
    commit_message: Optional[str] = None,
    author: str = "ECHO Codex",
) -> dict:
    """Transfère (copie ou move sandbox->main) un fichier/dossier sécurisé."""
    src_ws = str(source_workspace or "").strip().lower()
    dst_ws = str(destination_workspace or "").strip().lower()
    op_mode = str(mode or "copy").strip().lower()

    if src_ws not in CODEX_TRANSFER_SOURCES:
        raise ValueError(f"Espace source non autorisé : '{src_ws}'.")
    if dst_ws not in CODEX_WRITABLE_WORKSPACES:
        raise ValueError(f"Espace destination non autorisé : '{dst_ws}'.")
    if op_mode not in ("copy", "move"):
        raise ValueError(f"Mode de transfert invalide : '{op_mode}'.")
    if src_ws == dst_ws:
        raise ValueError(
            "Le transfert intra-workspace est interdit ; utilisez move_entry."
        )
    if op_mode == "move" and not (
        src_ws == CODEX_WORKSPACE_SANDBOX and dst_ws == CODEX_WORKSPACE_MAIN
    ):
        raise ValueError(
            "Le mode 'move' est réservé exclusivement à sandbox -> main."
        )
    if dst_ws == CODEX_WORKSPACE_MAIN and src_ws != CODEX_WORKSPACE_SANDBOX:
        raise ValueError(
            "Seul l'espace 'sandbox' peut transférer vers 'main'."
        )

    dst_repo = CodexRepo(user_id, chat_id, workspace=dst_ws)
    (
        clean_src_rel,
        src_abs,
        default_dst,
        follow_src_links,
        real_allowed_roots,
    ) = _resolve_transfer_source(user_id, chat_id, source_path, src_ws)

    raw_dst = (
        destination_path
        if (destination_path is not None and str(destination_path).strip())
        else default_dst
    )
    clean_dst_rel, dst_abs = resolve_confined_codex_path(
        dst_repo.repo_path,
        raw_dst,
        workspace=dst_ws,
        allow_empty=False,
    )

    if dst_ws == CODEX_WORKSPACE_MAIN:
        max_files = CODEX_TRANSFER_MAX_FILES_MAIN
        max_file_bytes = CODEX_TRANSFER_MAX_FILE_BYTES_MAIN
        max_total_bytes = CODEX_TRANSFER_MAX_TOTAL_BYTES_MAIN
    else:
        max_files = CODEX_TRANSFER_MAX_FILES_SANDBOX
        max_file_bytes = CODEX_TRANSFER_MAX_FILE_BYTES_SANDBOX
        max_total_bytes = CODEX_TRANSFER_MAX_TOTAL_BYTES_SANDBOX

    src_st = os.stat(src_abs) if follow_src_links else os.lstat(src_abs)
    if not follow_src_links and stat.S_ISLNK(src_st.st_mode):
        raise ValueError("Lien symbolique source non autorisé.")
    if follow_src_links and os.path.islink(src_abs):
        if not any(
            _is_under_real(os.path.realpath(src_abs), r)
            for r in real_allowed_roots
        ):
            raise ValueError("Lien symbolique source hors périmètre autorisé.")

    if stat.S_ISDIR(src_st.st_mode):
        target_type = "directory"
    elif stat.S_ISREG(src_st.st_mode):
        target_type = "file"
    else:
        raise ValueError("Type de source spécial non supporté.")

    if os.path.lexists(dst_abs):
        if os.path.islink(dst_abs):
            raise CodexPathConflictError(
                f"Conflit lien symbolique sur '{clean_dst_rel}'."
            )
        dst_st = os.lstat(dst_abs)
        if not overwrite:
            raise CodexPathConflictError(
                f"La destination '{clean_dst_rel}' existe déjà."
            )
        if target_type == "directory" or stat.S_ISDIR(dst_st.st_mode):
            raise CodexPathConflictError(
                f"Collision de dossier interdite sur '{clean_dst_rel}'."
            )

    copy_plan: list[tuple[str, str, str, int]] = []
    skipped_entries: list[str] = []
    total_bytes = 0

    def _scan_dir(cur_dir: str) -> None:
        nonlocal total_bytes
        with os.scandir(cur_dir) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            rel_in = os.path.relpath(entry.path, src_abs).replace("\\", "/")
            if _should_skip_transfer_name(entry.name):
                skipped_entries.append(rel_in)
                continue
            if entry.is_symlink():
                if not follow_src_links:
                    raise ValueError(
                        f"Lien symbolique interdit dans la source : {rel_in}"
                    )
                real_e = os.path.realpath(entry.path)
                if not any(
                    _is_under_real(real_e, r) for r in real_allowed_roots
                ):
                    raise ValueError(
                        f"Lien symbolique source hors périmètre : {rel_in}"
                    )
            est = (
                os.stat(entry.path)
                if follow_src_links
                else os.lstat(entry.path)
            )
            if stat.S_ISDIR(est.st_mode):
                _scan_dir(entry.path)
            elif stat.S_ISREG(est.st_mode):
                fsize = est.st_size
                if fsize > max_file_bytes:
                    raise CodexQuotaExceededError(
                        f"Fichier '{rel_in}' trop volumineux ({fsize} o)."
                    )
                if len(copy_plan) + 1 > max_files:
                    raise CodexQuotaExceededError(
                        f"Quota de fichiers dépassé (max {max_files})."
                    )
                if total_bytes + fsize > max_total_bytes:
                    raise CodexQuotaExceededError(
                        f"Quota total dépassé (max {max_total_bytes} o)."
                    )
                sub_rel, sub_abs = resolve_confined_codex_path(
                    dst_repo.repo_path,
                    f"{clean_dst_rel}/{rel_in}",
                    workspace=dst_ws,
                    allow_empty=False,
                )
                total_bytes += fsize
                copy_plan.append((entry.path, sub_rel, sub_abs, fsize))
            else:
                raise ValueError(
                    f"Fichier spécial non supporté dans la source : {rel_in}"
                )

    if target_type == "directory":
        _scan_dir(src_abs)
    else:
        fsize = src_st.st_size
        if fsize > max_file_bytes or fsize > max_total_bytes:
            raise CodexQuotaExceededError(
                f"Fichier '{clean_src_rel}' dépasse le quota ({fsize} o)."
            )
        total_bytes = fsize
        copy_plan.append((src_abs, clean_dst_rel, dst_abs, fsize))

    _check_dst_parents_readonly(dst_repo.repo_path, dst_abs)
    for _, sub_rel, sub_abs, _ in copy_plan:
        _check_dst_parents_readonly(dst_repo.repo_path, sub_abs)
        if os.path.lexists(sub_abs):
            if os.path.islink(sub_abs):
                raise CodexPathConflictError(
                    f"Conflit lien symbolique sur '{sub_rel}'."
                )
            if not overwrite:
                raise CodexPathConflictError(
                    f"La destination '{sub_rel}' existe déjà."
                )
            if stat.S_ISDIR(os.lstat(sub_abs).st_mode):
                raise CodexPathConflictError(
                    f"Collision avec un dossier sur '{sub_rel}'."
                )

    created_files: list[str] = []
    overwritten_backups: list[tuple[str, str]] = []
    commit_hash = "untracked"

    try:
        if target_type == "directory" and not copy_plan:
            dst_repo.create_directory(clean_dst_rel)
        else:
            for src_f, _, sub_abs, _ in copy_plan:
                dst_repo._ensure_parent_dirs_safe(sub_abs)
                if overwrite and os.path.lexists(sub_abs):
                    fd_bak, bak_path = tempfile.mkstemp(
                        prefix=".echo_bak_",
                        dir=os.path.dirname(sub_abs),
                    )
                    os.close(fd_bak)
                    os.replace(sub_abs, bak_path)
                    overwritten_backups.append((sub_abs, bak_path))
                else:
                    created_files.append(sub_abs)

                fd_in, _ = _open_regular_file_ro(
                    src_f, follow_symlinks=follow_src_links
                )
                with os.fdopen(fd_in, "rb") as fin, open(
                    sub_abs, "wb"
                ) as fout:
                    while True:
                        chunk = fin.read(CODEX_TRANSFER_CHUNK_BYTES)
                        if not chunk:
                            break
                        fout.write(chunk)

        if dst_repo.is_versioned and dst_repo.repo is not None:
            if copy_plan:
                rel_added = [sub_rel for _, sub_rel, _, _ in copy_plan]
                porcelain.add(dst_repo.repo_path, paths=rel_added)
                msg = commit_message or (
                    f"Transfer ({op_mode}) {src_ws}:{clean_src_rel} "
                    f"-> {dst_ws}:{clean_dst_rel}"
                )
                commit_sha = porcelain.commit(
                    dst_repo.repo_path,
                    message=msg.encode("utf-8"),
                    author=f"{author} <codex@echo.local>".encode("utf-8"),
                    committer=f"{author} <codex@echo.local>".encode("utf-8"),
                )
                commit_hash = (
                    commit_sha.decode("ascii")
                    if isinstance(commit_sha, bytes)
                    else str(commit_sha)
                )
            else:
                commit_hash = "no_commit"
    except Exception:
        for cf in reversed(created_files):
            try:
                if os.path.lexists(cf) and not os.path.isdir(cf):
                    os.remove(cf)
            except Exception:
                pass
        for orig_p, bak_p in reversed(overwritten_backups):
            try:
                if os.path.exists(bak_p):
                    os.replace(bak_p, orig_p)
            except Exception:
                pass
        raise
    finally:
        for _, bak_p in overwritten_backups:
            try:
                if os.path.exists(bak_p):
                    os.remove(bak_p)
            except Exception:
                pass

    if op_mode == "move":
        for src_f, _, _, _ in copy_plan:
            if (
                os.path.lexists(src_f)
                and not os.path.islink(src_f)
                and os.path.isfile(src_f)
            ):
                os.remove(src_f)
        if target_type == "directory":
            for root, _, _ in os.walk(
                src_abs, topdown=False, followlinks=False
            ):
                if not os.path.islink(root) and os.path.isdir(root):
                    if not os.listdir(root):
                        os.rmdir(root)

    return {
        "commit_hash": commit_hash,
        "target_type": target_type,
        "source_workspace": src_ws,
        "destination_workspace": dst_ws,
        "source_path": clean_src_rel,
        "destination_path": clean_dst_rel,
        "mode": op_mode,
        "transferred_files": [sub_rel for _, sub_rel, _, _ in copy_plan],
        "skipped_entries": skipped_entries,
        "total_bytes": total_bytes,
    }


async def sync_codex_registry_save(
    user_id: str,
    chat_id: str,
    workspace: str,
    rel_path: str,
    commit_hash: str,
    msg: str,
    language: Optional[str] = None,
    lines: Optional[int] = None,
    mime: Optional[str] = None,
    repo_base_dir: Optional[str] = None,
) -> None:
    """Synchronise une ressource Codex en SQLite si l'espace est indexé."""
    if not user_id or not chat_id or not is_codex_workspace_indexed(workspace):
        return
    try:
        from echo_state_manager import EchoStateManager

        clean_rel = (rel_path or "").strip().lstrip("/\\").replace("\\", "/")
        if not clean_rel:
            return
        lang = language or CodexRepo.detect_language(clean_rel)
        if lines is None and repo_base_dir:
            lines = EchoStateManager._count_text_file_lines(
                repo_base_dir, clean_rel
            )
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        res_type = get_codex_workspace_resource_type(workspace) or "codex"
        await asyncio.to_thread(
            state.save_resource,
            id=clean_rel,
            name=clean_rel,
            resource_type=res_type,
            status=FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
            mime=mime,
            git_tracked=True,
            language=lang,
            lines=lines,
            last_commit=(commit_hash or "")[:12],
            commit_msg=msg,
            storage_path=format_codex_storage_path(clean_rel, workspace),
        )
    except Exception as exc:
        print(
            f"[EchoCodexGit] sync_codex_registry_save skipped "
            f"({workspace}/{rel_path}): {exc}"
        )


async def sync_codex_registry_delete(
    user_id: str,
    chat_id: str,
    workspace: str,
    paths_to_rm: Optional[List[str]] = None,
    dir_rel_path: Optional[str] = None,
) -> None:
    """Supprime ou détache les ressources Codex supprimées si indexé."""
    if not user_id or not chat_id or not is_codex_workspace_indexed(workspace):
        return
    try:
        from echo_state_manager import EchoStateManager

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        if dir_rel_path:
            await asyncio.to_thread(
                state.delete_codex_prefix_resources,
                workspace,
                dir_rel_path,
            )
        for rel_path in (paths_to_rm or ()):
            if rel_path:
                await asyncio.to_thread(
                    state.delete_codex_resource,
                    workspace,
                    rel_path,
                )
    except Exception as exc:
        print(
            f"[EchoCodexGit] sync_codex_registry_delete skipped "
            f"({workspace}): {exc}"
        )


async def sync_codex_registry_relocate(
    user_id: str,
    chat_id: str,
    workspace: str,
    moved_pairs: List[tuple[str, str]],
    commit_hash: str,
    commit_msg: str,
    overwrite: bool = False,
    repo_base_dir: Optional[str] = None,
) -> int:
    """Relocalise en SQLite les ressources Codex déplacées si indexé."""
    if (
        not user_id
        or not chat_id
        or not moved_pairs
        or not is_codex_workspace_indexed(workspace)
    ):
        return 0
    try:
        from echo_state_manager import EchoStateManager

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        return await asyncio.to_thread(
            state.relocate_codex_resources,
            workspace,
            moved_pairs,
            commit_hash,
            commit_msg,
            CodexRepo.detect_language,
            overwrite,
            repo_base_dir,
        )
    except Exception as exc:
        print(
            f"[EchoCodexGit] sync_codex_registry_relocate skipped "
            f"({workspace}): {exc}"
        )
        return 0
