# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER SERVER
VERSION : 5.12 (Support Recursive Vault Maintenance)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-03-22

--- DESCRIPTION ARCHITECTURALE ---
Ce micro-service Flask agit comme le "Concierge" de l'infrastructure ECHO.
Il s'exécute dans un conteneur Docker dédié (echo-admin-manager) sur le port 3001.

--- CHANGELOG 5.12 ---
- Introduction de la maintenance récursive pour le Vault ECHO (users/{id}/files).
- Mise à jour des statistiques utilisateurs pour inclure les dossiers de chats ECHO.
- Optimisation du nettoyage des fichiers par parcours d'arborescence profond.

--- CHANGELOG 5.11 ---
- Support PWA et icônes.

--- CHANGELOG 5.10 ---
- Suppression de l'auto-nettoyage du presse-papier (Copie simple uniquement).

--- RESPONSABILITÉS ---
1. MONITORING : Exposition des métriques (CPU/RAM/Disque).
2. ORCHESTRATION : Gestion des conteneurs Docker.
3. SAUVEGARDE : Backup des données utilisateur.
4. MAINTENANCE : Nettoyage automatique des fichiers et des bases de données.
5. SECURITE : Gestion des tokens et changement de mot de passe système.
================================================================================
"""

from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for, flash, session # pyright: ignore[reportMissingImports]
from typing import Optional
import os
import subprocess
import datetime
import glob
import secrets
import json
import time
import threading
import shutil
import sqlite3
from werkzeug.utils import secure_filename # pyright: ignore[reportMissingImports]

# ==============================================================================
# SECTION 1 : GESTION DES DÉPENDANCES
# ==============================================================================
try:
    import docker
    import paramiko
    DOCKER_AVAILABLE = True
except ImportError:
    print("CRITIQUE: Docker/Paramiko non disponible (Dev Local ?)")
    docker = None
    paramiko = None
    DOCKER_AVAILABLE = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    print("CRITIQUE: httpx non disponible. Le nettoyage de la BDD est désactivé.")
    HAS_HTTPX = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

try:
    import schedule
    HAS_MAINT_SCHEDULER = True
except ImportError:
    print("WARNING: 'schedule' lib missing. Maintenance scheduler disabled.")
    HAS_MAINT_SCHEDULER = False

# ==============================================================================
# SECTION 2 : CONFIGURATION FLASK
# ==============================================================================
app = Flask(__name__, static_folder='/app/static')
app.secret_key = secrets.token_hex(32)
app.config['JSON_AS_ASCII'] = False

@app.after_request
def set_charset(response):
    if response.headers.get('Content-Type', '').startswith('text/html'):
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "ECHO Admin",
        "short_name": "ECHO Admin",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#0d1117",
        "icons": [
            {
                "src": "/static/logo-echo.png",
                "sizes": "512x512",
                "type": "image/png"
            }
        ]
    })

# ==============================================================================
# SECTION 3 : CONSTANTES & CHEMINS
# ==============================================================================
TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'echo-webui-core')
BACKUP_DIR = "/backups"
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")
OWUI_DATA_ROOT = "/app/backend/data"
USER_DBS_DIR = os.path.join(OWUI_DATA_ROOT, "user_dbs")
ECHO_USERS_ROOT = os.path.join(OWUI_DATA_ROOT, "users") # Nouvelle racine ECHO
WEBUI_DB_PATH = os.path.join(OWUI_DATA_ROOT, "webui.db")
OWUI_SECRETS_PATH = "/app/secrets/.owui-setting-secret"
OWUI_ADMIN_SECRET_PATH = "/app/secrets/.owui-admin-secret"

# Les répertoires de fichiers restants à nettoyer par date (MAJ v5.12 : Récursif)
DIRS = {
    "user_dbs": USER_DBS_DIR,
    "uploads": os.path.join(OWUI_DATA_ROOT, "uploads"),
    "debug_logs": os.path.join(OWUI_DATA_ROOT, "debug_logs"),
    "echo_vault": ECHO_USERS_ROOT
}

MAINT_CONFIG_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")
DATA_DIR_FOR_BACKUP = OWUI_DATA_ROOT 

DEFAULT_BACKUP_CONFIG = {
    "auto_backup": True, "auto_cleanup": True, "cleanup_mode": "count",
    "cleanup_value": 5, "backup_time": "03:00", "interval_days": 1
}

DEFAULT_MAINT_CONFIG = {
    "cleanup_hour": "03:00", "last_run": "Never",
    "retention": { "uploads_days": 1095, "debug_days": 14 }
}

# ==============================================================================
# SECTION 4 : INITIALISATION SERVICES & HELPERS
# ==============================================================================
client = None
if DOCKER_AVAILABLE:
    try: client = docker.from_env()
    except Exception as e: 
        print(f"Erreur init Docker: {e}")
        DOCKER_AVAILABLE = False

if HAS_SCHEDULER:
    try:
        backup_scheduler = BackgroundScheduler()
        backup_scheduler.start()
    except Exception as e: print(f"Erreur Backup Scheduler: {e}")

def get_cpu_model_name():
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if "model name" in line:
                    return line.split(':')[1].strip()
    except:
        return "N/A"

def get_echo_version():
    try:
        with open('/app/ECHO_VERSION', 'r', encoding='utf-8') as f:
            return f.read().strip()
    except:
        return "v?.?"

def human_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

# ==============================================================================
# SECTION 5 : LOGIQUE MÉTIER - MAINTENANCE UNIFIÉE
# ==============================================================================

def load_maint_config():
    config = DEFAULT_MAINT_CONFIG.copy()
    if os.path.exists(MAINT_CONFIG_FILE):
        try:
            with open(MAINT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                config.update({k: v for k, v in loaded.items() if k != 'retention'})
                if "retention" in loaded:
                    config["retention"].update(loaded["retention"])
        except: pass
    return config

def save_maint_config(config):
    try:
        with open(MAINT_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    except Exception as e: print(f"Erreur sauvegarde config maintenance: {e}")

def get_dir_stats(path, filter_ext=None):
    """Calcule les statistiques de stockage de manière récursive (v5.12)."""
    if not os.path.exists(path): return {"count": 0, "size": 0, "size_fmt": "0 B"}
    try:
        total_size = 0
        match_count = 0
        for root, _, files in os.walk(path):
            for f in files:
                if filter_ext and not f.endswith(filter_ext):
                    continue
                fpath = os.path.join(root, f)
                try:
                    total_size += os.path.getsize(fpath)
                    match_count += 1
                except: pass
        return {"count": match_count, "size": total_size, "size_fmt": human_size(total_size)}
    except: return {"count": 0, "size": 0, "size_fmt": "Err"}

def cleanup_directory(dir_key, retention_days):
    """Supprime récursivement les fichiers périmés (v5.12)."""
    if retention_days == -1: return 0, 0
    path = DIRS.get(dir_key)
    if not path or not os.path.exists(path) or dir_key == 'user_dbs': return 0, 0
    
    retention_sec = retention_days * 24 * 3600
    cutoff = time.time() - retention_sec
    deleted = 0
    skipped = 0
    
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fpath = os.path.join(root, f)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        deleted += 1
                    else:
                        skipped += 1
                except: pass
    except Exception as e:
        print(f"Error cleaning {dir_key}: {e}")
    
    if deleted > 0: print(f"🧹 [Maintenance] {dir_key}: {deleted} fichiers supprimés (> {retention_days} jours).")
    return deleted, skipped

def _get_owui_auth_token(owui_base_url: str) -> Optional[str]:
    if not HAS_HTTPX or not os.path.exists(OWUI_SECRETS_PATH): return None
    try:
        with open(OWUI_SECRETS_PATH, 'r') as f: service_password = f.read().strip()
        payload = {"email": "install-stack@echo.local", "password": service_password}
        with httpx.Client(timeout=10) as client:
            response = client.post(f"{owui_base_url}/api/v1/auths/signin", json=payload)
            response.raise_for_status()
            return response.json().get("token")
    except Exception as e:
        print(f"❌ [Auth] Erreur authentification OWUI: {e}")
    return None

def cleanup_orphan_user_dbs(owui_base_url: str, owui_auth_token: str):
    print("🔧 [DB Cleanup] Démarrage du nettoyage des bases de données utilisateur.")
    try:
        headers = {"Authorization": f"Bearer {owui_auth_token}"}
        with httpx.Client(timeout=30) as http_client:
            response = http_client.get(f"{owui_base_url}/api/v1/users/all", headers=headers)
            response.raise_for_status()
            valid_user_ids = {user['id'] for user in response.json()}
    except Exception as e:
        print(f"❌ [DB Cleanup] Erreur API Open-WebUI: {e}")
        return 0, 0

    if not os.path.exists(USER_DBS_DIR): return 0, 0
    
    deleted_count = 0
    vacuumed_count = 0
    
    # 1. Suppression des orphelines (Legacy user_dbs/)
    try:
        db_files = [f for f in os.listdir(USER_DBS_DIR) if f.startswith('user-') and f.endswith('.db')]
        for f in db_files:
            file_user_id = f[5:-3]
            if file_user_id not in valid_user_ids:
                try:
                    full_path = os.path.join(USER_DBS_DIR, f)
                    os.remove(full_path)
                    for ext in ['-wal', '-shm']:
                        try:
                            aux_path = full_path + ext
                            if os.path.exists(aux_path): os.remove(aux_path)
                        except: pass
                    deleted_count += 1
                except Exception as ex:
                    print(f"⚠️ [DB Cleanup] Échec suppression {f}: {ex}")
    except Exception as e:
        print(f"❌ [DB Cleanup] Erreur globale liste fichiers user_dbs: {e}")

    # 2. Maintenance des restantes (Vacuum)
    try:
        remaining_dbs = [f for f in os.listdir(USER_DBS_DIR) if f.startswith('user-') and f.endswith('.db')]
        for db_file in remaining_dbs:
            try:
                with sqlite3.connect(os.path.join(USER_DBS_DIR, db_file), timeout=15.0) as conn: conn.execute("VACUUM;")
                vacuumed_count += 1
            except Exception as e: print(f"⚠️ [DB Cleanup] Erreur VACUUM sur {db_file}: {e}")
    except: pass
    
    # 3. Compactage des bases de sessions ECHO (users/{id}/chats/*.db)
    if os.path.exists(ECHO_USERS_ROOT):
        try:
            for uid in os.listdir(ECHO_USERS_ROOT):
                chats_path = os.path.join(ECHO_USERS_ROOT, uid, "chats")
                if os.path.exists(chats_path):
                    for db_chat in os.listdir(chats_path):
                        if db_chat.endswith('.db'):
                            try:
                                with sqlite3.connect(os.path.join(chats_path, db_chat), timeout=15.0) as conn: conn.execute("VACUUM;")
                            except: pass
        except: pass

    print(f"✅ [DB Cleanup] Terminé. {deleted_count} BDD orphelines supprimées, {vacuumed_count} compactées.")
    return deleted_count, vacuumed_count

def run_global_maintenance():
    print(f"🔧 [Maintenance] Démarrage Global...")
    report = []
    config = load_maint_config()
    
    # Nettoyage fichiers (Récursif désormais)
    del_uploads, keep_uploads = cleanup_directory("uploads", config["retention"]["uploads_days"])
    del_logs, keep_logs = cleanup_directory("debug_logs", config["retention"]["debug_days"])
    del_vault, keep_vault = cleanup_directory("echo_vault", config["retention"]["uploads_days"])
    
    report.append(f"Fichiers: {del_uploads + del_vault} suppr. ({keep_uploads + keep_vault} conservés)")
    report.append(f"Logs: {del_logs} suppr. ({keep_logs} conservés)")
    
    # Nettoyage BDD
    owui_base_url = os.environ.get("OWUI_BASE_URL", "http://echo-webui-core:8080")
    if HAS_HTTPX:
        auth_token = _get_owui_auth_token(owui_base_url)
        if auth_token:
            del_db, vac_db = cleanup_orphan_user_dbs(owui_base_url, auth_token)
            report.append(f"BDD: {del_db} orphelines suppr., {vac_db} optimisées.")
        else:
            report.append("BDD: Ignoré (Échec Auth API).")
            print("⚠️ [Maintenance] Authentification OWUI échouée. Nettoyage BDD ignoré.")
    else:
        report.append("BDD: Ignoré (Module httpx manquant).")
        print("⚠️ [Maintenance] httpx non installé. Nettoyage BDD ignoré.")

    config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_maint_config(config)
    print(f"✅ [Maintenance] Terminée.")
    return " | ".join(report)

def maint_scheduler_loop():
    while True:
        schedule.run_pending()
        time.sleep(60)

def setup_maint_scheduler():
    if not HAS_MAINT_SCHEDULER: return
    config = load_maint_config()
    schedule.clear()
    schedule.every().day.at(config.get("cleanup_hour", "03:00")).do(run_global_maintenance)
    print(f"⏰ [Maintenance] Tâche planifiée pour {config.get('cleanup_hour', '03:00')}.")

if HAS_MAINT_SCHEDULER:
    setup_maint_scheduler()
    threading.Thread(target=maint_scheduler_loop, daemon=True).start()

# ==============================================================================
# SECTION 6 : BACKUPS & RESTAURATION
# ==============================================================================

def load_settings():
    config = DEFAULT_BACKUP_CONFIG.copy()
    if os.path.exists(SETTINGS_FILE):
        try: 
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f: config.update(json.load(f))
        except: pass
    return config

def save_settings(new_settings):
    clean = load_settings()
    clean.update(new_settings)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f: json.dump(clean, f, indent=2)
    update_backup_schedule()

def get_backup_list():
    try:
        files = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
        return [{'name': os.path.basename(f), 'size': human_size(os.path.getsize(f)), 'date': datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d/%m/%Y %H:%M')} for f in files]
    except: return []

def perform_backup_task():
    if not DOCKER_AVAILABLE or not client: return
    filepath = os.path.join(BACKUP_DIR, f"owui_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz")
    try:
        container = client.containers.get(TARGET_CONTAINER)
        container.stop()
        # tar inclut récursivement DATA_DIR_FOR_BACKUP (/app/backend/data)
        subprocess.run(['tar', '-czf', filepath, '-C', DATA_DIR_FOR_BACKUP, '.'], check=True)
        container.start()
        settings = load_settings()
        if settings.get("auto_cleanup", False):
            files = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
            mode, value = settings.get("cleanup_mode", "count"), int(settings.get("cleanup_value", 5))
            if mode == 'count' and len(files) > value:
                for f in files[value:]: os.remove(f)
            elif mode == 'days':
                for f in files:
                    if os.path.getmtime(f) < (time.time() - (value * 86400)): os.remove(f)
    except Exception as e:
        try: client.containers.get(TARGET_CONTAINER).start()
        except: pass
        print(f"Backup Error: {e}")

def update_backup_schedule():
    if not HAS_SCHEDULER: return
    backup_scheduler.remove_all_jobs()
    settings = load_settings()
    if settings.get("auto_backup"):
        try:
            hour, minute = map(int, settings.get("backup_time", "03:00").split(':'))
            now = datetime.datetime.now()
            start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if start_date <= now: start_date += datetime.timedelta(days=1)
            backup_scheduler.add_job(perform_backup_task, 'interval', days=int(settings.get("interval_days", 1)), start_date=start_date, id='auto_back')
        except Exception as e: print(f"Schedule Error: {e}")

try: update_backup_schedule()
except: pass

# ==============================================================================
# SECTION 7 : SECURITE SYSTEME (SSH / Passwd)
# ==============================================================================

def change_system_password(username, current_pwd, new_pwd):
    if not DOCKER_AVAILABLE: return False, "Module SSH manquant"
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=username, password=current_pwd, timeout=10)
        channel = ssh.invoke_shell()
        time.sleep(1)
        channel.send('passwd\n')
        time.sleep(1)
        channel.send(f'{current_pwd}\n')
        time.sleep(0.5)
        channel.send(f'{new_pwd}\n')
        time.sleep(0.5)
        channel.send(f'{new_pwd}\n')
        time.sleep(1)
        output = channel.recv(4096).decode('utf-8', errors='ignore')
        ssh.close()
        if "updated successfully" in output or "mis à jour avec succès" in output:
            return True, "Mot de passe modifié avec succès."
        elif "BAD PASSWORD" in output:
            return False, "Mot de passe trop faible."
        elif "Authentication token manipulation error" in output:
            return False, "Erreur système (Permissions)."
        else:
            return True, "Commande envoyée (Vérifiez la connexion)."
    except Exception as e:
        return False, str(e)

# ==============================================================================
# SECTION 8 : ROUTES FLASK
# ==============================================================================

@app.route('/')
def index():
    if not session.get('logged_in'): return render_template_string(HTML_LOGIN)
    storage_stats = {}
    for key, path in DIRS.items():
        filter_ext = '.db' if key == 'user_dbs' else None
        storage_stats[key] = get_dir_stats(path, filter_ext)
        
    return render_template_string(HTML_DASHBOARD, 
                                server_time_iso=datetime.datetime.now().isoformat(),
                                settings=load_settings(),
                                maint_config=load_maint_config(),
                                storage_stats=storage_stats,
                                current_user=session.get('username', 'Inconnu'),
                                has_scheduler=HAS_SCHEDULER,
                                has_maint_scheduler=HAS_MAINT_SCHEDULER,
                                has_httpx=HAS_HTTPX,
                                echo_version=get_echo_version())

@app.route('/', methods=['POST'])
def login():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=request.form.get('username'), password=request.form.get('password'), timeout=5)
        ssh.close()
        session['logged_in'] = True
        session['username'] = request.form.get('username')
    except Exception as e:
        flash(f'Échec authentification: {str(e)}', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/api/storage/analysis')
def storage_analysis():
    if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
    analysis = {}
    for key, path in DIRS.items():
        filter_ext = '.db' if key == 'user_dbs' else None
        analysis[key] = get_dir_stats(path, filter_ext)
    
    orphans_count = -1
    owui_base_url = os.environ.get("OWUI_BASE_URL", "http://echo-webui-core:8080")
    if HAS_HTTPX and os.path.exists(USER_DBS_DIR):
        auth_token = _get_owui_auth_token(owui_base_url)
        if auth_token:
            try:
                headers = {"Authorization": f"Bearer {auth_token}"}
                with httpx.Client(timeout=10) as http_client:
                    response = http_client.get(f"{owui_base_url}/api/v1/users/all", headers=headers)
                    if response.status_code == 200:
                        valid_ids = {u['id'] for u in response.json()}
                        db_files = [f for f in os.listdir(USER_DBS_DIR) if f.startswith('user-') and f.endswith('.db')]
                        orphans_count = sum(1 for f in db_files if f[5:-3] not in valid_ids)
            except: pass
    
    analysis['user_dbs']['orphans'] = orphans_count
    return jsonify(analysis)

@app.route('/api/stats')
def stats():
    if not HAS_PSUTIL: return jsonify({"error": "psutil manquant"})
    try:
        disk = psutil.disk_usage(BACKUP_DIR)
        mem = psutil.virtual_memory()
        return jsonify({
            "cpu_percent": psutil.cpu_percent(),
            "cpu_count": os.cpu_count(),
            "cpu_model": get_cpu_model_name(),
            "cpu_load": [round(l, 2) for l in psutil.getloadavg()],
            "ram_percent": mem.percent,
            "ram_used": human_size(mem.used),
            "ram_total": human_size(mem.total),
            "disk_percent": disk.percent,
            "disk_used": human_size(disk.used),
            "disk_total": human_size(disk.total)
        })
    except Exception as e: return jsonify({"error": str(e)})

@app.route('/api/user_stats')
def user_stats():
    """Récupère les stats utilisateurs incluant le Vault ECHO (v5.12)."""
    if not session.get('logged_in'): return jsonify([])
    if not os.path.exists(WEBUI_DB_PATH):
        return jsonify([{"error": f"Database not found: {WEBUI_DB_PATH}"}]), 500
    
    users_data = []
    try:
        # Utilisation de mode=ro pour sécurité
        con = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        users = cur.execute("SELECT id, name, email, role FROM user ORDER BY name COLLATE NOCASE ASC").fetchall()
        
        for user in users:
            uid = user['id']
            # 1. Calcul des conversations (Legacy OWUI + New ECHO Vault)
            legacy_count = cur.execute("SELECT COUNT(id) FROM chat WHERE user_id = ?", (uid,)).fetchone()[0]
            
            # 2. Scan physique du Vault ECHO pour les bases de session
            vault_chats_dir = os.path.join(ECHO_USERS_ROOT, str(uid), "chats")
            echo_chat_count = 0
            if os.path.exists(vault_chats_dir):
                echo_chat_count = len([f for f in os.listdir(vault_chats_dir) if f.endswith('.db')])
            
            users_data.append({
                "id": uid, "name": user['name'], "email": user['email'],
                "role": user['role'], "chat_count": legacy_count + echo_chat_count
            })
        con.close()
        return jsonify(users_data)
    except Exception as e:
        return jsonify([{"error": str(e)}]), 500

@app.route('/api/backups')
def backups():
    if not session.get('logged_in'): return jsonify([])
    return jsonify(get_backup_list())

@app.route('/api/containers')
def containers():
    if not session.get('logged_in'): return jsonify([])
    if not DOCKER_AVAILABLE or not client: return jsonify({"error": "Docker non connecté"})
    try:
        return jsonify([{"id": c.short_id, "name": c.name, "status": c.status.capitalize()} for c in client.containers.list(all=True)])
    except Exception as e: return jsonify({"error": str(e)})

@app.route('/settings/maintenance', methods=['POST'])
def update_maintenance_settings():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        config = load_maint_config()
        config["cleanup_hour"] = request.form.get("cleanup_hour", "03:00")
        config["retention"]["uploads_days"] = int(request.form.get("ret_uploads", 1095))
        config["retention"]["debug_days"] = int(request.form.get("ret_debug", 14))
        save_maint_config(config)
        if HAS_MAINT_SCHEDULER: setup_maint_scheduler()
        flash('Config maintenance mise à jour.', 'success')
    except Exception as e: flash(f'Erreur mise à jour: {str(e)}', 'danger')
    return redirect(url_for('index'))

@app.route('/action/maintenance/run', methods=['POST'])
def force_maintenance():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        report = run_global_maintenance()
        flash(f'Maintenance terminée: {report}', 'success' if 'Ignoré' not in report else 'warning')
    except Exception as e:
        flash(f'Erreur Maintenance: {str(e)}', 'danger')
    return redirect(url_for('index'))

@app.route('/action/auth/reset', methods=['POST'])
def global_auth_reset():
    if not session.get('logged_in'): return redirect(url_for('index'))
    purged_count, error_count = 0, 0
    # On purge les tokens dans user_dbs (Legacy) et dans users/*/identity.db (New)
    db_paths = glob.glob(os.path.join(USER_DBS_DIR, 'user-*.db'))
    db_paths += glob.glob(os.path.join(ECHO_USERS_ROOT, '*', 'identity.db'))
    
    for db_path in db_paths:
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM auth_data WHERE key = 'google_token' OR key = 'google_project_id'")
                if cursor.rowcount > 0:
                    purged_count += 1
        except Exception as e:
            print(f"⚠️ Erreur purge {db_path}: {e}")
            error_count += 1
    if purged_count > 0: flash(f'✅ Tokens Google purgés pour {purged_count} base(s).', 'success')
    else: flash('ℹ️ Aucun token Google à purger trouvé.', 'info')
    if error_count > 0: flash(f'❌ {error_count} erreurs rencontrées.', 'danger')
    return redirect(url_for('index'))

@app.route('/action/security/passwd', methods=['POST'])
def passwd_change():
    if not session.get('logged_in'): return redirect(url_for('index'))
    username = session.get('username')
    new_pwd, confirm_pwd = request.form.get('new_password'), request.form.get('confirm_password')
    if not username: flash('Erreur session.', 'danger')
    elif new_pwd != confirm_pwd: flash('❌ Les mots de passe ne correspondent pas.', 'danger')
    else:
        success, msg = change_system_password(username, request.form.get('current_password'), new_pwd)
        flash(f'✅ {msg}' if success else f'❌ Erreur: {msg}', 'success' if success else 'danger')
    return redirect(url_for('index'))

@app.route('/action/<action_type>', methods=['POST'])
def actions(action_type):
    if not session.get('logged_in'): return redirect(url_for('index'))
    if action_type == 'backup':
        threading.Thread(target=perform_backup_task).start()
        flash('Sauvegarde lancée en arrière-plan.', 'info')
    elif action_type == 'delete':
        fname = request.form.get('filename')
        if fname and os.path.exists(p := os.path.join(BACKUP_DIR, secure_filename(fname))):
            os.remove(p)
            flash('Fichier supprimé.', 'warning')
    elif action_type == 'restore':
        fname = request.form.get('filename')
        if fname and DOCKER_AVAILABLE and client and os.path.exists(p := os.path.join(BACKUP_DIR, secure_filename(fname))):
            try:
                container = client.containers.get(TARGET_CONTAINER)
                container.stop()
                subprocess.run(f"rm -rf {DATA_DIR_FOR_BACKUP}/*", shell=True)
                subprocess.run(['tar', '-xzf', p, '-C', DATA_DIR_FOR_BACKUP], check=True)
                container.start()
                flash('Restauration terminée.', 'success')
            except Exception as e: flash(f'Erreur Restauration: {e}', 'danger')
    elif action_type == 'restart':
        cid = request.form.get('container')
        if cid and DOCKER_AVAILABLE and client:
            try:
                client.containers.get(cid).restart()
                flash('Conteneur redémarré.', 'info')
            except Exception as e: flash(f'Erreur: {e}', 'danger')
    return redirect(url_for('index'))

@app.route('/api/admin/password')
def admin_password():
    if not session.get('logged_in'): return jsonify({"error": "Non autorisé"}), 403
    if not os.path.exists(OWUI_ADMIN_SECRET_PATH): return jsonify({"error": "Secret introuvable"}), 404
    try:
        with open(OWUI_ADMIN_SECRET_PATH, 'r') as f: return jsonify({"password": f.read().strip()})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/settings', methods=['POST'])
def settings():
    if not session.get('logged_in'): return redirect(url_for('index'))
    save_settings({
        "auto_backup": 'auto_backup' in request.form,
        "auto_cleanup": 'auto_cleanup' in request.form,
        "interval_days": int(request.form.get('interval_days', 1)),
        "backup_time": request.form.get('backup_time', "03:00"),
        "cleanup_mode": request.form.get('cleanup_mode', "count"),
        "cleanup_value": int(request.form.get('cleanup_value', 5))
    })
    flash('Paramètres Backups enregistrés.', 'success')
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload():
    if not session.get('logged_in') or 'file' not in request.files: return redirect(url_for('index'))
    f = request.files['file']
    if f and f.filename != '' and f.filename.endswith('.tar.gz'):
        f.save(os.path.join(BACKUP_DIR, secure_filename(f.filename)))
        flash('Upload réussi.', 'success')
    return redirect(url_for('index'))

@app.route('/download/<filename>')
def download(filename):
    if not session.get('logged_in'): return redirect(url_for('index'))
    return send_file(os.path.join(BACKUP_DIR, secure_filename(filename)), as_attachment=True)

# ==============================================================================
# SECTION 9 : TEMPLATES HTML
# ==============================================================================

HTML_LOGIN = """
<!doctype html>
<html lang="fr" data-bs-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Connexion ECHO Admin</title>
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#0d1117">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body { display: flex; align-items: center; justify-content: center; height: 100vh; background-color: #121212; } .card { width: 100%; max-width: 400px; border: 1px solid #333; }</style>
</head>
<body>
    <div class="card shadow-lg">
        <div class="card-body p-5">
            <h3 class="text-center mb-4">ECHO Admin</h3>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }}">{{ message }}</div>{% endfor %}{% endif %}
            {% endwith %}
            <form method="POST">
                <div class="mb-3"><label class="form-label">Utilisateur</label><input type="text" name="username" class="form-control" required autofocus></div>
                <div class="mb-3"><label class="form-label">Mot de Passe</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100">Se connecter</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

