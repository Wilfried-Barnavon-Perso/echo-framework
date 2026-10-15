# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - SEMANTIC PRUNING & GC
VERSION : 5.136
================================================================================
"""
import datetime
import html
import os
from pathlib import Path
import shutil
import sqlite3
import time
import traceback
from core.config import (
    COLLECTION_META_ARTIFACTS,
    COLLECTION_SESSION_RAG,
    DEFAULT_MAINT_CONFIG,
    DOWNLOADS_DIR,
    ECHO_USERS_ROOT,
    HAS_HTTPX,
    MAINTENANCE_STATE,
    MCP_CACHE_DIR,
    N8N_DB_PATH,
    N8N_WORKER_URL,
    OWUI_ADMIN_SECRET_PATH,
    QDRANT_URL,
    SYSTEM_ADMIN_EMAIL,
    UPLOADS_DIR,
    WEBUI_DB_PATH,
    WEBUI_URL,
    httpx,
)
from core.utils import _format_bytes, _get_tree_size
from core.settings_io import (
    load_maint_config,
    save_maint_config,
    save_maint_report,
)
from services.memory_consolidation import consolidate_memories_for_user

__all__ = [
    'run_semantic_pruning',
    '_run_semantic_pruning',
    'purge_user_artifacts',
]


def purge_user_artifacts(uid: str):
    """Purge atomique multi-volumes des artefacts d'un utilisateur certifié supprimé de la SSoT webui.db."""
    uid = str(uid or "").strip()
    if len(uid) <= 30 or "/" in uid or "\\" in uid or ".." in uid:
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
                        with sqlite3.connect(f"file:{session_db_path}?mode=ro", uri=True, timeout=5.0) as s_conn:
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
                with sqlite3.connect(N8N_DB_PATH, timeout=10.0) as n8n_db:
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
                with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True, timeout=5.0) as conn:
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
        
        # Injection de l'en-tête SSO si le domaine est configuré (cf. config-owui.sh)
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
        
        r_auth = httpx.get(f"{owui_url}/api/v1/users", headers=auth_headers, timeout=10)
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
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True, timeout=10.0) as conn:
            chk = conn.execute("PRAGMA quick_check(1)").fetchone()
            if not chk or chk[0] != "ok":
                raise sqlite3.DatabaseError(f"PRAGMA quick_check(1) en échec : {chk}")
            user_rows = conn.execute("SELECT id, email FROM user").fetchall()
            chat_rows = conn.execute("SELECT id, user_id FROM chat").fetchall()
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

    # Filtrage strict des chats valides par appartenance à un utilisateur présent dans la SSoT valid_ids
    db_valid_chats = {
        str(r[0]) for r in chat_rows
        if r and r[0] and str(r[1]) in valid_ids
    }

    stats = {
        "users": {"c": 0, "b": 0},
        "chats": {"c": 0, "b": 0},
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

    if os.path.exists(ECHO_USERS_ROOT):
        try:
            MAINTENANCE_STATE["percent"] = 35
            MAINTENANCE_STATE["status"] = "Élagage (3/7) : Génération liste blanche N8N..."
            
            # --- A0. N8N Safeguard & Garbage Collection ---
            n8n_active_ids = []
            n8n_whitelist_complete = True
            for folder in valid_ids:
                user_chats_dir = os.path.join(ECHO_USERS_ROOT, folder, "chats")
                if os.path.exists(user_chats_dir):
                    try:
                        chat_entries = os.listdir(user_chats_dir)
                    except Exception as e:
                        print(f"⚠️ [ECHO-LIFECYCLE] Lecture impossible de {user_chats_dir}: {e}")
                        n8n_whitelist_complete = False
                        continue
                    for cdir in chat_entries:
                        if cdir in db_valid_chats:
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
                                    with sqlite3.connect(f"file:{session_db_path}?mode=ro", uri=True, timeout=5.0) as s_conn:
                                        cur = s_conn.execute("SELECT status FROM echo_resources WHERE resource_type = 'n8n_workflow' AND status LIKE 'deployed_as_%'")
                                        for row in cur.fetchall():
                                            st = row[0]
                                            if st:
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
                        if not uid_dir.is_dir():
                            continue
                        if uid_dir.name not in valid_ids and len(uid_dir.name) > 30:
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
                    if not uid_dir.is_dir(): continue
                    
                    # 1. Purge Utilisateur Orphelin
                    if uid_dir.name not in valid_ids and len(uid_dir.name) > 30:
                        try:
                            sz = _get_tree_size(str(uid_dir))
                            shutil.rmtree(str(uid_dir), ignore_errors=True)
                            stats["drop_users"]["c"] += 1
                            stats["drop_users"]["b"] += sz
                        except Exception:
                            pass
                        continue

                    for cid_dir in uid_dir.iterdir():
                        if not cid_dir.is_dir(): continue
                        
                        # 2. Purge Chat Orphelin
                        if cid_dir.name not in db_valid_chats and len(cid_dir.name) > 30:
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

            # --- A. Purge des dossiers de l'Espace Personnel ---
            if config.get("purge_orphaned_users", False):
                for folder in os.listdir(ECHO_USERS_ROOT):
                    if folder not in valid_ids and len(folder) > 30:
                        folder_path = os.path.join(ECHO_USERS_ROOT, folder)
                        sz = _get_tree_size(folder_path)
                        shutil.rmtree(folder_path, ignore_errors=True)
                        stats["users"]["c"] += 1
                        stats["users"]["b"] += sz
            
            if config.get("purge_orphaned_chats", False):
                for folder in valid_ids:
                    user_chats_dir = os.path.join(ECHO_USERS_ROOT, folder, "chats")
                    if os.path.exists(user_chats_dir):
                        for cdir in os.listdir(user_chats_dir):
                            cdir_path = os.path.join(user_chats_dir, cdir)
                            if os.path.isdir(cdir_path):
                                chat_id = cdir
                                if chat_id not in db_valid_chats:
                                    sz = _get_tree_size(cdir_path)
                                    shutil.rmtree(cdir_path, ignore_errors=True)
                                    stats["chats"]["c"] += 1
                                    stats["chats"]["b"] += sz

                # --- A.bis Purge Granulaire des Fichiers (RefCounting FS) ---
                MAINTENANCE_STATE["percent"] = 65
                MAINTENANCE_STATE["status"] = "Élagage (4/7) : Garbage Collection des fichiers (FS)..."
                grace_period = time.time() - 900  # Carence de 15 minutes (900s)
                
                # Étape 1 : Cartographier tous les symlinks valides dans les chats existants
                active_symlink_targets = set()
                for folder in valid_ids:
                    user_chats_dir = os.path.join(ECHO_USERS_ROOT, folder, "chats")
                    if os.path.exists(user_chats_dir):
                        for cdir in os.listdir(user_chats_dir):
                            if cdir in db_valid_chats:
                                chat_files_dir = os.path.join(user_chats_dir, cdir, "files")
                                if os.path.exists(chat_files_dir):
                                    for f_name in os.listdir(chat_files_dir):
                                        f_path = os.path.join(chat_files_dir, f_name)
                                        if os.path.islink(f_path):
                                            try:
                                                active_symlink_targets.add(os.path.realpath(f_path))
                                            except Exception:
                                                pass
                
                # Étape 2 : Purger les fichiers du Vault Global (fichiers originaux/dérivés orphelins)
                for folder in valid_ids:
                    user_files_dir = os.path.join(ECHO_USERS_ROOT, folder, "files")
                    if os.path.exists(user_files_dir):
                        for f_name in os.listdir(user_files_dir):
                            f_path = os.path.join(user_files_dir, f_name)
                            if os.path.isfile(f_path) and not os.path.islink(f_path):
                                if f_path not in active_symlink_targets:
                                    # Vérification de l'âge pour éviter la suppression d'un upload en cours
                                    if os.path.getmtime(f_path) < grace_period:
                                        try:
                                            sz = os.path.getsize(f_path) if os.path.exists(f_path) else 0
                                            os.remove(f_path)
                                            stats["files"]["c"] += 1
                                            stats["files"]["b"] += sz
                                        except Exception:
                                            pass

                # Étape 3 : Nettoyer le dossier UPLOADS_DIR (Fichiers en dur et Liens morts)
                if os.path.exists(UPLOADS_DIR):
                    for f_name in os.listdir(UPLOADS_DIR):
                        f_path = os.path.join(UPLOADS_DIR, f_name)
                        if os.path.islink(f_path):
                            # os.path.exists() retourne False sur un Dead Symlink
                            if not os.path.exists(f_path):
                                try:
                                    os.unlink(f_path)
                                    stats["files"]["c"] += 1
                                except Exception:
                                    pass
                        elif os.path.isfile(f_path):
                            # Fichier physique orphelin (non converti en symlink par echo_ingestion)
                            if os.path.getmtime(f_path) < grace_period:
                                try:
                                    sz = os.path.getsize(f_path)
                                    os.remove(f_path)
                                    stats["files"]["c"] += 1
                                    stats["files"]["b"] += sz
                                except Exception:
                                    pass

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
                                valid_chat_ids_for_qdrant = list(db_valid_chats) + ["consolidated"]
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
                with sqlite3.connect(f"file:{WEBUI_DB_PATH}", uri=True, timeout=10.0) as conn:
                    orphans_db = 0
                    if config.get("purge_orphaned_users", False):
                        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
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

                    db_files = conn.execute("SELECT id, user_id FROM file").fetchall()
                    for file_id, file_user_id in db_files:
                        user_files_dir = os.path.join(ECHO_USERS_ROOT, str(file_user_id), "files")
                        file_exists_on_disk = False
                        
                        if os.path.exists(user_files_dir):
                            for f_name in os.listdir(user_files_dir):
                                if f_name.startswith(str(file_id)):
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
                        with sqlite3.connect(os.path.join(root, f), timeout=10.0) as db:
                            db.execute("VACUUM;")
                            stats["sqlite_vacuumed"] += 1
                    except Exception:
                        pass
        for global_db in (N8N_DB_PATH, WEBUI_DB_PATH, "/app/auth-data/auth.db"):
            if os.path.exists(global_db):
                try:
                    with sqlite3.connect(global_db, timeout=10.0) as db:
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
        💬 <b>Chats</b> : {stats['chats']['c']} <i>({_format_bytes(stats['chats']['b'])})</i><br>
        🗂️ <b>Fichiers FS</b> : {stats['files']['c']} <i>({_format_bytes(stats['files']['b'])})</i><br>
        🔑 <b>MCP Cache</b> : {stats['mcp_cache']['c']} <i>({_format_bytes(stats['mcp_cache']['b'])})</i><br>
        📥 <b>Drop Zones</b> : {stats['drop_users']['c']} Users, {stats['drop_chats']['c']} Chats, {stats['drop_n8n']['c']} N8N <i>(Libéré: {_format_bytes(stats['drop_users']['b'] + stats['drop_chats']['b'] + stats['drop_n8n']['b'])})</i><br>
        🧠 <b>Qdrant</b> : {'Synchronisé (Orphelins & TTL)' if qdrant_synced else 'Ignoré'}<br>
        🧬 <b>Consolidation</b> : {stats['promoted_clusters']} clusters promus (Lvl1->Lvl2)<br>
        🗃️ <b>SQLite</b> : {stats['sqlite_ghosts']} fantômes, {stats['sqlite_vacuumed']} bases optimisées{error_line}
    </div>
    """
    config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_maint_config(config)
    if not silent:
        save_maint_report(final_report)
    return final_report, stats
