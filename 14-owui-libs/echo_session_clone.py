"""
title: ECHO Session Clone & Migration Engine
author: Wilfried BARNAVON
version: 5.239.13.1
description: Moteur partagé de duplication atomique des conteneurs de session ECHO (Clone, Fork et Migration).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.13.1: duplicate_session_rag fiabilisé : retries bornés (_qdrant_call : réseau, 429, 5xx), upsert par lots de 64, comptage de contrôle exact et retour structuré RagCloneReport.
# 5.239.12.1: Purge de wm_state dans purge_conversational_state, ré-ancrage des ressources actives (_reanchor_resumed_resources) et des vecteurs conversationnels RAG (anchor_msg_id).
# 5.237.20.3: Alignement généalogique strict de ancestor_msg_ids sur live_msg_ids dans prune_forked_history.
# 5.237.20.2: Simplification de prune_forked_history et sync_cloned_session sur message_shadows v2 (support tuple/Row et lecture directe model_id).
# 5.235.5.2: Purge arborescente exacte via compute_dead_set + purge_dead_set, support live_ids dans sync_cloned_session et duplication RAG filtrée.

from contextlib import closing
import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import time
from typing import Callable, List, NamedTuple, Optional, Set, Tuple
import uuid

from echo_constants import (
    CODEX_DIR_NAME,
    CODEX_WORKSPACE_MAIN,
    CODEX_WORKSPACE_PLANS,
    COLLECTION_SESSION_RAG,
    ECHO_CODEX_WORKSPACES,
    ECHO_QDRANT_URL,
    SESSION_RAG_CONVERSATION_SOURCE_ID,
    is_codex_workspace_versioned,
)
from echo_paths import get_echo_session_path
from echo_session_gc import (
    compute_dead_set,
    extract_call_ids_from_json as _extract_call_ids_from_json,
    is_point_in_branch,
    purge_dead_set,
)
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
                "message_shadows",
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
    """Réassigne les identifiants composites _C_{cid}_T_ et les chemins physiques dans SQLite."""
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

    for tbl, col in (
        ("message_shadows", "full_parts_json"),
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
            ("message_shadows", "full_parts_json"),
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
        "message_shadows",
        "rag_index_state",
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
            "'last_active_model', 'turn_origin_model', 'compaction_state', "
            "'context_checkpoint', 'wm_state') "
            "OR key LIKE 'aec_tier_%' OR key LIKE 'cascade_%'"
        )
    except Exception:
        pass


def _reanchor_resumed_resources(
    cur: sqlite3.Cursor,
    live_msg_ids: Optional[Set[str]],
    anchor_msg_id: Optional[str],
) -> None:
    """Rattache au message d'ancrage de la session migrée les ressources de la branche active."""
    if not anchor_msg_id:
        return
    try:
        cur.execute(
            "SELECT id, message_id, metadata FROM echo_resources "
            "WHERE message_id IS NOT NULL AND message_id != ''"
        )
        rows = cur.fetchall()
        for row in rows:
            rid = row["id"] if isinstance(row, (sqlite3.Row, dict)) else row[0]
            mid = row["message_id"] if isinstance(row, (sqlite3.Row, dict)) else row[1]
            raw_meta = row["metadata"] if isinstance(row, (sqlite3.Row, dict)) else row[2]
            if live_msg_ids is not None and str(mid) not in live_msg_ids:
                continue
            new_meta = raw_meta
            if isinstance(raw_meta, str) and raw_meta.strip():
                try:
                    meta_obj = json.loads(raw_meta)
                    if isinstance(meta_obj, dict) and "message_id" in meta_obj:
                        meta_obj["message_id"] = anchor_msg_id
                        new_meta = json.dumps(meta_obj, ensure_ascii=False)
                except Exception:
                    pass
            cur.execute(
                "UPDATE echo_resources SET message_id = ?, metadata = ? WHERE id = ?",
                (anchor_msg_id, new_meta, rid),
            )
    except Exception:
        pass


