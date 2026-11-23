# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - SEMANTIC PRUNING & GC
VERSION : 6.20.4.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.20.4.1: Filet de sécurité sur les symlinks UPLOADS_DIR pointant sous ECHO_USERS_ROOT et Étape 3.bis de purge des fichiers orphelins dans TMP_UPLOADS_DIR (.tmp_uploads).
# 6.18.23.1: Durcissement SQLite WAL via open_sqlite_db, réalignement GC fichiers sur la topologie maître users/{uid}/files/ (symlinks chats/{cid}/files/), et appel filet cleanup_expired_purged_subagents_sync (Contrat C2).
# 6.18.3.1: Préservation du wildcard public principal_id != '*' lors de la purge des orphelins access_grant (évite la suppression des grants publics model/tool).
# 6.18.0.1: Compatibilité ECHO v6 (API Safeguard /api/v1/users/ + follow_redirects, GC fichiers dual-topologie EchoStorageProvider/Download Broker, délégation browserviews+MP4 à cleanup_expired_browserviews_sync, purge webui.db auth/access_grant/automation_run et levée du filtre len<=30 dans purge_user_artifacts).
# 6.15.1.1: Respect du délai de grâce BROWSER_TRANSIENT_GRACE_SEC (1800.0s) avant purge des captures browserviews éphémères.
import datetime
import html
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import time
import traceback
from core.config import (
    BROWSER_WORKER_URL,
    COLLECTION_META_ARTIFACTS,
    COLLECTION_SESSION_RAG,
    DEFAULT_MAINT_CONFIG,
    DOWNLOADS_DIR,
    ECHO_GC_GRACE_PERIOD_SEC,
    ECHO_USERS_ROOT,
    HAS_ECHO_LIBS,
    HAS_HTTPX,
    MAINTENANCE_STATE,
    MCP_CACHE_DIR,
    N8N_DB_PATH,
    N8N_WORKER_URL,
    OWUI_ADMIN_SECRET_PATH,
    QDRANT_URL,
    SYSTEM_ADMIN_EMAIL,
    TMP_UPLOADS_DIR,
    UPLOADS_DIR,
    WEBUI_DB_PATH,
    WEBUI_URL,
    httpx,
)
from core.utils import _format_bytes, _get_tree_size
from core.security import open_sqlite_db
from core.settings_io import (
    load_maint_config,
    save_maint_config,
    save_maint_report,
)
from services.memory_consolidation import consolidate_memories_for_user

logger = logging.getLogger("ECHO-PRUNING")
OWUI_DB_PATH = WEBUI_DB_PATH

if HAS_ECHO_LIBS:
    from echo_session_gc import gc_session_db, build_session_rag_point_id
    from echo_constants import (
        BROWSER_CLIP_EXT,
        BROWSER_TRANSIENT_GRACE_SEC,
        SESSION_RAG_CONVERSATION_SOURCE_ID,
    )
    try:
        from echo_browser_lib import cleanup_expired_browserviews_sync
    except ImportError:
        cleanup_expired_browserviews_sync = None
    try:
        from echo_subagent_hub import cleanup_expired_purged_subagents_sync
    except ImportError:
        cleanup_expired_purged_subagents_sync = None
else:
    BROWSER_CLIP_EXT = "_clip.mp4"
    BROWSER_TRANSIENT_GRACE_SEC = 1800.0
    cleanup_expired_browserviews_sync = None
    cleanup_expired_purged_subagents_sync = None


def _delete_rag_points(uid: str, chat_id: str, seeds: list) -> int:
    """Purge Qdrant hybride : filtre message_id (nouveaux points) + fallback uuid5 (points legacy)."""
    if not seeds or not HAS_HTTPX or not HAS_ECHO_LIBS:
        return 0
    deleted = 0
    try:
        with httpx.Client(timeout=10.0) as client:
            # 1. Suppression principale par filtre payload message_id
            del_url = f"{QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/delete"
            filter_payload = {
                "filter": {
                    "must": [
                        {"key": "user_id", "match": {"value": str(uid)}},
                        {"key": "chat_id", "match": {"value": str(chat_id)}},
                        {"key": "message_id", "match": {"any": [str(s) for s in seeds]}},
                    ]
                }
            }
            resp = client.post(del_url, json=filter_payload)
            if resp.status_code == 200:
                deleted += len(seeds)

            # 2. Fallback points legacy sans message_id via déterminisme uuid5 (lots de 32 indices)
            pts_url = f"{QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points"
            legacy_ids = []
            for seed in seeds:
                for batch_idx in range(64):
                    batch_ids = [
                        build_session_rag_point_id(
                            str(uid),
                            str(chat_id),
                            SESSION_RAG_CONVERSATION_SOURCE_ID,
                            batch_idx * 32 + offset,
                            unique_seed=str(seed),
                        )
                        for offset in range(32)
                    ]
                    r_pts = client.post(pts_url, json={"ids": batch_ids, "with_payload": False})
                    if r_pts.status_code != 200:
                        break
                    found = [pt.get("id") for pt in (r_pts.json().get("result") or []) if pt.get("id")]
                    if not found:
                        break
                    legacy_ids.extend(found)
            if legacy_ids:
                client.post(del_url, json={"points": legacy_ids})
                deleted += len(legacy_ids)
    except Exception as e:
        logger.warning(f"Purge RAG branches mortes échouée ({chat_id}) : {e}")
    return deleted


__all__ = [
    'run_semantic_pruning',
    '_run_semantic_pruning',
    'purge_user_artifacts',
]


