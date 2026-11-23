# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: Migration 001 - Topologie unifiée des fichiers v6
author: Wilfried BARNAVON
version: 6.21.0.2
description: Inverse les maîtres physiques chats/ -> files/, recâble les symlinks, aligne webui.db & identity.db et purge uploads/.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.21.0.2: Création de la migration topologique des fichiers v6 (maîtres physiques users/{uid}/files/, symlinks chats, alignement SQLite absolu et purge uploads/.tmp_uploads).

import os
import shutil
import sqlite3
import time
from typing import Any, Optional


def _check_sqlite_integrity(db_path: str) -> None:
    """Vérifie l'intégrité rapide d'une base SQLite avant toute opération."""
    if not os.path.isfile(db_path) or os.path.getsize(db_path) == 0:
        return
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(db_path, timeout=15.0)
        conn.execute("PRAGMA busy_timeout=15000")
        row = conn.execute("PRAGMA quick_check(1)").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"Intégrité SQLite invalide sur {db_path}: {row}")
    finally:
        if conn is not None:
            conn.close()


def _extract_uid_from_path(raw_path: str) -> Optional[str]:
    """Extrait l'identifiant utilisateur depuis un chemin contenant `users/<uid>/`."""
    norm = raw_path.replace("\\", "/")
    marker = "users/"
    idx = norm.find(marker)
    if idx == -1:
        return None
    rest = norm[idx + len(marker):]
    parts = [p for p in rest.split("/") if p]
    return parts[0] if parts else None


def _resolve_upload_source(data_dir: str, raw_path: str) -> Optional[str]:
    """Résout un chemin dans `uploads/` vers son chemin absolu sur disque."""
    norm = raw_path.replace("\\", "/")
    if os.path.isabs(raw_path) and os.path.exists(raw_path):
        return raw_path
    rel_candidate = os.path.join(data_dir, norm.lstrip("/"))
    if os.path.exists(rel_candidate):
        return rel_candidate
    uploads_candidate = os.path.join(data_dir, "uploads", os.path.basename(norm))
    if os.path.exists(uploads_candidate):
        return uploads_candidate
    return None


