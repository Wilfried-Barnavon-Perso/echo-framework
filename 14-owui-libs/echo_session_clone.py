"""
title: ECHO Session Clone & Migration Engine
author: Wilfried BARNAVON
version: 5.228.4.1
description: Moteur partagé de duplication atomique des conteneurs de session ECHO (Clone, Fork et Migration).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.228.4.1: Mise à jour de echo_resources.name, réinitialisation du statut N8N deployed_as_*, purge de last_model_id, réécriture du frontmatter chat_id des plans Codex et pré-marquage is_embedded=1 des messages initiaux lors d'une migration.
# 5.227.1.1: Création du module partagé de duplication atomique de session (clone, fork et migration).

from contextlib import closing
from typing import List, Optional, Tuple
import logging
import os
import shutil
import sqlite3
import uuid

from echo_constants import COLLECTION_SESSION_RAG, ECHO_QDRANT_URL
from echo_paths import get_echo_session_path
import httpx

logger = logging.getLogger("ECHO-SESSION-CLONE")


def backup_session_db(orig_db: str, new_db: str) -> bool:
    """Copie atomique de la base SQLite session.db via l'API backup native (immune au mode WAL)."""
    if not os.path.exists(orig_db) or os.path.exists(new_db):
        return False
    os.makedirs(os.path.dirname(new_db), exist_ok=True)
    with closing(sqlite3.connect(orig_db, timeout=15.0)) as src, closing(
        sqlite3.connect(new_db, timeout=15.0)
    ) as dst:
        src.backup(dst)
    return True


