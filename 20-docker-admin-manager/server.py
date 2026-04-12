# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER SERVER
VERSION : 5.52 (Semantic TTL Labels)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-04-12

--- DESCRIPTION ARCHITECTURALE ---
Ce micro-service assure la régulation et le monitoring du framework ECHO.
Architecture ECHO-Native avec distinction entre stockage et sessions.

--- CHANGELOG 5.52 ---
- UX : Ajout de labels sémantiques explicites (Trivial, Mineur, Utile, Majeur, Axiome) au-dessus des champs de rétention TTL pour une meilleure lisibilité administrative.
--- CHANGELOG 5.51 ---
- UX : Ajout de champs de configuration dans le Dashboard pour personnaliser les durées de rétention (TTL) des mémoires organiques (Niveaux 1 à 5).
--- CHANGELOG 5.50 ---
- Optimisation : Centralisation du processus d'oubli naturel (TTL Decay) de la mémoire organique dans l'Admin Manager pour alléger le traitement temps-réel du filtre conversationnel.
--- CHANGELOG 5.41 ---
- Correctif : Restauration des constantes QDRANT_URL et COLLECTION_MEMORY.
- UX : Ajout du label "Logs" sur le bouton d'historique de maintenance.
--- CHANGELOG 5.40 ---
- Ajout : Synchronisation automatique de la mémoire organique (Qdrant) pour éliminer les souvenirs orphelins (utilisateurs ou chats supprimés).
- Ajout : Historique d'audit persistant (1 an de rétention) affichable directement depuis l'interface UI.
--- CHANGELOG 5.30 ---
- Correction : Fallback robuste pour la copie du mot de passe Admin (support HTTP/Non-Secure).
- Ajout : Route API /api/backups pour le rafraîchissement dynamique.
- Ajout : Route /download/<filename> pour la récupération des sauvegardes.
- Sécurité : Vérification d'existence du fichier avant restauration destructive.
- Harmonisation : Usage strict de OWUI_ADMIN_SECRET_PATH.
--- CHANGELOG 5.25 ---
- Correction : Suppression définitive du décorateur @app.after_request orphelin (ligne 78).
--- CHANGELOG 5.24 ---
- Vérité Sémantique : Le compteur "Sessions Actives" ne cible plus que les .db de chats.
- Correction du décalage : Exclusion de identity.db et des fichiers docs du compteur de sessions.
- Maintien du Volume Global : Le volume Vault reste inclusif (docs + bases).
- Parité UX intégrale maintenue (Horloge, Tooltips, Monitoring).
================================================================================
"""

from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for, flash, session # pyright: ignore[reportMissingImports]
from typing import Optional, List, Dict, Tuple
import os
import subprocess
import datetime
import glob
import secrets
import orjson as json
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
    HAS_MAINT_SCHEDULER = False

# ==============================================================================
# SECTION 2 : CONFIGURATION & CHEMINS
# ==============================================================================
app = Flask(__name__, static_folder='/app/static')
app.secret_key = secrets.token_hex(32)
app.config['JSON_AS_ASCII'] = False

TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'echo-webui-core')
BACKUP_DIR = "/backups"
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")

OWUI_DATA_ROOT = "/app/backend/data"
ECHO_USERS_ROOT = os.path.join(OWUI_DATA_ROOT, "users") 
UPLOADS_DIR = os.path.join(OWUI_DATA_ROOT, "uploads")
WEBUI_DB_PATH = os.path.join(OWUI_DATA_ROOT, "webui.db")
OWUI_ADMIN_SECRET_PATH = "/app/secrets/.owui-admin-secret"

QDRANT_URL = "http://echo-qdrant:6333"
COLLECTION_MEMORY = "echo_memory"

DIRS = {
    "uploads": UPLOADS_DIR,
    "echo_vault": ECHO_USERS_ROOT,
    "debug_logs": os.path.join(OWUI_DATA_ROOT, "debug_logs")
}

DEFAULT_BACKUP_CONFIG = {
    "auto_backup": True, "auto_cleanup": True, "cleanup_mode": "count",
    "cleanup_value": 5, "backup_time": "03:00", "interval_days": 1
}

DEFAULT_MAINT_CONFIG = {
    "cleanup_hour": "03:00", "last_run": "Never",
    "retention": { "uploads_days": 1095, "vault_days": 1095 },
    "memory_ttl": { "lvl1": 30, "lvl2": 60, "lvl3": 180, "lvl4": 365, "lvl5": 540 }
}

# ==============================================================================
# SECTION 3 : HELPERS & LOGIQUE RÉCURSIVE
# ==============================================================================

def human_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def get_cpu_model_name():
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if "model name" in line: return line.split(':')[1].strip()
    except: return "N/A"

def get_dir_stats(path, filter_ext=None):
    if not os.path.exists(path): return {"count": 0, "size": 0, "size_fmt": "0 B"}
    t_size, t_count = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            if filter_ext and not f.endswith(filter_ext): continue
            try:
                t_size += os.path.getsize(os.path.join(root, f))
                t_count += 1
            except: pass
    return {"count": t_count, "size": t_size, "size_fmt": human_size(t_size)}

def prune_recursive(path: str, days: int, sanctuary_files: List[str] = ["identity.db"]):
    if not os.path.exists(path): return 0
    cutoff = time.time() - (days * 86400)
    removed = 0
    for root, _, files in os.walk(path):
        for f in files:
            if f in sanctuary_files: continue
            fpath = os.path.join(root, f)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath); removed += 1
            except: pass
    return removed

def get_echo_version():
    try:
        if os.path.exists('/app/ECHO_VERSION'):
            with open('/app/ECHO_VERSION', 'r', encoding='utf-8') as f: return f.read().strip()
    except: pass
    return "v?.?"

# ==============================================================================
# SECTION 4 : ÉLAGAGE SÉMANTIQUE & SÉCURITÉ
# ==============================================================================

def load_maint_config():
    c = DEFAULT_MAINT_CONFIG.copy()
    m_file = os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")
    if os.path.exists(m_file):
        try:
            with open(m_file, 'r') as f: c.update(json.load(f))
        except: pass
    return c

def save_maint_config(c):
    try:
        with open(os.path.join(OWUI_DATA_ROOT, "maintenance_config.json"), 'w') as f:
            json.dump(c, f, indent=4)
    except: pass

MAINT_HISTORY_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_history.json")

def load_maint_history():
    if os.path.exists(MAINT_HISTORY_FILE):
        try:
            with open(MAINT_HISTORY_FILE, 'r') as f: return json.load(f)
        except: pass
    return []

def save_maint_report(report_str):
    history = load_maint_history()
    new_entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "report": report_str
    }
    history.insert(0, new_entry)
    
    # Auto-purge de l'historique (Rétention 1 an / 365 entrées max)
    cutoff = time.time() - (365 * 86400)
    history = [h for h in history if time.mktime(time.strptime(h["timestamp"], "%Y-%m-%d %H:%M:%S")) > cutoff]
    
    try:
        with open(MAINT_HISTORY_FILE, 'w') as f: json.dump(history[:500], f, indent=4)
    except: pass

def run_semantic_pruning():
    """Élagage organique (v5.51 + Qdrant Sync & TTL)."""
    print("🧬 [ECHO-LIFECYCLE] Démarrage...")
    report = []
    config = load_maint_config()
    
    # 1. Orphelins (Dossiers Utilisateurs et Mémoire Qdrant)
    orphans = 0
    qdrant_synced = False
    if os.path.exists(ECHO_USERS_ROOT):
        try:
            conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
            valid_ids = {row[0] for row in conn.execute("SELECT id FROM user").fetchall()}
            conn.close()
            
            # --- A. Purge des dossiers du Vault ---
            for folder in os.listdir(ECHO_USERS_ROOT):
                if folder not in valid_ids and len(folder) > 30:
                    shutil.rmtree(os.path.join(ECHO_USERS_ROOT, folder))
                    orphans += 1
            
            # --- B. Garbage Collection & Oubli Organique (Qdrant) ---
            if HAS_HTTPX and valid_ids:
                try:
                    # Test de disponibilité Qdrant
                    r = httpx.get(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}", timeout=5)
                    if r.status_code == 200:
                        # 1) Utilisateurs orphelins
                        httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete", 
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
                            user_chats_dir = os.path.join(ECHO_USERS_ROOT, str_uid, "chats")
                            valid_chats = []
                            if os.path.exists(user_chats_dir):
                                valid_chats = [f.replace('.db', '') for f in os.listdir(user_chats_dir) if f.endswith('.db')]
                            
                            if not valid_chats:
                                payload = {"filter": {"must": [{"key": "user_id", "match": {"value": str_uid}}]}}
                            else:
                                payload = {
                                    "filter": {
                                        "must": [{"key": "user_id", "match": {"value": str_uid}}],
                                        "must_not": [{"key": "chat_id", "match": {"any": valid_chats}}]
                                    }
                                }
                            httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete", json=payload, timeout=30)
                            
                            # Decay TTL
                            for level, seconds in ttl_map.items():
                                decay_payload = {
                                    "filter": {
                                        "must": [
                                            {"key": "user_id", "match": {"value": str_uid}},
                                            {"key": "importance", "match": {"value": level}},
                                            {"key": "timestamp", "range": {"lt": now - seconds}}
                                        ]
                                    }
                                }
                                httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete", json=decay_payload, timeout=30)
                        
                        qdrant_synced = True
                except Exception as e:
                    print(f"[ECHO-LIFECYCLE] ❌ Erreur Qdrant : {e}")
        except Exception as e:
            print(f"[ECHO-LIFECYCLE] ❌ Erreur DB/Vault : {e}")
        
    report_str = f"Orphelins: {orphans}"
    if qdrant_synced:
        report_str += " | Qdrant: Synchro (Chats/Users/TTL Decay)"
    report.append(report_str)

    # 2. Atrophie
    rem_u = prune_recursive(UPLOADS_DIR, config["retention"]["uploads_days"])
    rem_v = prune_recursive(ECHO_USERS_ROOT, config["retention"]["vault_days"])
    report.append(f"Élagage: {rem_u + rem_v}")

    # 3. Vacuum
    vax = 0
    for root, _, files in os.walk(ECHO_USERS_ROOT):
        for f in files:
            if f.endswith('.db'):
                try:
                    with sqlite3.connect(os.path.join(root, f), timeout=10.0) as db:
                        db.execute("VACUUM;")
                        vax += 1
                except: pass
    report.append(f"Optimisés: {vax}")

    final_report = " | ".join(report)
    config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_maint_config(config)
    save_maint_report(final_report)
    return final_report

def setup_lifecycle_scheduler():
    if not HAS_MAINT_SCHEDULER: return
    try:
        config = load_maint_config()
        schedule.clear()
        schedule.every().day.at(config.get("cleanup_hour", "03:00")).do(run_semantic_pruning)
    except: pass

def change_system_password(username, current_pwd, new_pwd):
    if not DOCKER_AVAILABLE: return False, "Module SSH absent"
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=username, password=current_pwd, timeout=10)
        chan = ssh.invoke_shell(); time.sleep(1)
        chan.send('passwd\n'); time.sleep(1)
        chan.send(f'{current_pwd}\n'); time.sleep(0.5)
        chan.send(f'{new_pwd}\n'); time.sleep(0.5)
        chan.send(f'{new_pwd}\n'); time.sleep(1)
        out = chan.recv(4096).decode('utf-8', errors='ignore'); ssh.close()
        if "updated successfully" in out or "mis à jour avec succès" in out: return True, "Succès"
        return False, "Refus système"
    except Exception as e: return False, str(e)

# ==============================================================================
# SECTION 5 : SAUVEGARDES
# ==============================================================================

def load_settings():
    c = DEFAULT_BACKUP_CONFIG.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f: c.update(json.loads(f.read()))
        except: pass
    return c

def save_settings(new_s):
    c = load_settings(); c.update(new_s)
    with open(SETTINGS_FILE, 'w') as f: f.write(json.dumps(c, option=json.OPT_INDENT_2).decode('utf-8'))
    update_backup_schedule()

def perform_backup_task():
    if not DOCKER_AVAILABLE: return
    fpath = os.path.join(BACKUP_DIR, f"echo_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz")
    try:
        client = docker.from_env(); target = client.containers.get(TARGET_CONTAINER)
        target.stop()
        subprocess.run(['tar', '-czf', fpath, '-C', OWUI_DATA_ROOT, '.'], check=True)
        target.start()
        sets = load_settings()
        if sets.get("auto_cleanup"):
            files = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
            mode, val = sets.get("cleanup_mode", "count"), int(sets.get("cleanup_value", 5))
            if mode == 'count' and len(files) > val:
                for f in files[val:]: os.remove(f)
            elif mode == 'days':
                for f in files:
                    if os.path.getmtime(f) < (time.time() - (val * 86400)): os.remove(f)
    except:
        try: docker.from_env().containers.get(TARGET_CONTAINER).start()
        except: pass

def update_backup_schedule():
    if not HAS_SCHEDULER: return
    try:
        backup_scheduler.remove_all_jobs()
        sets = load_settings()
        if sets.get("auto_backup"):
            h, m = map(int, sets.get("backup_time", "03:00").split(':'))
            now = datetime.datetime.now()
            start = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if start <= now: start += datetime.timedelta(days=1)
            backup_scheduler.add_job(perform_backup_task, 'interval', days=int(sets.get("interval_days", 1)), start_date=start)
    except: pass

def get_backup_list():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
    return [{'name': os.path.basename(f), 'size': human_size(os.path.getsize(f)), 'date': datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d/%m/%Y %H:%M')} for f in files]

# ==============================================================================
# SECTION 6 : ROUTES FLASK
# ==============================================================================

@app.route('/')
def index():
    if not session.get('logged_in'): return render_template_string(HTML_LOGIN)
    
    # Volume Total (Docs + Bases)
    vault_storage = get_dir_stats(ECHO_USERS_ROOT)
    
    # Compteur de Sessions Réelles (Uniquement .db dans /chats/)
    real_session_count = 0
    if os.path.exists(ECHO_USERS_ROOT):
        for root, _, files in os.walk(ECHO_USERS_ROOT):
            if "chats" in root:
                for f in files:
                    if f.endswith('.db'): real_session_count += 1

    stats = {
        "uploads": get_dir_stats(UPLOADS_DIR),
        "vault": vault_storage,
        "logs": get_dir_stats(os.path.join(OWUI_DATA_ROOT, "debug_logs")),
        "real_sessions": real_session_count
    }
    return render_template_string(HTML_DASHBOARD, settings=load_settings(), 
                                storage_stats=stats, maint=load_maint_config(),
                                version=get_echo_version(), user=session.get('username'),
                                backups=get_backup_list(), history=load_maint_history(),
                                server_time_iso=datetime.datetime.now().isoformat())

@app.route('/api/maint/history')
def maint_history():
    if not session.get('logged_in'): return jsonify([]), 403
    return jsonify(load_maint_history())

@app.route('/', methods=['POST'])
def login():
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=request.form.get('username'), password=request.form.get('password'), timeout=5)
        ssh.close(); session['logged_in'] = True; session['username'] = request.form.get('username')
    except: flash('Authentification échouée.', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/api/user_stats')
def user_stats():
    if not session.get('logged_in'): return jsonify([])
    data = []
    try:
        conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
        users = conn.execute("SELECT id, name, email, role FROM user ORDER BY name ASC").fetchall()
        for u in users:
            v_dir = os.path.join(ECHO_USERS_ROOT, str(u[0]), "chats")
            count = len([f for f in os.listdir(v_dir) if f.endswith('.db')]) if os.path.exists(v_dir) else 0
            data.append({"name": u[1], "email": u[2], "role": u[3], "chat_count": count})
        conn.close()
    except: pass
    return jsonify(data)

@app.route('/api/admin/password')
def admin_password():
    if not session.get('logged_in'): return jsonify({}), 403
    try:
        if os.path.exists(OWUI_ADMIN_SECRET_PATH):
            with open(OWUI_ADMIN_SECRET_PATH, 'r') as f: return jsonify({"password": f.read().strip()})
    except: pass
    return jsonify({"password": "N/A"})

@app.route('/api/backups')
def api_backups():
    if not session.get('logged_in'): return jsonify([]), 403
    return jsonify(get_backup_list())

@app.route('/download/<filename>')
def download_backup(filename):
    if not session.get('logged_in'): return redirect(url_for('index'))
    path = os.path.join(BACKUP_DIR, secure_filename(filename))
    if os.path.exists(path): return send_file(path, as_attachment=True)
    flash('Fichier introuvable.', 'danger')
    return redirect(url_for('index'))

@app.route('/api/stats')
def sys_stats():
    if not HAS_PSUTIL: return jsonify({})
    mem = psutil.virtual_memory(); disk = psutil.disk_usage(BACKUP_DIR)
    return jsonify({
        "cpu_percent": psutil.cpu_percent(), "cpu_count": os.cpu_count(),
        "cpu_model": get_cpu_model_name(), "cpu_load": [round(l, 2) for l in os.getloadavg()],
        "ram_percent": mem.percent, "ram_used": human_size(mem.used), "ram_total": human_size(mem.total),
        "disk_percent": disk.percent, "disk_used": human_size(disk.used), "disk_total": human_size(disk.total)
    })

@app.route('/api/containers')
def containers():
    if not DOCKER_AVAILABLE: return jsonify([])
    try:
        return jsonify([{"id": c.short_id, "name": c.name, "status": c.status.capitalize()} for c in docker.from_env().containers.list(all=True)])
    except: return jsonify([])

@app.route('/action/<action>', methods=['POST'])
def handle_action(action):
    if not session.get('logged_in'): return redirect(url_for('index'))
    if action == 'backup': threading.Thread(target=perform_backup_task).start(); flash('Sauvegarde complète lancée.', 'info')
    elif action == 'pruning': threading.Thread(target=run_semantic_pruning).start(); flash('Élagage sémantique lancé.', 'info')
    elif action == 'restart':
        cid = request.form.get('container')
        if cid and DOCKER_AVAILABLE:
            try: docker.from_env().containers.get(cid).restart(); flash('Redémarré.', 'success')
            except: pass
    elif action == 'delete_backup':
        f = request.form.get('filename')
        if f and os.path.exists(p := os.path.join(BACKUP_DIR, secure_filename(f))): os.remove(p); flash('Supprimé.', 'warning')
    elif action == 'restore':
        f = request.form.get('filename')
        if f and DOCKER_AVAILABLE:
            p = os.path.join(BACKUP_DIR, secure_filename(f))
            if not os.path.exists(p):
                flash('Erreur: Fichier de sauvegarde introuvable.', 'danger')
                return redirect(url_for('index'))
            try:
                client = docker.from_env(); target = client.containers.get(TARGET_CONTAINER)
                target.stop(); subprocess.run(f"rm -rf {OWUI_DATA_ROOT}/*", shell=True)
                subprocess.run(['tar', '-xzf', p, '-C', OWUI_DATA_ROOT], check=True); target.start()
                flash('Restauration terminée.', 'success')
            except Exception as e: flash(f'Erreur: {e}', 'danger')
    elif action == 'auth_reset':
        db_paths = glob.glob(os.path.join(ECHO_USERS_ROOT, '*', 'identity.db'))
        for p in db_paths:
            try:
                with sqlite3.connect(p, timeout=5.0) as conn: conn.execute("DELETE FROM auth_data WHERE key LIKE 'google_%'")
            except: pass
        flash('Tokens Google purgés du Vault.', 'success')
    return redirect(url_for('index'))

@app.route('/settings', methods=['POST'])
def save_settings_route():
    save_settings({
        "auto_backup": 'auto_backup' in request.form,
        "auto_cleanup": 'auto_cleanup' in request.form,
        "interval_days": int(request.form.get('interval_days', 1)),
        "backup_time": request.form.get('backup_time', "03:00"),
        "cleanup_mode": request.form.get('cleanup_mode', "count"),
        "cleanup_value": int(request.form.get('cleanup_value', 5))
    })
    flash('Paramètres sauvegardes mis à jour.', 'success')
    return redirect(url_for('index'))

@app.route('/settings/maintenance', methods=['POST'])
def update_maint():
    c = load_maint_config()
    c["cleanup_hour"] = request.form.get("cleanup_hour", "03:00")
    c["retention"]["uploads_days"] = int(request.form.get("ret_uploads", 1095))
    c["retention"]["vault_days"] = int(request.form.get("ret_vault", 1095))
    
    if "memory_ttl" not in c: c["memory_ttl"] = {}
    c["memory_ttl"]["lvl1"] = int(request.form.get("ttl_lvl1", 30))
    c["memory_ttl"]["lvl2"] = int(request.form.get("ttl_lvl2", 60))
    c["memory_ttl"]["lvl3"] = int(request.form.get("ttl_lvl3", 180))
    c["memory_ttl"]["lvl4"] = int(request.form.get("ttl_lvl4", 365))
    c["memory_ttl"]["lvl5"] = int(request.form.get("ttl_lvl5", 540))

    save_maint_config(c); setup_lifecycle_scheduler(); flash('Cycle de vie et Mémoire mis à jour.', 'success')
    return redirect(url_for('index'))

@app.route('/action/security/passwd', methods=['POST'])
def passwd_change():
    u = session.get('username')
    new_p, conf_p = request.form.get('new_password'), request.form.get('confirm_password')
    if new_p != conf_p: flash('Mots de passe différents.', 'danger')
    else:
        ok, msg = change_system_password(u, request.form.get('current_password'), new_p)
        flash(f'✅ {msg}' if ok else f'❌ {msg}', 'success' if ok else 'danger')
    return redirect(url_for('index'))

# ==============================================================================
# SECTION 7 : TEMPLATES (UX VÉRITÉ SÉMANTIQUE)
# ==============================================================================

HTML_LOGIN = """
<!doctype html><html lang="fr" data-bs-theme="dark"><head><meta charset="utf-8"><title>Login - ECHO Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-black d-flex align-items-center justify-content-center vh-100"><div class="card p-5 border-secondary shadow-lg" style="width:400px;"><h2 class="text-center text-primary mb-4">ECHO Admin</h2><form method="POST"><input type="text" name="username" class="form-control mb-3" placeholder="Admin" required autofocus><input type="password" name="password" class="form-control mb-4" placeholder="Password" required><button class="btn btn-primary w-100">Entrer</button></form></div></body></html>"""

