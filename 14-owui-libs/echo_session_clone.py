"""
title: ECHO Session Clone & Migration Engine
author: Wilfried BARNAVON
version: 5.231.29.1
description: Moteur partagé de duplication atomique des conteneurs de session ECHO (Clone, Fork et Migration).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.29.1: Alignement sur les constantes SSOT des espaces de travail Codex (CODEX_DIR_NAME, CODEX_WORKSPACE_MAIN, CODEX_WORKSPACE_SANDBOX).
# 5.231.28.2: Suppression des requêtes SQLite sur les tables obsolètes plans et codex_docs (unification sur echo_resources).
# 5.231.27.2: Refonte clonage/fork (résolution causale, copie Espace Utilisateur/RAG <= cutoff_ts, réassignation ID).
# 5.228.4.1: Mise à jour echo_resources.name, reset N8N deployed_as_*, purge last_model_id, frontmatter Codex.
# 5.227.1.1: Création du module partagé de duplication atomique de session (clone, fork et migration).

from contextlib import closing
from typing import Callable, List, Optional, Set, Tuple
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid

from echo_constants import (
    CODEX_DIR_NAME,
    CODEX_WORKSPACE_MAIN,
    CODEX_WORKSPACE_SANDBOX,
    COLLECTION_SESSION_RAG,
    ECHO_QDRANT_URL,
)
from echo_paths import get_echo_session_path
import httpx

logger = logging.getLogger("ECHO-SESSION-CLONE")


def _is_session_db_populated(db_path: str) -> bool:
    """Vérifie si une base session.db contient déjà des données conversationnelles ou des ressources."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False
    try:
        with closing(sqlite3.connect(db_path, timeout=5.0)) as conn:
            cur = conn.cursor()
            for tbl in (
                "suture_index",
                "message_shadows",
                "cognitive_signatures",
                "call_bridge",
                "echo_resources",
            ):
                try:
                    row = cur.execute(f"SELECT 1 FROM {tbl} LIMIT 1").fetchone()
                    if row is not None:
                        return True
                except sqlite3.Error:
                    continue
    except Exception:
        return False
    return False


def backup_session_db(orig_db: str, new_db: str) -> bool:
    """Copie atomique de la base SQLite session.db via l'API backup native (immune au mode WAL)."""
    if not os.path.exists(orig_db) or _is_session_db_populated(new_db):
        return False
    os.makedirs(os.path.dirname(new_db), exist_ok=True)
    with closing(sqlite3.connect(orig_db, timeout=15.0)) as src, closing(
        sqlite3.connect(new_db, timeout=15.0)
    ) as dst:
        src.backup(dst)
    return True


def reassign_session_ids(cur: sqlite3.Cursor, orig_cid: str, new_cid: str) -> None:
    """Réassigne chat_id, les identifiants composites _C_{cid}_T_ et les chemins physiques dans SQLite."""
    for tbl in (
        "suture_index",
        "message_shadows",
        "cognitive_threads",
        "processed_files",
    ):
        try:
            cur.execute(f"UPDATE {tbl} SET chat_id = ?", (new_cid,))
        except Exception:
            pass

    orig_id_marker = f"_C_{orig_cid}_T_"
    new_id_marker = f"_C_{new_cid}_T_"
    try:
        cur.execute(
            "UPDATE echo_resources SET id = REPLACE(id, ?, ?) WHERE id LIKE ?",
            (orig_id_marker, new_id_marker, f"%{orig_id_marker}%"),
        )
        cur.execute(
            "UPDATE echo_resources SET name = REPLACE(name, ?, ?) WHERE name LIKE ?",
            (orig_id_marker, new_id_marker, f"%{orig_id_marker}%"),
        )
        cur.execute(
            "UPDATE echo_resources SET storage_path = REPLACE(storage_path, ?, ?) WHERE storage_path LIKE ?",
            (orig_id_marker, new_id_marker, f"%{orig_id_marker}%"),
        )
        cur.execute(
            "UPDATE echo_resources SET summary = REPLACE(summary, ?, ?) WHERE summary LIKE ?",
            (orig_id_marker, new_id_marker, f"%{orig_id_marker}%"),
        )
    except Exception:
        pass

    try:
        cur.execute(
            "UPDATE processed_files SET file_id = REPLACE(file_id, ?, ?) WHERE file_id LIKE ?",
            (orig_id_marker, new_id_marker, f"%{orig_id_marker}%"),
        )
        cur.execute(
            "UPDATE processed_files SET filename = REPLACE(filename, ?, ?) WHERE filename LIKE ?",
            (orig_id_marker, new_id_marker, f"%{orig_id_marker}%"),
        )
    except Exception:
        pass

    for tbl, col in (
        ("tool_journal", "io_json"),
        ("call_bridge", "args_json"),
        ("cognitive_threads", "content_json"),
    ):
        try:
            cur.execute(
                f"UPDATE {tbl} SET {col} = REPLACE({col}, ?, ?) WHERE {col} LIKE ?",
                (orig_id_marker, new_id_marker, f"%{orig_id_marker}%"),
            )
        except Exception:
            pass

    for sep in ("/", "\\"):
        old_seg = f"{sep}chats{sep}{orig_cid}{sep}"
        new_seg = f"{sep}chats{sep}{new_cid}{sep}"
        try:
            cur.execute(
                "UPDATE echo_resources SET storage_path = REPLACE(storage_path, ?, ?) WHERE storage_path LIKE ?",
                (old_seg, new_seg, f"%{old_seg}%"),
            )
        except Exception:
            pass
        for tbl, col in (
            ("tool_journal", "io_json"),
            ("call_bridge", "args_json"),
            ("cognitive_threads", "content_json"),
        ):
            try:
                cur.execute(
                    f"UPDATE {tbl} SET {col} = REPLACE({col}, ?, ?) WHERE {col} LIKE ?",
                    (old_seg, new_seg, f"%{old_seg}%"),
                )
            except Exception:
                pass

    try:
        cur.execute(
            "UPDATE echo_settings SET value = REPLACE(value, ?, ?) WHERE key = 'wm_state' AND value LIKE ?",
            (orig_cid, new_cid, f"%{orig_cid}%"),
        )
    except Exception:
        pass

    try:
        cur.execute(
            "UPDATE echo_resources SET status = 'ready' "
            "WHERE resource_type = 'n8n_workflow' AND status LIKE 'deployed_as_%'"
        )
    except Exception:
        pass