def reassign_session_ids(cur: sqlite3.Cursor, orig_cid: str, new_cid: str) -> None:
    """Réassigne chat_id, les identifiants composites _C_{cid}_T_ et les chemins physiques dans SQLite."""
    for tbl in ("suture_index", "message_shadows", "cognitive_threads", "processed_files"):
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
    except Exception:
        pass

    try:
        cur.execute(
            "UPDATE processed_files SET file_id = REPLACE(file_id, ?, ?) WHERE file_id LIKE ?",
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

    try:
        cur.execute(
            "UPDATE echo_resources SET status = 'ready' "
            "WHERE resource_type = 'n8n_workflow' AND status LIKE 'deployed_as_%'"
        )
    except Exception:
        pass


def purge_conversational_state(cur: sqlite3.Cursor) -> None:
    """Purge l'état conversationnel d'une session migrée tout en préservant les ressources (Codex, Plans, Fichiers, N8N)."""
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
            "DELETE FROM echo_settings WHERE key IN ('context_stats', 'last_model_id', 'last_active_model', 'turn_origin_model', 'compaction_state') "
            "OR key LIKE 'aec_tier_%' OR key LIKE 'cascade_%'"
        )
    except Exception:
        pass


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

    start_cumul = None
    cutoff_ts = None

    cur.execute(
        "SELECT cumulative_hash, timestamp FROM suture_index WHERE message_id = ? ORDER BY timestamp DESC LIMIT 1",
        (forked_msg_id,),
    )
    row_suture = cur.fetchone()
    if row_suture:
        start_cumul = row_suture["cumulative_hash"]
        cutoff_ts = int(row_suture["timestamp"] or 0)

    cur.execute(
        "SELECT cumulative_hash, updated_at FROM cognitive_signatures WHERE message_id = ? ORDER BY updated_at DESC LIMIT 1",
        (forked_msg_id,),
    )
    row_sig = cur.fetchone()
    if row_sig:
        if not start_cumul and row_sig["cumulative_hash"] in by_cumul:
            start_cumul = row_sig["cumulative_hash"]
        sig_ts = int(row_sig["updated_at"] or 0)
        if cutoff_ts is None or sig_ts > cutoff_ts:
            cutoff_ts = sig_ts

    cur.execute(
        "SELECT role, updated_at FROM message_shadows WHERE message_id = ? LIMIT 1",
        (forked_msg_id,),
    )
    row_shadow = cur.fetchone()
    if row_shadow:
        sh_ts = int(row_shadow["updated_at"] or 0)
        if cutoff_ts is None or sh_ts > cutoff_ts:
            cutoff_ts = sh_ts
        if not start_cumul and suture_rows:
            candidates = [
                r for r in suture_rows if int(r["timestamp"] or 0) <= sh_ts
            ]
            if candidates:
                candidates.sort(key=lambda r: int(r["timestamp"] or 0), reverse=True)
                start_cumul = candidates[0]["cumulative_hash"]

    if cutoff_ts is None and start_cumul is None:
        return None, False

    ancestor_cumuls = set()
    ancestor_msg_ids = {forked_msg_id}
    curr_hash = start_cumul
    while curr_hash and curr_hash in by_cumul and curr_hash not in ancestor_cumuls:
        ancestor_cumuls.add(curr_hash)
        node = by_cumul[curr_hash]
        if node["message_id"]:
            ancestor_msg_ids.add(node["message_id"])
        node_ts = int(node["timestamp"] or 0)
        if cutoff_ts is None or (curr_hash == start_cumul and node_ts > cutoff_ts):
            cutoff_ts = node_ts
        curr_hash = node["parent_hash"]

    if ancestor_cumuls:
        placeholders_c = ",".join("?" for _ in ancestor_cumuls)
        cur.execute(
            f"SELECT message_id, updated_at FROM cognitive_signatures WHERE cumulative_hash IN ({placeholders_c})",
            tuple(ancestor_cumuls),
        )
        for sig_r in cur.fetchall():
            if sig_r["message_id"]:
                ancestor_msg_ids.add(sig_r["message_id"])
            if sig_r["message_id"] == forked_msg_id:
                sig_u = int(sig_r["updated_at"] or 0)
                if cutoff_ts is None or sig_u > cutoff_ts:
                    cutoff_ts = sig_u

    if cutoff_ts is not None:
        cur.execute(
            "SELECT message_id FROM message_shadows WHERE updated_at <= ?",
            (cutoff_ts,),
        )
        for m_r in cur.fetchall():
            if m_r["message_id"]:
                ancestor_msg_ids.add(m_r["message_id"])

    cur.execute("SELECT COUNT(*) as cnt FROM message_shadows")
    total_shadows = int(cur.fetchone()["cnt"] or 0)
    cur.execute("SELECT MAX(updated_at) as max_u FROM message_shadows")
    max_shadow_ts = int(cur.fetchone()["max_u"] or 0)
    cur.execute("SELECT MAX(timestamp) as max_t FROM suture_index")
    max_suture_ts = int(cur.fetchone()["max_t"] or 0)
    max_session_ts = max(max_shadow_ts, max_suture_ts)

    is_partial_fork = (
        (cutoff_ts is not None and cutoff_ts < max_session_ts)
        or (len(ancestor_cumuls) < len(suture_rows))
        or (len(ancestor_msg_ids) < total_shadows)
    )

    if not is_partial_fork:
        return cutoff_ts, False

    if ancestor_cumuls:
        ph_c = ",".join("?" for _ in ancestor_cumuls)
        cur.execute(
            f"DELETE FROM suture_index WHERE cumulative_hash NOT IN ({ph_c})",
            tuple(ancestor_cumuls),
        )
    elif cutoff_ts is not None:
        cur.execute("DELETE FROM suture_index WHERE timestamp > ?", (cutoff_ts,))

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
            tuple(ancestor_msg_ids) + (tuple(ancestor_cumuls) if ancestor_cumuls else ()),
        )
    elif cutoff_ts is not None:
        cur.execute("DELETE FROM message_shadows WHERE updated_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM cognitive_signatures WHERE updated_at > ?", (cutoff_ts,))

    if cutoff_ts is not None:
        cur.execute("DELETE FROM tool_journal WHERE updated_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM rich_payloads WHERE created_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM cognitive_threads WHERE updated_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM processed_files WHERE timestamp > ?", (cutoff_ts,))
        cur.execute("DELETE FROM call_bridge WHERE timestamp > ?", (cutoff_ts,))
        cur.execute("DELETE FROM echo_resources WHERE created_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM plans WHERE created_at > ?", (cutoff_ts,))
        cur.execute("DELETE FROM codex_docs WHERE created_at > ?", (cutoff_ts,))

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


def rewrite_codex_plan_frontmatter(codex_dir: str, orig_cid: str, new_cid: str) -> None:
    """Met à jour chat_id dans le frontmatter YAML des plans du Codex."""
    main_dir = os.path.join(codex_dir, "main")
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


def duplicate_session_domains(user_id: str, orig_cid: str, new_cid: str) -> None:
    """Duplique physiquement les répertoires annexes (files, codex, n8n_workflows, dependencies) avec réécriture des symlinks et fichiers."""
    for domain in ("files", "codex", "n8n_workflows", "dependencies"):
        s_dom = get_echo_session_path(user_id, orig_cid, domain)
        d_dom = get_echo_session_path(user_id, new_cid, domain)
        if os.path.exists(s_dom):
            shutil.copytree(s_dom, d_dom, symlinks=True, dirs_exist_ok=True)
            rewrite_symlinks(d_dom, orig_cid, new_cid)
            if domain == "files":
                rename_cloned_files(d_dom, orig_cid, new_cid)
            elif domain == "codex":
                rewrite_codex_plan_frontmatter(d_dom, orig_cid, new_cid)


