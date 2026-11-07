"""
title: ECHO Session GC
author: ECHO Framework
version: 5.235.5.3
description: Purge des branches mortes, filtrage RAG par branche active et provenance des productions (stdlib-only).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.235.5.3: Normalisation timestamp dans is_point_in_branch et flexibilité CLI (alias --backfill-rag et défaut gc).
# 5.235.5.2: Garbage collection arborescent des branches mortes (SAVEPOINT echo_purge_dead, compute_dead_set, build_branch_rag_filter, provenance).

import argparse
from dataclasses import dataclass
import json
import os
import sqlite3
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set
import urllib.error
import urllib.request
import uuid

from echo_constants import (
    COLLECTION_SESSION_RAG,
    ECHO_GC_GRACE_PERIOD_SEC,
    ECHO_QDRANT_URL,
    ECHO_USERS_ROOT,
    SESSION_RAG_CONVERSATION_SOURCE_ID,
)
from echo_db_schema import (
    EchoDbMissingError,
    EchoDbOutdatedError,
    extract_call_ids_from_json,
    open_db,
)

# Réexport explicite de l'extracteur d'appels d'outils
__all__ = [
    "extract_call_ids_from_json",
    "build_session_rag_point_id",
    "load_owui_live_message_ids",
    "DeadSet",
    "compute_dead_set",
    "purge_dead_set",
    "gc_session_db",
    "gc_all",
    "build_branch_rag_filter",
    "is_point_in_branch",
    "resource_provenance",
    "backfill_rag_message_ids",
    "cli",
]


# ==============================================================================
# CALCUL D'IDENTIFIANTS RAG FACTORISÉ (LOTS 1, 2)
# ==============================================================================

def build_session_rag_point_id(
    uid: str,
    chat_id: str,
    source_id: str,
    index: int,
    unique_seed: Optional[str] = None,
    sub_sid: Optional[str] = None,
) -> str:
    """Reproduction exacte de la formule d'ID de point Qdrant de echo_gemini_client.py L583-585."""
    sub_prefix = f"{sub_sid}_" if sub_sid else ""
    seed_str = (
        f"{uid}_{chat_id}_{sub_prefix}{source_id}_{unique_seed}_{index}"
        if unique_seed
        else f"{uid}_{chat_id}_{sub_prefix}{source_id}_{index}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed_str))


# ==============================================================================
# LECTURE DE L'ARBRE LIVE OPEN WEBUI (FAIL-CLOSED)
# ==============================================================================

def load_owui_live_message_ids(webui_conn: sqlite3.Connection, chat_id: str) -> Optional[Set[str]]:
    """Extrait l'ensemble des message_id vivants dans l'arbre conversationnel d'Open WebUI (table chat et chat_message).

    Renvoie None en cas d'absence du chat ou d'ensemble vide afin d'appliquer une politique Fail-Closed
    stricte : si l'état live d'Open WebUI ne peut pas être établi, aucun GC n'est exécuté.
    """
    row = webui_conn.execute("SELECT chat FROM chat WHERE id = ?", (chat_id,)).fetchone()
    if not row or row[0] is None:
        return None

    raw_chat = row[0]
    live_ids: Set[str] = set()

    # Tentative prioritaire par json_each natif SQLite
    extracted_via_json_each = False
    try:
        cur = webui_conn.execute(
            "SELECT key FROM json_each((SELECT chat FROM chat WHERE id = ?), '$.history.messages')",
            (chat_id,),
        )
        for (k,) in cur.fetchall():
            if k:
                live_ids.add(str(k))
        extracted_via_json_each = True
    except Exception:
        extracted_via_json_each = False

    # Repli Python standard si json_each est indisponible ou échoue
    if not extracted_via_json_each:
        try:
            chat_obj = json.loads(raw_chat) if isinstance(raw_chat, str) else raw_chat
            if isinstance(chat_obj, dict):
                hist = chat_obj.get("history") or {}
                msgs = hist.get("messages") or {}
                if isinstance(msgs, dict):
                    for k in msgs.keys():
                        if k:
                            live_ids.add(str(k))
        except Exception:
            pass

    # Prise en compte de la table relationnelle chat_message si présente
    has_chat_msg = webui_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_message'"
    ).fetchone() is not None

    if has_chat_msg:
        prefix = f"{chat_id}-"
        cur = webui_conn.execute("SELECT id FROM chat_message WHERE chat_id = ?", (chat_id,))
        for (mid,) in cur.fetchall():
            if mid:
                s_mid = str(mid)
                if s_mid.startswith(prefix):
                    s_mid = s_mid[len(prefix):]
                live_ids.add(s_mid)

    if not live_ids:
        return None

    return live_ids