def purge_conversational_state(cur: sqlite3.Cursor) -> None:
    """Purge l'état conversationnel d'une session migrée en préservant les ressources (Codex, Plans, Fichiers, N8N)."""
    for tbl in (
        "suture_index",
        "message_shadows",
        "cognitive_signatures",
        "tool_journal",
        "rich_payloads",
        "call_bridge",
        "cognitive_threads",
    ):
        try:
            cur.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass

    try:
        cur.execute(
            "DELETE FROM echo_resources WHERE resource_type IN ('aec_event', 'aec_directive')"
        )
    except Exception:
        pass

    try:
        cur.execute(
            "DELETE FROM echo_settings WHERE key IN ("
            "'context_stats', 'previous_context_stats', 'last_model_id', "
            "'last_active_model', 'turn_origin_model', 'compaction_state', 'context_checkpoint') "
            "OR key LIKE 'aec_tier_%' OR key LIKE 'cascade_%'"
        )
        cur.execute("DELETE FROM auth_data WHERE key LIKE 'cascade_%'")
    except Exception:
        pass


def _extract_call_ids_from_json(raw_json: Optional[str]) -> Set[str]:
    """Extrait tous les identifiants d'appels d'outils (call_id) depuis io_json ou full_parts_json."""
    call_ids: Set[str] = set()
    if not raw_json:
        return call_ids
    try:
        data = json.loads(raw_json)
    except Exception:
        return call_ids

    def _walk(node):
        if isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            calls = node.get("calls")
            if isinstance(calls, list):
                for c in calls:
                    if isinstance(c, dict) and c.get("id"):
                        call_ids.add(str(c["id"]))
            fc = node.get("functionCall")
            if isinstance(fc, dict) and fc.get("id"):
                call_ids.add(str(fc["id"]))
            fr = node.get("functionResponse")
            if isinstance(fr, dict) and fr.get("id"):
                call_ids.add(str(fr["id"]))
            parts = node.get("parts")
            if isinstance(parts, list):
                _walk(parts)

    _walk(data)
    return call_ids