HTML_DASHBOARD = """
<!doctype html><html lang="fr" data-bs-theme="dark"><head><meta charset="utf-8"><title>Console - ECHO Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css"><style>.card{background-color:#161b22;border-color:#30363d}.navbar{background-color:#161b22;border-bottom:1px solid #30363d}.table{--bs-table-bg:transparent;--bs-table-border-color:#30363d;color:#c9d1d9}.x-small{font-size:0.7rem;color:#888}</style></head>
<body style="background-color:#0d1117;">
    <div id="loader" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;display:none;flex-direction:column;justify-content:center;align-items:center;"><div class="spinner-border text-primary mb-3"></div><h4 id="loader-msg">Action en cours...</h4></div>
    <nav class="navbar px-4 py-2 mb-4"><span class="navbar-brand text-primary fw-bold" data-bs-toggle="tooltip" title="ECHO Infrastructure Manager"><i class="bi bi-tree-fill"></i> ECHO CONSOLE {{ version }}</span><div class="d-flex align-items-center gap-3"><span class="badge bg-dark border border-secondary" id="clock">--:--:--</span><span class="text-muted small">{{ user }}</span><a href="/logout" class="btn btn-sm btn-outline-danger">Quitter</a></div></nav>
    <div class="container">
        {% with msgs = get_flashed_messages(with_categories=true) %}{% for c,m in msgs %}<div class="alert alert-{{c}} alert-dismissible fade show border-0 mb-4">{{m}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}{% endwith %}
        <div class="row g-4 mb-4">
            <div class="col-md-3"><div class="card h-100 p-3" data-bs-toggle="tooltip" title="Charge CPU du serveur hôte">CPU: <span id="cpu">--</span>%<br><small class="x-small" id="cpu-details">...</small><div class="progress mt-2" style="height:4px;"><div id="cpu-bar" class="progress-bar bg-primary"></div></div></div></div>
            <div class="col-md-3"><div class="card h-100 p-3" data-bs-toggle="tooltip" title="Utilisation de la mémoire vive">RAM: <span id="ram">--</span>%<br><small class="x-small" id="ram-text">--/--</small><div class="progress mt-2" style="height:4px;"><div id="ram-bar" class="progress-bar bg-info"></div></div></div></div>
            <div class="col-md-3"><div class="card h-100 p-3 text-center" data-bs-toggle="tooltip" title="Sessions de chat ECHO réelles (fichiers .db dans /chats/)">Sessions Actives<br><b class="text-primary fs-4">{{ storage_stats.real_sessions }}</b></div></div>
            <div class="col-md-3"><div class="card h-100 p-3 text-center" data-bs-toggle="tooltip" title="Volume total occupé par le Vault (Docs + Bases + Identités)">Volume Vault<br><b class="text-success fs-4">{{ storage_stats.vault.size_fmt }}</b></div></div>
        </div>
        <div class="row g-4">
            <div class="col-lg-7">
                <div class="card mb-4"><div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-people"></i> Sessions par Utilisateur</span><button class="btn btn-sm btn-outline-secondary" onclick="refreshUsers()"><i class="bi bi-arrow-repeat"></i></button></div><div class="p-0"><table class="table table-hover mb-0"><thead><tr><th class="ps-3">Nom</th><th>Email</th><th class="text-center">Sessions</th></tr></thead><tbody id="user-list"></tbody></table></div></div>
                <div class="card"><div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-hdd-network"></i> Sauvegardes</span><div class="btn-group"><button class="btn btn-sm btn-outline-secondary" onclick="refreshBackups()"><i class="bi bi-arrow-repeat"></i></button><form action="/action/backup" method="post" onsubmit="showLoader()"><button class="btn btn-sm btn-success">+</button></form></div></div><div class="p-0"><table class="table table-sm mb-0"><thead><tr><th class="ps-3">Fichier</th><th>Date</th><th>Taille</th><th class="text-end pe-3">Action</th></tr></thead><tbody id="backup-rows"></tbody></table></div></div>
            </div>
            <div class="col-lg-5">
                <div class="card mb-4 border-info"><div class="card-header text-info"><i class="bi bi-scissors"></i> Élagage & Cycle de Vie (Jours)</div><div class="card-body small">
                    <form action="/settings/maintenance" method="post" class="mb-3">
                        <div class="row g-2 mb-2">
                            <div class="col-6"><label class="x-small">Uploads</label><input type="number" name="ret_uploads" class="form-control form-control-sm" value="{{maint.retention.uploads_days}}"></div>
                            <div class="col-6"><label class="x-small">Vault</label><input type="number" name="ret_vault" class="form-control form-control-sm" value="{{maint.retention.vault_days}}"></div>
                        </div>
                        <div class="row g-2 mb-2">
                            <div class="col-12"><label class="x-small text-muted">Durée de conservation de la mémoire (TTL par niveau) :</label></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Trivial</label><input type="number" name="ttl_lvl1" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl1}}" title="Lv1 (Trivial)"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Mineur</label><input type="number" name="ttl_lvl2" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl2}}" title="Lv2"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Utile</label><input type="number" name="ttl_lvl3" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl3}}" title="Lv3"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Majeur</label><input type="number" name="ttl_lvl4" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl4}}" title="Lv4"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Axiome</label><input type="number" name="ttl_lvl5" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl5}}" title="Lv5 (Axiome/Critique)"></div>
                        </div>
                        <label class="x-small">Heure d'élagage automatique</label><input type="time" name="cleanup_hour" class="form-control form-control-sm mb-2" value="{{maint.cleanup_hour}}">
                        <button class="btn btn-sm btn-info w-100">Programmer le Cycle</button>
                    </form>
                    <hr><p>Transit (Uploads) : <b>{{ storage_stats.uploads.size_fmt }}</b></p>
                    <div class="d-flex gap-2">
                        <form action="/action/pruning" method="post" onsubmit="showLoader('Élagage profond...')" class="flex-grow-1"><button class="btn btn-outline-info btn-sm w-100">Lancer l'Élagage</button></form>
                        <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="collapse" data-bs-target="#historyLog"><i class="bi bi-journal-text"></i> Logs</button>
                    </div>
                    <div class="collapse mt-3" id="historyLog">
                        <div class="bg-dark p-2 rounded border border-secondary" style="max-height: 200px; overflow-y: auto;">
                            <h6 class="x-small text-uppercase text-muted border-bottom border-secondary pb-1">Historique 1 an</h6>
                            {% for entry in history %}
                            <div class="mb-2 pb-1 border-bottom border-secondary last-child-border-0">
                                <span class="x-small text-info">{{ entry.timestamp }}</span><br>
                                <span style="font-size: 0.75rem;">{{ entry.report }}</span>
                            </div>
                            {% endfor %}
                            {% if not history %}<span class="x-small text-muted">Aucun log disponible.</span>{% endif %}
                        </div>
                    </div>
                </div></div>
                <div class="card mb-4 border-warning"><div class="card-header text-warning"><i class="bi bi-shield-lock"></i> Sécurité & Backups Auto</div><div class="card-body small">
                    <form action="/settings" method="post" class="mb-3">
                        <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="auto_backup" {% if settings.auto_backup %}checked{% endif %}> Backup Auto</div>
                        <div class="row g-2 mt-1"><div class="col-6"><label class="x-small">Intervalle (j)</label><input type="number" name="interval_days" class="form-control form-control-sm" value="{{settings.interval_days}}"></div><div class="col-6"><label class="x-small">Heure</label><input type="time" name="backup_time" class="form-control form-control-sm" value="{{settings.backup_time}}"></div></div>
                        <div class="input-group mt-2"><select name="cleanup_mode" class="form-select form-select-sm"><option value="count" {% if settings.cleanup_mode == 'count' %}selected{% endif %}>Garder X</option><option value="days" {% if settings.cleanup_mode == 'days' %}selected{% endif %}>Max X jours</option></select><input type="number" name="cleanup_value" class="form-control form-control-sm" value="{{settings.cleanup_value}}"></div>
                        <button class="btn btn-sm btn-primary w-100 mt-2">Sauver Paramètres Backup</button>
                    </form>
                    <button class="btn btn-sm btn-outline-warning w-100 mb-2" onclick="copyPwd()">Copier Pass Admin OWUI</button>
                    <button class="btn btn-sm btn-outline-secondary w-100 mb-2" data-bs-toggle="collapse" data-bs-target="#ssh">Changer Pass Système</button>
                    <div class="collapse mt-2" id="ssh"><form action="/action/security/passwd" method="post" class="bg-dark p-2 rounded"><input type="password" name="current_password" class="form-control form-control-sm mb-1" placeholder="Actuel"><input type="password" name="new_password" class="form-control form-control-sm mb-1" placeholder="Nouveau"><button class="btn btn-sm btn-warning w-100">Valider</button></form></div>
                    <form action="/action/auth_reset" method="post" onsubmit="return confirm('Purger Google ?')"><button class="btn btn-sm btn-link text-danger w-100">Réinitialiser Tokens Google</button></form>
                </div></div>
                <div class="card border-secondary"><div class="card-header d-flex justify-content-between align-items-center"><span>Containers</span><button class="btn btn-sm btn-link text-secondary p-0" onclick="refreshContainers()"><i class="bi bi-arrow-repeat"></i></button></div><ul class="list-group list-group-flush" id="container-list"></ul></div>
            </div>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        const initialTime = new Date('{{ server_time_iso }}');
        function initClock() {
            let now = initialTime;
            setInterval(() => {
                now.setSeconds(now.getSeconds() + 1);
                document.getElementById('clock').innerText = now.toLocaleTimeString();
            }, 1000);
        }
        function showLoader(m='Action en cours...'){document.getElementById('loader-msg').innerText=m;document.getElementById('loader').style.display='flex'}
        async function refreshUsers(){const r=await fetch('/api/user_stats');const d=await r.json();document.getElementById('user-list').innerHTML=d.map(u=>`<tr><td class="ps-3">${u.name}</td><td>${u.email}</td><td class="text-center"><span class="badge bg-primary">${u.chat_count}</span></td></tr>`).join('')}
        async function refreshBackups(){const r=await fetch('/api/backups');const d=await r.json();document.getElementById('backup-rows').innerHTML=d.map(b=>`<tr><td class="ps-3 text-truncate" style="max-width:200px;">${b.name}</td><td>${b.date}</td><td><span class="badge bg-secondary">${b.size}</span></td><td class="text-end pe-3"><div class="btn-group"><a href="/download/${b.name}" class="btn btn-sm text-primary"><i class="bi bi-download"></i></a><form action="/action/restore" method="post" onsubmit="return confirm('RESTAURER ?')" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-sm text-warning">↺</button></form><form action="/action/delete_backup" method="post" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-sm text-danger">×</button></form></div></td></tr>`).join('')}
        async function refreshContainers(){const r=await fetch('/api/containers');const d=await r.json();document.getElementById('container-list').innerHTML=d.map(c=>`<li class="list-group-item bg-transparent small d-flex justify-content-between align-items-center"><span>${c.name}</span><div class="d-flex align-items-center gap-2"><span class="badge ${c.status.startsWith('Up')?'bg-success':'bg-danger'}">${c.status}</span><form action="/action/restart" method="post"><input type="hidden" name="container" value="${c.id}"><button class="btn btn-sm btn-link text-secondary p-0"><i class="bi bi-power"></i></button></form></div></li>`).join('')}
        async function copyPwd(){
            try {
                const r = await fetch('/api/admin/password');
                const d = await r.json();
                if (d.password === "N/A") { alert('Erreur : Mot de passe introuvable sur le serveur.'); return; }

                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(d.password);
                    alert('Copié dans le presse-papier !');
                } else {
                    let textArea = document.createElement("textarea");
                    textArea.value = d.password;
                    textArea.style.position = "fixed"; textArea.style.left = "-999999px"; textArea.style.top = "-999999px";
                    document.body.appendChild(textArea); textArea.focus(); textArea.select();
                    try { document.execCommand('copy'); alert('Copié ! (Méthode fallback)'); }
                    catch (err) { alert('Erreur lors de la copie manuelle'); }
                    document.body.removeChild(textArea);
                }
            } catch (e) { alert('Erreur réseau lors de la récupération du mot de passe.'); }
        }
        setInterval(async()=>{const r=await fetch('/api/stats');
const d=await r.json();document.getElementById('cpu').innerText=d.cpu_percent;document.getElementById('cpu-bar').style.width=d.cpu_percent+'%';document.getElementById('cpu-details').innerText=`${d.cpu_count} cœurs | Load: ${d.cpu_load.join(', ')}`;document.getElementById('ram').innerText=d.ram_percent;document.getElementById('ram-bar').style.width=d.ram_percent+'%';document.getElementById('ram-text').innerText=d.ram_used+' / '+d.ram_total},3000);
        initClock();refreshUsers();refreshContainers();refreshBackups();
        var tList=[].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]')).map(function(el){return new bootstrap.Tooltip(el)})
    </script>
</body></html>"""

# ==============================================================================
# SECTION 8 : POINT D'ENTRÉE
# ==============================================================================
if __name__ == '__main__':
    if HAS_PSUTIL: psutil.cpu_percent(interval=None)
    if HAS_SCHEDULER:
        backup_scheduler = BackgroundScheduler(); backup_scheduler.start(); update_backup_schedule()
    if HAS_MAINT_SCHEDULER:
        setup_lifecycle_scheduler()
        def maint_loop():
            while True:
                try: schedule.run_pending()
                except: pass
                time.sleep(60)
        threading.Thread(target=maint_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=3001, debug=False, threaded=True)