def purge_user_artifacts(uid: str):
    """Purge atomique multi-volumes des artefacts d'un utilisateur certifié supprimé de la SSoT webui.db."""
    uid = str(uid or "").strip()
    if not uid or uid.startswith(".") or "/" in uid or "\\" in uid or ".." in uid:
        return

    user_dir = Path(ECHO_USERS_ROOT) / uid

    # 1. N8N (echo-n8n-data) : arrêt et suppression des workflows de l'utilisateur avant suppression de son dossier
    wf_ids_to_delete = set()
    if user_dir.exists() and user_dir.is_dir():
        chats_dir = user_dir / "chats"
        if chats_dir.exists() and chats_dir.is_dir():
            for cdir in chats_dir.iterdir():
                if not cdir.is_dir():
                    continue
                wf_dir = cdir / "n8n_workflows"
                if wf_dir.exists() and wf_dir.is_dir():
                    for f in wf_dir.iterdir():
                        if f.is_file() and f.name.endswith(".json"):
                            wf_ids_to_delete.add(f.stem)
                session_db_path = cdir / "session.db"
                if session_db_path.exists():
                    try:
                        with open_sqlite_db(str(session_db_path), readonly=True, timeout=5.0) as s_conn:
                            cur = s_conn.execute(
                                "SELECT status FROM echo_resources WHERE resource_type = 'n8n_workflow' AND status LIKE 'deployed_as_%'"
                            )
                            for row in cur.fetchall():
                                if row and row[0]:
                                    wf_ids_to_delete.add(str(row[0]).replace("deployed_as_", ""))
                    except Exception:
                        pass
        tpl_dir = user_dir / "n8n_workflow_templates"
        if tpl_dir.exists() and tpl_dir.is_dir():
            for f in tpl_dir.iterdir():
                if f.is_file() and f.name.endswith(".json"):
                    wf_ids_to_delete.add(f.stem)

    if HAS_HTTPX and httpx is not None and wf_ids_to_delete:
        for wf_id in wf_ids_to_delete:
            try:
                httpx.delete(f"{N8N_WORKER_URL}/workflow/{wf_id}", timeout=15.0)
            except Exception:
                pass
        if os.path.exists(N8N_DB_PATH):
            try:
                with open_sqlite_db(N8N_DB_PATH, timeout=10.0, auto_commit=False) as n8n_db:
                    n8n_db.execute("VACUUM;")
            except Exception:
                pass

    # 2. MCP Cache (echo-mcp-cache) : suppression de /app/mcp_cache/<service>/<uid>
    mcp_root = Path(MCP_CACHE_DIR)
    if mcp_root.exists() and mcp_root.is_dir():
        for svc_dir in mcp_root.iterdir():
            if not svc_dir.is_dir():
                continue
            target_mcp_user = svc_dir / uid
            if target_mcp_user.exists() and target_mcp_user.is_dir():
                shutil.rmtree(str(target_mcp_user), ignore_errors=True)
            try:
                if not any(svc_dir.iterdir()):
                    svc_dir.rmdir()
            except Exception:
                pass

    # 3. Drop Zone (echo-download-broker-data) : suppression de /app/downloads/<uid>
    dl_user_dir = Path(DOWNLOADS_DIR) / uid
    if dl_user_dir.exists() and dl_user_dir.is_dir():
        shutil.rmtree(str(dl_user_dir), ignore_errors=True)

    # 4. Espace Personnel (echo-webui-data) : suppression de /app/backend/data/users/<uid>
    if user_dir.exists() and user_dir.is_dir():
        shutil.rmtree(str(user_dir), ignore_errors=True)

    # 5. Qdrant (echo-qdrant-data) : suppression des vecteurs de l'utilisateur
    if HAS_HTTPX and httpx is not None:
        q_filter = {"filter": {"must": [{"key": "user_id", "match": {"value": uid}}]}}
        for col in (COLLECTION_META_ARTIFACTS, COLLECTION_SESSION_RAG):
            try:
                httpx.post(f"{QDRANT_URL}/collections/{col}/points/delete", json=q_filter, timeout=15.0)
            except Exception:
                pass


def run_semantic_pruning(silent=False):
    """Wrapper pour la Purge Temporelle gérant l'état global asynchrone."""
    if MAINTENANCE_STATE["is_running"]:
        print("⚠️ [ECHO-LIFECYCLE] Purge déjà en cours, annulation.")
        return "Déjà en cours", {}
    
    MAINTENANCE_STATE["is_running"] = True
    MAINTENANCE_STATE["task_type"] = "pruning"
    MAINTENANCE_STATE["start_time"] = time.time()
    MAINTENANCE_STATE["percent"] = 5
    MAINTENANCE_STATE["status"] = "Élagage : Démarrage (Attente des services)..."
    try:
        return _run_semantic_pruning(silent=silent)
    finally:
        MAINTENANCE_STATE["is_running"] = False
        MAINTENANCE_STATE["task_type"] = ""
        MAINTENANCE_STATE["status"] = ""
        MAINTENANCE_STATE["percent"] = 0
        MAINTENANCE_STATE["start_time"] = None