def prune_forked_history(
    cur: sqlite3.Cursor,
    forked_msg_id: str,
    live_msg_ids: Optional[Set[str]] = None,
) -> Tuple[Optional[int], bool]:
    """Résout la chaîne causale jusqu'à forked_msg_id et élague les tables SQLite si le fork est partiel.
    Retourne (cutoff_ts, is_partial_fork)."""
    cur.execute(
        "SELECT message_id, role, full_parts_json, updated_at FROM message_shadows"
    )
    raw_rows = cur.fetchall()
    shadow_by_id = {}
    for r in raw_rows:
        if isinstance(r, (sqlite3.Row, dict)):
            mid, role, fpj, uat = r["message_id"], r["role"], r["full_parts_json"], r["updated_at"]
        else:
            mid, role, fpj, uat = r[0], r[1], r[2], r[3]
        if mid:
            shadow_by_id[str(mid)] = {
                "message_id": str(mid),
                "role": role,
                "full_parts_json": fpj,
                "updated_at": uat,
            }
    all_shadow_rows = list(shadow_by_id.values())

    target_shadow = shadow_by_id.get(forked_msg_id)
    is_user_only_fork = (
        target_shadow is not None and target_shadow["role"] == "user"
    )

    cutoff_candidates: List[int] = []
    if target_shadow and target_shadow["updated_at"]:
        cutoff_candidates.append(int(target_shadow["updated_at"]))

    if not is_user_only_fork:
        target_call_ids: Set[str] = (
            _extract_call_ids_from_json(target_shadow["full_parts_json"])
            if (target_shadow and target_shadow["full_parts_json"])
            else set()
        )
        if target_call_ids:
            ph_cb = ",".join("?" for _ in target_call_ids)
            cur.execute(
                f"SELECT MAX(timestamp) as max_cb FROM call_bridge WHERE message_id = ? OR call_id IN ({ph_cb})",
                (forked_msg_id, *tuple(target_call_ids)),
            )
        else:
            cur.execute(
                "SELECT MAX(timestamp) as max_cb FROM call_bridge WHERE message_id = ?",
                (forked_msg_id,),
            )
        row_cb = cur.fetchone()
        if row_cb:
            val_cb = row_cb["max_cb"] if (isinstance(row_cb, sqlite3.Row) or isinstance(row_cb, dict)) else row_cb[0]
            if val_cb is not None:
                cutoff_candidates.append(int(val_cb))

    if not cutoff_candidates:
        return None, False

    cutoff_ts = max(cutoff_candidates)

    if live_msg_ids is not None:
        ancestor_msg_ids: Set[str] = {str(m) for m in live_msg_ids if m} | {forked_msg_id}
    else:
        ancestor_msg_ids = {forked_msg_id}
        for sh_r in all_shadow_rows:
            sh_mid = sh_r["message_id"]
            sh_ts = int(sh_r["updated_at"] or 0)
            if (
                is_user_only_fork
                and sh_r["role"] != "user"
                and sh_ts >= cutoff_ts
                and sh_mid != forked_msg_id
            ):
                continue
            elif sh_ts <= cutoff_ts and sh_mid:
                ancestor_msg_ids.add(sh_mid)

    kept_call_ids: Set[str] = set()
    for mid in ancestor_msg_ids:
        sh_row = shadow_by_id.get(mid)
        if sh_row and sh_row["full_parts_json"]:
            kept_call_ids |= _extract_call_ids_from_json(sh_row["full_parts_json"])

    has_unkept_shadows = any(
        sh_r["message_id"] not in ancestor_msg_ids for sh_r in all_shadow_rows
    )

    if live_msg_ids is not None:
        effective_live = set(live_msg_ids)
        dead = compute_dead_set(cur.connection, effective_live, float("inf"))
        is_partial_fork = bool(
            dead.message_ids
            or dead.call_ids
            or dead.rag_seeds
            or has_unkept_shadows
        )
        if not is_partial_fork:
            return cutoff_ts, False

        purge_dead_set(cur.connection, dead, dry_run=False)

        kept_call_ids -= dead.call_ids
        if cutoff_ts is not None:
            cur.execute("DELETE FROM cognitive_threads WHERE updated_at > ?", (cutoff_ts,))
            if effective_live:
                ph_m = ",".join("?" for _ in effective_live)
                cur.execute(
                    f"DELETE FROM echo_resources WHERE created_at > ? "
                    f"AND (message_id IS NULL OR message_id NOT IN ({ph_m}))",
                    (cutoff_ts, *tuple(effective_live)),
                )
            else:
                cur.execute("DELETE FROM echo_resources WHERE created_at > ?", (cutoff_ts,))
            if kept_call_ids:
                ph_k = ",".join("?" for _ in kept_call_ids)
                cur.execute(
                    f"DELETE FROM call_bridge WHERE timestamp > ? AND call_id NOT IN ({ph_k})",
                    (cutoff_ts, *tuple(kept_call_ids)),
                )
            else:
                cur.execute("DELETE FROM call_bridge WHERE timestamp > ?", (cutoff_ts,))
        return cutoff_ts, True

    if not has_unkept_shadows:
        return cutoff_ts, False

    if ancestor_msg_ids:
        ph_m = ",".join("?" for _ in ancestor_msg_ids)
        cur.execute(
            f"DELETE FROM message_shadows WHERE message_id NOT IN ({ph_m})",
            tuple(ancestor_msg_ids),
        )
        cur.execute(
            f"DELETE FROM rag_index_state WHERE message_id NOT IN ({ph_m})",
            tuple(ancestor_msg_ids),
        )
    elif cutoff_ts is not None:
        cur.execute("DELETE FROM message_shadows WHERE updated_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM rag_index_state WHERE embedded_at > ?", (cutoff_ts,))

    if cutoff_ts is not None:
        cur.execute("DELETE FROM cognitive_threads WHERE updated_at > ?", (cutoff_ts,))
        if ancestor_msg_ids:
            ph_m = ",".join("?" for _ in ancestor_msg_ids)
            cur.execute(
                f"DELETE FROM echo_resources WHERE created_at > ? "
                f"AND (message_id IS NULL OR message_id NOT IN ({ph_m}))",
                (cutoff_ts, *tuple(ancestor_msg_ids)),
            )
        else:
            cur.execute("DELETE FROM echo_resources WHERE created_at > ?", (cutoff_ts,))
        if kept_call_ids:
            ph_k = ",".join("?" for _ in kept_call_ids)
            cur.execute(
                f"DELETE FROM call_bridge WHERE timestamp > ? AND call_id NOT IN ({ph_k})",
                (cutoff_ts, *tuple(kept_call_ids)),
            )
        else:
            cur.execute("DELETE FROM call_bridge WHERE timestamp > ?", (cutoff_ts,))

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
    """Met à jour chat_id dans le frontmatter YAML des plans du Codex (codex/plans)."""
    plans_dir = os.path.join(codex_dir, CODEX_WORKSPACE_PLANS)
    if not os.path.exists(plans_dir):
        return
    old_line = f"chat_id: {orig_cid}"
    new_line = f"chat_id: {new_cid}"
    for entry in os.listdir(plans_dir):
        if not entry.endswith(".md"):
            continue
        fp = os.path.join(plans_dir, entry)
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
        for ws_name in ECHO_CODEX_WORKSPACES:
            if is_codex_workspace_versioned(ws_name):
                continue
            untracked_dir = os.path.join(domain_dir, ws_name)
            if os.path.exists(untracked_dir):
                for dirpath, dirnames, filenames in os.walk(untracked_dir, topdown=False):
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
    """Aligne tous les dépôts Git Codex versionnés ('main', 'plans', 'generated') sur le commit <= cutoff_ts et synchronise echo_resources."""
    try:
        from echo_codex_git import CodexRepo

        base_codex = get_echo_session_path(user_id, new_cid, CODEX_DIR_NAME)
        ws_tracked: dict[str, set[str]] = {}
        ws_short_sha: dict[str, Optional[str]] = {}

        for ws_name in ECHO_CODEX_WORKSPACES:
            if not is_codex_workspace_versioned(ws_name):
                continue
            ws_dir = os.path.join(base_codex, ws_name)
            if not os.path.exists(os.path.join(ws_dir, ".git")):
                ws_tracked[ws_name] = set()
                ws_short_sha[ws_name] = None
                continue
            repo = CodexRepo(user_id=user_id, chat_id=new_cid, workspace=ws_name)
            res = repo.restore_repo_to_timestamp(cutoff_ts)
            ws_tracked[ws_name] = res.get("tracked_files") or set()
            restored_sha = res.get("restored_commit")
            ws_short_sha[ws_name] = restored_sha[:12] if restored_sha else None

        with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, resource_type, storage_path FROM echo_resources "
                "WHERE resource_type IN ('codex', 'plan', 'generated') AND git_tracked = 1"
            )
            for r_id, r_name, r_type, r_spath in cur.fetchall():
                spath = str(r_spath or "")
                target_ws = CODEX_WORKSPACE_MAIN
                rel_fn = str(r_name or "")
                if spath.startswith(f"{CODEX_DIR_NAME}/"):
                    parts = spath.split("/", 2)
                    if len(parts) >= 3 and parts[1] in ECHO_CODEX_WORKSPACES:
                        target_ws = parts[1]
                        rel_fn = parts[2]
                elif r_type == "plan":
                    target_ws = CODEX_WORKSPACE_PLANS
                    rel_fn = os.path.basename(spath or r_name or "")

                tracked_files = ws_tracked.get(target_ws, set())
                short_sha = ws_short_sha.get(target_ws)

                if rel_fn not in tracked_files:
                    cur.execute("DELETE FROM echo_resources WHERE id = ?", (r_id,))
                else:
                    fp = os.path.join(base_codex, target_ws, rel_fn)
                    if os.path.isfile(fp) and r_type in ("codex", "plan"):
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
                    elif os.path.isfile(fp) and r_type == "generated":
                        cur.execute(
                            "UPDATE echo_resources SET last_commit = COALESCE(?, last_commit) WHERE id = ?",
                            (short_sha, r_id),
                        )
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
    live_msg_ids: Optional[Set[str]] = None,
    forked_from_msg_id: Optional[str] = None,
) -> Tuple[Optional[int], bool]:
    """Synchronise un clone ou fork de session (utilisé par echo_chat_cloned_event).
    1. Copie session.db et élague l'historique à l'état du message cloné (forked_msg_id).
    2. Duplique les répertoires de l'Espace Utilisateur limités à cutoff_ts (et restaure Git Codex).
    3. Réajuste chat_id (orig_cid -> new_cid) dans SQLite et sur le système de fichiers.
    Retourne (cutoff_ts, is_partial_fork)."""
    target_fork_id = forked_msg_id or forked_from_msg_id
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

        if target_fork_id:
            cutoff_ts, is_partial_fork = prune_forked_history(
                cur, target_fork_id, live_msg_ids=live_msg_ids
            )

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
                "SELECT model_id FROM message_shadows "
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

            if target_fork_id:
                cur.execute(
                    "DELETE FROM rag_index_state WHERE message_id = ?",
                    (target_fork_id,),
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
    live_msg_ids: Optional[Set[str]] = None,
    anchor_msg_id: Optional[str] = None,
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
            _reanchor_resumed_resources(cur, live_msg_ids, anchor_msg_id)
            if initial_embedded_msg_ids:
                now_ts = int(time.time())
                for mid in initial_embedded_msg_ids:
                    if mid:
                        cur.execute(
                            "INSERT OR IGNORE INTO rag_index_state (message_id, embedded_at) VALUES (?, ?)",
                            (mid, now_ts),
                        )
            conn.commit()
    elif initial_embedded_msg_ids:
        logger.info(
            f"[ECHO-SESSION-CLONE] Base source absente pour {new_cid}, "
            "ré-indexation RAG idempotente au prochain tour."
        )

    duplicate_session_domains(user_id, orig_cid, new_cid)
    try:
        from echo_window_manager import get_window_manager

        wm = get_window_manager()
        for cid_target in (orig_cid, new_cid):
            wm._chat_states.pop((user_id, cid_target), None)
            wm._chat_states.pop(("system", cid_target), None)
    except Exception:
        pass


# Paramètres de robustesse du clonage SESSION_RAG
_RAG_CLONE_SCROLL_LIMIT = 256
_RAG_CLONE_UPSERT_BATCH = 64
_RAG_CLONE_ATTEMPTS = 3


class RagCloneReport(NamedTuple):
    """Bilan de duplication des vecteurs COLLECTION_SESSION_RAG."""
    cloned: int      # Points écrits avec succès dans la session cible
    eligible: int    # Points retenus après filtrage de branche / cutoff
    scanned: int     # Points lus dans la session source
    failed: int      # Points éligibles non écrits (échec définitif d'upsert)
    complete: bool   # Scroll intégral, aucun échec et comptage de contrôle cohérent
    error: str       # Dernière erreur rencontrée ("" si aucune)


def _qdrant_ok(resp: Optional[httpx.Response]) -> bool:
    """Vrai si la réponse Qdrant est un succès HTTP."""
    return resp is not None and resp.status_code in (200, 201)


async def _qdrant_call(
    client: httpx.AsyncClient, method: str, url: str, payload: dict
) -> Tuple[Optional[httpx.Response], str]:
    """Appel Qdrant avec retries bornés (backoff 1 s puis 2 s) sur erreurs réseau, 429 et 5xx.
    Les erreurs client non transitoires (400, 404) ne sont pas réessayées.
    Retourne (dernière réponse ou None, dernière erreur)."""
    resp, last_err = None, ""
    for attempt in range(_RAG_CLONE_ATTEMPTS):
        try:
            resp = await client.request(method, url, json=payload)
            if _qdrant_ok(resp):
                return resp, ""
            last_err = f"HTTP {resp.status_code} : {resp.text[:200]}"
            if resp.status_code != 429 and resp.status_code < 500:
                break
        except (httpx.TimeoutException, httpx.TransportError) as e:
            resp, last_err = None, f"{type(e).__name__} : {e}"
        if attempt < _RAG_CLONE_ATTEMPTS - 1:
            await asyncio.sleep(attempt + 1)
    return resp, last_err


async def duplicate_session_rag(
    user_id: str,
    orig_cid: str,
    new_cid: str,
    cutoff_ts: Optional[int] = None,
    live_msg_ids: Optional[Set[str]] = None,
    anchor_msg_id: Optional[str] = None,
) -> RagCloneReport:
    """Duplique par pagination les vecteurs Qdrant de COLLECTION_SESSION_RAG de orig_cid vers new_cid
    (retries bornés, upsert par lots, comptage de contrôle). Si cutoff_ts est fourni, filtre
    timestamp <= cutoff_ts ; réajuste _C_{orig_cid}_T_ -> _C_{new_cid}_T_.
    Ne touche jamais à COLLECTION_META_ARTIFACTS."""
    base_url = f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points"
    orig_marker = f"_C_{orig_cid}_T_"
    new_marker = f"_C_{new_cid}_T_"
    live_set = set(live_msg_ids) if live_msg_ids else None

    must_conditions = [
        {"key": "user_id", "match": {"value": user_id}},
        {"key": "chat_id", "match": {"value": orig_cid}},
    ]
    if not live_set and cutoff_ts is not None:
        must_conditions.append({"key": "timestamp", "range": {"lte": int(cutoff_ts)}})

    cloned = eligible = scanned = failed = 0
    scroll_ok, last_err = True, ""
    verified: Optional[int] = None
    next_offset = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            scroll_payload = {
                "filter": {"must": must_conditions},
                "limit": _RAG_CLONE_SCROLL_LIMIT,
                "with_payload": True,
                "with_vector": True,
            }
            if next_offset is not None:
                scroll_payload["offset"] = next_offset

            resp, err = await _qdrant_call(client, "POST", f"{base_url}/scroll", scroll_payload)
            if not _qdrant_ok(resp):
                if resp is not None and resp.status_code == 404 and scanned == 0:
                    break  # Collection absente : aucune donnée à cloner
                scroll_ok, last_err = False, err
                logger.warning(
                    f"[ECHO-SESSION-CLONE] Scroll SESSION_RAG interrompu ({orig_cid} -> {new_cid}) : {err}"
                )
                break

            result = resp.json().get("result") or {}
            points = result.get("points") or []
            if not points:
                break

            new_points = []
            for pt in points:
                scanned += 1
                vec = pt.get("vector")
                if not vec:
                    continue
                payload = dict(pt.get("payload") or {})
                if live_set:
                    if not is_point_in_branch(payload, live_set, cutoff_ts):
                        continue
                elif cutoff_ts is not None:
                    pt_ts = payload.get("timestamp")
                    if isinstance(pt_ts, (int, float)) and int(pt_ts) > int(cutoff_ts):
                        continue
                payload["chat_id"] = new_cid
                if anchor_msg_id and (
                    payload.get("source_id") == SESSION_RAG_CONVERSATION_SOURCE_ID
                    or payload.get("message_id")
                ):
                    payload["message_id"] = anchor_msg_id
                if isinstance(payload.get("source_id"), str) and orig_marker in payload["source_id"]:
                    payload["source_id"] = payload["source_id"].replace(orig_marker, new_marker)
                if isinstance(payload.get("text"), str) and orig_marker in payload["text"]:
                    payload["text"] = payload["text"].replace(orig_marker, new_marker)
                new_points.append({
                    "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{new_cid}_{pt.get('id')}")),
                    "vector": vec,
                    "payload": payload,
                })

            eligible += len(new_points)
            # Upsert par lots réduits : requêtes plus courtes, échec circonscrit au lot
            for i in range(0, len(new_points), _RAG_CLONE_UPSERT_BATCH):
                batch = new_points[i:i + _RAG_CLONE_UPSERT_BATCH]
                up_resp, up_err = await _qdrant_call(
                    client, "PUT", f"{base_url}?wait=true", {"points": batch}
                )
                if _qdrant_ok(up_resp):
                    cloned += len(batch)
                else:
                    failed += len(batch)
                    last_err = up_err
                    logger.warning(
                        f"[ECHO-SESSION-CLONE] Upsert SESSION_RAG en échec ({len(batch)} points, "
                        f"{orig_cid} -> {new_cid}) : {up_err}"
                    )

            next_offset = result.get("next_page_offset")
            if next_offset is None:
                break

        # Comptage de contrôle : présence effective des points dans la session cible
        if eligible:
            c_resp, c_err = await _qdrant_call(
                client,
                "POST",
                f"{base_url}/count",
                {
                    "exact": True,
                    "filter": {"must": [
                        {"key": "user_id", "match": {"value": user_id}},
                        {"key": "chat_id", "match": {"value": new_cid}},
                    ]},
                },
            )
            if _qdrant_ok(c_resp):
                verified = int((c_resp.json().get("result") or {}).get("count") or 0)
            elif not last_err:
                last_err = f"Comptage de contrôle indisponible : {c_err}"

    complete = scroll_ok and failed == 0 and (
        eligible == 0 or (verified is not None and verified >= eligible)
    )
    if eligible and verified is not None and verified < eligible and not last_err:
        last_err = f"Comptage de contrôle : {verified}/{eligible} points présents"

    summary = (
        f"[ECHO-SESSION-CLONE] SESSION_RAG {orig_cid} -> {new_cid} : {cloned}/{eligible} clonés, "
        f"{scanned} lus, {failed} en échec, contrôle={verified}, complet={complete}"
    )
    if complete:
        logger.info(summary)
    else:
        logger.warning(f"{summary} | {last_err}")
    return RagCloneReport(cloned, eligible, scanned, failed, complete, last_err)