HTML_DASHBOARD = """
<!doctype html>
<html lang="fr" data-bs-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ECHO Admin Console</title>
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#0d1117">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css">
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', system-ui, sans-serif; }
        .navbar { background-color: #161b22; border-bottom: 1px solid #30363d; }
        .card { background-color: #161b22; border: 1px solid #30363d; }
        .card-header { background-color: #21262d; border-bottom: 1px solid #30363d; font-weight: 600; }
        .progress { background-color: #30363d; }
        .table { --bs-table-bg: transparent; --bs-table-border-color: #30363d; color: #c9d1d9; vertical-align: middle; }
        .table-hover tbody tr:hover { --bs-table-hover-bg: rgba(255, 255, 255, 0.075); }
        .scrollable-table { max-height: 350px; overflow-y: auto; }
        .error-text { color: #ff7b72; }
    </style>
</head>
<body>
    <div id="loader" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(13,17,23,0.9);z-index:9999;display:none;flex-direction:column;justify-content:center;align-items:center;">
        <div class="spinner-border text-primary" style="width:3rem;height:3rem;"></div>
        <h4 class="mt-3 text-light" id="loader-msg">Traitement...</h4>
    </div>
    
    <nav class="navbar py-3 mb-4">
        <div class="container">
            <span class="navbar-brand mb-0 h1"><i class="bi bi-cpu-fill text-primary"></i> ECHO Admin <span class="ms-3 badge bg-secondary fw-normal fs-6" id="dynamic-clock">--:--:--</span> <span class="badge bg-dark border border-secondary ms-2">{{ echo_version }}</span></span>
            <div class="d-flex align-items-center gap-3">
                <span class="text-muted small"><i class="bi bi-person-circle"></i> {{ current_user }}</span>
                <a href="/logout" class="btn btn-outline-danger btn-sm"><i class="bi bi-box-arrow-right"></i></a>
            </div>
        </div>
    </nav>

    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}{% for c, m in messages %}
                <div class="alert alert-{{ c }} alert-dismissible fade show border-0">{{ m }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>
            {% endfor %}{% endif %}
        {% endwith %}

        <div class="row g-4">
            <div class="col-md-4"><div class="card h-100 p-3"><div class="d-flex justify-content-between text-secondary small"><span>CPU</span><span id="cpu-details">...</span></div><div class="display-6" id="cpu-percent">--%</div><div class="progress mt-1" style="height:6px;"><div class="progress-bar bg-primary" id="cpu-bar" style="width:0%"></div></div><small class="text-secondary mt-2">Load: <span id="cpu-load">...</span></small></div></div>
            <div class="col-md-4"><div class="card h-100 p-3"><div class="d-flex justify-content-between text-secondary small"><span>RAM</span><i class="bi bi-memory"></i></div><div class="display-6" id="ram-percent">--%</div><div class="progress mt-1" style="height:6px;"><div class="progress-bar bg-info" id="ram-bar" style="width:0%"></div></div><small class="text-secondary mt-2" id="ram-details">...</small></div></div>
            <div class="col-md-4"><div class="card h-100 p-3"><div class="d-flex justify-content-between text-secondary small"><span>Disk (Backups)</span><i class="bi bi-hdd"></i></div><div class="display-6" id="disk-percent">--%</div><div class="progress mt-1" style="height:6px;"><div class="progress-bar bg-success" id="disk-bar" style="width:0%"></div></div><small class="text-secondary mt-2" id="disk-details">...</small></div></div>
        </div>

        <div class="row g-4 mt-1">
            <div class="col-lg-7 d-flex flex-column gap-4">
                <div class="card">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <span><i class="bi bi-archive"></i> Sauvegardes</span>
                        <div class="d-flex gap-2">
                             <button class="btn btn-sm btn-outline-secondary" onclick="refreshBackups()"><i class="bi bi-arrow-repeat"></i></button>
                             <button class="btn btn-sm btn-outline-secondary" onclick="document.getElementById('uploadInput').click()"><i class="bi bi-upload"></i></button>
                             <form action="/action/backup" method="post" class="d-inline" onsubmit="showLoader('Création du backup...')"><button type="submit" class="btn btn-sm btn-success"><i class="bi bi-plus-lg"></i> Backup</button></form>
                        </div>
                    </div>
                    <div class="scrollable-table"><table class="table table-hover mb-0"><thead><tr><th>Fichier</th><th>Date</th><th>Taille</th><th class="text-end">Actions</th></tr></thead><tbody id="backup-list"></tbody></table></div>
                    <form id="uploadForm" action="/upload" method="post" enctype="multipart/form-data" class="d-none"><input type="file" id="uploadInput" name="file" accept=".tar.gz" onchange="showLoader('Upload...'); this.form.submit()"></form>
                </div>

                <div class="card">
                    <div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-people"></i> Statistiques Utilisateurs</span><button class="btn btn-sm btn-outline-secondary" onclick="refreshUserStats()"><i class="bi bi-arrow-repeat"></i></button></div>
                    <div class="scrollable-table"><table class="table table-hover mb-0"><thead><tr><th>Nom</th><th>Email</th><th>Rôle</th><th class="text-center">Conversations</th></tr></thead><tbody id="user-stats-list"></tbody></table></div>
                </div>
            </div>

            <div class="col-lg-5 d-flex flex-column gap-4">
                <div class="card"><div class="card-header"><i class="bi bi-robot"></i> Auto-Backup</div>
                    <div class="card-body">
                        <form action="/settings" method="post">
                            <div class="form-check form-switch mb-3"><input class="form-check-input" type="checkbox" name="auto_backup" id="autoBackup" {% if settings.auto_backup %}checked{% endif %}><label class="form-check-label" for="autoBackup">Backup Auto</label></div>
                            <div class="row g-2 mb-3"><div class="col-6"><label class="form-label small">Jours</label><input type="number" name="interval_days" class="form-control" value="{{ settings.interval_days }}" min="1"></div><div class="col-6"><label class="form-label small">Heure</label><input type="time" name="backup_time" class="form-control" value="{{ settings.backup_time }}"></div></div>
                            <hr class="my-3">
                            <div class="form-check form-switch mb-2"><input class="form-check-input" type="checkbox" name="auto_cleanup" id="autoCleanup" {% if settings.auto_cleanup %}checked{% endif %}><label class="form-check-label" for="autoCleanup">Nettoyage Vieux Backups</label></div>
                            <div class="input-group"><select name="cleanup_mode" class="form-select"><option value="count" {% if settings.cleanup_mode == 'count' %}selected{% endif %}>Garder X derniers</option><option value="days" {% if settings.cleanup_mode == 'days' %}selected{% endif %}>Supprimer > X jours</option></select><input type="number" name="cleanup_value" class="form-control" value="{{ settings.cleanup_value }}" min="1"></div>
                            <button type="submit" class="btn btn-primary w-100 mt-3">Enregistrer</button>
                        </form>
                    </div>
                </div>

                <div class="card border-warning"><div class="card-header text-warning"><i class="bi bi-key"></i> Sécurité Compte</div>
                    <div class="card-body">
                        <button id="btn-copy-pwd" class="btn btn-sm btn-outline-light w-100 mb-2" onclick="copyAdminPassword()"><i class="bi bi-clipboard"></i> Copier Mot de Passe Admin OWUI</button>
                        <button class="btn btn-sm btn-outline-warning w-100" type="button" data-bs-toggle="collapse" data-bs-target="#passwdForm">Changer Mot de Passe Système</button>
                        <div class="collapse mt-3" id="passwdForm"><form action="/action/security/passwd" method="post" onsubmit="showLoader('Changement...')"><input type="password" name="current_password" class="form-control mb-2" placeholder="Mot de passe ACTUEL" required><input type="password" name="new_password" class="form-control mb-2" placeholder="NOUVEAU Mot de passe" required><input type="password" name="confirm_password" class="form-control mb-2" placeholder="Confirmer le nouveau" required><button type="submit" class="btn btn-warning w-100">Valider</button></form></div>
                    </div>
                </div>

                <div class="card border-info"><div class="card-header text-info"><div class="d-flex justify-content-between align-items-center"><span><i class="bi bi-shield-check"></i> Stockage & Maintenance</span><button class="btn btn-sm btn-outline-info" onclick="refreshStorageAnalysis()"><i class="bi bi-arrow-repeat"></i></button></div></div>
                    <div class="card-body">
                        <div class="table-responsive mb-3"><table class="table table-sm small"><tbody>
                            <tr>
                                <td>User Databases <span class="badge bg-secondary ms-1">Orphelins: <span id="st-orphans">...</span></span></td>
                                <td class="text-end" id="st-db-count">{{ storage_stats.user_dbs.count }}</td>
                                <td class="text-end" id="st-db-size">{{ storage_stats.user_dbs.size_fmt }}</td>
                            </tr>
                            <tr>
                                <td>Uploads <span class="badge bg-secondary ms-1">{{ maint_config.retention.uploads_days }} jours</span></td>
                                <td class="text-end" id="st-up-count">{{ storage_stats.uploads.count }}</td>
                                <td class="text-end" id="st-up-size">{{ storage_stats.uploads.size_fmt }}</td>
                            </tr>
                            <tr>
                                <td>Debug Logs <span class="badge bg-secondary ms-1">{{ maint_config.retention.debug_days }} jours</span></td>
                                <td class="text-end" id="st-log-count">{{ storage_stats.debug_logs.count }}</td>
                                <td class="text-end" id="st-log-size">{{ storage_stats.debug_logs.size_fmt }}</td>
                            </tr>
                            <tr>
                                <td>ECHO Vault <span class="badge bg-secondary ms-1">Total</span></td>
                                <td class="text-end" id="st-vault-count">{{ storage_stats.echo_vault.count }}</td>
                                <td class="text-end" id="st-vault-size">{{ storage_stats.echo_vault.size_fmt }}</td>
                            </tr>
                        </tbody></table></div>
                        <button class="btn btn-sm btn-outline-secondary w-100 mb-2" type="button" data-bs-toggle="collapse" data-bs-target="#maintConfig">⚙️ Configurer Rétention</button>
                        <div class="collapse" id="maintConfig"><div class="card card-body bg-dark p-2 mb-2"><form action="/settings/maintenance" method="post"><div class="row g-2 mb-2"><div class="col-6"><label class="form-label small">Uploads (j)</label><input type="number" name="ret_uploads" class="form-control form-control-sm" value="{{ maint_config.retention.uploads_days }}"></div><div class="col-6"><label class="form-label small">Logs (j)</label><input type="number" name="ret_debug" class="form-control form-control-sm" value="{{ maint_config.retention.debug_days }}"></div></div><div class="mb-2"><label class="form-label small">Heure Nettoyage</label><input type="time" name="cleanup_hour" class="form-control form-control-sm" value="{{ maint_config.cleanup_hour }}"></div><button type="submit" class="btn btn-sm btn-success w-100">Enregistrer</button></form></div></div>
                        <div class="d-flex gap-2"><form action="/action/maintenance/run" method="post" class="flex-grow-1"><button type="submit" class="btn btn-sm btn-info w-100" {% if not has_httpx %}disabled{% endif %}><i class="bi bi-stars"></i> Nettoyer Tout</button></form><form action="/action/auth/reset" method="post" onsubmit="return confirm('⚠️ Forcer TOUS les utilisateurs à se ré-authentifier auprès de Google ?')"><button type="submit" class="btn btn-sm btn-danger" title="Purger les tokens Google"><i class="bi bi-shield-slash"></i> Purge Tokens</button></form></div>
                    </div>
                </div>

                <div class="card"><div class="card-header d-flex justify-content-between"><span><i class="bi bi-box-seam"></i> Services</span><button onclick="refreshContainers()" class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-repeat"></i></button></div><ul class="list-group list-group-flush" id="container-list"></ul></div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            // Initialisation de l horloge dynamique basée sur l heure du serveur
            const initialServerTime = new Date('{{ server_time_iso }}');
            function initServerClock() {
                let serverNow = initialServerTime;
                const clockElement = document.getElementById('dynamic-clock');
                if(clockElement) {
                    clockElement.textContent = serverNow.toLocaleTimeString(); // Affichage initial
                    setInterval(() => {
                        serverNow.setSeconds(serverNow.getSeconds() + 1);
                        clockElement.textContent = serverNow.toLocaleTimeString();
                    }, 1000);
                }
            }

            // --- Fonctions utilitaires ---
            function showLoader(msg) { document.getElementById('loader-msg').innerText = msg; document.getElementById('loader').style.display = 'flex'; }
            window.showLoader = showLoader;
            window.confirmRestore = () => { if(confirm("RESTAURATION DESTRUCTIVE ! Les données actuelles seront écrasées. Confirmer ?")) { showLoader("Restauration..."); return true; } return false; };
            async function fetchData(url) { 
                const res = await fetch(url);
                if (!res.ok) {
                    const errorText = await res.text();
                    try {
                        const errorData = JSON.parse(errorText);
                        throw new Error(`HTTP ${res.status}: ${errorData.error || errorText}`);
                    } catch {
                        throw new Error(`HTTP ${res.status}: ${errorText}`);
                    }
                }
                return await res.json();
            }

            async function copyAdminPassword() {
                const btn = document.getElementById('btn-copy-pwd');
                const originalHtml = `<i class="bi bi-clipboard"></i> Copier Mot de Passe Admin OWUI`;
                
                try {
                    const data = await fetchData('/api/admin/password');
                    if(data.error) throw new Error(data.error);
                    const pwd = data.password;

                    try {
                        await navigator.clipboard.writeText(pwd);
                    } catch(err) {
                        const ta = document.createElement('textarea');
                        ta.value = pwd;
                        ta.style.position = 'fixed';
                        ta.style.left = '-9999px';
                        document.body.appendChild(ta);
                        ta.select();
                        document.execCommand('copy');
                        document.body.removeChild(ta);
                    }

                    btn.innerHTML = '<i class="bi bi-check-lg"></i> Copié !';
                    btn.classList.replace('btn-outline-light', 'btn-success');
                    
                    setTimeout(() => {
                        btn.innerHTML = originalHtml;
                        btn.classList.replace('btn-success', 'btn-outline-light');
                    }, 2000);

                } catch(e) {
                    alert("Erreur: " + e.message);
                    btn.innerHTML = originalHtml;
                }
            }
            window.copyAdminPassword = copyAdminPassword;
            
            // --- Fonctions de rafraîchissement des données ---
            async function updateStats() {
                try {
                    const d = await fetchData('/api/stats');
                    document.getElementById('cpu-percent').textContent = d.cpu_percent + '%';
                    document.getElementById('cpu-bar').style.width = d.cpu_percent + '%';
                    document.getElementById('cpu-details').textContent = `${d.cpu_count} vCPU @ ${d.cpu_model}`;
                    document.getElementById('cpu-load').textContent = d.cpu_load.join(', ');
                    
                    document.getElementById('ram-percent').textContent = d.ram_percent + '%';
                    document.getElementById('ram-bar').style.width = d.ram_percent + '%';
                    document.getElementById('ram-details').textContent = `${d.ram_used} / ${d.ram_total}`;

                    document.getElementById('disk-percent').textContent = d.disk_percent + '%';
                    document.getElementById('disk-bar').style.width = d.disk_percent + '%';
                    document.getElementById('disk-details').textContent = `${d.disk_used} / ${d.disk_total}`;
                } catch(e) { 
                    console.error("Stats Error:", e);
                    document.getElementById('cpu-details').innerHTML = `<span class="error-text">${e.message}</span>`;
                }
            }
            
            async function refreshStorageAnalysis() {
                 document.getElementById('st-orphans').textContent = '...';
                 try {
                     const data = await fetchData('/api/storage/analysis');
                     // Mise à jour User DBs
                     document.getElementById('st-db-count').textContent = data.user_dbs.count;
                     document.getElementById('st-db-size').textContent = data.user_dbs.size_fmt;
                     const orphans = data.user_dbs.orphans;
                     const orphanEl = document.getElementById('st-orphans');
                     if(orphans === -1) orphanEl.textContent = "Err Auth";
                     else if(orphans === 0) { orphanEl.textContent = "0"; orphanEl.className = "badge bg-success ms-1"; }
                     else { orphanEl.textContent = orphans; orphanEl.className = "badge bg-danger ms-1"; }
                     
                     // Mise à jour autres
                     document.getElementById('st-up-count').textContent = data.uploads.count;
                     document.getElementById('st-up-size').textContent = data.uploads.size_fmt;
                     document.getElementById('st-log-count').textContent = data.debug_logs.count;
                     document.getElementById('st-log-size').textContent = data.debug_logs.size_fmt;
                     document.getElementById('st-vault-count').textContent = data.echo_vault.count;
                     document.getElementById('st-vault-size').textContent = data.echo_vault.size_fmt;
                     
                 } catch(e) {
                     console.error("Storage Error:", e);
                     document.getElementById('st-orphans').textContent = "?";
                 }
            }

            async function refreshBackups() {
                const list = document.getElementById('backup-list');
                list.innerHTML = '<tr><td colspan="4" class="text-center py-5">Chargement...</td></tr>';
                try {
                    const data = await fetchData('/api/backups');
                    list.innerHTML = data.length === 0 ? '<tr><td colspan="4" class="text-center py-5 text-muted">Aucune sauvegarde</td></tr>' 
                        : data.map(b => `<tr><td><i class="bi bi-file-earmark-zip me-2"></i>${b.name}</td><td>${b.date}</td><td><span class="badge bg-secondary">${b.size}</span></td><td class="text-end"><div class="btn-group"><a href="/download/${b.name}" class="btn btn-outline-primary btn-sm" title="Télécharger"><i class="bi bi-download"></i></a><form action="/action/restore" method="post" onsubmit="return confirmRestore()" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-outline-warning btn-sm" title="Restaurer"><i class="bi bi-arrow-counterclockwise"></i></button></form><form action="/action/delete" method="post" onsubmit="return confirm('Supprimer la sauvegarde ${b.name} ?')" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-danger btn-sm" title="Supprimer"><i class="bi bi-trash3-fill"></i></button></form></div></td></tr>`).join('');
                } catch(e) { list.innerHTML = `<tr><td colspan="4" class="text-center py-5 error-text">${e.message}</td></tr>`; }
            }

            async function refreshContainers() {
                const list = document.getElementById('container-list');
                try {
                    const data = await fetchData('/api/containers');
                    list.innerHTML = data.length === 0 ? `<li class="list-group-item bg-transparent text-muted">Aucun conteneur trouvé.</li>`
                        : data.map(c => `<li class="list-group-item bg-transparent d-flex justify-content-between align-items-center"><div><span class="status-badge rounded-circle d-inline-block me-2" style="width:10px;height:10px;background-color:${c.status.startsWith('Up') ? '#28a745' : '#dc3545'};"></span>${c.name}<br><small class="text-muted">${c.status}</small></div><form action="/action/restart" method="post" onsubmit="showLoader('Redémarrage...')"><input type="hidden" name="container" value="${c.id}"><button class="btn btn-sm btn-outline-secondary py-0" title="Redémarrer"><i class="bi bi-power"></i></button></form></li>`).join('');
                } catch(e) { list.innerHTML = `<li class="list-group-item bg-transparent text-danger">${e.message}</li>`; }
            }
            
            async function refreshUserStats() {
                const list = document.getElementById('user-stats-list');
                list.innerHTML = '<tr><td colspan="4" class="text-center py-5">Chargement...</td></tr>';
                try {
                    const data = await fetchData('/api/user_stats');
                    if (data.length > 0 && data[0].error) { throw new Error(data[0].error); }
                    list.innerHTML = data.length === 0 ? '<tr><td colspan="4" class="text-center py-5 text-muted">Aucun utilisateur trouvé</td></tr>' 
                        : data.map(u => `<tr><td>${u.name}</td><td>${u.email||'N/A'}</td><td><span class="badge bg-info text-dark">${u.role}</span></td><td class="text-center">${u.chat_count}</td></tr>`).join('');
                } catch(e) { list.innerHTML = `<tr><td colspan="4" class="text-center py-5 error-text">${e.message}</td></tr>`; }
            }

            // --- Initialisation au chargement de la page ---
            initServerClock();
            updateStats(); setInterval(updateStats, 5000);
            refreshBackups(); window.refreshBackups = refreshBackups;
            refreshContainers(); window.refreshContainers = refreshContainers;
            refreshUserStats(); window.refreshUserStats = refreshUserStats;
            refreshStorageAnalysis(); window.refreshStorageAnalysis = refreshStorageAnalysis; // Scan initial
            
            var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
            var tooltipList = tooltipTriggerList.map(function (el) { return new bootstrap.Tooltip(el) })
        });
    </script>
</body>
</html>

""";

# ==============================================================================
# SECTION 10 : POINT D ENTRÉE (MAIN)
# ==============================================================================
if __name__ == '__main__':
    # Lancement du serveur Flask
    app.run(host='0.0.0.0', port=3001, debug=False, threaded=True)
