# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER - DATA EXPORT SERVICE (TAR.XZ)
VERSION : 5.237.20.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.237.20.1: Décodage JSON natif de full_parts_json (message_shadows) et content_json (cognitive_threads) dans dump_sqlite_db_to_json.
# 5.237.5.1: Sanctuarisation de l'archive pendant le téléchargement (is_downloading),
#            purge conditionnée au succès strict à 100% et exposition de expires_at.
# 5.237.2.2: Priorité système minimale (nice 19, ionice idle c3, XZ_OPT="-T2 -1", IDLE_PRIORITY_CLASS) et régulation I/O.
# 5.237.2.1: Pipeline d'export asynchrone avec suivi de progression temps réel (0-100%), TTL de rétention (15 min) et annulation.
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
import time
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
from core.utils import _format_bytes, _get_tree_size, get_echo_version

__all__ = [
    "EXPORT_RETENTION_SEC",
    "get_user_export_lock",
    "get_user_export_status",
    "start_user_export_job",
    "cancel_user_export_job",
    "cleanup_expired_export_jobs",
    "count_user_qdrant_points",
    "scroll_user_qdrant_collection",
    "export_webui_user_records",
    "dump_sqlite_db_to_json",
    "build_user_export_archive",
    "stream_archive_file",
    "stream_and_cleanup_archive",
]

EXPORT_RETENTION_SEC = 900  # 15 minutes de disponibilité max avant purge automatique

_EXPORT_LOCKS_GUARD = threading.Lock()
_USER_EXPORT_LOCKS: dict[str, threading.Lock] = {}

_EXPORT_JOBS_LOCK = threading.Lock()
_EXPORT_JOBS: dict[str, dict] = {}
_EXPORT_SUBPROCESSES: dict[str, subprocess.Popen] = {}
_EXPORT_STOP_EVENTS: dict[str, threading.Event] = {}


def get_user_export_lock(user_key: str) -> threading.Lock:
    """Retourne un verrou dédié par utilisateur pour permettre les exports parallèles inter-utilisateurs."""
    key = str(user_key or "anonymous").strip().lower()
    with _EXPORT_LOCKS_GUARD:
        lock = _USER_EXPORT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _USER_EXPORT_LOCKS[key] = lock
        return lock


def cleanup_expired_export_jobs():
    """Purge les archives et dossiers temporaires expirés (> 15 min après génération)."""
    now = time.time()
    with _EXPORT_JOBS_LOCK:
        expired_keys = [
            k for k, j in _EXPORT_JOBS.items()
            if j.get("expires_at") and j["expires_at"] < now and not j.get("is_running") and not j.get("is_downloading")
        ]
        for k in expired_keys:
            job = _EXPORT_JOBS.pop(k, None)
            if job and job.get("tmp_dir") and os.path.isdir(job["tmp_dir"]):
                try:
                    shutil.rmtree(job["tmp_dir"], ignore_errors=True)
                except Exception:
                    pass


def get_user_export_status(user_key: str) -> dict:
    """Retourne l'état courant de l'export d'un utilisateur pour le polling JS."""
    cleanup_expired_export_jobs()
    key = str(user_key or "anonymous").strip().lower()
    with _EXPORT_JOBS_LOCK:
        job = _EXPORT_JOBS.get(key)
        if not job:
            return {
                "is_running": False,
                "ready": False,
                "percent": 0.0,
                "step": "Prêt",
                "filename": None,
                "size_fmt": None,
                "error": None,
                "expires_in_sec": None,
                "expires_at": None,
                "is_downloading": False,
            }
        expires_in = max(0, int(job["expires_at"] - time.time())) if job.get("expires_at") else None
        return {
            "is_running": bool(job.get("is_running")),
            "ready": bool(job.get("ready")),
            "percent": float(job.get("percent", 0.0)),
            "step": str(job.get("step") or ""),
            "filename": job.get("filename"),
            "size_fmt": job.get("size_fmt"),
            "error": job.get("error"),
            "expires_in_sec": expires_in,
            "expires_at": job.get("expires_at"),
            "is_downloading": bool(job.get("is_downloading", False)),
        }


def cancel_user_export_job(user_key: str) -> bool:
    """Annule l'export en cours ou supprime l'archive prête d'un utilisateur."""
    key = str(user_key or "anonymous").strip().lower()
    with _EXPORT_JOBS_LOCK:
        stop_event = _EXPORT_STOP_EVENTS.get(key)
        if stop_event:
            stop_event.set()
        proc = _EXPORT_SUBPROCESSES.get(key)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        job = _EXPORT_JOBS.pop(key, None)
        if job and job.get("tmp_dir") and os.path.isdir(job["tmp_dir"]):
            try:
                shutil.rmtree(job["tmp_dir"], ignore_errors=True)
            except Exception:
                pass
        return True