def align_codex_git_to_cutoff(
    user_id: str, new_cid: str, cutoff_ts: int, new_db: str
) -> None:
    """Aligne le dépôt Git Codex 'main' sur le dernier commit <= cutoff_ts et synchronise echo_resources/codex_docs."""
    try:
        from echo_codex_git import CodexRepo

        codex_main_dir = os.path.join(
            get_echo_session_path(user_id, new_cid, "codex"), "main"
        )
        if not os.path.exists(os.path.join(codex_main_dir, ".git")):
            return

        repo = CodexRepo(user_id=user_id, chat_id=new_cid, workspace="main")
        res = repo.restore_repo_to_timestamp(cutoff_ts)
        tracked_files = res.get("tracked_files") or set()

        with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
            cur = conn.cursor()
            if not tracked_files:
                cur.execute("DELETE FROM echo_resources WHERE resource_type = 'codex'")
                cur.execute("DELETE FROM codex_docs")
            else:
                cur.execute(
                    "SELECT id, name FROM echo_resources WHERE resource_type = 'codex'"
                )
                for r_id, r_name in cur.fetchall():
                    if r_name not in tracked_files:
                        cur.execute("DELETE FROM echo_resources WHERE id = ?", (r_id,))
                cur.execute("SELECT filename FROM codex_docs")
                for (doc_fn,) in cur.fetchall():
                    if doc_fn not in tracked_files:
                        cur.execute(
                            "DELETE FROM codex_docs WHERE filename = ?", (doc_fn,)
                        )
            conn.commit()
    except Exception as e:
        logger.warning(f"[ECHO-SESSION-CLONE] ⚠️ Alignement Git Codex ignoré : {e}")


def sync_cloned_session(
    user_id: str,
    orig_cid: str,
    new_cid: str,
    forked_msg_id: Optional[str] = None,
) -> None:
    """Synchronise un clone ou fork de session (utilisé par echo_chat_cloned_event)."""
    orig_db = get_echo_session_path(user_id, orig_cid, "db")
    new_db = get_echo_session_path(user_id, new_cid, "db")

    if not backup_session_db(orig_db, new_db):
        return

    cutoff_ts = None
    is_partial_fork = False

    with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        reassign_session_ids(cur, orig_cid, new_cid)

        if forked_msg_id:
            cutoff_ts, is_partial_fork = prune_forked_history(cur, forked_msg_id)

        if is_partial_fork and cutoff_ts is not None:
            cur.execute(
                "DELETE FROM echo_resources WHERE resource_type IN ('aec_event', 'aec_directive') AND created_at > ?",
                (cutoff_ts,),
            )
            cur.execute("DELETE FROM echo_settings WHERE key = 'context_stats'")

        cur.execute("UPDATE message_shadows SET is_embedded = 0")
        conn.commit()

    duplicate_session_domains(user_id, orig_cid, new_cid)

    if is_partial_fork and cutoff_ts is not None:
        align_codex_git_to_cutoff(user_id, new_cid, cutoff_ts, new_db)


def sync_resumed_session(
    user_id: str,
    orig_cid: str,
    new_cid: str,
    initial_embedded_msg_ids: Optional[List[str]] = None,
) -> None:
    """Duplique le conteneur de session pour une migration vers une nouvelle conversation (purge l'historique conversationnel et préserve les ressources)."""
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
                            "INSERT OR REPLACE INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at, is_embedded) "
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


async def duplicate_session_rag(user_id: str, orig_cid: str, new_cid: str) -> int:
    """Duplique par pagination (lots de 256 points) les vecteurs Qdrant de COLLECTION_SESSION_RAG de orig_cid vers new_cid.
    Ne touche jamais à COLLECTION_META_ARTIFACTS (globale à user_id)."""
    scroll_url = f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/scroll"
    upsert_url = f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points?wait=true"

    total_cloned = 0
    next_offset = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            scroll_payload = {
                "filter": {
                    "must": [
                        {"key": "user_id", "match": {"value": user_id}},
                        {"key": "chat_id", "match": {"value": orig_cid}},
                    ]
                },
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
                payload["chat_id"] = new_cid
                new_points.append(
                    {
                        "id": str(uuid.uuid4()),
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
