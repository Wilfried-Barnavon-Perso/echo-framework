# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER - DATA EXPORT SERVICE (TAR.XZ)
VERSION : 5.236.5.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.236.5.1: Service d'export souverain .tar.xz (domaine utilisateur Open WebUI + collections Qdrant en JSON).

from contextlib import closing
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
from core.config import (
    COLLECTION_META_ARTIFACTS,
    COLLECTION_SESSION_RAG,
    ECHO_SYNC_EXCLUDE_LIST,
    ECHO_USERS_ROOT,
    HAS_HTTPX,
    QDRANT_URL,
    WEBUI_DB_PATH,
    httpx,
    sanitize_echo_id,
)
from core.utils import get_echo_version

__all__ = [
    "get_user_export_lock",
    "count_user_qdrant_points",
    "scroll_user_qdrant_collection",
    "export_webui_user_records",
    "dump_sqlite_db_to_json",
    "build_user_export_archive",
    "stream_and_cleanup_archive",
]

_EXPORT_LOCKS_GUARD = threading.Lock()
_USER_EXPORT_LOCKS: dict[str, threading.Lock] = {}


def get_user_export_lock(user_key: str) -> threading.Lock:
    """Retourne un verrou dédié par utilisateur pour permettre les exports parallèles inter-utilisateurs."""
    key = str(user_key or "anonymous").strip().lower()
    with _EXPORT_LOCKS_GUARD:
        lock = _USER_EXPORT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _USER_EXPORT_LOCKS[key] = lock
        return lock


def count_user_qdrant_points(user_id: str | None) -> dict:
    """Compte le nombre de points vectoriels appartenant à user_id dans les collections Qdrant."""
    counts = {
        "meta_artifacts": 0,
        "session_rag": 0,
        "total": 0,
    }
    if not user_id or not HAS_HTTPX or httpx is None:
        return counts

    q_filter = {
        "filter": {
            "must": [
                {"key": "user_id", "match": {"value": str(user_id)}},
            ]
        },
        "exact": True,
    }
    try:
        with httpx.Client(timeout=5.0) as client:
            for key, col in (
                ("meta_artifacts", COLLECTION_META_ARTIFACTS),
                ("session_rag", COLLECTION_SESSION_RAG),
            ):
                try:
                    resp = client.post(
                        f"{QDRANT_URL}/collections/{col}/points/count",
                        json=q_filter,
                    )
                    if resp.status_code == 200:
                        c_val = int((resp.json().get("result") or {}).get("count", 0))
                        counts[key] = c_val
                except Exception:
                    pass
    except Exception:
        pass

    counts["total"] = counts["meta_artifacts"] + counts["session_rag"]
    return counts


def scroll_user_qdrant_collection(collection_name: str, user_id: str | None) -> list:
    """Extrait par pagination (scroll) tous les points Qdrant (payload + vector) d'un utilisateur."""
    if not user_id or not HAS_HTTPX or httpx is None:
        return []

    all_points = []
    next_offset = None
    try:
        with httpx.Client(timeout=20.0) as client:
            for _ in range(500):
                body = {
                    "filter": {
                        "must": [
                            {"key": "user_id", "match": {"value": str(user_id)}},
                        ]
                    },
                    "limit": 250,
                    "with_payload": True,
                    "with_vector": True,
                }
                if next_offset is not None:
                    body["offset"] = next_offset

                resp = client.post(
                    f"{QDRANT_URL}/collections/{collection_name}/points/scroll",
                    json=body,
                )
                if resp.status_code != 200:
                    break
                result = resp.json().get("result") or {}
                pts = result.get("points") or []
                if not pts:
                    break
                all_points.extend(pts)
                next_offset = result.get("next_page_offset")
                if next_offset is None:
                    break
    except Exception:
        pass
    return all_points


def _try_parse_json_fields(row_dict: dict, json_keys: tuple[str, ...]) -> dict:
    """Décode les colonnes JSON stockées sous forme de chaînes SQLite pour produire un JSON propre."""
    for k in json_keys:
        val = row_dict.get(k)
        if isinstance(val, str) and val.strip().startswith(("{", "[")):
            try:
                row_dict[k] = json.loads(val)
            except Exception:
                pass
    return row_dict