def prune_forked_history(
    cur: sqlite3.Cursor, forked_msg_id: str
) -> Tuple[Optional[int], bool]:
    """Résout la chaîne causale jusqu'à forked_msg_id et élague les tables SQLite si le fork est partiel.
    Retourne (cutoff_ts, is_partial_fork)."""
    cur.execute(
        "SELECT cumulative_hash, parent_hash, message_id, timestamp FROM suture_index"
    )
    suture_rows = cur.fetchall()
    by_cumul = {r["cumulative_hash"]: r for r in suture_rows}

    cur.execute(
        "SELECT cumulative_hash, message_id, updated_at FROM cognitive_signatures"
    )
    all_sig_rows = cur.fetchall()

    cur.execute(
        "SELECT message_id, role, full_parts_json, updated_at FROM message_shadows"
    )
    all_shadow_rows = cur.fetchall()
    shadow_by_id = {r["message_id"]: r for r in all_shadow_rows if r["message_id"]}

    cur.execute("SELECT cumulative_hash, io_json, updated_at FROM tool_journal")
    all_journal_rows = cur.fetchall()
    journal_by_cumul = {
        r["cumulative_hash"]: r for r in all_journal_rows if r["cumulative_hash"]
    }

    target_sig_rows = [r for r in all_sig_rows if r["message_id"] == forked_msg_id]
    target_shadow = shadow_by_id.get(forked_msg_id)
    target_suture_rows = [r for r in suture_rows if r["message_id"] == forked_msg_id]

    is_user_only_fork = (
        not target_sig_rows
        and target_shadow is not None
        and target_shadow["role"] == "user"
    )

    cutoff_candidates: List[int] = []
    target_cumuls: Set[str] = set()
    target_msg_ids: Set[str] = {forked_msg_id}

    if is_user_only_fork:
        cutoff_candidates.append(int(target_shadow["updated_at"] or 0))
    else:
        for r in target_sig_rows:
            if r["cumulative_hash"]:
                target_cumuls.add(r["cumulative_hash"])
            if r["updated_at"]:
                cutoff_candidates.append(int(r["updated_at"]))
        for r in target_suture_rows:
            if r["cumulative_hash"]:
                target_cumuls.add(r["cumulative_hash"])
            if r["timestamp"]:
                cutoff_candidates.append(int(r["timestamp"]))
        if target_shadow and target_shadow["updated_at"]:
            cutoff_candidates.append(int(target_shadow["updated_at"]))

        for ch in list(target_cumuls):
            s_node = by_cumul.get(ch)
            if s_node:
                if s_node["timestamp"]:
                    cutoff_candidates.append(int(s_node["timestamp"]))
                if s_node["message_id"]:
                    target_msg_ids.add(s_node["message_id"])
                    for sr in suture_rows:
                        if (
                            sr["message_id"] == s_node["message_id"]
                            and sr["cumulative_hash"]
                        ):
                            target_cumuls.add(sr["cumulative_hash"])
                            if sr["timestamp"]:
                                cutoff_candidates.append(int(sr["timestamp"]))

        for mid in list(target_msg_ids):
            sh_row = shadow_by_id.get(mid)
            if sh_row and sh_row["updated_at"]:
                cutoff_candidates.append(int(sh_row["updated_at"]))

        target_call_ids: Set[str] = set()
        for ch in target_cumuls:
            j_row = journal_by_cumul.get(ch)
            if j_row:
                if j_row["updated_at"]:
                    cutoff_candidates.append(int(j_row["updated_at"]))
                target_call_ids |= _extract_call_ids_from_json(j_row["io_json"])
        for mid in target_msg_ids:
            sh_row = shadow_by_id.get(mid)
            if sh_row:
                target_call_ids |= _extract_call_ids_from_json(
                    sh_row["full_parts_json"]
                )

        if target_call_ids:
            ph_cb = ",".join("?" for _ in target_call_ids)
            cur.execute(
                f"SELECT MAX(timestamp) as max_cb FROM call_bridge WHERE call_id IN ({ph_cb})",
                tuple(target_call_ids),
            )
            row_cb = cur.fetchone()
            if row_cb and row_cb["max_cb"]:
                cutoff_candidates.append(int(row_cb["max_cb"]))

    if not cutoff_candidates and not target_cumuls:
        return None, False

    cutoff_ts = max(cutoff_candidates) if cutoff_candidates else None

    ancestor_cumuls: Set[str] = set(target_cumuls)
    ancestor_msg_ids: Set[str] = set(target_msg_ids)
    excluded_cumuls: Set[str] = {
        r["cumulative_hash"]
        for r in suture_rows
        if is_user_only_fork
        and r["message_id"] == forked_msg_id
        and r["cumulative_hash"]
    }
    excluded_msg_ids: Set[str] = {
        r["message_id"]
        for r in all_sig_rows
        if is_user_only_fork
        and r["cumulative_hash"] in excluded_cumuls
        and r["message_id"]
    }

    if cutoff_ts is not None:
        for sr in suture_rows:
            sr_ts = int(sr["timestamp"] or 0)
            if sr_ts <= cutoff_ts and sr["cumulative_hash"] not in excluded_cumuls:
                if sr["cumulative_hash"]:
                    ancestor_cumuls.add(sr["cumulative_hash"])
                if sr["message_id"]:
                    ancestor_msg_ids.add(sr["message_id"])

        for sig_r in all_sig_rows:
            if (
                sig_r["cumulative_hash"] in excluded_cumuls
                or sig_r["message_id"] in excluded_msg_ids
            ):
                continue
            sig_ts = int(sig_r["updated_at"] or 0)
            if (
                sig_ts <= cutoff_ts
                or sig_r["cumulative_hash"] in ancestor_cumuls
                or (not is_user_only_fork and sig_r["message_id"] in ancestor_msg_ids)
            ):
                if (
                    is_user_only_fork
                    and sig_r["cumulative_hash"] not in ancestor_cumuls
                    and sig_r["message_id"] != forked_msg_id
                ):
                    continue
                if sig_r["cumulative_hash"]:
                    ancestor_cumuls.add(sig_r["cumulative_hash"])
                if sig_r["message_id"]:
                    ancestor_msg_ids.add(sig_r["message_id"])

        for sr in suture_rows:
            if sr["cumulative_hash"] in excluded_cumuls:
                continue
            if sr["cumulative_hash"] in ancestor_cumuls and sr["message_id"]:
                ancestor_msg_ids.add(sr["message_id"])
            elif (
                not is_user_only_fork
                and sr["message_id"] in ancestor_msg_ids
                and sr["cumulative_hash"]
            ):
                ancestor_cumuls.add(sr["cumulative_hash"])

        for sig_r in all_sig_rows:
            if (
                sig_r["cumulative_hash"] in excluded_cumuls
                or sig_r["message_id"] in excluded_msg_ids
            ):
                continue
            if sig_r["cumulative_hash"] in ancestor_cumuls and sig_r["message_id"]:
                ancestor_msg_ids.add(sig_r["message_id"])
            elif sig_r["message_id"] in ancestor_msg_ids and sig_r["cumulative_hash"]:
                ancestor_cumuls.add(sig_r["cumulative_hash"])

        for sh_r in all_shadow_rows:
            if sh_r["message_id"] in excluded_msg_ids or (
                is_user_only_fork
                and sh_r["role"] != "user"
                and sh_r["message_id"] not in ancestor_msg_ids
            ):
                continue
            sh_ts = int(sh_r["updated_at"] or 0)
            if sh_ts <= cutoff_ts or sh_r["message_id"] in ancestor_msg_ids:
                if sh_r["message_id"]:
                    ancestor_msg_ids.add(sh_r["message_id"])
                if not is_user_only_fork and sh_ts > cutoff_ts:
                    cutoff_ts = sh_ts

    kept_call_ids: Set[str] = set()
    for ch in ancestor_cumuls:
        j_row = journal_by_cumul.get(ch)
        if j_row:
            kept_call_ids |= _extract_call_ids_from_json(j_row["io_json"])
    for mid in ancestor_msg_ids:
        sh_row = shadow_by_id.get(mid)
        if sh_row:
            kept_call_ids |= _extract_call_ids_from_json(sh_row["full_parts_json"])

    has_unkept_suture = any(
        sr["cumulative_hash"] not in ancestor_cumuls for sr in suture_rows
    )
    has_unkept_sigs = any(
        sig_r["cumulative_hash"] not in ancestor_cumuls
        and sig_r["message_id"] not in ancestor_msg_ids
        for sig_r in all_sig_rows
    )
    has_unkept_shadows = any(
        sh_r["message_id"] not in ancestor_msg_ids for sh_r in all_shadow_rows
    )
    is_partial_fork = has_unkept_suture or has_unkept_sigs or has_unkept_shadows

    if not is_partial_fork:
        return cutoff_ts, False

    if ancestor_cumuls:
        ph_c = ",".join("?" for _ in ancestor_cumuls)
        cur.execute(
            f"DELETE FROM suture_index WHERE cumulative_hash NOT IN ({ph_c})",
            tuple(ancestor_cumuls),
        )
        cur.execute(
            f"DELETE FROM tool_journal WHERE cumulative_hash NOT IN ({ph_c})",
            tuple(ancestor_cumuls),
        )
    elif is_user_only_fork:
        cur.execute("DELETE FROM suture_index")
        cur.execute("DELETE FROM tool_journal")
    elif cutoff_ts is not None:
        cur.execute("DELETE FROM suture_index WHERE timestamp > ?", (cutoff_ts,))
        cur.execute("DELETE FROM tool_journal WHERE updated_at > ?", (cutoff_ts,))

    if ancestor_msg_ids:
        ph_m = ",".join("?" for _ in ancestor_msg_ids)
        cur.execute(
            f"DELETE FROM message_shadows WHERE message_id NOT IN ({ph_m})",
            tuple(ancestor_msg_ids),
        )
        cur.execute(
            f"DELETE FROM cognitive_signatures WHERE message_id NOT IN ({ph_m})"
            + (
                f" AND cumulative_hash NOT IN ({','.join('?' for _ in ancestor_cumuls)})"
                if ancestor_cumuls
                else ""
            ),
            tuple(ancestor_msg_ids)
            + (tuple(ancestor_cumuls) if ancestor_cumuls else ()),
        )
    elif cutoff_ts is not None:
        cur.execute("DELETE FROM message_shadows WHERE updated_at > ?", (cutoff_ts,))
        cur.execute(
            "DELETE FROM cognitive_signatures WHERE updated_at > ?", (cutoff_ts,)
        )

    if cutoff_ts is not None:
        cur.execute("DELETE FROM rich_payloads WHERE created_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM cognitive_threads WHERE updated_at > ?", (cutoff_ts,))
        if ancestor_msg_ids:
            ph_m = ",".join("?" for _ in ancestor_msg_ids)
            cur.execute(
                f"DELETE FROM processed_files WHERE timestamp > ? "
                f"AND (message_id IS NULL OR message_id NOT IN ({ph_m}))",
                (cutoff_ts, *tuple(ancestor_msg_ids)),
            )
            cur.execute(
                f"DELETE FROM echo_resources WHERE created_at > ? "
                f"AND (message_id IS NULL OR message_id NOT IN ({ph_m}))",
                (cutoff_ts, *tuple(ancestor_msg_ids)),
            )
        else:
            cur.execute("DELETE FROM processed_files WHERE timestamp > ?", (cutoff_ts,))
            cur.execute("DELETE FROM echo_resources WHERE created_at > ?", (cutoff_ts,))
        if kept_call_ids:
            ph_k = ",".join("?" for _ in kept_call_ids)
            cur.execute(
                f"DELETE FROM call_bridge WHERE timestamp > ? AND call_id NOT IN ({ph_k})",
                (cutoff_ts, *tuple(kept_call_ids)),
            )
        else:
            cur.execute("DELETE FROM call_bridge WHERE timestamp > ?", (cutoff_ts,))
        cur.execute(
            "DELETE FROM auth_data WHERE key LIKE 'cascade_%' AND updated_at > ?",
            (cutoff_ts,),
        )

    return cutoff_ts, True


