# -*- coding: utf-8 -*-
"""
title: ECHO Chat Cloned Event Sync
author: Wilfried BARNAVON
version: 5.226.18.1
description: Synchronisation atomique du conteneur de données ECHO lors du clonage ou du fork d'une conversation.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.1: Création de l'Event Function native pour la synchronisation bit-perfect des clones et forks de session.

from typing import Any, Optional
from pydantic import BaseModel, Field
import asyncio
import json
import logging
import os
import shutil
import sqlite3
import sys

sys.path.append("/app/backend/echo_libs")
from echo_paths import get_echo_session_path, get_chat_clone_lock

logger = logging.getLogger("ECHO-EVENT-SYNC")


class Event:
    class Valves(BaseModel):
        ENABLE_CHAT_CLONING_SYNC: bool = Field(
            default=True,
            description="Active la synchronisation automatique du conteneur de données ECHO lors d'un clone/fork.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def event(
        self,
        event: dict,
        __event_name__: Optional[str] = None,
        __request__: Optional[Any] = None,
        **kwargs,
    ) -> None:
        if not self.valves.ENABLE_CHAT_CLONING_SYNC or __event_name__ != "chat.cloned":
            return

        try:
            user_id = (event.get("actor") or {}).get("id")
            new_chat_id = (event.get("subject") or {}).get("id")
            data = event.get("data") or {}
            orig_chat_id = data.get("original_chat_id")
            forked_from_msg_id = data.get("forked_from_message_id")

            if not user_id or not new_chat_id or not orig_chat_id:
                return

            # Verrouillage asyncio sur new_chat_id pour protéger les filtres Inlet concurrents
            clone_lock = get_chat_clone_lock(new_chat_id)
            async with clone_lock:
                await asyncio.to_thread(
                    self._sync_cloned_session,
                    user_id,
                    orig_chat_id,
                    new_chat_id,
                    forked_from_msg_id,
                )
        except Exception as e:
            logger.error(f"[ECHO-EVENT-SYNC] ❌ Erreur lors du clonage de session : {e}")

    def _sync_cloned_session(
        self,
        user_id: str,
        orig_cid: str,
        new_cid: str,
        forked_msg_id: Optional[str],
    ) -> None:
        orig_db = get_echo_session_path(user_id, orig_cid, "db")
        new_db = get_echo_session_path(user_id, new_cid, "db")
        orig_dir = os.path.dirname(orig_db)
        new_dir = os.path.dirname(new_db)

        if not os.path.exists(orig_db) or os.path.exists(new_db):
            return

        os.makedirs(new_dir, exist_ok=True)

        # 1. Copie atomique de la base SQLite (immune au mode WAL)
        from contextlib import closing
        with closing(sqlite3.connect(orig_db, timeout=15.0)) as src, closing(sqlite3.connect(new_db, timeout=15.0)) as dst:
            src.backup(dst)

        # 2. Réassignation des identifiants, réécriture des chemins et élagage de Fork
        cutoff_ts = None
        is_partial_fork = False

        with closing(sqlite3.connect(new_db, timeout=15.0)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # 2.1 Réassignation du chat_id sur les tables disposant de la colonne chat_id
            for tbl in ("suture_index", "message_shadows", "cognitive_threads", "processed_files"):
                try:
                    cur.execute(f"UPDATE {tbl} SET chat_id = ?", (new_cid,))
                except Exception:
                    pass

            # 2.2 Réécriture des identifiants composites de ressources (_C_{orig_cid}_T_ -> _C_{new_cid}_T_)
            # et des chemins physiques stockés (/chats/{orig_cid}/ -> /chats/{new_cid}/)
            orig_id_marker = f"_C_{orig_cid}_T_"
            new_id_marker = f"_C_{new_cid}_T_"
            try:
                cur.execute(
                    "UPDATE echo_resources SET id = REPLACE(id, ?, ?) WHERE id LIKE ?",
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

            # Réécriture universelle des chemins de stockage (compatible "/" et os.sep)
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

            # 2.3 Remontée généalogique et élagage en cas de Fork (forked_msg_id fourni)
            if forked_msg_id:
                cutoff_ts, is_partial_fork = self._prune_forked_history(cur, forked_msg_id)

            # 2.4 Gestion des directives/événements AEC en attente et de la jauge context_stats
            if is_partial_fork and cutoff_ts is not None:
                # Purge uniquement les directives et événements postérieurs au point de fork
                cur.execute(
                    "DELETE FROM echo_settings WHERE (key LIKE 'aec_event_%' OR key LIKE 'aec_directive_%') AND updated_at > ?",
                    (cutoff_ts,),
                )
                # Suppression des statistiques de tokens du dernier tour (inexactes après troncature)
                cur.execute("DELETE FROM echo_settings WHERE key = 'context_stats'")

            # 2.5 Réinitialisation du flag d'indexation vectorielle pour déclencher l'upsert sous new_cid
            cur.execute("UPDATE message_shadows SET is_embedded = 0")
            conn.commit()

        # 3. Duplication physique des répertoires annexes de session
        for domain in ("files", "codex", "n8n_workflows", "dependencies"):
            s_dom = get_echo_session_path(user_id, orig_cid, domain)
            d_dom = get_echo_session_path(user_id, new_cid, domain)
            if os.path.exists(s_dom):
                shutil.copytree(s_dom, d_dom, symlinks=True, dirs_exist_ok=True)
                # Réécriture des liens symboliques pointant vers le conteneur parent
                self._rewrite_symlinks(d_dom, orig_cid, new_cid)
                # Si domain == "files", renommer les fichiers physiques préfixés par U_{uid}_C_{orig_cid}_T_
                if domain == "files":
                    self._rename_cloned_files(d_dom, orig_cid, new_cid)

        # 4. Alignement temporel du dépôt Git Codex 'main' lors d'un Fork intermédiaire
        if is_partial_fork and cutoff_ts is not None:
            self._align_codex_git_to_cutoff(user_id, new_cid, cutoff_ts, new_db)

    def _prune_forked_history(
        self, cur: sqlite3.Cursor, forked_msg_id: str
    ) -> tuple[Optional[int], bool]:
        """Résout la chaîne causale jusqu'à forked_msg_id (user ou assistant) et élague les tables SQLite si le fork est partiel.
        Retourne (cutoff_ts, is_partial_fork)."""
        # Récupération de tous les noeuds suture_index
        cur.execute(
            "SELECT cumulative_hash, parent_hash, message_id, timestamp FROM suture_index"
        )
        suture_rows = cur.fetchall()
        by_cumul = {r["cumulative_hash"]: r for r in suture_rows}

        # Recherche du point d'ancrage dans suture_index (message_id = user_msg_id)
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

        # Recherche dans cognitive_signatures (message_id = asst_msg_id -> cumulative_hash)
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

        # Recherche dans message_shadows (couvre user_msg_id et asst_msg_id)
        cur.execute(
            "SELECT role, updated_at FROM message_shadows WHERE message_id = ? LIMIT 1",
            (forked_msg_id,),
        )
        row_shadow = cur.fetchone()
        if row_shadow:
            sh_ts = int(row_shadow["updated_at"] or 0)
            if cutoff_ts is None or sh_ts > cutoff_ts:
                cutoff_ts = sh_ts
            # Si c'est un message assistant qui n'avait pas de cognitive_signature,
            # retrouver le noeud suture_index du tour correspondant (timestamp <= sh_ts)
            if not start_cumul and suture_rows:
                candidates = [
                    r for r in suture_rows if int(r["timestamp"] or 0) <= sh_ts
                ]
                if candidates:
                    candidates.sort(key=lambda r: int(r["timestamp"] or 0), reverse=True)
                    start_cumul = candidates[0]["cumulative_hash"]

        if cutoff_ts is None and start_cumul is None:
            return None, False

        # Remontée de l'arbre généalogique dans suture_index via parent_hash
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

        # Ajout des message_id assistant liés aux ancestor_cumuls via cognitive_signatures
        if ancestor_cumuls:
            placeholders_c = ",".join("?" for _ in ancestor_cumuls)
            cur.execute(
                f"SELECT message_id, updated_at FROM cognitive_signatures WHERE cumulative_hash IN ({placeholders_c})",
                tuple(ancestor_cumuls),
            )
            for sig_r in cur.fetchall():
                if sig_r["message_id"]:
                    ancestor_msg_ids.add(sig_r["message_id"])
                # Si ce message assistant appartient au tour de start_cumul, étendre cutoff_ts
                # uniquement si forked_msg_id est ce message assistant (ou si cutoff_ts couvre le tour)
                if sig_r["message_id"] == forked_msg_id:
                    sig_u = int(sig_r["updated_at"] or 0)
                    if cutoff_ts is None or sig_u > cutoff_ts:
                        cutoff_ts = sig_u

        # Ajout de tous les message_shadows antérieurs ou égaux à cutoff_ts
        if cutoff_ts is not None:
            cur.execute(
                "SELECT message_id FROM message_shadows WHERE updated_at <= ?",
                (cutoff_ts,),
            )
            for m_r in cur.fetchall():
                if m_r["message_id"]:
                    ancestor_msg_ids.add(m_r["message_id"])

        # Détermination : est-ce un fork partiel (intermédiaire) ou sur le tout dernier message ?
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

        # Élagage SQL des enregistrements postérieurs au point de fork
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
                + (f" AND cumulative_hash NOT IN ({','.join('?' for _ in ancestor_cumuls)})" if ancestor_cumuls else ""),
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

        # Rétrogradation ou invalidation de compaction_state si son pivot_msg_id a été élagué
        cur.execute("SELECT value FROM echo_settings WHERE key = 'compaction_state'")
        cp_row = cur.fetchone()
        if cp_row and cp_row["value"]:
            try:
                cp_state = json.loads(cp_row["value"])
                pivot_id = cp_state.get("pivot_msg_id")
                if pivot_id and pivot_id not in ancestor_msg_ids:
                    cur.execute("DELETE FROM echo_settings WHERE key = 'compaction_state'")
            except Exception:
                pass

        return cutoff_ts, True

    def _rewrite_symlinks(self, root_dir: str, orig_cid: str, new_cid: str) -> None:
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

    def _rename_cloned_files(self, files_dir: str, orig_cid: str, new_cid: str) -> None:
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

    def _align_codex_git_to_cutoff(
        self, user_id: str, new_cid: str, cutoff_ts: int, new_db: str
    ) -> None:
        """Aligne le dépôt Git Codex 'main' sur le dernier commit <= cutoff_ts et synchronise echo_resources/codex_docs."""
        try:
            from contextlib import closing
            from echo_codex_git import CodexRepo

            codex_main_dir = os.path.join(get_echo_session_path(user_id, new_cid, "codex"), "main")
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
                    # Conserver uniquement les entrées correspondant aux fichiers présents dans l'arbre Git restauré
                    cur.execute("SELECT id, name FROM echo_resources WHERE resource_type = 'codex'")
                    for r_id, r_name in cur.fetchall():
                        if r_name not in tracked_files:
                            cur.execute("DELETE FROM echo_resources WHERE id = ?", (r_id,))
                    cur.execute("SELECT filename FROM codex_docs")
                    for (doc_fn,) in cur.fetchall():
                        if doc_fn not in tracked_files:
                            cur.execute("DELETE FROM codex_docs WHERE filename = ?", (doc_fn,))
                conn.commit()
        except Exception as e:
            logger.warning(f"[ECHO-EVENT-SYNC] ⚠️ Alignement Git Codex ignoré : {e}")