def _run_semantic_pruning(silent=False):
    print("🧬 [ECHO-LIFECYCLE] Démarrage...")
    
    # 0. Wait-for-it (Asynchrone 1h max) : Attente de dispo post-backup
    services_ready = False
    for _ in range(60):
        MAINTENANCE_STATE["percent"] = 8
        MAINTENANCE_STATE["status"] = f"Élagage : Attente des services (Itération {_+1}/60)..."
        
        db_ok = False
        qdrant_ok = False
        owui_ok = False
        
        # Check SQLite SSoT (existence, taille > 0, présence d'au moins 1 user et table chat lisible)
        if os.path.exists(WEBUI_DB_PATH) and os.path.getsize(WEBUI_DB_PATH) > 0:
            try:
                with open_sqlite_db(WEBUI_DB_PATH, readonly=True, timeout=5.0) as conn:
                    first_user = conn.execute("SELECT id FROM user LIMIT 1").fetchone()
                    conn.execute("SELECT COUNT(id) FROM chat").fetchone()
                    if first_user is not None:
                        db_ok = True
            except Exception as e:
                print(f"⚠️ [ECHO-LIFECYCLE] Probe SQLite Error: {e}")
        else:
            print(f"⚠️ [ECHO-LIFECYCLE] Probe SQLite: Fichier introuvable ou vide {WEBUI_DB_PATH}")
        # Check Qdrant
        if HAS_HTTPX:
            try:
                r = httpx.get(f"{QDRANT_URL}/collections", timeout=5.0)
                if r.status_code == 200:
                    qdrant_ok = True
            except Exception:
                pass
        else:
            qdrant_ok = False
            
        # Check Open WebUI API
        if HAS_HTTPX:
            try:
                r_owui = httpx.get(f"{WEBUI_URL}/health", timeout=5.0)
                if r_owui.status_code == 200:
                    owui_ok = True
            except Exception:
                pass
        else:
            owui_ok = False
            
        if db_ok and qdrant_ok and owui_ok:
            services_ready = True
            break
            
        # Sommeil d'une minute après vérification, pour laisser le temps à l'orchestrateur
        # de redémarrer l'infrastructure lors d'une sauvegarde planifiée.
        time.sleep(60)
        
    if not services_ready:
        err_msg = f"Pruning annulé : Délai d'attente dépassé (1h). Diag: db_ok={db_ok}, qdrant_ok={qdrant_ok}, owui_ok={owui_ok}"
        print(f"❌ [ECHO-LIFECYCLE] {err_msg}")
        if not silent:
            save_maint_report(err_msg)
        raise TimeoutError(err_msg)

    config = load_maint_config()
    
    MAINTENANCE_STATE["percent"] = 15
    MAINTENANCE_STATE["status"] = "Élagage (1/7) : API Safeguard (OWUI)..."
    
    # 1. API Safeguard (Garde-fou Fail-Closed)
    if not HAS_HTTPX or httpx is None:
        err_msg = "API Safeguard: Module httpx indisponible. Pruning annulé par sécurité."
        print(f"❌ {err_msg}")
        if not silent:
            save_maint_report(err_msg)
        return err_msg, {}

    try:
        admin_pwd = ""
        if os.path.exists(OWUI_ADMIN_SECRET_PATH):
            with open(OWUI_ADMIN_SECRET_PATH, "r") as f:
                admin_pwd = f.read().strip()
        
        # Authentification de secours/admin pour obtenir un vrai jeton JWT
        owui_url = WEBUI_URL
        
        # Injection de l'en-tête SSO si le domaine est configuré (cf. stack-echo.yml)
        extra_headers = {}
        if os.environ.get("ECHO_DOMAIN"):
            extra_headers["X-Webui-User"] = SYSTEM_ADMIN_EMAIL
            
        r_signin = httpx.post(f"{owui_url}/api/v1/auths/signin", json={
            "email": SYSTEM_ADMIN_EMAIL,
            "password": admin_pwd
        }, headers=extra_headers, timeout=10)
        
        if r_signin.status_code != 200:
            err_msg = f"API Safeguard: Connexion OWUI échouée (HTTP {r_signin.status_code}). Pruning annulé."
            print(f"❌ {err_msg}")
            if not silent:
                save_maint_report(err_msg)
            return err_msg, {}
            
        admin_token = r_signin.json().get("token")
        if not admin_token:
            err_msg = "API Safeguard: Aucun jeton JWT reçu. Pruning annulé."
            print(f"❌ {err_msg}")
            if not silent:
                save_maint_report(err_msg)
            return err_msg, {}

        auth_headers = {"Authorization": f"Bearer {admin_token}"}
        auth_headers.update(extra_headers)
        
        r_auth = httpx.get(f"{owui_url}/api/v1/users/", headers=auth_headers, follow_redirects=True, timeout=10)
        if r_auth.status_code != 200:
            err_msg = f"API Safeguard: Auth OWUI échouée (HTTP {r_auth.status_code}). Pruning annulé."
            print(f"❌ {err_msg}")
            if not silent:
                save_maint_report(err_msg)
            return err_msg, {}
    except Exception as e:
        err_msg = f"API Safeguard: OWUI injoignable ({str(e)}). Pruning annulé."
        print(f"❌ {err_msg}")
        if not silent:
            save_maint_report(err_msg)
        return err_msg, {}

    MAINTENANCE_STATE["percent"] = 25
    MAINTENANCE_STATE["status"] = "Élagage (2/7) : SSoT SQLite Safeguard & Inspection..."

    # 2. Garde-fous BDD SQLite SSoT (Fail-Closed : Base injoignable/invalide = ZÉRO ACTION)
    if not os.path.exists(WEBUI_DB_PATH) or os.path.getsize(WEBUI_DB_PATH) == 0:
        err_msg = f"BDD Safeguard: Fichier de base de données SSoT introuvable ou vide ({WEBUI_DB_PATH}). Pruning annulé."
        print(f"❌ {err_msg}")
        if not silent:
            save_maint_report(err_msg)
        return err_msg, {}

    try:
        with open_sqlite_db(WEBUI_DB_PATH, readonly=True, timeout=10.0) as conn:
            chk = conn.execute("PRAGMA quick_check(1)").fetchone()
            if not chk or chk[0] != "ok":
                raise sqlite3.DatabaseError(f"PRAGMA quick_check(1) en échec : {chk}")
            user_rows = conn.execute("SELECT id, email FROM user").fetchall()
            chat_rows = conn.execute("SELECT id, user_id FROM chat").fetchall()
            try:
                file_rows = conn.execute("SELECT id, user_id FROM file").fetchall()
            except sqlite3.OperationalError:
                file_rows = []
    except Exception as e:
        err_msg = f"BDD Safeguard: Base SSoT webui.db injoignable, verrouillée ou corrompue ({e}). Pruning annulé."
        print(f"❌ {err_msg}")
        if not silent:
            save_maint_report(err_msg)
        return err_msg, {}

    valid_ids = {str(r[0]) for r in user_rows if r and r[0]}
    valid_emails = {str(r[1]).strip().lower() for r in user_rows if r and len(r) > 1 and r[1]}

    if not valid_ids or SYSTEM_ADMIN_EMAIL.lower() not in valid_emails:
        err_msg = "BDD Safeguard: Table user vide ou compte système absent dans webui.db. Pruning annulé par sécurité."
        print(f"❌ {err_msg}")
        if not silent:
            save_maint_report(err_msg)
        return err_msg, {}

    # Cartographie stricte des chats et fichiers valides par propriétaire SSoT
    user_valid_chats = {uid: set() for uid in valid_ids}
    for r in chat_rows:
        if r and r[0] and str(r[1]) in user_valid_chats:
            user_valid_chats[str(r[1])].add(str(r[0]))
    user_valid_file_ids = {uid: set() for uid in valid_ids}
    for r in file_rows:
        if r and r[0] and str(r[1]) in user_valid_file_ids:
            user_valid_file_ids[str(r[1])].add(str(r[0]))
    all_valid_file_ids = {fid for fids in user_valid_file_ids.values() for fid in fids}
    db_valid_chats = {cid for cids in user_valid_chats.values() for cid in cids}

    stats = {
        "users": {"c": 0, "b": 0},
        "chats": {"c": 0, "b": 0},
        "sessions_db": {"c": 0, "b": 0},
        "dead_branches": {"chats": 0, "rows": 0, "rag_points": 0},
        "files": {"c": 0, "b": 0},
        "mcp_cache": {"c": 0, "b": 0},
        "drop_users": {"c": 0, "b": 0},
        "drop_chats": {"c": 0, "b": 0},
        "drop_n8n": {"c": 0, "b": 0},
        "sqlite_ghosts": 0,
        "sqlite_vacuumed": 0,
        "promoted_clusters": 0
    }
    qdrant_synced = False
    pruning_error = ""  # Exception du bloc DB/Espace Personnel (remontée dans le rapport)
    libs_warning_line = ""

    if os.path.exists(ECHO_USERS_ROOT):
        try:
            MAINTENANCE_STATE["percent"] = 35
            MAINTENANCE_STATE["status"] = "Élagage (3/7) : Génération liste blanche N8N..."
            
            # --- A0. N8N Safeguard & Garbage Collection ---
            n8n_active_ids = []
            n8n_whitelist_complete = True
            for folder in valid_ids:
                valid_u_chats = user_valid_chats.get(folder, set())
                user_chats_dir = os.path.join(ECHO_USERS_ROOT, folder, "chats")
                if os.path.exists(user_chats_dir):
                    try:
                        chat_entries = os.listdir(user_chats_dir)
                    except Exception as e:
                        print(f"⚠️ [ECHO-LIFECYCLE] Lecture impossible de {user_chats_dir}: {e}")
                        n8n_whitelist_complete = False
                        continue
                    for cdir in chat_entries:
                        if cdir in valid_u_chats:
                            wf_dir = os.path.join(user_chats_dir, cdir, "n8n_workflows")
                            if os.path.exists(wf_dir):
                                try:
                                    for f in os.listdir(wf_dir):
                                        if f.endswith('.json'):
                                            n8n_active_ids.append(f.split('.')[0])
                                except Exception as e:
                                    print(f"⚠️ [ECHO-LIFECYCLE] Lecture impossible de {wf_dir}: {e}")
                                    n8n_whitelist_complete = False
                            session_db_path = os.path.join(user_chats_dir, cdir, "session.db")
                            if os.path.exists(session_db_path):
                                try:
                                    with open_sqlite_db(session_db_path, readonly=True, timeout=5.0) as s_conn:
                                        cur = s_conn.execute("SELECT id, status FROM echo_resources WHERE resource_type = 'n8n_workflow'")
                                        for row in cur.fetchall():
                                            wf_echo_id, st = row[0], row[1]
                                            if wf_echo_id:
                                                n8n_active_ids.append(wf_echo_id)
                                            if st and st.startswith("deployed_as_"):
                                                real_id = st.replace("deployed_as_", "")
                                                n8n_active_ids.append(real_id)
                                except sqlite3.OperationalError as e:
                                    if "no such table" not in str(e).lower():
                                        print(f"⚠️ [ECHO-LIFECYCLE] Erreur SQL session.db pour {folder}/{cdir}: {e}")
                                        n8n_whitelist_complete = False
                                except Exception as e:
                                    print(f"⚠️ [ECHO-LIFECYCLE] Erreur session.db N8N daemon parse pour {folder}/{cdir}: {e}")
                                    n8n_whitelist_complete = False
                                    
                tpl_dir = os.path.join(ECHO_USERS_ROOT, folder, "n8n_workflow_templates")
                if os.path.exists(tpl_dir):
                    try:
                        for f in os.listdir(tpl_dir):
                            if f.endswith('.json'):
                                n8n_active_ids.append(f.split('.')[0])
                    except Exception as e:
                        print(f"⚠️ [ECHO-LIFECYCLE] Lecture impossible de {tpl_dir}: {e}")
                        n8n_whitelist_complete = False
                            
            if HAS_HTTPX and n8n_whitelist_complete:
                try:
                    r = httpx.post(
                        f"{N8N_WORKER_URL}/prune",
                        json={"active_ids": n8n_active_ids},
                        timeout=60.0
                    )
                    if r.status_code != 200:
                        raise Exception(f"HTTP {r.status_code}: {r.text}")
                    print(f"✅ [ECHO-LIFECYCLE] N8N Safeguard: Purge exécutée ({len(n8n_active_ids)} IDs whitelist).")
                except Exception as e:
                    # Non-bloquant : un N8N down ne doit pas paralyser le pruning global
                    print(f"⚠️ [ECHO-LIFECYCLE] N8N Safeguard: {e}. Purge N8N ignorée (non bloquant).")
            elif not n8n_whitelist_complete:
                print("⚠️ [ECHO-LIFECYCLE] N8N Safeguard: Whitelist incomplète (session.db verrouillée), appel /prune annulé par sécurité.")

            # --- A0.bis Garbage Collection du MCP Cache (/app/mcp_cache) ---
            MAINTENANCE_STATE["percent"] = 40
            MAINTENANCE_STATE["status"] = "Élagage (3/7) : Garbage Collection du MCP Cache..."
            mcp_root = Path(MCP_CACHE_DIR)
            if mcp_root.exists() and mcp_root.is_dir():
                for svc_dir in mcp_root.iterdir():
                    if not svc_dir.is_dir():
                        continue
                    for uid_dir in svc_dir.iterdir():
                        if not uid_dir.is_dir() or uid_dir.name.startswith("."):
                            continue
                        if uid_dir.name not in valid_ids:
                            try:
                                sz = _get_tree_size(str(uid_dir))
                                shutil.rmtree(str(uid_dir), ignore_errors=True)
                                stats["mcp_cache"]["c"] += 1
                                stats["mcp_cache"]["b"] += sz
                            except Exception:
                                pass
                    try:
                        if not any(svc_dir.iterdir()):
                            svc_dir.rmdir()
                    except Exception:
                        pass

            MAINTENANCE_STATE["percent"] = 45
            MAINTENANCE_STATE["status"] = "Élagage (4/7) : Garbage Collection des Drop Zones..."
            downloads_root = Path(DOWNLOADS_DIR)
            if downloads_root.exists() and downloads_root.is_dir():
                active_wf_set = set(n8n_active_ids)
                for uid_dir in downloads_root.iterdir():
                    if not uid_dir.is_dir() or uid_dir.name.startswith("."):
                        continue
                    
                    # 1. Purge Utilisateur Orphelin
                    if uid_dir.name not in valid_ids:
                        try:
                            sz = _get_tree_size(str(uid_dir))
                            shutil.rmtree(str(uid_dir), ignore_errors=True)
                            stats["drop_users"]["c"] += 1
                            stats["drop_users"]["b"] += sz
                        except Exception:
                            pass
                        continue

                    valid_u_chats = user_valid_chats.get(uid_dir.name, set())
                    for cid_dir in uid_dir.iterdir():
                        if not cid_dir.is_dir() or cid_dir.name.startswith("."):
                            continue
                        
                        # 2. Purge Chat Orphelin (vérification stricte par propriétaire)
                        if cid_dir.name not in valid_u_chats:
                            try:
                                sz = _get_tree_size(str(cid_dir))
                                shutil.rmtree(str(cid_dir), ignore_errors=True)
                                stats["drop_chats"]["c"] += 1
                                stats["drop_chats"]["b"] += sz
                            except Exception:
                                pass
                            continue
                            
                        # 3. Purge Workflows N8N (Les dossiers "browser" sont purgés avec cid_dir)
                        n8n_dir = cid_dir / "n8n"
                        if n8n_whitelist_complete and n8n_dir.exists() and n8n_dir.is_dir():
                            for wf_dir in n8n_dir.iterdir():
                                if wf_dir.is_dir() and wf_dir.name not in active_wf_set:
                                    try:
                                        sz = _get_tree_size(str(wf_dir))
                                        shutil.rmtree(str(wf_dir), ignore_errors=True)
                                        stats["drop_n8n"]["c"] += 1
                                        stats["drop_n8n"]["b"] += sz
                                    except Exception:
                                        pass

            MAINTENANCE_STATE["percent"] = 55
            MAINTENANCE_STATE["status"] = "Élagage (4/7) : Purge des dossiers de l'Espace Personnel (FS)..."
            libs_warning_line = ""

            # --- A. Purge des dossiers de l'Espace Personnel ---
            if config.get("purge_orphaned_users", False):
                for folder in os.listdir(ECHO_USERS_ROOT):
                    if folder.startswith("."):
                        continue
                    folder_path = os.path.join(ECHO_USERS_ROOT, folder)
                    if os.path.isdir(folder_path) and folder not in valid_ids:
                        sz = _get_tree_size(folder_path)
                        # Dénombrement préalable des session.db contenues
                        u_s_db_count = 0
                        u_s_db_size = 0
                        for root, _, files in os.walk(folder_path):
                            if "session.db" in files:
                                f_p = os.path.join(root, "session.db")
                                try:
                                    u_s_db_size += os.path.getsize(f_p)
                                    u_s_db_count += 1
                                except Exception:
                                    pass
                        shutil.rmtree(folder_path, ignore_errors=True)
                        if not os.path.exists(folder_path):
                            stats["users"]["c"] += 1
                            stats["users"]["b"] += sz
                            stats["sessions_db"]["c"] += u_s_db_count
                            stats["sessions_db"]["b"] += u_s_db_size

            if config.get("purge_orphaned_chats", False):
                for folder in os.listdir(ECHO_USERS_ROOT):
                    if folder.startswith("."):
                        continue
                    folder_path = os.path.join(ECHO_USERS_ROOT, folder)
                    if not os.path.isdir(folder_path):
                        continue
                    valid_u_chats = user_valid_chats.get(folder, set())
                    user_chats_dir = os.path.join(folder_path, "chats")
                    if os.path.exists(user_chats_dir):
                        for cdir in os.listdir(user_chats_dir):
                            cdir_path = os.path.join(user_chats_dir, cdir)
                            if os.path.isdir(cdir_path):
                                chat_id = cdir
                                if chat_id not in valid_u_chats:
                                    s_db_path = os.path.join(cdir_path, "session.db")
                                    has_s_db = os.path.exists(s_db_path)
                                    s_db_size = os.path.getsize(s_db_path) if has_s_db else 0
                                    sz = _get_tree_size(cdir_path)
                                    shutil.rmtree(cdir_path, ignore_errors=True)
                                    if not os.path.exists(cdir_path):
                                        stats["chats"]["c"] += 1
                                        stats["chats"]["b"] += sz
                                        if has_s_db:
                                            stats["sessions_db"]["c"] += 1
                                            stats["sessions_db"]["b"] += s_db_size
                    # Nettoyage des dossiers fantômes hors SSoT (ex: system ou email) dépourvus de chats valides
                    if folder not in valid_ids and len(folder) <= 30:
                        sz = _get_tree_size(folder_path)
                        shutil.rmtree(folder_path, ignore_errors=True)
                        if not os.path.exists(folder_path):
                            stats["users"]["c"] += 1
                            stats["users"]["b"] += sz

                # --- A.bis Purge Granulaire des Fichiers (Topologie Unifiée v6 : maître users/{uid}/files) ---
                if HAS_ECHO_LIBS and ECHO_GC_GRACE_PERIOD_SEC is not None:
                    MAINTENANCE_STATE["percent"] = 65
                    MAINTENANCE_STATE["status"] = "Élagage (4/7) : Garbage Collection des fichiers (FS)..."
                    grace_period = time.time() - ECHO_GC_GRACE_PERIOD_SEC

                    # Étape 1 : Cartographier les symlinks valides dans les chats actifs (et purger les symlinks cassés)
                    active_symlink_targets = set()
                    for folder in valid_ids:
                        valid_u_chats = user_valid_chats.get(folder, set())
                        user_chats_dir = os.path.join(ECHO_USERS_ROOT, folder, "chats")
                        if os.path.exists(user_chats_dir):
                            for cdir in os.listdir(user_chats_dir):
                                if cdir in valid_u_chats:
                                    chat_files_dir = os.path.join(user_chats_dir, cdir, "files")
                                    if os.path.exists(chat_files_dir):
                                        for f_name in os.listdir(chat_files_dir):
                                            f_path = os.path.join(chat_files_dir, f_name)
                                            if os.path.islink(f_path):
                                                if os.path.exists(f_path):
                                                    try:
                                                        active_symlink_targets.add(os.path.realpath(f_path))
                                                    except Exception:
                                                        pass
                                                else:
                                                    try:
                                                        os.unlink(f_path)
                                                        stats["files"]["c"] += 1
                                                    except Exception:
                                                        pass

                    # Étape 2 : Nettoyer users/<uid>/files/ (dead symlinks v6 + physiques orphelins hors webui.db et hors chats actifs)
                    for folder in valid_ids:
                        user_files_dir = os.path.join(ECHO_USERS_ROOT, folder, "files")
                        valid_u_fids = user_valid_file_ids.get(folder, set())
                        if os.path.exists(user_files_dir):
                            for f_name in os.listdir(user_files_dir):
                                f_path = os.path.join(user_files_dir, f_name)
                                if os.path.islink(f_path):
                                    # Topologie A v6 : symlink miroir vers chats/<cid>/files devenu mort après purge du chat
                                    if not os.path.exists(f_path):
                                        try:
                                            os.unlink(f_path)
                                            stats["files"]["c"] += 1
                                        except Exception:
                                            pass
                                elif os.path.isfile(f_path):
                                    file_prefix = f_name.split("_", 1)[0]
                                    real_fp = os.path.realpath(f_path)
                                    is_referenced = (
                                        real_fp in active_symlink_targets
                                        or f_path in active_symlink_targets
                                        or file_prefix in valid_u_fids
                                        or f_name in valid_u_fids
                                    )
                                    if not is_referenced and os.path.getmtime(f_path) < grace_period:
                                        try:
                                            sz = os.path.getsize(f_path) if os.path.exists(f_path) else 0
                                            os.remove(f_path)
                                            stats["files"]["c"] += 1
                                            stats["files"]["b"] += sz
                                        except Exception:
                                            pass

                    # Étape 3 : Purge résiduelle historique d'UPLOADS_DIR (aucune nouvelle écriture/symlink n'y est créée)
                    if os.path.exists(UPLOADS_DIR):
                        users_root_real = os.path.realpath(ECHO_USERS_ROOT)
                        for f_name in os.listdir(UPLOADS_DIR):
                            f_path = os.path.join(UPLOADS_DIR, f_name)
                            if os.path.islink(f_path):
                                try:
                                    target_real = os.path.realpath(f_path)
                                except Exception:
                                    target_real = ""
                                if not os.path.exists(f_path) or target_real.startswith(users_root_real + os.sep):
                                    try:
                                        os.unlink(f_path)
                                        stats["files"]["c"] += 1
                                    except Exception:
                                        pass
                            elif os.path.isfile(f_path):
                                file_prefix = f_name.split("_", 1)[0]
                                if (
                                    file_prefix not in all_valid_file_ids
                                    and f_name not in all_valid_file_ids
                                    and os.path.getmtime(f_path) < grace_period
                                ):
                                    try:
                                        sz = os.path.getsize(f_path)
                                        os.remove(f_path)
                                        stats["files"]["c"] += 1
                                        stats["files"]["b"] += sz
                                    except Exception:
                                        pass

                    # Étape 3.bis : Purge des fichiers de staging multipart orphelins dans .tmp_uploads
                    if os.path.isdir(TMP_UPLOADS_DIR):
                        for f_name in os.listdir(TMP_UPLOADS_DIR):
                            f_path = os.path.join(TMP_UPLOADS_DIR, f_name)
                            try:
                                if os.path.islink(f_path):
                                    os.unlink(f_path)
                                    stats["files"]["c"] += 1
                                elif os.path.isfile(f_path) and os.path.getmtime(f_path) < grace_period:
                                    sz = os.path.getsize(f_path)
                                    os.remove(f_path)
                                    stats["files"]["c"] += 1
                                    stats["files"]["b"] += sz
                            except Exception:
                                pass

            # --- A.ter Purge des Branches Mortes (SQLite & Qdrant) ---
            if not HAS_ECHO_LIBS:
                libs_warning_line = "<br>⚠️ <b class='text-warning'>Libs ECHO non montées</b> : GC fichiers et branches mortes ignorés"
            elif config.get("purge_dead_branches", True):
                MAINTENANCE_STATE["percent"] = 70
                MAINTENANCE_STATE["status"] = "Élagage (4/7) : Purge des branches mortes..."
                grace_period = time.time() - ECHO_GC_GRACE_PERIOD_SEC
                if os.path.exists(OWUI_DB_PATH):
                    try:
                        with open_sqlite_db(OWUI_DB_PATH, readonly=True, timeout=15.0) as w_conn:
                            for uid in valid_ids:
                                chats_root = os.path.join(ECHO_USERS_ROOT, uid, "chats")
                                if not os.path.exists(chats_root):
                                    continue
                                for cid in user_valid_chats.get(uid, set()):
                                    session_db_path = os.path.join(chats_root, cid, "session.db")
                                    if not os.path.isfile(session_db_path):
                                        continue
                                    res = gc_session_db(session_db_path, w_conn, cid, grace_period, dry_run=False)
                                    if res.get("status") == "purged":
                                        stats["dead_branches"]["chats"] += 1
                                        stats["dead_branches"]["rows"] += sum(res.get("counts", {}).values())
                                        rag_seeds = res.get("rag_seeds") or []
                                        if rag_seeds and HAS_HTTPX:
                                            stats["dead_branches"]["rag_points"] += _delete_rag_points(uid, cid, rag_seeds)
                    except Exception as e:
                        logger.error(f"Erreur GC branches mortes : {e}")

            # --- A.quater Purge des captures & clips browserviews éphémères (SSOT echo_browser_lib) ---
            if HAS_HTTPX and valid_ids:
                try:
                    active_bv_sessions = None
                    try:
                        nav_base = os.environ.get("NAVIGATION_ENGINE_URL", BROWSER_WORKER_URL).rstrip("/")
                        bv_resp = httpx.get(f"{nav_base}/sessions", timeout=5)
                        if bv_resp.status_code == 200:
                            bv_data = bv_resp.json()
                            if isinstance(bv_data, dict) and bv_data.get("status") == "success":
                                active_bv_sessions = set(bv_data.get("active_sessions") or [])
                    except Exception:
                        active_bv_sessions = None

                    if active_bv_sessions is not None:
                        for uid in valid_ids:
                            chats_root = os.path.join(ECHO_USERS_ROOT, uid, "chats")
                            if not os.path.exists(chats_root):
                                continue
                            for cid in user_valid_chats.get(uid, set()):
                                chat_dir = os.path.join(chats_root, cid)
                                session_db_path = os.path.join(chat_dir, "session.db")
                                if not os.path.isfile(session_db_path):
                                    continue
                                if cleanup_expired_browserviews_sync is not None:
                                    try:
                                        cleanup_expired_browserviews_sync(
                                            uid,
                                            cid,
                                            active_session_ids=active_bv_sessions,
                                            min_age_sec=BROWSER_TRANSIENT_GRACE_SEC,
                                        )
                                    except Exception as e:
                                        logger.warning(f"Erreur GC browserviews SSOT ({uid}/{cid}) : {e}")
                                else:
                                    bv_dir = os.path.join(chat_dir, "browserviews")
                                    files_dir = os.path.join(chat_dir, "files")
                                    kept_fnames = set()
                                    now_ts = time.time()
                                    try:
                                        with open_sqlite_db(session_db_path, timeout=10.0) as s_conn:
                                            rows = s_conn.execute(
                                                "SELECT id, name, status, metadata, created_at FROM echo_resources WHERE resource_type = 'browserview'"
                                            ).fetchall()
                                            del_ids = []
                                            for r_id, r_name, r_status, r_meta, r_created_at in rows:
                                                meta = {}
                                                if r_meta:
                                                    try:
                                                        parsed = json.loads(r_meta)
                                                        if isinstance(parsed, dict):
                                                            meta = parsed
                                                    except Exception:
                                                        meta = {}
                                                fname = os.path.basename(r_name or "") or f"{r_id}_frame.jpg"
                                                clip_names = []
                                                raw_clip = os.path.basename(str(meta.get("clip_filename") or ""))
                                                if raw_clip:
                                                    clip_names.append(raw_clip)
                                                def_clip = f"{r_id}{BROWSER_CLIP_EXT}"
                                                if def_clip not in clip_names:
                                                    clip_names.append(def_clip)
                                                if r_status == "retained" or meta.get("retained") is True:
                                                    kept_fnames.add(fname)
                                                    kept_fnames.update(clip_names)
                                                    continue
                                                item_ts = float(meta.get("timestamp") or r_created_at or 0)
                                                if item_ts > 0 and (now_ts - item_ts) < BROWSER_TRANSIENT_GRACE_SEC:
                                                    kept_fnames.add(fname)
                                                    kept_fnames.update(clip_names)
                                                    continue
                                                sess_id = meta.get("session_id") or cid
                                                if sess_id not in active_bv_sessions:
                                                    for b_dir in (bv_dir, files_dir):
                                                        for cand_name in (fname, *clip_names):
                                                            fp = os.path.join(b_dir, cand_name)
                                                            if os.path.isfile(fp):
                                                                try:
                                                                    os.unlink(fp)
                                                                except Exception:
                                                                    pass
                                                    del_ids.append((r_id,))
                                                else:
                                                    kept_fnames.add(fname)
                                                    kept_fnames.update(clip_names)
                                            if del_ids:
                                                s_conn.executemany("DELETE FROM echo_resources WHERE id = ?", del_ids)
                                    except Exception:
                                        pass
                                    if os.path.isdir(bv_dir):
                                        now_ts = time.time()
                                        for entry in os.listdir(bv_dir):
                                            if not entry.endswith(("_frame.jpg", BROWSER_CLIP_EXT)) or entry in kept_fnames:
                                                continue
                                            fp = os.path.join(bv_dir, entry)
                                            if os.path.isfile(fp) and (now_ts - os.path.getmtime(fp) > 60.0):
                                                try:
                                                    os.unlink(fp)
                                                except Exception:
                                                    pass
                except Exception as e:
                    logger.error(f"Erreur GC browserviews : {e}")

            # --- A.quinquies Purge filet de sécurité des sous-agents purgés expirés (Contrat C2) ---
            if cleanup_expired_purged_subagents_sync is not None and valid_ids:
                purged_subagents_total = 0
                for uid in valid_ids:
                    chats_root = os.path.join(ECHO_USERS_ROOT, uid, "chats")
                    if not os.path.exists(chats_root):
                        continue
                    for cid in user_valid_chats.get(uid, set()):
                        session_db_path = os.path.join(chats_root, cid, "session.db")
                        if not os.path.isfile(session_db_path):
                            continue
                        try:
                            purged_subagents_total += int(cleanup_expired_purged_subagents_sync(uid, cid) or 0)
                        except Exception as e:
                            logger.warning(f"Erreur GC sous-agents purgés ({uid}/{cid}) : {e}")
                if purged_subagents_total > 0:
                    logger.info(f"🧹 [ECHO-LIFECYCLE] {purged_subagents_total} sous-agent(s) purgé(s) expiré(s) supprimé(s).")

            # --- B. Purge Temporelle des Souvenirs & Garbage Collection (Qdrant) ---
            MAINTENANCE_STATE["percent"] = 75
            MAINTENANCE_STATE["status"] = "Élagage (5/7) : Purge Vectorielle (Qdrant)..."
            if HAS_HTTPX and valid_ids:
                try:
                    # Test de disponibilité globale Qdrant
                    r = httpx.get(f"{QDRANT_URL}/collections", timeout=5)
                    if r.status_code == 200:
                        # 1) Utilisateurs orphelins
                        if config.get("purge_orphaned_users", False):
                            httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/delete", 
                                       json={"filter": {"must_not": [{"key": "user_id", "match": {"any": list(valid_ids)}}]}}, 
                                       timeout=30)
                            httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/delete", 
                                       json={"filter": {"must_not": [{"key": "user_id", "match": {"any": list(valid_ids)}}]}}, 
                                       timeout=30)
                        
                        # 2) Chats orphelins & TTL
                        now = int(time.time())
                        ttl_cfg = config.get("memory_ttl", DEFAULT_MAINT_CONFIG["memory_ttl"])
                        ttl_map = {
                            1: int(ttl_cfg.get("lvl1", 30)) * 86400,
                            2: int(ttl_cfg.get("lvl2", 60)) * 86400,
                            3: int(ttl_cfg.get("lvl3", 180)) * 86400,
                            4: int(ttl_cfg.get("lvl4", 365)) * 86400,
                            5: int(ttl_cfg.get("lvl5", 540)) * 86400
                        }
                        
                        for uid in valid_ids:
                            str_uid = str(uid)
                            # Purge Chats
                            if config.get("purge_orphaned_chats", False):
                                valid_chat_ids_for_qdrant = list(user_valid_chats.get(str_uid, set())) + ["consolidated"]
                                payload = {
                                    "filter": {
                                        "must": [{"key": "user_id", "match": {"value": str_uid}}],
                                        "must_not": [{"key": "chat_id", "match": {"any": valid_chat_ids_for_qdrant}}]
                                    }
                                }
                                httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/delete", json=payload, timeout=30)
                                httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/delete", json=payload, timeout=30)
                            
                            # Decay TTL
                            for level, seconds in ttl_map.items():
                                decay_payload = {
                                    "filter": {
                                        "must": [
                                            {"key": "user_id", "match": {"value": str_uid}},
                                            {"key": "memory_importance", "match": {"value": level}},
                                            {"key": "timestamp", "range": {"lt": now - seconds}}
                                        ]
                                    }
                                }
                                httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/delete", json=decay_payload, timeout=30)
                        
                        qdrant_synced = True
                        
                        # --- B.1 Consolidation Lvl1 -> Lvl2 ---
                        MAINTENANCE_STATE["percent"] = 85
                        MAINTENANCE_STATE["status"] = "Élagage (6/7) : Consolidation Vectorielle Lvl1..."
                        total_promoted = 0
                        for uid in valid_ids:
                            result = consolidate_memories_for_user(uid, config)
                            total_promoted += result.get("points_promoted", 0)
                        stats["promoted_clusters"] = total_promoted
                except Exception as e:
                    print(f"[ECHO-LIFECYCLE] ❌ Erreur Qdrant : {e}")

            # --- C. Synchronisation Base de Données (Purge des Lignes Orphelines & Fichiers Fantômes) ---
            MAINTENANCE_STATE["percent"] = 92
            MAINTENANCE_STATE["status"] = "Élagage (7/7) : Synchronisation BDD & VACUUM SQLite..."
            try:
                with open_sqlite_db(WEBUI_DB_PATH, timeout=10.0) as conn:
                    orphans_db = 0
                    if config.get("purge_orphaned_users", False):
                        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                        tables_set = set(tables)
                        for table in tables:
                            if table == "user":
                                continue
                            try:
                                cols = [col[1] for col in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                                if "user_id" in cols:
                                    cur_del = conn.execute(
                                        f"DELETE FROM {table} WHERE user_id IS NOT NULL AND user_id != '' AND user_id NOT IN (SELECT id FROM user)"
                                    )
                                    if cur_del.rowcount and cur_del.rowcount > 0:
                                        orphans_db += cur_del.rowcount
                            except Exception as te:
                                print(f"⚠️ [ECHO-GC] Erreur purge orphelins table {table} : {te}")
                        # Tables ECHO-UI v6 liées aux utilisateurs sans colonne user_id
                        for extra_tbl, extra_sql in (
                            ("auth", "DELETE FROM auth WHERE id NOT IN (SELECT id FROM user)"),
                            ("access_grant", "DELETE FROM access_grant WHERE principal_type = 'user' AND principal_id != '*' AND principal_id NOT IN (SELECT id FROM user)"),
                            ("automation_run", "DELETE FROM automation_run WHERE automation_id NOT IN (SELECT id FROM automation)" if "automation" in tables_set else ""),
                        ):
                            if extra_tbl in tables_set and extra_sql:
                                try:
                                    cur_del = conn.execute(extra_sql)
                                    if cur_del.rowcount and cur_del.rowcount > 0:
                                        orphans_db += cur_del.rowcount
                                except Exception as te:
                                    print(f"⚠️ [ECHO-GC] Erreur purge orphelins table {extra_tbl} : {te}")
                    elif config.get("purge_orphaned_chats", False):
                        try:
                            cur_del = conn.execute(
                                "DELETE FROM chat WHERE user_id IS NOT NULL AND user_id != '' AND user_id NOT IN (SELECT id FROM user)"
                            )
                            if cur_del.rowcount and cur_del.rowcount > 0:
                                orphans_db += cur_del.rowcount
                        except Exception as te:
                            print(f"⚠️ [ECHO-GC] Erreur purge chats orphelins table chat : {te}")

                    file_cols = {col[1] for col in conn.execute("PRAGMA table_info(file)").fetchall()}
                    if "path" in file_cols:
                        db_files = conn.execute("SELECT id, user_id, path FROM file").fetchall()
                    else:
                        db_files = [(r[0], r[1], None) for r in conn.execute("SELECT id, user_id FROM file").fetchall()]
                    for file_id, file_user_id, file_path in db_files:
                        file_exists_on_disk = bool(file_path and os.path.isfile(file_path))
                        if not file_exists_on_disk:
                            user_files_dir = os.path.join(ECHO_USERS_ROOT, str(file_user_id), "files")
                            if os.path.exists(user_files_dir):
                                for f_name in os.listdir(user_files_dir):
                                    if f_name.startswith(str(file_id)) and os.path.isfile(os.path.join(user_files_dir, f_name)):
                                        file_exists_on_disk = True
                                        break
                        if not file_exists_on_disk:
                            conn.execute("DELETE FROM file WHERE id = ?", (file_id,))
                            orphans_db += 1
                            
                    if orphans_db > 0:
                        conn.commit()
                        print(f"🧹 [ECHO-GC] {orphans_db} entrées fantômes/orphelines purgées de webui.db")
            except Exception as e:
                print(f"⚠️ [ECHO-GC] Erreur lors de la synchronisation BDD (Fichiers) : {e}")
        except Exception as e:
            # Trace complète : une erreur ici court-circuite toute la purge FS/Qdrant/BDD
            pruning_error = f"{type(e).__name__}: {e}"
            print(f"[ECHO-LIFECYCLE] ❌ Erreur DB/Espace Personnel : {pruning_error}\n{traceback.format_exc()}")
        
    # 3. Vacuum (Uniquement si le cycle ne s'est pas interrompu sur une erreur critique)
    MAINTENANCE_STATE["percent"] = 96
    if not pruning_error:
        for root, _, files in os.walk(ECHO_USERS_ROOT):
            for f in files:
                if f.endswith('.db'):
                    try:
                        with open_sqlite_db(os.path.join(root, f), timeout=10.0, auto_commit=False) as db:
                            db.execute("VACUUM;")
                            stats["sqlite_vacuumed"] += 1
                    except Exception:
                        pass
        for global_db in (N8N_DB_PATH, WEBUI_DB_PATH, "/app/auth-data/auth.db"):
            if os.path.exists(global_db):
                try:
                    with open_sqlite_db(global_db, timeout=10.0, auto_commit=False) as db:
                        db.execute("VACUUM;")
                        stats["sqlite_vacuumed"] += 1
                except Exception:
                    pass
                
    sqlite_ghosts = locals().get('orphans_db', 0)
    stats['sqlite_ghosts'] = sqlite_ghosts

    # Ligne d'erreur échappée (le rapport est rendu en HTML brut via | safe)
    error_line = (
        f"<br>⚠️ <b class='text-danger'>Purge interrompue</b> : {html.escape(pruning_error)}"
        if pruning_error else ""
    )

    final_report = f"""
    <div style='line-height: 1.4;'>
        <b><i class='bi bi-trash text-danger'></i> Rapport d'Élagage</b><br>
        👤 <b>Utilisateurs</b> : {stats['users']['c']} <i>({_format_bytes(stats['users']['b'])})</i><br>
        💬 <b>Chats</b> : {stats['chats']['c']} orphelins ({stats['sessions_db']['c']} session.db) <i>({_format_bytes(stats['chats']['b'])})</i><br>
        🌿 <b>Branches mortes</b> : {stats['dead_branches']['chats']} chats, {stats['dead_branches']['rows']} lignes SQLite, {stats['dead_branches']['rag_points']} vecteurs<br>
        🗂️ <b>Fichiers FS</b> : {stats['files']['c']} <i>({_format_bytes(stats['files']['b'])})</i><br>
        🔑 <b>MCP Cache</b> : {stats['mcp_cache']['c']} <i>({_format_bytes(stats['mcp_cache']['b'])})</i><br>
        📥 <b>Drop Zones</b> : {stats['drop_users']['c']} Users, {stats['drop_chats']['c']} Chats, {stats['drop_n8n']['c']} N8N <i>(Libéré: {_format_bytes(stats['drop_users']['b'] + stats['drop_chats']['b'] + stats['drop_n8n']['b'])})</i><br>
        🧠 <b>Qdrant</b> : {'Synchronisé (Orphelins & TTL)' if qdrant_synced else 'Ignoré'}<br>
        🧬 <b>Consolidation</b> : {stats['promoted_clusters']} clusters promus (Lvl1->Lvl2)<br>
        🗃️ <b>SQLite</b> : {stats['sqlite_ghosts']} fantômes, {stats['sqlite_vacuumed']} bases optimisées{libs_warning_line}{error_line}
    </div>
    """
    config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_maint_config(config)
    if not silent:
        save_maint_report(final_report)
    return final_report, stats