def rewrite_symlinks(root_dir: str, orig_cid: str, new_cid: str) -> None:
    """Réécrit les liens symboliques pointant vers le conteneur de session parent."""
    orig_marker = f"_C_{orig_cid}_T_"
    new_marker = f"_C_{new_cid}_T_"
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for name in dirnames + filenames:
            full_path = os.path.join(dirpath, name)
            if os.path.islink(full_path):
                try:
                    target = os.readlink(full_path)
                    new_target = (
                        target.replace(f"/chats/{orig_cid}/", f"/chats/{new_cid}/")
                        .replace(f"\\chats\\{orig_cid}\\", f"\\chats\\{new_cid}\\")
                        .replace(orig_marker, new_marker)
                    )
                    if new_target != target:
                        os.unlink(full_path)
                        os.symlink(new_target, full_path)
                except Exception:
                    pass


def rename_cloned_files(files_dir: str, orig_cid: str, new_cid: str) -> None:
    """Renomme les fichiers physiques dans files/ contenant _C_{orig_cid}_T_."""
    if not os.path.exists(files_dir):
        return
    orig_marker = f"_C_{orig_cid}_T_"
    new_marker = f"_C_{new_cid}_T_"
    for entry in os.listdir(files_dir):
        if orig_marker in entry:
            old_p = os.path.join(files_dir, entry)
            new_p = os.path.join(files_dir, entry.replace(orig_marker, new_marker))
            try:
                os.rename(old_p, new_p)
            except Exception:
                pass