def start_user_export_job(
    user_id: str | None,
    email: str,
    display_name: str,
) -> dict:
    """Déclenche la préparation asynchrone de l'archive .tar.xz dans un thread d'arrière-plan."""
    cleanup_expired_export_jobs()
    key = str(user_id or email).strip().lower()

    with _EXPORT_JOBS_LOCK:
        existing = _EXPORT_JOBS.get(key)
        if existing and existing.get("is_running"):
            return get_user_export_status(key)

        if existing and existing.get("ready") and existing.get("archive_path") and os.path.isfile(existing["archive_path"]):
            return get_user_export_status(key)

        stop_event = threading.Event()
        _EXPORT_STOP_EVENTS[key] = stop_event
        _EXPORT_JOBS[key] = {
            "is_running": True,
            "is_downloading": False,
            "ready": False,
            "percent": 2.0,
            "step": "Initialisation de l'exportation...",
            "archive_path": None,
            "filename": None,
            "tmp_dir": None,
            "size_fmt": None,
            "error": None,
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
        }

    def _worker():
        try:
            if hasattr(os, "nice"):
                os.nice(19)
        except Exception:
            pass
        user_lock = get_user_export_lock(key)
        with user_lock:
            def _progress_cb(pct: float, step_msg: str):
                with _EXPORT_JOBS_LOCK:
                    j = _EXPORT_JOBS.get(key)
                    if j and j.get("is_running"):
                        j["percent"] = round(float(pct), 1)
                        j["step"] = step_msg

            try:
                arch_path, dl_name, tmp_dir = build_user_export_archive(
                    user_id=user_id,
                    email=email,
                    display_name=display_name,
                    progress_cb=_progress_cb,
                    stop_event=stop_event,
                    user_key=key,
                )
                if stop_event.is_set():
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    with _EXPORT_JOBS_LOCK:
                        _EXPORT_JOBS.pop(key, None)
                    return

                sz = os.path.getsize(arch_path) if os.path.isfile(arch_path) else 0
                with _EXPORT_JOBS_LOCK:
                    _EXPORT_JOBS[key] = {
                        "is_running": False,
                        "is_downloading": False,
                        "ready": True,
                        "percent": 100.0,
                        "step": "Archive prête pour le téléchargement !",
                        "archive_path": arch_path,
                        "filename": dl_name,
                        "tmp_dir": tmp_dir,
                        "size_bytes": sz,
                        "size_fmt": _format_bytes(sz),
                        "error": None,
                        "created_at": time.time(),
                        "expires_at": time.time() + EXPORT_RETENTION_SEC,
                    }
            except Exception as e:
                with _EXPORT_JOBS_LOCK:
                    _EXPORT_JOBS[key] = {
                        "is_running": False,
                        "is_downloading": False,
                        "ready": False,
                        "percent": 0.0,
                        "step": "Échec de l'exportation",
                        "error": str(e),
                        "created_at": time.time(),
                        "expires_at": time.time() + 60,
                    }
            finally:
                with _EXPORT_JOBS_LOCK:
                    _EXPORT_STOP_EVENTS.pop(key, None)
                    _EXPORT_SUBPROCESSES.pop(key, None)

    th = threading.Thread(target=_worker, name=f"export_{key[:12]}", daemon=True)
    th.start()
    return get_user_export_status(key)


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
                            ("payload", "value", "metadata", "args_json", "result_json", "full_parts_json", "content_json"),
                        )
                        tbl_rows.append(d)
                    result[tbl] = tbl_rows
                except Exception:
                    pass
    except Exception:
        pass
    return result


def _copy_user_domain_tree(
    src_user_dir: str,
    dst_user_dir: str,
    progress_cb=None,
    base_pct: float = 10.0,
    span_pct: float = 25.0,
) -> dict:
    """Copie l'arbre du domaine utilisateur (/users/<uid>) avec dumps JSON des bases SQLite et suivi de progression."""
    summary = {"files_copied": 0, "bytes_copied": 0, "sessions_exported": 0}
    if not src_user_dir or not os.path.isdir(src_user_dir):
        return summary

    total_src_bytes = max(1, _get_tree_size(src_user_dir))
    real_src_root = os.path.realpath(src_user_dir)
    exclude_set = set(ECHO_SYNC_EXCLUDE_LIST or [])
    last_sleep_bytes = 0

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

                if progress_cb and summary["files_copied"] % 5 == 0:
                    sub_ratio = min(1.0, summary["bytes_copied"] / total_src_bytes)
                    curr_pct = base_pct + (sub_ratio * span_pct)
                    progress_cb(
                        curr_pct,
                        f"Copie des fichiers personnels ({summary['files_copied']} fichiers - {_format_bytes(summary['bytes_copied'])})...",
                    )

                # Régulation I/O : micro-pause tous les 10 Mo pour céder les accès disque à la production
                if summary["bytes_copied"] - last_sleep_bytes >= 10 * 1024 * 1024:
                    last_sleep_bytes = summary["bytes_copied"]
                    time.sleep(0.005)
            except Exception:
                pass
    return summary