def run(dry_run: bool = True) -> dict[str, Any]:
    """Exécute les 5 passes idempotentes de migration topologique des fichiers v6."""
    data_dir = os.environ.get("ECHO_DATA_DIR") or os.environ.get("DATA_DIR") or "/app/backend/data"

    if not os.path.isdir(data_dir) or not os.access(data_dir, os.W_OK):
        raise RuntimeError(f"Répertoire data_dir absent ou non inscriptible : {data_dir}")

    webui_db_path = os.path.join(data_dir, "webui.db")
    _check_sqlite_integrity(webui_db_path)

    users_dir = os.path.join(data_dir, "users")
    user_ids: list[str] = []
    if os.path.isdir(users_dir):
        for entry in sorted(os.listdir(users_dir)):
            user_path = os.path.join(users_dir, entry)
            if os.path.isdir(user_path):
                user_ids.append(entry)
                _check_sqlite_integrity(os.path.join(user_path, "identity.db"))

    stats: dict[str, int] = {
        "inverted": 0,
        "rewired": 0,
        "dead_symlinks_removed": 0,
        "webui_paths_updated": 0,
        "uploads_repatriated": 0,
        "identity_paths_updated": 0,
        "uploads_symlinks_purged": 0,
        "tmp_parts_purged": 0,
    }

    # real_target -> new_master_abs_path
    moved_map: dict[str, str] = {}
    projected_physical: set[tuple[str, str]] = set()
    inverted_targets: set[str] = set()

    # --- Pass 1 : Inversion users/{uid}/files/* ---
    for uid in user_ids:
        files_dir = os.path.join(users_dir, uid, "files")
        if not os.path.isdir(files_dir):
            continue
        chats_prefix = os.path.realpath(os.path.join(users_dir, uid, "chats")) + os.sep

        for fname in sorted(os.listdir(files_dir)):
            up = os.path.join(files_dir, fname)
            if os.path.islink(up):
                if not os.path.exists(up):
                    stats["dead_symlinks_removed"] += 1
                    if not dry_run:
                        os.unlink(up)
                    continue

                real_target = os.path.realpath(up)
                if os.path.isfile(real_target) and real_target.startswith(chats_prefix):
                    abs_up = os.path.abspath(up)
                    abs_real_target = os.path.abspath(real_target)
                    moved_map[real_target] = abs_up
                    moved_map[abs_real_target] = abs_up
                    projected_physical.add((uid, fname))
                    inverted_targets.add(abs_real_target)
                    stats["inverted"] += 1
                    if not dry_run:
                        os.unlink(up)
                        shutil.move(real_target, up)
                        os.symlink(abs_up, real_target)
            elif os.path.isfile(up):
                projected_physical.add((uid, fname))

    # --- Pass 1.bis : Recâblage et nettoyage dans users/{uid}/chats/{cid}/files/* ---
    for uid in user_ids:
        chats_dir = os.path.join(users_dir, uid, "chats")
        if not os.path.isdir(chats_dir):
            continue
        for cid in sorted(os.listdir(chats_dir)):
            chat_files_dir = os.path.join(chats_dir, cid, "files")
            if not os.path.isdir(chat_files_dir):
                continue
            for cname in sorted(os.listdir(chat_files_dir)):
                cp = os.path.join(chat_files_dir, cname)
                abs_cp = os.path.abspath(cp)
                if abs_cp in inverted_targets:
                    continue
                if os.path.islink(cp):
                    raw_link = os.readlink(cp)
                    resolved_target = (
                        os.path.abspath(raw_link)
                        if os.path.isabs(raw_link)
                        else os.path.abspath(os.path.join(chat_files_dir, raw_link))
                    )
                    real_resolved = os.path.realpath(cp)
                    new_master = moved_map.get(real_resolved) or moved_map.get(resolved_target)
                    if new_master:
                        if os.path.abspath(resolved_target) != new_master:
                            stats["rewired"] += 1
                            if not dry_run:
                                os.unlink(cp)
                                os.symlink(new_master, cp)
                    elif not os.path.exists(cp):
                        if (uid, cname) not in projected_physical:
                            stats["dead_symlinks_removed"] += 1
                            if not dry_run:
                                os.unlink(cp)

    # --- Pass 2 : Alignement webui.db::file et rapatriement depuis uploads/ ---
    if os.path.isfile(webui_db_path) and os.path.getsize(webui_db_path) > 0:
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(webui_db_path, timeout=15.0)
            conn.execute("PRAGMA busy_timeout=15000")
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(file)").fetchall()
            }
            if "path" in cols:
                has_user_id = "user_id" in cols
                query = (
                    "SELECT id, user_id, path FROM file WHERE path IS NOT NULL"
                    if has_user_id
                    else "SELECT id, NULL, path FROM file WHERE path IS NOT NULL"
                )
                rows = conn.execute(query).fetchall()
                for file_id, row_uid, raw_path in rows:
                    if not raw_path:
                        continue
                    norm_path = str(raw_path).replace("\\", "/")
                    uid = row_uid or _extract_uid_from_path(norm_path)
                    if not uid:
                        continue
                    basename = os.path.basename(norm_path)
                    if not basename:
                        continue
                    target_abs = os.path.abspath(os.path.join(users_dir, str(uid), "files", basename))

                    # Vérification si déjà pointé sur l'ancienne cible déplacée ou self-healing
                    abs_or_rel = (
                        norm_path
                        if os.path.isabs(norm_path)
                        else os.path.join(data_dir, norm_path.lstrip("/"))
                    )
                    in_moved = os.path.realpath(abs_or_rel) in moved_map or os.path.abspath(abs_or_rel) in moved_map
                    is_legacy_location = ("/chats/" in norm_path) or ("uploads/" in norm_path)
                    master_known = (uid, basename) in projected_physical or (
                        os.path.isfile(target_abs) and not os.path.islink(target_abs)
                    )

                    if in_moved or (is_legacy_location and master_known):
                        if os.path.abspath(str(raw_path)) != target_abs:
                            stats["webui_paths_updated"] += 1
                            if not dry_run:
                                conn.execute("UPDATE file SET path = ? WHERE id = ?", (target_abs, file_id))
                    elif "uploads/" in norm_path:
                        upload_src = _resolve_upload_source(data_dir, norm_path)
                        if upload_src and not os.path.islink(upload_src) and os.path.isfile(upload_src):
                            stats["uploads_repatriated"] += 1
                            stats["webui_paths_updated"] += 1
                            projected_physical.add((uid, basename))
                            if not dry_run:
                                os.makedirs(os.path.dirname(target_abs), exist_ok=True)
                                shutil.move(upload_src, target_abs)
                                conn.execute("UPDATE file SET path = ? WHERE id = ?", (target_abs, file_id))
                if not dry_run:
                    conn.commit()
        finally:
            if conn is not None:
                conn.close()

    # --- Pass 3 : Alignement users/{uid}/identity.db::echo_resources ---
    for uid in user_ids:
        id_db_path = os.path.join(users_dir, uid, "identity.db")
        if not os.path.isfile(id_db_path) or os.path.getsize(id_db_path) == 0:
            continue
        conn_id: Optional[sqlite3.Connection] = None
        try:
            conn_id = sqlite3.connect(id_db_path, timeout=15.0)
            conn_id.execute("PRAGMA busy_timeout=15000")
            tbl = conn_id.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='echo_resources'"
            ).fetchone()
            if not tbl:
                continue
            cols_id = {
                row[1]
                for row in conn_id.execute("PRAGMA table_info(echo_resources)").fetchall()
            }
            if "storage_path" not in cols_id:
                continue

            rows_id = conn_id.execute(
                "SELECT rowid, storage_path FROM echo_resources WHERE storage_path IS NOT NULL"
            ).fetchall()
            for rowid, storage_path in rows_id:
                if not storage_path:
                    continue
                norm_sp = str(storage_path).replace("\\", "/")
                if "/chats/" not in norm_sp and "uploads/" not in norm_sp:
                    continue
                basename = os.path.basename(norm_sp)
                if not basename:
                    continue
                target_abs = os.path.abspath(os.path.join(users_dir, str(uid), "files", basename))
                if (uid, basename) in projected_physical or (
                    os.path.isfile(target_abs) and not os.path.islink(target_abs)
                ):
                    if os.path.abspath(str(storage_path)) != target_abs:
                        stats["identity_paths_updated"] += 1
                        if not dry_run:
                            conn_id.execute(
                                "UPDATE echo_resources SET storage_path = ? WHERE rowid = ?",
                                (target_abs, rowid),
                            )
            if not dry_run:
                conn_id.commit()
        finally:
            if conn_id is not None:
                conn_id.close()

    # --- Pass 4 : Purge des symlinks résiduels dans uploads/ ---
    uploads_dir = os.path.join(data_dir, "uploads")
    if os.path.isdir(uploads_dir):
        for entry in sorted(os.listdir(uploads_dir)):
            upath = os.path.join(uploads_dir, entry)
            if os.path.islink(upath):
                stats["uploads_symlinks_purged"] += 1
                if not dry_run:
                    os.unlink(upath)

    # --- Pass 5 : Purge des .part périmés (> 3600s) dans .tmp_uploads/ ---
    tmp_uploads_dir = os.path.join(data_dir, ".tmp_uploads")
    if os.path.isdir(tmp_uploads_dir):
        cutoff_ts = time.time() - 3600.0
        for entry in sorted(os.listdir(tmp_uploads_dir)):
            if not entry.endswith(".part"):
                continue
            tpath = os.path.join(tmp_uploads_dir, entry)
            if not os.path.islink(tpath) and os.path.isfile(tpath):
                if os.path.getmtime(tpath) < cutoff_ts:
                    stats["tmp_parts_purged"] += 1
                    if not dry_run:
                        os.unlink(tpath)

    return {"dry_run": bool(dry_run), "stats": stats}