def rewrite_codex_plan_frontmatter(
    codex_dir: str, orig_cid: str, new_cid: str
) -> None:
    """Met à jour chat_id dans le frontmatter YAML des plans du Codex."""
    main_dir = os.path.join(codex_dir, CODEX_WORKSPACE_MAIN)
    if not os.path.exists(main_dir):
        return
    old_line = f"chat_id: {orig_cid}"
    new_line = f"chat_id: {new_cid}"
    for entry in os.listdir(main_dir):
        if not entry.endswith(".md"):
            continue
        fp = os.path.join(main_dir, entry)
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            if old_line in content:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(content.replace(old_line, new_line, 1))
        except Exception:
            pass


def _extract_embedded_ts_sec(filename: str) -> Optional[int]:
    """Extrait le timestamp en secondes depuis un nom de fichier ECHO U_{uid}_C_{cid}_T_{ts_ms}_..."""
    m = re.search(r"_T_(\d{10,16})(?:_|\.|$)", filename)
    if not m:
        return None
    raw = int(m.group(1))
    return raw // 1000 if raw >= 10**11 else raw


def _collect_kept_domain_markers(cur: sqlite3.Cursor) -> Tuple[Set[str], Set[str]]:
    """Collecte depuis session.db (élaguée) les marqueurs de fichiers et d'identifiants de ressources conservés."""
    kept_markers: Set[str] = set()
    kept_wf_ids: Set[str] = set()
    try:
        cur.execute("SELECT id, name, resource_type, storage_path FROM echo_resources")
        for row in cur.fetchall():
            rid = str(row["id"] or "")
            rname = str(row["name"] or "")
            rtype = str(row["resource_type"] or "")
            spath = str(row["storage_path"] or "")
            if rid:
                kept_markers.add(rid)
            if rname:
                kept_markers.add(os.path.basename(rname))
            if spath:
                kept_markers.add(os.path.basename(spath))
            if rtype == "n8n_workflow" and rid:
                kept_wf_ids.add(rid)
    except Exception:
        pass
    try:
        cur.execute("SELECT file_id, filename FROM processed_files")
        for row in cur.fetchall():
            fid = str(row["file_id"] or "")
            fname = str(row["filename"] or "")
            if fid:
                kept_markers.add(fid)
            if fname:
                kept_markers.add(os.path.basename(fname))
    except Exception:
        pass
    return kept_markers, kept_wf_ids