# ==============================================================================
# ALGORITHME DE RÉSOLUTION DES BRANCHES MORTES (DEAD-SET)
# ==============================================================================

@dataclass
class DeadSet:
    """Ensemble des entités orphelines candidates à la purge."""
    message_ids: Set[str]
    cumuls: Set[str]
    call_ids: Set[str]
    rag_seeds: Set[str]

    @property
    def is_empty(self) -> bool:
        return not (self.message_ids or self.cumuls or self.call_ids or self.rag_seeds)


def compute_dead_set(
    session_conn: sqlite3.Connection,
    live_ids: Set[str],
    grace_ts: float,
) -> DeadSet:
    """Calcule l'ensemble 'morts moins vivants' à purger dans une base de session SQLite.

    Logique fondamentale :
    1. Identification des messages morts : tout message_id absent de live_ids dont l'horodatage
       maximal est strictly inférieur à grace_ts (protégeant les créations récentes en vol).
       Les messages présents dans live_ids ou sous délai de grâce forment protected_msg_ids.
    2. Priorité de propriété cognitive_signatures sur suture_index : historiquement, suture_index
       pouvait enregistrer un tour assistant sous le user_message_id (parent). En donnant priorité
       au message_id de cognitive_signatures sur le cumulative_hash, on réattribue infailliblement
       le hash à son véritable propriétaire assistant.
    3. Propagation causale le long de suture_index.parent_hash :
       - Les ancêtres d'un cumulative_hash vivant sont impérativement sanctuarisés (protection
         du préfixe de contexte commun partagé entre la branche principale et les branches dérivées).
       - Les descendants d'un cumulative_hash mort sans propriétaire vivant sont entraînés dans
         l'élagage des branches mortes.
    4. Isolement des appels d'outils (call_ids) : un call_id n'est mort que s'il n'est rattaché
       à aucun message ni cumulative_hash vivant.
    """
    max_ts: Dict[str, float] = {}

    def _collect_ts(query: str) -> None:
        try:
            for mid, ts in session_conn.execute(query).fetchall():
                if mid and not str(mid).isdigit():
                    s_mid = str(mid)
                    val = float(ts or 0)
                    if s_mid not in max_ts or val > max_ts[s_mid]:
                        max_ts[s_mid] = val
        except Exception:
            pass

    _collect_ts("SELECT message_id, updated_at FROM message_shadows WHERE message_id IS NOT NULL")
    _collect_ts("SELECT message_id, updated_at FROM cognitive_signatures WHERE message_id IS NOT NULL")
    _collect_ts("SELECT message_id, timestamp FROM suture_index WHERE message_id IS NOT NULL")
    _collect_ts("SELECT message_id, timestamp FROM call_bridge WHERE message_id IS NOT NULL")
    _collect_ts("SELECT message_id, embedded_at FROM rag_index_state WHERE message_id IS NOT NULL")

    dead_message_ids: Set[str] = set()
    protected_msg_ids: Set[str] = set()

    for mid, ts in max_ts.items():
        if mid in live_ids or ts >= grace_ts:
            protected_msg_ids.add(mid)
        else:
            dead_message_ids.add(mid)

    if not dead_message_ids:
        return DeadSet(set(), set(), set(), set())

    # --- Résolution des cumulative_hash ---
    suture_rows = session_conn.execute(
        "SELECT cumulative_hash, parent_hash, message_id FROM suture_index"
    ).fetchall()
    sig_rows = session_conn.execute(
        "SELECT cumulative_hash, message_id FROM cognitive_signatures"
    ).fetchall()

    sig_owners: Dict[str, Set[str]] = {}
    for chash, mid in sig_rows:
        if chash and mid and not str(mid).isdigit():
            sig_owners.setdefault(chash, set()).add(str(mid))

    suture_owners: Dict[str, Set[str]] = {}
    parent_map: Dict[str, str] = {}
    children_map: Dict[str, Set[str]] = {}
    for chash, parent, mid in suture_rows:
        if chash:
            if mid and not str(mid).isdigit():
                suture_owners.setdefault(chash, set()).add(str(mid))
            if parent and parent != chash:
                parent_map[chash] = parent
                children_map.setdefault(parent, set()).add(chash)

    all_cumuls = set(sig_owners.keys()) | set(suture_owners.keys()) | set(parent_map.keys())
    for chash, _, _ in suture_rows:
        if chash:
            all_cumuls.add(chash)

    # Attribution avec priorité cognitive_signatures sur suture_index
    direct_owners: Dict[str, Set[str]] = {}
    for chash in all_cumuls:
        if chash in sig_owners:
            direct_owners[chash] = sig_owners[chash]
        elif chash in suture_owners:
            direct_owners[chash] = suture_owners[chash]
        else:
            direct_owners[chash] = set()

    live_cumuls: Set[str] = set()
    dead_cumuls: Set[str] = set()

    for chash, owners in direct_owners.items():
        if owners & protected_msg_ids:
            live_cumuls.add(chash)
        elif owners & dead_message_ids:
            dead_cumuls.add(chash)

    # (a) Sanctuarisation des ancêtres des cumuls vivants (préservation du tronc commun)
    queue = list(live_cumuls)
    visited_live = set(live_cumuls)
    while queue:
        curr = queue.pop()
        p = parent_map.get(curr)
        if p and p not in visited_live:
            visited_live.add(p)
            live_cumuls.add(p)
            dead_cumuls.discard(p)
            queue.append(p)

    # (b) Propagation vers les enfants des cumuls morts n'ayant pas de propriétaire protégé
    dead_queue = list(dead_cumuls - live_cumuls)
    visited_dead = set(dead_queue)
    while dead_queue:
        curr = dead_queue.pop()
        for child in children_map.get(curr, ()):
            if child not in live_cumuls and child not in visited_dead:
                child_owners = direct_owners.get(child, set())
                if not (child_owners & protected_msg_ids):
                    visited_dead.add(child)
                    dead_cumuls.add(child)
                    dead_queue.append(child)

    final_dead_cumuls = dead_cumuls - live_cumuls

    # --- Résolution des call_ids ---
    dead_calls: Set[str] = set()
    live_calls: Set[str] = set()

    try:
        cb_rows = session_conn.execute("SELECT call_id, message_id FROM call_bridge").fetchall()
        for cid, mid in cb_rows:
            if not cid:
                continue
            s_mid = str(mid) if mid else None
            if s_mid in dead_message_ids:
                dead_calls.add(str(cid))
            elif s_mid in protected_msg_ids:
                live_calls.add(str(cid))
    except Exception:
        pass

    try:
        ms_rows = session_conn.execute(
            "SELECT message_id, full_parts_json FROM message_shadows WHERE full_parts_json IS NOT NULL"
        ).fetchall()
        for mid, raw_json in ms_rows:
            s_mid = str(mid) if mid else None
            extracted = extract_call_ids_from_json(raw_json)
            if s_mid in dead_message_ids:
                dead_calls.update(extracted)
            elif s_mid not in dead_message_ids:
                live_calls.update(extracted)
    except Exception:
        pass

    try:
        tj_rows = session_conn.execute(
            "SELECT cumulative_hash, io_json FROM tool_journal WHERE io_json IS NOT NULL"
        ).fetchall()
        for chash, raw_json in tj_rows:
            extracted = extract_call_ids_from_json(raw_json)
            if chash in final_dead_cumuls:
                dead_calls.update(extracted)
            elif chash not in final_dead_cumuls:
                live_calls.update(extracted)
    except Exception:
        pass

    final_dead_calls = dead_calls - live_calls

    # --- Résolution des graines RAG de messages morts ---
    rag_seeds: Set[str] = set()
    try:
        has_rag_tbl = session_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rag_index_state'"
        ).fetchone() is not None
        if has_rag_tbl:
            for (mid,) in session_conn.execute("SELECT message_id FROM rag_index_state").fetchall():
                if mid and not str(mid).isdigit() and str(mid) in dead_message_ids:
                    rag_seeds.add(str(mid))
    except Exception:
        pass

    return DeadSet(dead_message_ids, final_dead_cumuls, final_dead_calls, rag_seeds)


