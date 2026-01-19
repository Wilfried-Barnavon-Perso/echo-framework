# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER SERVER
VERSION : 3.2 (Security & Config Update)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-01-20

--- DESCRIPTION ARCHITECTURALE ---
Ce micro-service Flask agit comme le "Concierge" de l'infrastructure ECHO.
Il s'exécute dans un conteneur Docker dédié (echo-admin-manager) sur le port 3001.

--- CHANGELOG 3.2 ---
- Ajout de la fonctionnalité "Changer Mot de Passe" (SSH Interactif).
- Introduction de DEFAULT_BACKUP_CONFIG pour une configuration propre.
- Uniformisation des rétentions en "jours" pour simplifier la lecture.

--- RESPONSABILITÉS ---
1. MONITORING : Exposition des métriques (CPU/RAM/Disque).
2. ORCHESTRATION : Gestion des conteneurs Docker.
3. SAUVEGARDE : Backup des données utilisateur.
4. MAINTENANCE : Nettoyage automatique des fichiers temporaires/logs.
5. SECURITE : Gestion des tokens et changement de mot de passe système.
================================================================================
"""

from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for, flash, session # pyright: ignore[reportMissingImports]
import os
import subprocess
import datetime
import glob
import secrets
import json
import time
import threading
import shutil
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
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['JSON_AS_ASCII'] = False

@app.after_request
def set_charset(response):
    if response.headers.get('Content-Type', '').startswith('text/html'):
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response

# ==============================================================================
# SECTION 3 : CONSTANTES & CHEMINS
# ==============================================================================
TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'echo-webui-core')
BACKUP_DIR = "/backups"
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")
OWUI_DATA_ROOT = "/app/backend/data"

# Dossiers gérés par la maintenance
DIRS = {
    "signatures": os.path.join(OWUI_DATA_ROOT, "signatures"),
    "stats": os.path.join(OWUI_DATA_ROOT, "stats"),
    "tokens": os.path.join(OWUI_DATA_ROOT, "tokens"),
    "uploads": os.path.join(OWUI_DATA_ROOT, "uploads"),
    "debug_logs": os.path.join(OWUI_DATA_ROOT, "debug_logs")
}

MAINT_CONFIG_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")
DATA_DIR_FOR_BACKUP = OWUI_DATA_ROOT 

# --- CONFIGURATIONS PAR DEFAUT (Constantes) ---

# Configuration des Backups (v3.2)
DEFAULT_BACKUP_CONFIG = {
    "auto_backup": True,
    "auto_cleanup": True,
    "cleanup_mode": "count",  # 'count' ou 'days'
    "cleanup_value": 5,       # Garder 5 fichiers ou 5 jours
    "backup_time": "03:00",
    "interval_days": 1
}

# Configuration de la Maintenance (v3.2 - Tout en jours)
DEFAULT_MAINT_CONFIG = {
    "cleanup_hour": "03:00",
    "last_run": "Never",
    "file_count_trigger": 100000, 
    "retention": {
        "signatures_days": 1095,  # 3 ans
        "stats_days": 30,         # 1 mois
        "uploads_days": 1095,     # 3 ans
        "debug_days": 14,         # 14 jours
        "tokens_days": 30         # 1 mois (Auth inactive)
    }
}

# ==============================================================================
# SECTION 4 : INITIALISATION SERVICES
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

# ==============================================================================
# SECTION 5 : LOGIQUE MÉTIER - MAINTENANCE UNIFIÉE
# ==============================================================================

def load_maint_config():
    """Charge la configuration de maintenance."""
    config = DEFAULT_MAINT_CONFIG.copy()
    if os.path.exists(MAINT_CONFIG_FILE):
        try:
            with open(MAINT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                config.update(loaded)
                # Ensure nested dict exists and merge defaults
                if "retention" in loaded:
                    for k, v in DEFAULT_MAINT_CONFIG["retention"].items():
                        if k not in config["retention"]: config["retention"][k] = v
                else:
                    config["retention"] = DEFAULT_MAINT_CONFIG["retention"].copy()
        except: pass
    return config

def save_maint_config(config):
    try:
        with open(MAINT_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    except Exception as e: print(f"Erreur sauvegarde config maintenance: {e}")

def get_dir_stats(path):
    if not os.path.exists(path): return {"count": 0, "size": 0, "size_fmt": "0 B"}
    try:
        files = [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        size = sum(os.path.getsize(f) for f in files)
        return {"count": len(files), "size": size, "size_fmt": human_size(size)}
    except: return {"count": 0, "size": 0, "size_fmt": "Err"}

def cleanup_directory(dir_key, retention_days):
    """Nettoyage basé sur l'âge des fichiers (jours)."""
    if retention_days == -1: return 0
    
    path = DIRS.get(dir_key)
    if not path or not os.path.exists(path): return 0
    
    retention_sec = retention_days * 24 * 3600
    cutoff = time.time() - retention_sec
    deleted = 0
    
    try:
        files = [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        for fpath in files:
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    deleted += 1
            except: pass
    except Exception as e:
        print(f"Error cleaning {dir_key}: {e}")
        
    if deleted > 0: print(f"🧹 [Maintenance] {dir_key}: {deleted} fichiers supprimés (> {retention_days} jours).")
    return deleted

def run_global_maintenance():
    print(f"🔧 [Maintenance] Démarrage Global...")
    config = load_maint_config()
    ret = config["retention"]
    total_deleted = 0
    
    # Signatures : Check trigger volumétrique
    sig_stats = get_dir_stats(DIRS["signatures"])
    if sig_stats["count"] > config.get("file_count_trigger", 100000):
        # Utilisation de signatures_days (v3.2) ou fallback sur semaines (legacy) converties
        days = ret.get("signatures_days", ret.get("signatures_weeks", 156) * 7)
        total_deleted += cleanup_directory("signatures", days)
    
    # Autres dossiers
    total_deleted += cleanup_directory("stats", ret["stats_days"])
    total_deleted += cleanup_directory("uploads", ret["uploads_days"])
    total_deleted += cleanup_directory("debug_logs", ret["debug_days"])
    total_deleted += cleanup_directory("tokens", ret["tokens_days"])
    
    config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_maint_config(config)
    print(f"✅ [Maintenance] Terminée. Total: {total_deleted}")

def maint_scheduler_loop():
    while True:
        schedule.run_pending()
        time.sleep(60)

def setup_maint_scheduler():
    if not HAS_MAINT_SCHEDULER: return
    config = load_maint_config()
    target_time = config.get("cleanup_hour", "03:00")
    schedule.clear()
    schedule.every().day.at(target_time).do(run_global_maintenance)
    print(f"⏰ [Maintenance] Tâche planifiée pour {target_time}.")

if HAS_MAINT_SCHEDULER:
    setup_maint_scheduler()
    t_maint = threading.Thread(target=maint_scheduler_loop, daemon=True)
    t_maint.start()

# ==============================================================================
# SECTION 6 : BACKUPS & RESTAURATION
# ==============================================================================
def human_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def load_settings():
    """Charge les paramètres de backup en utilisant DEFAULT_BACKUP_CONFIG."""
    config = DEFAULT_BACKUP_CONFIG.copy()
    if os.path.exists(SETTINGS_FILE):
        try: 
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            config.update(loaded)
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
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"owui_backup_{timestamp}.tar.gz"
    filepath = os.path.join(BACKUP_DIR, filename)
    try:
        container = client.containers.get(TARGET_CONTAINER)
        container.stop()
        subprocess.run(['tar', '-czf', filepath, '-C', DATA_DIR_FOR_BACKUP, '.'], check=True)
        container.start()
        
        settings = load_settings()
        if settings.get("auto_cleanup", False):
            mode = settings.get("cleanup_mode", "count")
            value = int(settings.get("cleanup_value", 5))
            files = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
            if mode == 'count' and len(files) > value:
                for f in files[value:]: os.remove(f)
            elif mode == 'days':
                cutoff = time.time() - (value * 86400)
                for f in files:
                    if os.path.getmtime(f) < cutoff: os.remove(f)
    except Exception as e:
        try: client.containers.get(TARGET_CONTAINER).start()
        except: pass
        print(f"Backup Error: {e}")

def update_backup_schedule():
    if not HAS_SCHEDULER: return
    backup_scheduler.remove_all_jobs()
    settings = load_settings()
    if settings.get("auto_backup"):
        time_str = settings.get("backup_time", "03:00")
        try:
            interval = int(settings.get("interval_days", 1))
            hour, minute = map(int, time_str.split(':'))
            now = datetime.datetime.now()
            start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if start_date <= now: start_date += datetime.timedelta(days=1)
            backup_scheduler.add_job(perform_backup_task, 'interval', days=interval, start_date=start_date, id='auto_back')
        except Exception as e: print(f"Schedule Error: {e}")

try: update_backup_schedule()
except: pass

# ==============================================================================
# SECTION 7 : SECURITE SYSTEME (SSH / Passwd)
# ==============================================================================

def change_system_password(username, current_pwd, new_pwd):
    """
    Change le mot de passe de l'utilisateur hôte via SSH interactif.
    Utilise 'passwd' et gère les prompts standards Linux.
    """
    if not DOCKER_AVAILABLE: return False, "Module SSH manquant"
    
    try:
        # Connexion SSH
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=username, password=current_pwd, timeout=10)
        
        # Ouverture d'un shell interactif (PTY)
        # Nécessaire car 'passwd' refuse de lire depuis stdin non-TTY par sécurité
        channel = ssh.invoke_shell()
        time.sleep(1) # Attente initialisation shell
        
        # Envoi de la commande
        channel.send('passwd\n')
        time.sleep(1)
        
        # Le prompt dépend de la config (root ou user standard)
        # Cas standard: "Current password:" -> "New password:" -> "Retype new password:"
        # On envoie les séquences avec des pauses
        
        channel.send(f'{current_pwd}\n')
        time.sleep(0.5)
        
        channel.send(f'{new_pwd}\n')
        time.sleep(0.5)
        
        channel.send(f'{new_pwd}\n')
        time.sleep(1)
        
        # Lecture de la sortie pour vérifier le succès
        output = channel.recv(4096).decode('utf-8', errors='ignore')
        ssh.close()
        
        if "updated successfully" in output or "mis à jour avec succès" in output:
            return True, "Mot de passe modifié avec succès."
        elif "BAD PASSWORD" in output:
            return False, "Le nouveau mot de passe est trop faible (Dictionnaire/Court)."
        elif "Authentication token manipulation error" in output:
            return False, "Erreur système (Permissions/Shadow)."
        else:
            # Fallback optimiste si on ne parse pas le message exact mais pas d'erreur flagrante
            # Mais souvent si ça échoue 'passwd' le dit.
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
        storage_stats[key] = get_dir_stats(path)

    return render_template_string(HTML_DASHBOARD, 
                                server_time=datetime.datetime.now().strftime('%H:%M:%S'),
                                settings=load_settings(),
                                maint_config=load_maint_config(),
                                storage_stats=storage_stats,
                                current_user=session.get('username', 'Inconnu'),
                                has_scheduler=HAS_SCHEDULER,
                                has_maint_scheduler=HAS_MAINT_SCHEDULER)