def _prune_domain_files_to_cutoff(
    domain: str,
    domain_dir: str,
    cutoff_ts: int,
    kept_markers: Set[str],
    kept_wf_ids: Set[str],
) -> None:
    """Supprime du répertoire copié les fichiers créés après cutoff_ts lors d'un fork partiel."""
    if not os.path.exists(domain_dir):
        return

    if domain == "files":
        for entry in list(os.listdir(domain_dir)):
            full_p = os.path.join(domain_dir, entry)
            if any(entry == m or entry.startswith(f"{m}_") for m in kept_markers if m):
                continue
            emb_ts = _extract_embedded_ts_sec(entry)
            if emb_ts is not None:
                if emb_ts > cutoff_ts:
                    try:
                        if os.path.isdir(full_p) and not os.path.islink(full_p):
                            shutil.rmtree(full_p)
                        else:
                            os.unlink(full_p)
                    except Exception:
                        pass
                continue
            try:
                st = os.lstat(full_p)
                if int(st.st_mtime) > cutoff_ts + 1:
                    if os.path.isdir(full_p) and not os.path.islink(full_p):
                        shutil.rmtree(full_p)
                    else:
                        os.unlink(full_p)
            except Exception:
                pass

    elif domain == "n8n_workflows":
        for entry in list(os.listdir(domain_dir)):
            full_p = os.path.join(domain_dir, entry)
            wf_stem = os.path.splitext(entry)[0]
            if wf_stem in kept_wf_ids or entry in kept_markers:
                continue
            try:
                st = os.lstat(full_p)
                if int(st.st_mtime) > cutoff_ts + 1 or kept_wf_ids is not None:
                    if os.path.isfile(full_p) or os.path.islink(full_p):
                        os.unlink(full_p)
            except Exception:
                pass

    elif domain == CODEX_DIR_NAME:
        sandbox_dir = os.path.join(domain_dir, CODEX_WORKSPACE_SANDBOX)
        if os.path.exists(sandbox_dir):
            for dirpath, dirnames, filenames in os.walk(sandbox_dir, topdown=False):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    if fn in kept_markers:
                        continue
                    try:
                        if int(os.lstat(fp).st_mtime) > cutoff_ts + 1:
                            os.unlink(fp)
                    except Exception:
                        pass
                for dn in dirnames:
                    dp = os.path.join(dirpath, dn)
                    try:
                        if not os.path.islink(dp) and not os.listdir(dp):
                            os.rmdir(dp)
                    except Exception:
                        pass


def align_codex_git_to_cutoff(
    user_id: str, new_cid: str, cutoff_ts: int, new_db: str
) -> None:
    """Aligne le dépôt Git Codex 'main' sur le commit <= cutoff_ts et synchronise echo_resources."""
    try:
        from echo_codex_git import CodexRepo

        codex_main_dir = os.path.join(
            get_echo_session_path(user_id, new_cid, CODEX_DIR_NAME), CODEX_WORKSPACE_MAIN
        )
        if not os.path.exists(os.path.join(codex_main_dir, ".git")):
            return

        repo = CodexRepo(user_id=user_id, chat_id=new_cid, workspace=CODEX_WORKSPACE_MAIN)
        res = repo.restore_repo_to_timestamp(cutoff_ts)
        tracked_files = res.get("tracked_files") or set()
        restored_sha = res.get("restored_commit")
        short_sha = restored_sha[:12] if restored_sha else None

        with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
            cur = conn.cursor()
            if not tracked_files:
                cur.execute(
                    "DELETE FROM echo_resources WHERE resource_type IN ('codex', 'plan') AND git_tracked = 1"
                )
            else:
                cur.execute(
                    "SELECT id, name, resource_type, storage_path FROM echo_resources "
                    "WHERE resource_type IN ('codex', 'plan') AND git_tracked = 1"
                )
                for r_id, r_name, r_type, r_spath in cur.fetchall():
                    rel_fn = r_name if r_type == "codex" else os.path.basename(r_spath or r_name or "")
                    if rel_fn not in tracked_files:
                        cur.execute("DELETE FROM echo_resources WHERE id = ?", (r_id,))
                    else:
                        fp = os.path.join(codex_main_dir, rel_fn)
                        if os.path.isfile(fp):
                            try:
                                with open(
                                    fp, "r", encoding="utf-8", errors="replace"
                                ) as f:
                                    txt = f.read()
                                line_cnt = txt.count("\n") + (1 if txt and not txt.endswith("\n") else 0)
                                status_match = (
                                    re.search(r"^status:\s*(\S+)", txt, re.MULTILINE)
                                    if r_type == "plan"
                                    else None
                                )
                                restored_status = status_match.group(1) if status_match else None
                                cur.execute(
                                    "UPDATE echo_resources SET lines = ?, "
                                    "status = COALESCE(?, status), "
                                    "last_commit = COALESCE(?, last_commit) WHERE id = ?",
                                    (line_cnt, restored_status, short_sha, r_id),
                                )
                            except Exception:
                                pass
            conn.commit()
    except Exception as e:
        logger.warning(f"[ECHO-SESSION-CLONE] ⚠️ Alignement Git Codex ignoré : {e}")