# ==============================================================================
# EXÉCUTION DE LA PURGE TRANSACTIONNELLE (SAVEPOINT)
# ==============================================================================

def _chunked_delete(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    values: Set[str],
    batch_size: int = 500,
) -> int:
    """Supprime par lots pour ne jamais saturer la limite de variables d'hôte SQLite (SQLITE_MAX_VARIABLE_NUMBER)."""
    if not values:
        return 0
    val_list = list(values)
    deleted = 0
    for i in range(0, len(val_list), batch_size):
        chunk = val_list[i:i + batch_size]
        placeholders = ", ".join("?" for _ in chunk)
        cur = conn.execute(f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders})', chunk)
        deleted += cur.rowcount
    return deleted


def purge_dead_set(
    session_conn: sqlite3.Connection,
    dead: DeadSet,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Exécute la suppression atomique des lignes orphelines dans un SAVEPOINT réversible.

    L'utilisation de SAVEPOINT echo_purge_dead garantit une compatibilité totale qu'une transaction
    externe soit déjà active (ex: lors du clonage de session sync_cloned_session) ou non.
    """
    counts = {
        "message_shadows": 0,
        "rag_index_state": 0,
        "cognitive_signatures": 0,
        "suture_index": 0,
        "tool_journal": 0,
        "call_bridge": 0,
    }
    if dead.is_empty:
        return counts

    session_conn.execute("SAVEPOINT echo_purge_dead")
    try:
        counts["message_shadows"] = _chunked_delete(session_conn, "message_shadows", "message_id", dead.message_ids)
        counts["rag_index_state"] = _chunked_delete(session_conn, "rag_index_state", "message_id", dead.message_ids)
        counts["cognitive_signatures"] = _chunked_delete(session_conn, "cognitive_signatures", "cumulative_hash", dead.cumuls)
        counts["suture_index"] = _chunked_delete(session_conn, "suture_index", "cumulative_hash", dead.cumuls)
        counts["tool_journal"] = _chunked_delete(session_conn, "tool_journal", "cumulative_hash", dead.cumuls)

        cb_del_calls = _chunked_delete(session_conn, "call_bridge", "call_id", dead.call_ids)
        cb_del_msgs = _chunked_delete(session_conn, "call_bridge", "message_id", dead.message_ids)
        counts["call_bridge"] = cb_del_calls + cb_del_msgs

        if dry_run:
            session_conn.execute("ROLLBACK TO SAVEPOINT echo_purge_dead")
            session_conn.execute("RELEASE SAVEPOINT echo_purge_dead")
        else:
            session_conn.execute("RELEASE SAVEPOINT echo_purge_dead")
            if not session_conn.in_transaction:
                session_conn.commit()

        return counts
    except Exception:
        try:
            session_conn.execute("ROLLBACK TO SAVEPOINT echo_purge_dead")
            session_conn.execute("RELEASE SAVEPOINT echo_purge_dead")
        except Exception:
            pass
        raise


def gc_session_db(
    session_db_path: str,
    webui_conn: sqlite3.Connection,
    chat_id: str,
    grace_ts: float,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Exécute le GC sur une base session.db individuelle en confrontant ses identifiants à webui.db."""
    try:
        conn = open_db(session_db_path, readonly=False, create=False, require_version=True, timeout=2.0)
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            return {
                "chat_id": chat_id,
                "status": "skipped_locked",
                "counts": {},
                "rows_deleted": 0,
                "rag_seeds": [],
            }
        raise
    except (EchoDbOutdatedError, EchoDbMissingError):
        return {
            "chat_id": chat_id,
            "status": "schema_outdated",
            "counts": {},
            "rows_deleted": 0,
            "rag_seeds": [],
        }

    try:
        live_ids = load_owui_live_message_ids(webui_conn, chat_id)
        if live_ids is None:
            return {
                "chat_id": chat_id,
                "status": "no_live_set",
                "counts": {},
                "rows_deleted": 0,
                "rag_seeds": [],
            }

        dead = compute_dead_set(conn, live_ids, grace_ts)
        if dead.is_empty:
            return {
                "chat_id": chat_id,
                "status": "nothing",
                "counts": {},
                "rows_deleted": 0,
                "rag_seeds": [],
            }

        counts = purge_dead_set(conn, dead, dry_run=dry_run)
        return {
            "chat_id": chat_id,
            "status": "purged",
            "counts": counts,
            "rows_deleted": sum(counts.values()),
            "rag_seeds": sorted(dead.rag_seeds),
        }
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            return {
                "chat_id": chat_id,
                "status": "skipped_locked",
                "counts": {},
                "rows_deleted": 0,
                "rag_seeds": [],
            }
        raise
    finally:
        conn.close()


def gc_all(
    users_root: str = ECHO_USERS_ROOT,
    webui_db_path: Optional[str] = None,
    grace_sec: int = ECHO_GC_GRACE_PERIOD_SEC,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Exécute le GC des branches mortes à travers l'ensemble des conteneurs de session du système."""
    if webui_db_path is None:
        webui_db_path = os.path.join(os.path.dirname(users_root.rstrip("/\\")), "webui.db")

    if not os.path.exists(webui_db_path):
        return {
            "dry_run": dry_run,
            "error": f"Base Open WebUI introuvable: {webui_db_path}",
            "total_sessions": 0,
            "details": [],
        }

    now = time.time()
    grace_ts = now - grace_sec

    # Lecture seule absolue sur webui.db
    uri_path = os.path.abspath(webui_db_path).replace("\\", "/")
    webui_conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=10.0)

    details: List[Dict[str, Any]] = []
    total_deleted = 0
    rag_seeds_all: List[str] = []

    try:
        if os.path.isdir(users_root):
            for user_entry in os.scandir(users_root):
                if not user_entry.is_dir() or user_entry.name.startswith("."):
                    continue
                chats_dir = os.path.join(user_entry.path, "chats")
                if os.path.isdir(chats_dir):
                    for chat_entry in os.scandir(chats_dir):
                        if not chat_entry.is_dir() or chat_entry.name.startswith("."):
                            continue
                        session_db = os.path.join(chat_entry.path, "session.db")
                        if os.path.exists(session_db):
                            cid = chat_entry.name
                            res = gc_session_db(session_db, webui_conn, cid, grace_ts, dry_run=dry_run)
                            details.append(res)
                            total_deleted += res.get("rows_deleted", 0)
                            rag_seeds_all.extend(res.get("rag_seeds", []))
    finally:
        webui_conn.close()

    return {
        "dry_run": dry_run,
        "users_root": users_root,
        "webui_db": webui_db_path,
        "grace_sec": grace_sec,
        "total_sessions": len(details),
        "total_rows_deleted": total_deleted,
        "total_rag_seeds": len(rag_seeds_all),
        "details": details,
    }


# ==============================================================================
# FONCTIONS LOT 2 : FILTRAGE RAG DE BRANCHE & PROVENANCE DES PRODUCTIONS
# ==============================================================================

def build_branch_rag_filter(active_ids: Iterable[str]) -> Dict[str, Any]:
    """Construit le filtre Qdrant restreignant la recherche locale à la branche active.

    La clause {"is_empty": {"key": "message_id"}} est un garde-fou de compatibilité descendante :
    elle autorise la recherche sur les points vectoriels indexés avant le déploiement du Lot 2
    (chats historiques sans rétro-remplissage) sans provoquer de régression de rappel.
    """
    return {
        "should": [
            {"must_not": [{"key": "source_id", "match": {"value": SESSION_RAG_CONVERSATION_SOURCE_ID}}]},
            {"key": "message_id", "match": {"any": list(active_ids)}},
            {"is_empty": {"key": "message_id"}},
        ]
    }


def is_point_in_branch(
    payload: Dict[str, Any],
    live_ids: Set[str],
    cutoff_ts: Optional[int] = None,
) -> bool:
    """Vérifie si un point vectoriel Qdrant doit être conservé lors du clonage/fork d'une session."""
    source_id = payload.get("source_id")
    if source_id != SESSION_RAG_CONVERSATION_SOURCE_ID:
        if cutoff_ts is None:
            return True
        pt_ts = payload.get("timestamp") if payload.get("timestamp") is not None else payload.get("created_at")
        return pt_ts is None or pt_ts <= cutoff_ts

    mid = payload.get("message_id")
    if mid:
        return str(mid) in live_ids

    # Repli temporel pour les anciens points conversationnels sans message_id
    if cutoff_ts is None:
        return True
    pt_ts = payload.get("timestamp") if payload.get("timestamp") is not None else payload.get("created_at")
    return pt_ts is None or pt_ts <= cutoff_ts


def resource_provenance(resource: Dict[str, Any], active_ids: Iterable[str]) -> str:
    """Détermine la provenance contextuelle d'une production ('active', 'hors_branche' ou 'inconnue')."""
    mid = (resource or {}).get("message_id")
    if not mid:
        return "inconnue"
    if str(mid) in set(active_ids):
        return "active"
    return "hors_branche"


def _default_http_post(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Émet une requête HTTP POST JSON via urllib.request (stdlib pure)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        res_bytes = resp.read()
        return json.loads(res_bytes.decode("utf-8")) if res_bytes else {}


def backfill_rag_message_ids(
    users_root: str = ECHO_USERS_ROOT,
    qdrant_url: str = ECHO_QDRANT_URL,
    collection: str = COLLECTION_SESSION_RAG,
    dry_run: bool = False,
    http_post: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Rétro-remplit le champ message_id dans le payload des points vectoriels Qdrant existants."""
    post_fn = http_post or _default_http_post
    points_matched = 0
    points_updated = 0
    sessions_checked = 0

    if not os.path.isdir(users_root):
        return {
            "dry_run": dry_run,
            "sessions_checked": 0,
            "points_matched": 0,
            "points_updated": 0,
        }

    for user_entry in os.scandir(users_root):
        if not user_entry.is_dir() or user_entry.name.startswith("."):
            continue
        uid = user_entry.name
        chats_dir = os.path.join(user_entry.path, "chats")
        if not os.path.isdir(chats_dir):
            continue

        for chat_entry in os.scandir(chats_dir):
            if not chat_entry.is_dir() or chat_entry.name.startswith("."):
                continue
            cid = chat_entry.name
            session_db = os.path.join(chat_entry.path, "session.db")
            if not os.path.exists(session_db):
                continue

            try:
                conn = open_db(session_db, readonly=True, create=False, require_version=True)
            except (EchoDbMissingError, EchoDbOutdatedError):
                continue

            sessions_checked += 1
            try:
                cur = conn.execute("SELECT message_id FROM rag_index_state")
                seeds = [row[0] for row in cur.fetchall() if row[0] and not str(row[0]).isdigit()]
            finally:
                conn.close()

            points_url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points"
            payload_url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points/payload"

            for seed in seeds:
                s_seed = str(seed)
                # Exploration par lots de 32 indices, plafond de 64 lots (jusqu'à 2048 fragments par tour)
                for batch_num in range(64):
                    start_i = batch_num * 32
                    batch_ids = [
                        build_session_rag_point_id(uid, cid, SESSION_RAG_CONVERSATION_SOURCE_ID, i, unique_seed=s_seed)
                        for i in range(start_i, start_i + 32)
                    ]
                    try:
                        resp = post_fn(points_url, {"ids": batch_ids, "with_payload": False, "with_vector": False})
                    except Exception as e:
                        print(f"[ECHO-GC] Erreur récupération points Qdrant: {e}")
                        break

                    pts = resp.get("result") or []
                    found_ids = [
                        str(pt["id"]) for pt in pts if isinstance(pt, dict) and pt.get("id")
                    ]
                    if not found_ids:
                        break

                    points_matched += len(found_ids)
                    if not dry_run:
                        try:
                            post_fn(payload_url, {"payload": {"message_id": s_seed}, "points": found_ids})
                            points_updated += len(found_ids)
                        except Exception as e:
                            print(f"[ECHO-GC] Erreur mise à jour payload Qdrant: {e}")

    return {
        "dry_run": dry_run,
        "sessions_checked": sessions_checked,
        "points_matched": points_matched,
        "points_updated": points_updated,
    }


# ==============================================================================
# CLI
# ==============================================================================

def cli(argv: Optional[List[str]] = None) -> int:
    """Point d'entrée CLI pour la commande de GC et de rétro-remplissage RAG."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "--backfill-rag":
        effective_argv = ["rag-backfill"] + raw_args[1:]
    elif "--backfill-rag" in raw_args and not any(cmd in raw_args for cmd in ("gc", "rag-backfill")):
        filtered = [a for a in raw_args if a != "--backfill-rag"]
        effective_argv = ["rag-backfill"] + filtered
    elif raw_args and raw_args[0] in ("gc", "rag-backfill", "-h", "--help"):
        effective_argv = list(raw_args)
    else:
        effective_argv = ["gc"] + raw_args

    parser = argparse.ArgumentParser(description="Outil de maintenance ECHO Session GC et RAG Backfill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Commande gc
    gc_parser = subparsers.add_parser("gc", help="Purge des branches mortes dans les bases session.db.")
    gc_parser.add_argument("--dry-run", action="store_true", help="Simule la purge sans l'exécuter.")
    gc_parser.add_argument("--users-root", default=ECHO_USERS_ROOT, help="Chemin vers le dossier users.")
    gc_parser.add_argument("--webui-db", default=None, help="Chemin vers webui.db (défaut: parent(users_root)/webui.db).")
    gc_parser.add_argument("--grace-sec", type=int, default=ECHO_GC_GRACE_PERIOD_SEC, help="Délai de grâce en secondes (défaut: 900).")

    # Commande rag-backfill
    rag_parser = subparsers.add_parser("rag-backfill", help="Rétro-remplissage de message_id dans Qdrant.")
    rag_parser.add_argument("--dry-run", action="store_true", help="Simule le rétro-remplissage sans appliquer les payloads.")
    rag_parser.add_argument("--users-root", default=ECHO_USERS_ROOT, help="Chemin vers le dossier users.")
    rag_parser.add_argument("--qdrant-url", default=ECHO_QDRANT_URL, help="URL du serveur Qdrant.")

    args = parser.parse_args(effective_argv)

    if args.command == "gc":
        res = gc_all(
            users_root=args.users_root,
            webui_db_path=args.webui_db,
            grace_sec=args.grace_sec,
            dry_run=args.dry_run,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if not res.get("error") else 1
    elif args.command == "rag-backfill":
        res = backfill_rag_message_ids(
            users_root=args.users_root,
            qdrant_url=args.qdrant_url,
            dry_run=args.dry_run,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(cli())