def build_user_export_archive(
    user_id: str | None,
    email: str,
    display_name: str,
    progress_cb=None,
    stop_event=None,
    user_key: str | None = None,
) -> tuple[str, str, str]:
    """
    Construit l'archive .tar.xz complète des données de l'utilisateur (Open WebUI + Qdrant JSON)
    avec suivi de progression temps réel et prise en charge de l'interruption (stop_event).
    Retourne (archive_path, download_filename, tmp_dir).
    """
    if stop_event and stop_event.is_set():
        raise RuntimeError("Export annulé par l'utilisateur")

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
    if progress_cb:
        progress_cb(5.0, "Export JSON du profil et historique des conversations (webui.db)...")

    if stop_event and stop_event.is_set():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("Export annulé par l'utilisateur")

    webui_records = export_webui_user_records(user_id, email)
    resolved_uid = webui_records.get("user_id") or user_id
    with open(os.path.join(webui_dir, "user_profile_and_chats.json"), "w", encoding="utf-8") as f:
        json.dump(webui_records, f, ensure_ascii=False, indent=2)

    # 2. Copie du domaine de données utilisateur (/app/backend/data/users/<user_id>) + dumps SQLite JSON
    if progress_cb:
        progress_cb(10.0, "Analyse et copie des données du domaine utilisateur...")

    if stop_event and stop_event.is_set():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("Export annulé par l'utilisateur")

    domain_summary = {"files_copied": 0, "bytes_copied": 0, "sessions_exported": 0}
    if resolved_uid:
        src_u_dir = os.path.join(ECHO_USERS_ROOT, sanitize_echo_id(resolved_uid))
        domain_summary = _copy_user_domain_tree(
            src_u_dir,
            user_domain_dir,
            progress_cb=progress_cb,
            base_pct=10.0,
            span_pct=25.0,
        )

    # 3. Export JSON des collections Qdrant (echo_meta_artifacts & echo_session_rag)
    if progress_cb:
        progress_cb(35.0, "Exportation des souvenirs et segments vectoriels Qdrant...")

    if stop_event and stop_event.is_set():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("Export annulé par l'utilisateur")

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

    if progress_cb:
        progress_cb(43.0, f"Export Qdrant terminé ({len(meta_points) + len(rag_points)} points).")

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

    # 5. Compression .tar.xz (tar -cJf sous Linux avec suivi live, fallback natif Python tarfile w:xz)
    archive_path = os.path.join(tmp_dir, download_filename)
    total_staging_bytes = max(1, _get_tree_size(staging_dir))
    tar_used = False

    if progress_cb:
        progress_cb(45.0, "Démarrage de la compression de l'archive tar.xz...")

    if os.name != "nt" and shutil.which("tar"):
        try:
            tar_cmd = ["tar", "-cJf", archive_path, "-C", staging_dir, "."]
            # Priorité I/O Idle (ionice -c 3) si disponible sous Linux
            if shutil.which("ionice"):
                tar_cmd = ["ionice", "-c", "3"] + tar_cmd
            # Priorité CPU la plus basse (nice -n 19)
            if shutil.which("nice"):
                tar_cmd = ["nice", "-n", "19"] + tar_cmd

            # Environnement de compression : bridage à 2 threads max et niveau rapide (-1)
            tar_env = os.environ.copy()
            tar_env["XZ_OPT"] = "-T2 -1"

            subp_kwargs = {"stderr": subprocess.PIPE, "text": True, "env": tar_env}
            if hasattr(os, "nice"):
                subp_kwargs["preexec_fn"] = lambda: os.nice(19)
            elif os.name == "nt":
                subp_kwargs["creationflags"] = 0x00000040  # IDLE_PRIORITY_CLASS

            proc = subprocess.Popen(
                tar_cmd,
                **subp_kwargs,
            )
            if user_key:
                with _EXPORT_JOBS_LOCK:
                    _EXPORT_SUBPROCESSES[user_key] = proc

            start_t = time.time()
            while proc.poll() is None:
                if stop_event and stop_event.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise RuntimeError("Export annulé par l'utilisateur")

                cur_sz = os.path.getsize(archive_path) if os.path.isfile(archive_path) else 0
                cur_sz_fmt = _format_bytes(cur_sz)

                read_b = 0
                try:
                    io_path = f"/proc/{proc.pid}/io"
                    if os.path.isfile(io_path):
                        with open(io_path, "r", encoding="utf-8") as f_io:
                            for line in f_io:
                                if line.startswith("read_bytes:"):
                                    read_b = int(line.split()[1])
                                    break
                except Exception:
                    pass

                if read_b > 0:
                    tar_pct = min(98.0, (read_b / total_staging_bytes) * 100.0)
                else:
                    elapsed = time.time() - start_t
                    tar_pct = min(95.0, (elapsed / (elapsed + 45.0)) * 100.0)

                overall_pct = 45.0 + (tar_pct / 100.0) * 50.0
                if progress_cb:
                    progress_cb(
                        overall_pct,
                        f"Compression tar.xz en cours... {tar_pct:.0f}% (Archive : {cur_sz_fmt})",
                    )
                time.sleep(1.0)

            if proc.returncode != 0:
                err_msg = (proc.stderr.read() if proc.stderr else "").strip()[:200]
                raise RuntimeError(f"Échec de la commande tar (code {proc.returncode}): {err_msg}")
            tar_used = True
        except RuntimeError:
            raise
        except Exception:
            tar_used = False
        finally:
            if user_key:
                with _EXPORT_JOBS_LOCK:
                    _EXPORT_SUBPROCESSES.pop(user_key, None)

    if not tar_used or not os.path.isfile(archive_path):
        if progress_cb:
            progress_cb(60.0, "Compression de l'archive tar.xz en cours...")
        with tarfile.open(archive_path, mode="w:xz") as tf:
            items = os.listdir(staging_dir)
            for idx, item in enumerate(items):
                if stop_event and stop_event.is_set():
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise RuntimeError("Export annulé par l'utilisateur")
                tf.add(os.path.join(staging_dir, item), arcname=item)
                if progress_cb:
                    p = 45.0 + ((idx + 1) / max(1, len(items))) * 50.0
                    progress_cb(p, f"Compression {item}... ({idx + 1}/{len(items)})")

    if progress_cb:
        final_sz = os.path.getsize(archive_path) if os.path.isfile(archive_path) else 0
        progress_cb(98.0, f"Archive compressée ({_format_bytes(final_sz)}). Finalisation...")

    shutil.rmtree(staging_dir, ignore_errors=True)
    return archive_path, download_filename, tmp_dir