def duplicate_session_domains(
    user_id: str,
    orig_cid: str,
    new_cid: str,
    cutoff_ts: Optional[int] = None,
    is_partial_fork: bool = False,
    kept_markers: Optional[Set[str]] = None,
    kept_wf_ids: Optional[Set[str]] = None,
    new_db: Optional[str] = None,
) -> None:
    """Duplique les répertoires de l'Espace Utilisateur (files, codex, n8n_workflows, dependencies),
    limite leur contenu à cutoff_ts si is_partial_fork=True, et réajuste orig_cid -> new_cid."""
    for domain in ("files", CODEX_DIR_NAME, "n8n_workflows", "dependencies"):
        s_dom = get_echo_session_path(user_id, orig_cid, domain)
        d_dom = get_echo_session_path(user_id, new_cid, domain)
        if os.path.exists(s_dom):
            shutil.copytree(s_dom, d_dom, symlinks=True, dirs_exist_ok=True)
            if is_partial_fork and cutoff_ts is not None:
                if domain == CODEX_DIR_NAME and new_db:
                    align_codex_git_to_cutoff(user_id, new_cid, cutoff_ts, new_db)
                _prune_domain_files_to_cutoff(
                    domain,
                    d_dom,
                    cutoff_ts,
                    kept_markers or set(),
                    kept_wf_ids or set(),
                )
            rewrite_symlinks(d_dom, orig_cid, new_cid)
            if domain in ("files", "n8n_workflows"):
                rename_cloned_files(d_dom, orig_cid, new_cid)
            elif domain == CODEX_DIR_NAME:
                rewrite_codex_plan_frontmatter(d_dom, orig_cid, new_cid)