@app.route('/', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=username, password=password, timeout=5)
        ssh.close()
        session['logged_in'] = True
        session['username'] = username # Stockage pour le changement de mdp
        return redirect(url_for('index'))
    except Exception as e:
        flash(f'Échec authentification: {str(e)}', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- API ---

@app.route('/api/stats')
def stats():
    if not HAS_PSUTIL: return jsonify({"error": "psutil manquant"})
    try:
        disk = psutil.disk_usage('/backups')
        return jsonify({
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "disk_used": human_size(disk.used),
            "disk_total": human_size(disk.total),
            "disk_percent": disk.percent
        })
    except Exception as e: return jsonify({"error": str(e)})

@app.route('/api/backups')
def backups():
    if not session.get('logged_in'): return jsonify([])
    return jsonify(get_backup_list())

@app.route('/api/containers')
def containers():
    if not session.get('logged_in'): return jsonify([])
    if not DOCKER_AVAILABLE or not client: return jsonify({"error": "Docker non connecté"})
    try:
        cl = []
        for c in client.containers.list(all=True):
            cl.append({"id": c.short_id, "name": c.name, "status": c.status.capitalize()})
        return jsonify(cl)
    except Exception as e: return jsonify({"error": str(e)})

# --- ACTIONS ---

@app.route('/settings/maintenance', methods=['POST'])
def update_maintenance_settings():
    if not session.get('logged_in'): return redirect(url_for('index'))
    config = load_maint_config()
    try:
        config["cleanup_hour"] = request.form.get("cleanup_hour", "03:00")
        config["file_count_trigger"] = int(request.form.get("file_count_trigger", 100000))
        
        # Rétentions (Jours)
        config["retention"]["signatures_days"] = int(request.form.get("ret_sigs", 1095))
        config["retention"]["stats_days"] = int(request.form.get("ret_stats", 30))
        config["retention"]["uploads_days"] = int(request.form.get("ret_uploads", 1095))
        config["retention"]["debug_days"] = int(request.form.get("ret_debug", 14))
        config["retention"]["tokens_days"] = int(request.form.get("ret_tokens", 30))
        
        save_maint_config(config)
        if HAS_MAINT_SCHEDULER: setup_maint_scheduler()
        flash('Config maintenance mise à jour.', 'success')
    except Exception as e: flash(f'Erreur mise à jour: {str(e)}', 'danger')
    return redirect(url_for('index'))

@app.route('/action/maintenance/run', methods=['POST'])
def force_maintenance():
    if not session.get('logged_in'): return redirect(url_for('index'))
    threading.Thread(target=run_global_maintenance).start()
    flash('Maintenance globale lancée en arrière-plan.', 'info')
    return redirect(url_for('index'))

@app.route('/action/auth/reset', methods=['POST'])
def global_auth_reset():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        if os.path.exists(DIRS["tokens"]):
            shutil.rmtree(DIRS["tokens"])
            os.makedirs(DIRS["tokens"], exist_ok=True)
            flash('✅ Tous les tokens ont été purgés.', 'warning')
        else:
            flash('Dossier tokens introuvable.', 'info')
    except Exception as e:
        flash(f'❌ Erreur Reset Auth : {e}', 'danger')
    return redirect(url_for('index'))

@app.route('/action/security/passwd', methods=['POST'])
def passwd_change():
    """Route pour le changement de mot de passe."""
    if not session.get('logged_in'): return redirect(url_for('index'))
    
    current_pwd = request.form.get('current_password')
    new_pwd = request.form.get('new_password')
    username = session.get('username')
    
    if not username:
        flash('Erreur session: Utilisateur inconnu. Reconnectez-vous.', 'danger')
        return redirect(url_for('index'))
        
    success, msg = change_system_password(username, current_pwd, new_pwd)
    
    if success:
        flash(f'✅ {msg}', 'success')
    else:
        flash(f'❌ Erreur: {msg}', 'danger')
        
    return redirect(url_for('index'))

@app.route('/action/<action_type>', methods=['POST'])
def actions(action_type):
    if not session.get('logged_in'): return redirect(url_for('index'))
    if action_type == 'backup':
        perform_backup_task()
        flash('Sauvegarde effectuée.', 'success')
    elif action_type == 'delete':
        fname = request.form.get('filename')
        if fname:
            p = os.path.join(BACKUP_DIR, secure_filename(fname))
            if os.path.exists(p): os.remove(p)
            flash('Fichier supprimé.', 'warning')
    elif action_type == 'restore':
        fname = request.form.get('filename')
        if fname and DOCKER_AVAILABLE and client:
            p = os.path.join(BACKUP_DIR, secure_filename(fname))
            if os.path.exists(p):
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

@app.route('/settings', methods=['POST'])
def settings():
    if not session.get('logged_in'): return redirect(url_for('index'))
    new_conf = {
        "auto_backup": 'auto_backup' in request.form,
        "auto_cleanup": 'auto_cleanup' in request.form,
        "interval_days": int(request.form.get('interval_days', 1)),
        "backup_time": request.form.get('backup_time', "03:00"),
        "cleanup_mode": request.form.get('cleanup_mode', "count"),
        "cleanup_value": int(request.form.get('cleanup_value', 5))
    }
    save_settings(new_conf)
    flash('Paramètres Backups enregistrés.', 'success')
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload():
    if not session.get('logged_in'): return redirect(url_for('index'))
    if 'file' not in request.files: return redirect(url_for('index'))
    f = request.files['file']
    if f.filename == '': return redirect(url_for('index'))
    if f:
        path = os.path.join(BACKUP_DIR, secure_filename(f.filename))
        f.save(path)
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
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css">
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', system-ui, sans-serif; padding-bottom: 60px; }
        .navbar { background-color: #161b22; border-bottom: 1px solid #30363d; padding: 15px 0; }
        .card { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; margin-bottom: 24px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        .card-header { background-color: #21262d; border-bottom: 1px solid #30363d; font-weight: 600; padding: 12px 20px; }
        .stat-card { padding: 20px; }
        .stat-val { font-size: 2rem; font-weight: 700; margin: 10px 0; }
        .progress { background-color: #30363d; height: 8px; border-radius: 4px; }
        .table { --bs-table-bg: transparent; --bs-table-border-color: #30363d; color: #c9d1d9; vertical-align: middle; }
        #loader { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(13, 17, 23, 0.9); z-index: 9999; display: none; flex-direction: column; justify-content: center; align-items: center; }
        .status-badge { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
        .error-text { color: #ff7b72; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div id="loader"><div class="spinner-border text-primary" style="width: 3rem; height: 3rem;" role="status"></div><h4 class="mt-3 text-light" id="loader-msg">Traitement...</h4></div>

    <nav class="navbar mb-4">
        <div class="container">
            <span class="navbar-brand mb-0 h1"><i class="bi bi-cpu-fill text-primary"></i> ECHO Admin <span class="ms-3 badge bg-secondary fw-normal" style="font-size:0.8rem">{{ server_time }}</span></span>
            <div class="d-flex align-items-center gap-3">
                <span class="text-muted small"><i class="bi bi-person-circle"></i> {{ current_user }}</span>
                <a href="/logout" class="btn btn-outline-danger btn-sm"><i class="bi bi-box-arrow-right"></i></a>
            </div>
        </div>
    </nav>

    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}{% for c, m in messages %}
                <div class="alert alert-{{ c }} alert-dismissible fade show shadow-sm border-0">{{ m }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>
            {% endfor %}{% endif %}
        {% endwith %}

        <!-- MONITORING -->
        <div class="row g-4 mb-4">
            <div class="col-md-4"><div class="card stat-card h-100"><div class="d-flex justify-content-between text-secondary"><span>CPU</span><i class="bi bi-cpu"></i></div><div class="stat-val text-primary" id="cpu-val">--%</div><div class="progress"><div class="progress-bar bg-primary" id="cpu-bar" style="width:0%"></div></div></div></div>
            <div class="col-md-4"><div class="card stat-card h-100"><div class="d-flex justify-content-between text-secondary"><span>RAM</span><i class="bi bi-memory"></i></div><div class="stat-val text-info" id="ram-val">--%</div><div class="progress"><div class="progress-bar bg-info" id="ram-bar" style="width:0%"></div></div></div></div>
            <div class="col-md-4"><div class="card stat-card h-100"><div class="d-flex justify-content-between text-secondary"><span>Disk</span><i class="bi bi-hdd"></i></div><div class="stat-val text-success" id="disk-val">--%</div><div class="progress mb-2"><div class="progress-bar bg-success" id="disk-bar" style="width:0%"></div></div><small class="text-muted" id="disk-text">...</small></div></div>
        </div>

        <div class="row g-4">
            <!-- GAUCHE : Backups -->
            <div class="col-lg-7">
                <div class="card h-100">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <span><i class="bi bi-archive"></i> Sauvegardes</span>
                        <div class="d-flex gap-2">
                             <button class="btn btn-sm btn-link text-white p-0 me-2" onclick="refreshBackups()"><i class="bi bi-arrow-repeat fs-5"></i></button>
                             <button class="btn btn-sm btn-outline-secondary" onclick="document.getElementById('uploadInput').click()"><i class="bi bi-upload"></i></button>
                             <form action="/action/backup" method="post" style="display:inline" onsubmit="showLoader('Création du backup...')">
                                <button class="btn btn-sm btn-success"><i class="bi bi-plus-lg"></i> Backup</button>
                             </form>
                        </div>
                    </div>
                    <form id="uploadForm" action="/upload" method="post" enctype="multipart/form-data" class="d-none">
                        <input type="file" id="uploadInput" name="file" accept=".gz" onchange="showLoader('Upload...'); this.form.submit()">
                    </form>
                    <div class="table-responsive">
                        <table class="table table-hover mb-0">
                            <thead class="table-dark"><tr><th>Fichier</th><th>Date</th><th>Taille</th><th class="text-end">Actions</th></tr></thead>
                            <tbody id="backup-list"><tr><td colspan="4" class="text-center py-4 text-muted">Chargement...</td></tr></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- DROITE : Maintenance & Config -->
            <div class="col-lg-5">
                
                <!-- SECURITE COMPTE (NOUVEAU v3.2) -->
                <div class="card mb-4 border-warning">
                    <div class="card-header text-warning"><i class="bi bi-key"></i> Sécurité Compte</div>
                    <div class="card-body">
                        <button class="btn btn-sm btn-outline-warning w-100" type="button" data-bs-toggle="collapse" data-bs-target="#passwdForm">Changer Mot de Passe</button>
                        <div class="collapse mt-2" id="passwdForm">
                            <form action="/action/security/passwd" method="post" class="bg-dark p-3 rounded border border-secondary">
                                <div class="mb-2">
                                    <input type="password" name="current_password" class="form-control form-control-sm bg-black text-white border-secondary" placeholder="Mot de passe ACTUEL" required>
                                </div>
                                <div class="mb-2">
                                    <input type="password" name="new_password" class="form-control form-control-sm bg-black text-white border-secondary" placeholder="NOUVEAU Mot de passe" required>
                                </div>
                                <button type="submit" class="btn btn-warning btn-sm w-100" onclick="showLoader('Changement en cours...')">Valider</button>
                            </form>
                        </div>
                    </div>
                </div>

                <!-- MAINTENANCE -->
                <div class="card mb-4 border-info">
                    <div class="card-header text-info"><i class="bi bi-shield-check"></i> Stockage & Maintenance</div>
                    <div class="card-body">
                        {% if not has_maint_scheduler %}<div class="alert alert-warning small">Automatisation désactivée (lib manquante).</div>{% endif %}
                        
                        <div class="table-responsive mb-3">
                            <table class="table table-sm table-borderless small text-light mb-0">
                                <thead><tr class="text-muted border-bottom border-secondary"><th>Type</th><th>Fichiers</th><th>Taille</th><th>Rétention</th></tr></thead>
                                <tbody>
                                    <tr><td>Signatures</td><td class="text-end">{{ storage_stats.signatures.count }}</td><td class="text-end">{{ storage_stats.signatures.size_fmt }}</td><td class="text-info">{{ maint_config.retention.signatures_days }} j.</td></tr>
                                    <tr><td>Uploads</td><td class="text-end">{{ storage_stats.uploads.count }}</td><td class="text-end">{{ storage_stats.uploads.size_fmt }}</td><td class="text-info">{{ maint_config.retention.uploads_days }} j.</td></tr>
                                    <tr><td>Stats Usage</td><td class="text-end">{{ storage_stats.stats.count }}</td><td class="text-end">{{ storage_stats.stats.size_fmt }}</td><td class="text-info">{{ maint_config.retention.stats_days }} j.</td></tr>
                                    <tr><td>Debug Logs</td><td class="text-end">{{ storage_stats.debug_logs.count }}</td><td class="text-end">{{ storage_stats.debug_logs.size_fmt }}</td><td class="text-info">{{ maint_config.retention.debug_days }} j.</td></tr>
                                    <tr><td>Auth Tokens</td><td class="text-end">{{ storage_stats.tokens.count }}</td><td class="text-end">{{ storage_stats.tokens.size_fmt }}</td><td class="text-info">{{ maint_config.retention.tokens_days }} j.</td></tr>
                                </tbody>
                            </table>
                        </div>

                        <button class="btn btn-sm btn-outline-secondary w-100 mb-2" type="button" data-bs-toggle="collapse" data-bs-target="#maintConfig">⚙️ Configurer Rétention</button>
                        
                        <div class="collapse" id="maintConfig">
                            <div class="card card-body bg-dark border-secondary p-2 mb-2">
                                <form action="/settings/maintenance" method="post">
                                    <div class="row g-2 mb-2">
                                        <div class="col-6"><label class="form-label small text-muted mb-0">Signatures</label><input type="number" name="ret_sigs" class="form-control form-control-sm bg-black text-white border-secondary" value="{{ maint_config.retention.signatures_days }}"></div>
                                        <div class="col-6"><label class="form-label small text-muted mb-0">Uploads</label><input type="number" name="ret_uploads" class="form-control form-control-sm bg-black text-white border-secondary" value="{{ maint_config.retention.uploads_days }}"></div>
                                        <div class="col-6"><label class="form-label small text-muted mb-0">Logs</label><input type="number" name="ret_debug" class="form-control form-control-sm bg-black text-white border-secondary" value="{{ maint_config.retention.debug_days }}"></div>
                                        <div class="col-6"><label class="form-label small text-muted mb-0">Tokens</label><input type="number" name="ret_tokens" class="form-control form-control-sm bg-black text-white border-secondary" value="{{ maint_config.retention.tokens_days }}"></div>
                                    </div>
                                    <div class="mb-2"><label class="form-label small text-muted mb-0">Heure du Clean</label><input type="time" name="cleanup_hour" class="form-control form-control-sm bg-black text-white border-secondary" value="{{ maint_config.cleanup_hour }}"></div>
                                    <input type="hidden" name="file_count_trigger" value="{{ maint_config.file_count_trigger }}">
                                    <input type="hidden" name="ret_stats" value="{{ maint_config.retention.stats_days }}">
                                    <button type="submit" class="btn btn-sm btn-success w-100">Enregistrer</button>
                                </form>
                            </div>
                        </div>

                        <div class="d-flex gap-2">
                            <form action="/action/maintenance/run" method="post" class="flex-grow-1"><button type="submit" class="btn btn-sm btn-info w-100"><i class="bi bi-stars"></i> Nettoyer Tout</button></form>
                            <form action="/action/auth/reset" method="post" onsubmit="return confirm('⚠️ ATTENTION : Déconnexion de TOUS les utilisateurs. Confirmer ?')"><button type="submit" class="btn btn-sm btn-danger"><i class="bi bi-trash3"></i> Reset Auth</button></form>
                        </div>
                    </div>
                </div>

                <!-- DOCKER SERVICES -->
                <div class="card">
                    <div class="card-header d-flex justify-content-between"><span><i class="bi bi-box-seam"></i> Services</span><button onclick="refreshContainers()" class="btn btn-sm btn-link text-white p-0"><i class="bi bi-arrow-repeat"></i></button></div>
                    <ul class="list-group list-group-flush" id="container-list"></ul>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
            var tooltipList = tooltipTriggerList.map(function (el) { return new bootstrap.Tooltip(el) })
            function showLoader(msg) { document.getElementById('loader-msg').innerText = msg; document.getElementById('loader').style.display = 'flex'; }
            window.showLoader = showLoader; 
            window.confirmRestore = function() { if(confirm("RESTAURATION DESTRUCTIVE ! Confirmer ?")) { showLoader("Restauration..."); return true; } return false; };
            async function fetchData(url) { const res = await fetch(url); if (!res.ok) throw new Error(`HTTP Error ${res.status}`); return await res.json(); }
            async function updateStats() {
                try {
                    const d = await fetchData('/api/stats');
                    if(d.error) { document.getElementById('disk-text').innerHTML = `<span class="error-text">${d.error}</span>`; return; }
                    document.getElementById('cpu-val').innerText = d.cpu + '%'; document.getElementById('cpu-bar').style.width = d.cpu + '%';
                    document.getElementById('ram-val').innerText = d.ram + '%'; document.getElementById('ram-bar').style.width = d.ram + '%';
                    document.getElementById('disk-val').innerText = d.disk_percent + '%'; document.getElementById('disk-bar').style.width = d.disk_percent + '%';
                    document.getElementById('disk-text').innerText = `${d.disk_used} / ${d.disk_total}`;
                } catch(e) { console.error("Stats Error:", e); }
            }
            async function refreshBackups() {
                try {
                    const data = await fetchData('/api/backups');
                    const list = document.getElementById('backup-list');
                    list.innerHTML = '';
                    if(data.length === 0) { list.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">Vide</td></tr>'; return; }
                    data.forEach(b => {
                        list.innerHTML += `<tr><td><i class="bi bi-file-earmark-zip text-warning me-2"></i>${b.name}</td><td>${b.date}</td><td><span class="badge bg-secondary">${b.size}</span></td><td class="text-end"><a href="/download/${b.name}" class="btn btn-outline-primary btn-sm"><i class="bi bi-download"></i></a><form action="/action/restore" method="post" onsubmit="return confirmRestore()" style="display:inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-outline-warning btn-sm mx-1"><i class="bi bi-arrow-counterclockwise"></i></button></form><form action="/action/delete" method="post" onsubmit="return confirm('Supprimer ?')" style="display:inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-danger btn-sm"><i class="bi bi-trash3-fill"></i></button></form></td></tr>`;
                    });
                } catch(e) { console.error("Backup Error:", e); }
            }
            async function refreshContainers() {
                try {
                    const data = await fetchData('/api/containers');
                    const list = document.getElementById('container-list');
                    list.innerHTML = '';
                    if (data.error) { list.innerHTML = `<li class="list-group-item bg-transparent text-danger small">${data.error}</li>`; return; }
                    data.forEach(c => {
                        const isUp = c.status.startsWith('Up');
                        const color = isUp ? 'bg-success' : 'bg-danger';
                        list.innerHTML += `<li class="list-group-item bg-transparent border-secondary text-light d-flex justify-content-between align-items-center"><div><div class="fw-bold"><span class="status-badge ${color}"></span>${c.name}</div><div class="small text-muted" style="font-size:0.75rem">${c.status}</div></div><form action="/action/restart" method="post" onsubmit="showLoader('Redémarrage...')"><input type="hidden" name="container" value="${c.id}"><button class="btn btn-sm btn-outline-secondary py-0"><i class="bi bi-power"></i></button></form></li>`;
                    });
                } catch(e) { console.error("Container Error:", e); }
            }
            updateStats(); refreshBackups(); refreshContainers();
            setInterval(updateStats, 3000); setInterval(refreshContainers, 5000); 
            window.refreshBackups = refreshBackups; window.refreshContainers = refreshContainers;
        });
    </script>
</body>
</html>
"""

# ==============================================================================
# SECTION 10 : POINT D'ENTRÉE (MAIN)
# ==============================================================================

if __name__ == '__main__':
    # Lancement du serveur Flask
    app.run(host='0.0.0.0', port=3001, debug=False, threaded=True)