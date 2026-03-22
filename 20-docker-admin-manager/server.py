# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER SERVER
VERSION : 5.15 (Stable Recovery / ECHO-Native)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-03-22

--- DESCRIPTION ARCHITECTURALE ---
Ce micro-service Flask assure la maintenance, les backups et le monitoring
exclusif du framework ECHO. Architecture ECHO-Native sans legacy.

--- CHANGELOG 5.15 ---
- Correction NameError (HAS_SCHEDULER) : Rétablissement des flags de dépendances.
- Rétablissement complet des planificateurs de tâches (Backups & Maintenance).
- Stabilisation du point d'entrée pour éviter les boucles de redémarrage.
================================================================================
"""

from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for, flash, session # pyright: ignore[reportMissingImports]
from typing import Optional, List, Dict, Tuple
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
# SECTION 1 : GESTION DES DÉPENDANCES (FLAGS DE CONTRÔLE)
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

DEFAULT_BACKUP_CONFIG = {
    "auto_backup": True, "auto_cleanup": True, "cleanup_mode": "count",
    "cleanup_value": 5, "backup_time": "03:00", "interval_days": 1
}

DEFAULT_MAINT_CONFIG = {
    "cleanup_hour": "03:00", "last_run": "Never",
    "retention": { "uploads_days": 1095, "vault_days": 1095 }
}

# ==============================================================================
# SECTION 3 : LOGIQUE MÉTIER - MAINTENANCE & STOCKAGE
# ==============================================================================

def human_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def get_recursive_stats(path: str, extension: str = None):
    if not os.path.exists(path): return {"count": 0, "size": 0, "size_fmt": "0 B"}
    t_size, t_count = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            if extension and not f.endswith(extension): continue
            try:
                t_size += os.path.getsize(os.path.join(root, f))
                t_count += 1
            except: pass
    return {"count": t_count, "size": t_size, "size_fmt": human_size(t_size)}

def load_maint_config():
    c = DEFAULT_MAINT_CONFIG.copy()
    if os.path.exists(MAINT_CONFIG_FILE := os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")):
        try:
            with open(MAINT_CONFIG_FILE, 'r') as f: loaded = json.load(f); c.update(loaded)
        except: pass
    return c

def save_maint_config(c):
    try:
        with open(os.path.join(OWUI_DATA_ROOT, "maintenance_config.json"), 'w') as f: json.dump(c, f, indent=4)
    except: pass

def run_deep_maintenance():
    """Maintenance ECHO Native (v5.15)."""
    print(f"🔧 [ECHO-MAINT] Démarrage...")
    report = []
    config = load_maint_config()
    
    # 1. Orphelins
    orphans = 0
    if os.path.exists(ECHO_USERS_ROOT):
        try:
            conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
            valid_ids = {row[0] for row in conn.execute("SELECT id FROM user").fetchall()}
            conn.close()
            for folder in os.listdir(ECHO_USERS_ROOT):
                if folder not in valid_ids and len(folder) > 30:
                    shutil.rmtree(os.path.join(ECHO_USERS_ROOT, folder)); orphans += 1
        except: pass
    report.append(f"Orphelins: {orphans}")

    # 2. Nettoyage Fichiers
    cutoff_v = time.time() - (config["retention"].get("vault_days", 1095) * 86400)
    removed = 0
    for root, _, files in os.walk(ECHO_USERS_ROOT):
        if "files" in root:
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if os.path.getmtime(fp) < cutoff_v: os.remove(fp); removed += 1
                except: pass
    report.append(f"Nettoyage: {removed}")

    # 3. Vacuum
    vax = 0
    for root, _, files in os.walk(ECHO_USERS_ROOT):
        for f in files:
            if f.endswith('.db'):
                try:
                    with sqlite3.connect(os.path.join(root, f), timeout=10.0) as conn: conn.execute("VACUUM;"); vax += 1
                except: pass
    report.append(f"Optimisés: {vax}")

    config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_maint_config(config)
    return " | ".join(report)

# ==============================================================================
# SECTION 4 : BACKUPS & SCHEDULERS
# ==============================================================================

def load_settings():
    c = DEFAULT_BACKUP_CONFIG.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f: c.update(json.load(f))
        except: pass
    return c

def perform_backup_task():
    if not DOCKER_AVAILABLE: return
    fname = f"echo_native_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    fpath = os.path.join(BACKUP_DIR, fname)
    try:
        c_docker = docker.from_env()
        target = c_docker.containers.get(TARGET_CONTAINER)
        target.stop()
        subprocess.run(['tar', '-czf', fpath, '-C', OWUI_DATA_ROOT, '.'], check=True)
        target.start()
        # Cleanup vieux backups
        sets = load_settings()
        if sets.get("auto_cleanup"):
            list_b = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
            if len(list_b) > int(sets.get("cleanup_value", 5)):
                for b in list_b[int(sets.get("cleanup_value", 5)):]: os.remove(b)
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

def setup_maint_scheduler():
    if not HAS_MAINT_SCHEDULER: return
    config = load_maint_config()
    schedule.clear()
    schedule.every().day.at(config.get("cleanup_hour", "03:00")).do(run_deep_maintenance)

# ==============================================================================
# SECTION 5 : ROUTES API
# ==============================================================================

@app.route('/')
def index():
    if not session.get('logged_in'): return render_template_string(HTML_LOGIN)
    v_stats = get_recursive_stats(ECHO_USERS_ROOT)
    u_stats = get_recursive_stats(UPLOADS_DIR)
    ver = "v?.?"
    if os.path.exists('/app/ECHO_VERSION'):
        with open('/app/ECHO_VERSION', 'r') as f: ver = f.read().strip()
    
    return render_template_string(HTML_DASHBOARD, 
                                vault=v_stats, uploads=u_stats,
                                version=ver, user=session.get('username'),
                                maint=load_maint_config(),
                                backups=sorted([{'name': os.path.basename(f), 'size': human_size(os.path.getsize(f)), 'date': datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d/%m/%Y %H:%M')} for f in glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz'))], key=lambda x: x['date'], reverse=True))

@app.route('/', methods=['POST'])
def login():
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=request.form.get('username'), password=request.form.get('password'), timeout=5)
        ssh.close(); session['logged_in'] = True; session['username'] = request.form.get('username')
    except: flash('Accès refusé.', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/api/user_stats')
def user_stats():
    if not session.get('logged_in'): return jsonify([])
    users_data = []
    try:
        conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        users = conn.execute("SELECT id, name, email, role FROM user ORDER BY name COLLATE NOCASE ASC").fetchall()
        for u in users:
            vault_chats_dir = os.path.join(ECHO_USERS_ROOT, str(u['id']), "chats")
            echo_count = len([f for f in os.listdir(vault_chats_dir) if f.endswith('.db')]) if os.path.exists(vault_chats_dir) else 0
            users_data.append({"name": u['name'], "email": u['email'], "role": u['role'], "echo_chats": echo_count})
        conn.close()
    except: pass
    return jsonify(users_data)

@app.route('/api/stats')
def sys_stats():
    if not HAS_PSUTIL: return jsonify({})
    mem = psutil.virtual_memory(); disk = psutil.disk_usage(BACKUP_DIR)
    return jsonify({"cpu": psutil.cpu_percent(), "ram": mem.percent, "disk": disk.percent})

@app.route('/action/<act>', methods=['POST'])
def run_action(act):
    if not session.get('logged_in'): return redirect(url_for('index'))
    if act == 'maint': threading.Thread(target=run_deep_maintenance).start(); flash('Maintenance lancée.', 'info')
    elif act == 'backup': threading.Thread(target=perform_backup_task).start(); flash('Sauvegarde lancée.', 'info')
    elif act == 'delete_backup':
        f = request.form.get('filename')
        if f and os.path.exists(p := os.path.join(BACKUP_DIR, secure_filename(f))): os.remove(p); flash('Backup supprimé.', 'warning')
    return redirect(url_for('index'))

@app.route('/api/admin/password')
def get_admin_pwd():
    if not session.get('logged_in'): return jsonify({}), 403
    if os.path.exists(OWUI_ADMIN_SECRET_PATH):
        with open(OWUI_ADMIN_SECRET_PATH, 'r') as f: return jsonify({"password": f.read().strip()})
    return jsonify({"password": "N/A"})

# ==============================================================================
# SECTION 6 : TEMPLATES
# ==============================================================================

HTML_LOGIN = """
<!doctype html>
<html lang="fr" data-bs-theme="dark">
<head><meta charset="utf-8"><title>ECHO Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-black d-flex align-items-center justify-content-center vh-100">
    <div class="card p-4 border-secondary shadow" style="width:350px;">
        <h2 class="text-center text-primary mb-4">ECHO Admin</h2>
        <form method="POST">
            <input type="text" name="username" class="form-control mb-3" placeholder="Utilisateur" required>
            <input type="password" name="password" class="form-control mb-3" placeholder="Mot de passe" required>
            <button class="btn btn-primary w-100">Connexion</button>
        </form>
    </div>