def export_webui_user_records(user_id: str | None, email: str) -> dict:
    """Extrait les données de l'utilisateur issues de la base webui.db d'Open WebUI."""
    export_data = {
        "user_id": user_id,
        "email": email,
        "user": None,
        "tables": {},
    }
    if not os.path.exists(WEBUI_DB_PATH):
        return export_data

    try:
        with closing(sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True, timeout=10.0)) as conn:
            conn.row_factory = sqlite3.Row
            if user_id:
                u_row = conn.execute("SELECT * FROM user WHERE id = ?", (str(user_id),)).fetchone()
            else:
                u_row = conn.execute("SELECT * FROM user WHERE email = ?", (email,)).fetchone()

            if u_row:
                u_dict = dict(u_row)
                if "api_key" in u_dict and u_dict["api_key"]:
                    u_dict["api_key"] = "***REDACTED***"
                export_data["user"] = _try_parse_json_fields(u_dict, ("info", "settings", "oauth"))
                if not user_id and u_dict.get("id"):
                    user_id = str(u_dict["id"])
                    export_data["user_id"] = user_id

            if not user_id:
                return export_data

            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name ASC"
                ).fetchall()
            ]
            for table in tables:
                if table in ("user", "auth", "alembic_version", "migratehistory"):
                    continue
                try:
                    cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                    if "user_id" not in cols:
                        continue
                    rows = conn.execute(
                        f"SELECT * FROM {table} WHERE user_id = ?",
                        (str(user_id),),
                    ).fetchall()
                    if rows:
                        serialized = []
                        for r in rows:
                            d = dict(r)
                            if isinstance(d.get(" preload"), bytes):
                                d.pop(" preload", None)
                            for k, v in list(d.items()):
                                if isinstance(v, bytes):
                                    d[k] = v.hex()
                            _try_parse_json_fields(d, ("chat", "meta", "data", "info", "params", "access_control"))
                            serialized.append(d)
                        export_data["tables"][table] = serialized
                except Exception:
                    pass
    except Exception:
        pass
    return export_data


def dump_sqlite_db_to_json(db_path: str, redact_tables: set[str] | None = None) -> dict:
    """Exporte toutes les tables d'une base SQLite locale (session.db ou identity.db) en dictionnaire JSON."""
    result = {}
    if not db_path or not os.path.isfile(db_path):
        return result
    redact = redact_tables or set()
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC"
                ).fetchall()
            ]
            for tbl in tables:
                if tbl in redact:
                    result[tbl] = ["***REDACTED_FOR_SECURITY***"]
                    continue
                try:
                    rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
                    tbl_rows = []
                    for r in rows:
                        d = dict(r)
                        for k, v in list(d.items()):
                            if isinstance(v, bytes):
                                d[k] = v.hex()
                        _try_parse_json_fields(
                            d,
                            ("payload", "value", "metadata", "args_json", "result_json"),
                        )
                        tbl_rows.append(d)
                    result[tbl] = tbl_rows
                except Exception:
                    pass
    except Exception:
        pass
    return result


def _copy_user_domain_tree(src_user_dir: str, dst_user_dir: str) -> dict:
    """Copie l'arbre du domaine utilisateur (/users/<uid>) avec dumps JSON des bases SQLite."""
    summary = {"files_copied": 0, "bytes_copied": 0, "sessions_exported": 0}
    if not src_user_dir or not os.path.isdir(src_user_dir):
        return summary

    real_src_root = os.path.realpath(src_user_dir)
    exclude_set = set(ECHO_SYNC_EXCLUDE_LIST or [])

    for root, dirs, files in os.walk(src_user_dir):
        dirs[:] = [d for d in dirs if d not in exclude_set]
        rel_dir = os.path.relpath(root, src_user_dir)
        target_dir = dst_user_dir if rel_dir == "." else os.path.join(dst_user_dir, rel_dir)
        os.makedirs(target_dir, exist_ok=True)

        for fname in files:
            if fname.endswith(("-wal", "-shm", ".pyc")):
                continue
            src_file = os.path.join(root, fname)
            dst_file = os.path.join(target_dir, fname)
            try:
                if os.path.islink(src_file):
                    real_target = os.path.realpath(src_file)
                    in_root = os.path.commonpath([real_src_root, real_target]) == real_src_root
                    if os.path.isfile(real_target) and in_root:
                        shutil.copy2(real_target, dst_file)
                        summary["files_copied"] += 1
                        summary["bytes_copied"] += os.path.getsize(dst_file)
                elif os.path.isfile(src_file):
                    shutil.copy2(src_file, dst_file)
                    summary["files_copied"] += 1
                    summary["bytes_copied"] += os.path.getsize(dst_file)

                    if fname == "session.db":
                        session_json = dump_sqlite_db_to_json(src_file)
                        json_path = os.path.join(target_dir, "session_db.json")
                        with open(json_path, "w", encoding="utf-8") as jf:
                            json.dump(session_json, jf, ensure_ascii=False, indent=2)
                        summary["sessions_exported"] += 1
                    elif fname == "identity.db":
                        identity_json = dump_sqlite_db_to_json(
                            src_file,
                            redact_tables={"auth_data", "identity_vault"},
                        )
                        json_path = os.path.join(target_dir, "identity_db_public.json")
                        with open(json_path, "w", encoding="utf-8") as jf:
                            json.dump(identity_json, jf, ensure_ascii=False, indent=2)
                        if os.path.exists(dst_file):
                            os.remove(dst_file)
            except Exception:
                pass
    return summary