def stream_archive_file(
    user_key: str,
    archive_path: str,
    tmp_dir: str,
    chunk_size: int = 65536,
):
    """
    Générateur de streaming sécurisé pour le téléchargement de l'archive .tar.xz.
    Marque le job en cours de téléchargement (is_downloading=True) pour sanctuariser l'archive
    face au nettoyage automatique (cleanup_expired_export_jobs).
    Ne purge l'archive et tmp_dir qu'en cas de succès strict à 100% (bytes_sent >= file_size).
    En cas de coupure réseau ou d'abandon côté client, l'archive est conservée jusqu'à expiration du TTL.
    """
    key = str(user_key or "anonymous").strip().lower()
    with _EXPORT_JOBS_LOCK:
        job = _EXPORT_JOBS.get(key)
        if job:
            job["is_downloading"] = True

    bytes_sent = 0
    file_size = os.path.getsize(archive_path) if (archive_path and os.path.isfile(archive_path)) else 0
    completed = False

    try:
        if file_size > 0:
            with open(archive_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    bytes_sent += len(chunk)
                    yield chunk
            if bytes_sent >= file_size:
                completed = True
    finally:
        with _EXPORT_JOBS_LOCK:
            job = _EXPORT_JOBS.get(key)
            if job:
                job["is_downloading"] = False
            if completed:
                _EXPORT_JOBS.pop(key, None)
                if user_key != key:
                    _EXPORT_JOBS.pop(user_key, None)

        if completed and tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def stream_and_cleanup_archive(archive_path: str, tmp_dir: str, on_cleanup=None):
    """Générateur streamant l'archive par blocs de 64 Ko et supprimant le dossier temporaire en fin de flux."""
    try:
        if os.path.isfile(archive_path):
            with open(archive_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if callable(on_cleanup):
            try:
                on_cleanup()
            except Exception:
                pass