</body></html>"""

HTML_DASHBOARD = """
<!doctype html>
<html lang="fr" data-bs-theme="dark">
<head><meta charset="utf-8"><title>ECHO Console</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css"></head>
<body style="background-color:#0d1117;">
    <nav class="navbar border-bottom border-secondary px-4 py-3 mb-4"><span class="navbar-brand text-primary fw-bold">ECHO CONSOLE {{ version }}</span><div class="d-flex align-items-center gap-3"><span class="text-muted small">{{ user }}</span><a href="/logout" class="btn btn-sm btn-outline-danger">Quitter</a></div></nav>
    <div class="container">
        {% with messages = get_flashed_messages() %}{% for m in messages %}<div class="alert alert-info border-0 shadow">{{ m }}</div>{% endfor %}{% endwith %}
        <div class="row g-4">
            <div class="col-md-3"><div class="card p-3 border-secondary">CPU: <span id="cpu">--</span>%<div class="progress mt-2" style="height:4px;"><div id="cpu-bar" class="progress-bar bg-primary"></div></div></div></div>
            <div class="col-md-3"><div class="card p-3 border-secondary">RAM: <span id="ram">--</span>%<div class="progress mt-2" style="height:4px;"><div id="ram-bar" class="progress-bar bg-info"></div></div></div></div>
            <div class="col-md-3"><div class="card p-3 border-secondary text-center">Sessions ECHO<br><b class="text-primary fs-4">{{ vault.count }}</b></div></div>
            <div class="col-md-3"><div class="card p-3 border-secondary text-center">Stockage Vault<br><b class="text-success fs-4">{{ vault.size_fmt }}</b></div></div>
            <div class="col-lg-8">
                <div class="card border-secondary">
                    <div class="card-header border-secondary d-flex justify-content-between"><span><i class="bi bi-people"></i> État des Sessions ECHO</span><button class="btn btn-sm btn-link text-secondary p-0" onclick="updateUsers()"><i class="bi bi-arrow-repeat"></i></button></div>
                    <div class="card-body p-0"><table class="table table-hover mb-0"><thead><tr><th class="ps-3">Nom</th><th>Email</th><th class="text-center">Sessions ECHO</th></tr></thead><tbody id="user-rows"></tbody></table></div>
                </div>
            </div>
            <div class="col-lg-4">
                <div class="card border-info mb-4">
                    <div class="card-header bg-info text-dark fw-bold">Maintenance ECHO Native</div>
                    <div class="card-body small">
                        <p>Transit (Uploads) : <b>{{ uploads.size_fmt }}</b></p>
                        <p class="text-muted">Dernière exécution : {{ maint.last_run }}</p>
                        <form action="/action/maint" method="post"><button class="btn btn-info btn-sm w-100 mb-2">Lancer Maintenance Profonde</button></form>
                    </div>
                </div>
                <div class="card border-warning mb-4"><div class="card-header text-warning">Secrets</div><div class="card-body"><button class="btn btn-sm btn-outline-warning w-100" onclick="copyPwd()">Copier Pass Admin OWUI</button></div></div>
                <div class="card border-secondary"><div class="card-header d-flex justify-content-between"><span>Sauvegardes</span><form action="/action/backup" method="post"><button class="btn btn-sm btn-success p-0 px-2">+</button></form></div><div class="card-body p-0 small"><ul class="list-group list-group-flush">{% for b in backups %}<li class="list-group-item bg-transparent d-flex justify-content-between"><span>{{ b.name }}</span><form action="/action/delete_backup" method="post" class="d-inline"><input type="hidden" name="filename" value="{{ b.name }}"><button class="btn btn-sm text-danger p-0">×</button></form></li>{% endfor %}</ul></div></div>
            </div>
        </div>
    </div>
    <script>
        async function updateUsers() {
            const res = await fetch('/api/user_stats'); const data = await res.json();
            document.getElementById('user-rows').innerHTML = data.map(u => `<tr><td class="ps-3">${u.name}</td><td>${u.email}</td><td class="text-center"><span class="badge ${u.echo_chats > 0 ? 'bg-primary':'bg-secondary'}">${u.echo_chats}</span></td></tr>`).join('');
        }
        async function copyPwd() { const res = await fetch('/api/admin/password'); const data = await res.json(); navigator.clipboard.writeText(data.password); alert('Mot de passe copié !'); }
        setInterval(async () => {
            const res = await fetch('/api/stats'); const d = await res.json();
            document.getElementById('cpu').innerText = d.cpu; document.getElementById('cpu-bar').style.width = d.cpu+'%';
            document.getElementById('ram').innerText = d.ram; document.getElementById('ram-bar').style.width = d.ram+'%';
        }, 3000);
        updateUsers();
    </script>
</body></html>"""

# ==============================================================================
# SECTION 7 : POINT D'ENTRÉE (FIXED)
# ==============================================================================
if __name__ == '__main__':
    if HAS_SCHEDULER:
        backup_scheduler = BackgroundScheduler()
        backup_scheduler.start()
        update_backup_schedule()
    
    if HAS_MAINT_SCHEDULER:
        setup_maint_scheduler()
        def schedule_loop():
            while True:
                schedule.run_pending()
                time.sleep(60)
        threading.Thread(target=schedule_loop, daemon=True).start()

    app.run(host='0.0.0.0', port=3001, debug=False, threaded=True)