def build_user_export_archive(
    user_id: str | None,
    email: str,
    display_name: str,
) -> tuple[str, str, str]:
    """
    Construit l'archive .tar.xz complète des données de l'utilisateur (Open WebUI + Qdrant JSON).
    Retourne (archive_path, download_filename, tmp_dir).
    """
    safe_key = sanitize_echo_id(user_id or email.split("@")[0], fallback="user")
    ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    download_filename = f"echo_user_export_{safe_key[:16]}_{ts_str}.tar.xz"

    tmp_dir = tempfile.mkdtemp(prefix=f"echo_export_{safe_key[:12]}_")
    staging_dir = os.path.join(tmp_dir, "export")
    webui_dir = os.path.join(staging_dir, "webui")
    qdrant_dir = os.path.join(staging_dir, "qdrant")
    user_domain_dir = os.path.join(staging_dir, "user_domain")

    os.makedirs(webui_dir, exist_ok=True)
    os.makedirs(qdrant_dir, exist_ok=True)
    os.makedirs(user_domain_dir, exist_ok=True)

    # 1. Export JSON des enregistrements Open WebUI (webui.db)
    webui_records = export_webui_user_records(user_id, email)
    resolved_uid = webui_records.get("user_id") or user_id
    with open(os.path.join(webui_dir, "user_profile_and_chats.json"), "w", encoding="utf-8") as f:
        json.dump(webui_records, f, ensure_ascii=False, indent=2)

    # 2. Copie du domaine de données utilisateur (/app/backend/data/users/<user_id>) + dumps SQLite JSON
    domain_summary = {"files_copied": 0, "bytes_copied": 0, "sessions_exported": 0}
    if resolved_uid:
        src_u_dir = os.path.join(ECHO_USERS_ROOT, sanitize_echo_id(resolved_uid))
        domain_summary = _copy_user_domain_tree(src_u_dir, user_domain_dir)

    # 3. Export JSON des collections Qdrant (echo_meta_artifacts & echo_session_rag)
    meta_points = scroll_user_qdrant_collection(COLLECTION_META_ARTIFACTS, resolved_uid)
    rag_points = scroll_user_qdrant_collection(COLLECTION_SESSION_RAG, resolved_uid)

    with open(os.path.join(qdrant_dir, "echo_meta_artifacts.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "collection": COLLECTION_META_ARTIFACTS,
                "user_id": resolved_uid,
                "count": len(meta_points),
                "points": meta_points,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    with open(os.path.join(qdrant_dir, "echo_session_rag.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "collection": COLLECTION_SESSION_RAG,
                "user_id": resolved_uid,
                "count": len(rag_points),
                "points": rag_points,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # 4. Manifeste de l'archive
    manifest = {
        "framework": "ECHO Framework",
        "version": get_echo_version(),
        "exported_at_iso": datetime.datetime.now().isoformat(),
        "user": {
            "email": email,
            "user_id": resolved_uid,
            "display_name": display_name,
        },
        "statistics": {
            "webui_chats_count": len((webui_records.get("tables") or {}).get("chat") or []),
            "user_domain_files": domain_summary["files_copied"],
            "user_domain_bytes": domain_summary["bytes_copied"],
            "session_dbs_exported": domain_summary["sessions_exported"],
            "qdrant_meta_artifacts_points": len(meta_points),
            "qdrant_session_rag_points": len(rag_points),
        },
    }
    with open(os.path.join(staging_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 5. Compression .tar.xz (tar -cJf sous Linux, fallback natif Python tarfile w:xz)
    archive_path = os.path.join(tmp_dir, download_filename)
    tar_used = False
    if os.name != "nt" and shutil.which("tar"):
        try:
            subprocess.run(
                ["tar", "-cJf", archive_path, "-C", staging_dir, "."],
                check=True,
                timeout=300,
            )
            tar_used = True
        except Exception:
            tar_used = False

    if not tar_used or not os.path.isfile(archive_path):
        with tarfile.open(archive_path, mode="w:xz") as tf:
            for item in os.listdir(staging_dir):
                tf.add(os.path.join(staging_dir, item), arcname=item)

    shutil.rmtree(staging_dir, ignore_errors=True)
    return archive_path, download_filename, tmp_dir


def stream_and_cleanup_archive(archive_path: str, tmp_dir: str):
    """Générateur streamant l'archive par blocs de 64 Ko et supprimant le dossier temporaire en fin de flux."""
    try:
        with open(archive_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