def sync_cloned_session(
    user_id: str,
    orig_cid: str,
    new_cid: str,
    forked_msg_id: Optional[str] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Tuple[Optional[int], bool]:
    """Synchronise un clone ou fork de session (utilisé par echo_chat_cloned_event).
    1. Copie session.db et élague l'historique à l'état du message cloné (forked_msg_id).
    2. Duplique les répertoires de l'Espace Utilisateur limités à cutoff_ts (et restaure Git Codex).
    3. Réajuste chat_id (orig_cid -> new_cid) dans SQLite et sur le système de fichiers.
    Retourne (cutoff_ts, is_partial_fork)."""
    orig_db = get_echo_session_path(user_id, orig_cid, "db")
    new_db = get_echo_session_path(user_id, new_cid, "db")

    if progress_cb:
        progress_cb(20, "📋 Copie de la base SQLite et analyse du point de fork...")

    if not backup_session_db(orig_db, new_db):
        return None, False

    cutoff_ts = None
    is_partial_fork = False
    kept_markers: Set[str] = set()
    kept_wf_ids: Set[str] = set()

    with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        if forked_msg_id:
            cutoff_ts, is_partial_fork = prune_forked_history(cur, forked_msg_id)

        if is_partial_fork and cutoff_ts is not None:
            cur.execute(
                "DELETE FROM echo_resources WHERE resource_type IN ('aec_event', 'aec_directive') AND created_at > ?",
                (cutoff_ts,),
            )
            cur.execute(
                "DELETE FROM echo_settings WHERE key IN "
                "('context_stats', 'previous_context_stats', 'turn_origin_model')"
            )
            cur.execute(
                "DELETE FROM echo_settings WHERE key = 'context_checkpoint' AND updated_at > ?",
                (cutoff_ts,),
            )
            cur.execute(
                "SELECT model_id FROM cognitive_signatures "
                "WHERE model_id IS NOT NULL AND model_id != '' "
                "ORDER BY updated_at DESC, rowid DESC LIMIT 1"
            )
            last_sig_model = cur.fetchone()
            if last_sig_model and last_sig_model["model_id"]:
                cur.execute(
                    "INSERT OR REPLACE INTO echo_settings (key, value, updated_at) VALUES ('last_model_id', ?, ?)",
                    (last_sig_model["model_id"], int(cutoff_ts)),
                )
            else:
                cur.execute("DELETE FROM echo_settings WHERE key = 'last_model_id'")

            if forked_msg_id:
                cur.execute(
                    "UPDATE message_shadows SET is_embedded = 0 WHERE message_id = ?",
                    (forked_msg_id,),
                )

            kept_markers, kept_wf_ids = _collect_kept_domain_markers(cur)

        conn.commit()

    if progress_cb:
        progress_cb(55, "📁 Copie de l'Espace Utilisateur et alignement du Codex Git...")

    duplicate_session_domains(
        user_id,
        orig_cid,
        new_cid,
        cutoff_ts=cutoff_ts,
        is_partial_fork=is_partial_fork,
        kept_markers=kept_markers,
        kept_wf_ids=kept_wf_ids,
        new_db=new_db,
    )

    if progress_cb:
        progress_cb(75, "🔗 Réajustement des identifiants de session...")

    with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        reassign_session_ids(cur, orig_cid, new_cid)
        conn.commit()

    try:
        from echo_window_manager import get_window_manager

        wm = get_window_manager()
        wm._chat_states.pop((user_id, new_cid), None)
        wm._chat_states.pop(("system", new_cid), None)
    except Exception:
        pass

    return cutoff_ts, is_partial_fork


def sync_resumed_session(
    user_id: str,
    orig_cid: str,
    new_cid: str,
    initial_embedded_msg_ids: Optional[List[str]] = None,
) -> None:
    """Duplique le conteneur de session pour une migration vers une nouvelle conversation
    (purge l'historique conversationnel et préserve les ressources)."""
    orig_db = get_echo_session_path(user_id, orig_cid, "db")
    new_db = get_echo_session_path(user_id, new_cid, "db")

    if backup_session_db(orig_db, new_db):
        with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            reassign_session_ids(cur, orig_cid, new_cid)
            purge_conversational_state(cur)
            if initial_embedded_msg_ids:
                for mid in initial_embedded_msg_ids:
                    if mid:
                        cur.execute(
                            "INSERT OR REPLACE INTO message_shadows "
                            "(message_id, chat_id, role, full_parts_json, updated_at, is_embedded) "
                            "VALUES (?, ?, 'assistant', '[]', 0, 1)",
                            (mid, new_cid),
                        )
            conn.commit()
    elif initial_embedded_msg_ids:
        try:
            from echo_state_manager import EchoStateManager

            state_mgr = EchoStateManager(user_id=user_id, chat_id=new_cid)
            for mid in initial_embedded_msg_ids:
                if mid:
                    state_mgr.mark_message_embedded(mid)
        except Exception:
            pass

    duplicate_session_domains(user_id, orig_cid, new_cid)


async def duplicate_session_rag(
    user_id: str,
    orig_cid: str,
    new_cid: str,
    cutoff_ts: Optional[int] = None,
) -> int:
    """Duplique par pagination (lots de 256 points) les vecteurs Qdrant de COLLECTION_SESSION_RAG
    de orig_cid vers new_cid. Si cutoff_ts est fourni, filtre uniquement timestamp <= cutoff_ts
    et réajuste _C_{orig_cid}_T_ -> _C_{new_cid}_T_. Ne touche jamais à COLLECTION_META_ARTIFACTS."""
    scroll_url = (
        f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/scroll"
    )
    upsert_url = (
        f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points?wait=true"
    )

    total_cloned = 0
    next_offset = None
    orig_marker = f"_C_{orig_cid}_T_"
    new_marker = f"_C_{new_cid}_T_"

    must_conditions = [
        {"key": "user_id", "match": {"value": user_id}},
        {"key": "chat_id", "match": {"value": orig_cid}},
    ]
    if cutoff_ts is not None:
        must_conditions.append({"key": "timestamp", "range": {"lte": int(cutoff_ts)}})

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            scroll_payload = {
                "filter": {"must": must_conditions},
                "limit": 256,
                "with_payload": True,
                "with_vector": True,
            }
            if next_offset is not None:
                scroll_payload["offset"] = next_offset

            resp = await client.post(scroll_url, json=scroll_payload)
            if resp.status_code != 200:
                break

            result = resp.json().get("result") or {}
            points = result.get("points") or []
            if not points:
                break

            new_points = []
            for pt in points:
                vec = pt.get("vector")
                if not vec:
                    continue
                payload = dict(pt.get("payload") or {})
                if cutoff_ts is not None:
                    pt_ts = payload.get("timestamp")
                    if isinstance(pt_ts, (int, float)) and int(pt_ts) > int(cutoff_ts):
                        continue
                payload["chat_id"] = new_cid
                if (
                    isinstance(payload.get("source_id"), str)
                    and orig_marker in payload["source_id"]
                ):
                    payload["source_id"] = payload["source_id"].replace(
                        orig_marker, new_marker
                    )
                if (
                    isinstance(payload.get("text"), str)
                    and orig_marker in payload["text"]
                ):
                    payload["text"] = payload["text"].replace(orig_marker, new_marker)
                new_points.append(
                    {
                        "id": str(
                            uuid.uuid5(uuid.NAMESPACE_DNS, f"{new_cid}_{pt.get('id')}")
                        ),
                        "vector": vec,
                        "payload": payload,
                    }
                )

            if new_points:
                up_resp = await client.put(upsert_url, json={"points": new_points})
                if up_resp.status_code in (200, 201):
                    total_cloned += len(new_points)

            next_offset = result.get("next_page_offset")
            if next_offset is None:
                break

    return total_cloned
