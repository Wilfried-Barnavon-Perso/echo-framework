"""
ADMIN MANAGER SERVER v2.6 (Maintenance & Monitoring)

RÔLE :
Ce serveur Flask léger tourne dans un conteneur dédié pour gérer les opérations système
que l'application principale (Open WebUI) ne devrait pas faire elle-même pour des raisons
de sécurité et de performance.

FONCTIONNALITÉS :
1. Dashboard de Monitoring : CPU, RAM, Disque.
2. Gestion Docker : Redémarrage des conteneurs via socket Docker.
3. Backups : Création d'archives .tar.gz du volume de données.
4. MAINTENANCE SIGNATURES (Nouveau) : Nettoyage périodique des fichiers de contexte Gemini.

ARCHITECTURE DE STOCKAGE :
- Les données Open WebUI sont montées dans /app/backend/data
- Les signatures Gemini sont dans /app/backend/data/signatures
"""

# --- FIX ENCODING UTF-8 ---
# On force l'encodage par défaut pour éviter les caractères corrompus dans les logs/templates
import sys
if sys.version_info.major == 3:
    import importlib
    importlib.reload(sys)

from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for, flash, session # pyright: ignore[reportMissingImports]
from functools import wraps
import os, subprocess, datetime, glob, secrets, json, time, threading
from werkzeug.utils import secure_filename # pyright: ignore[reportMissingImports]

# --- VÉRIFICATION DES DÉPENDANCES ---
# Le script doit être robuste même si certaines libs manquent (mode dégradé)

try:
    import docker # Interface avec le socket Docker (/var/run/docker.sock)
    import paramiko # Pour les connexions SSH (Legacy)
    DOCKER_AVAILABLE = True
except ImportError:
    print("CRITIQUE: Docker/Paramiko non disponible (Dev Local ?)")
    docker = None
    paramiko = None
    DOCKER_AVAILABLE = False

try:
    import psutil # Pour lire l'usage CPU/RAM/Disque
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler # Scheduler robuste pour les backups
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False
    
try:
    import schedule # Scheduler simple pour la maintenance des signatures
    HAS_SIG_SCHEDULER = True
except ImportError:
    print("WARNING: 'schedule' lib missing. Signature maintenance scheduler disabled.")
    HAS_SIG_SCHEDULER = False

app = Flask(__name__)
app.secret_key = secrets.token_hex(32) # Sécurisation des sessions Flask
# --- FIX FLASK CONFIG ---
app.config['JSON_AS_ASCII'] = False # Permet les accents dans les réponses JSON

# ==============================================================================
# CONFIGURATION DES CHEMINS ET VOLUMES
# ==============================================================================
TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'open-webui')
BACKUP_DIR = "/backups" # Volume monté pour stocker les archives
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")

# IMPORTANT : Harmonisation des chemins avec Open WebUI
# Le volume de données est monté dans /app/backend/data
OWUI_DATA_ROOT = "/app/backend/data"

# Chemins spécifiques pour la maintenance des signatures Gemini
# Ces fichiers sont générés par le Pipe Engine
SIG_DATA_DIR = os.path.join(OWUI_DATA_ROOT, "signatures") 
SIG_CONFIG_FILE = os.path.join(OWUI_DATA_ROOT, "signature_maintenance_config.json")

# Pour les backups globaux, on archive tout le dossier de données
DATA_DIR_FOR_BACKUP = OWUI_DATA_ROOT 

# Configuration par défaut de la maintenance
DEFAULT_SIG_CONFIG = {
    "retention_weeks": 156, # 3 ans (52 semaines * 3)
    "file_count_trigger": 100000, # Ne nettoie QUE si on dépasse 100k fichiers
    "cleanup_hour": "03:00", # Heure d'exécution (nuit)
    "last_run": "Never"
}

# Initialisation du client Docker
client = None
if DOCKER_AVAILABLE:
    try:
        client = docker.from_env()
    except Exception as e:
        print(f"Erreur init Docker: {e}")
        DOCKER_AVAILABLE = False

# Démarrage du Scheduler pour les Backups (APScheduler)
if HAS_SCHEDULER:
    try:
        backup_scheduler = BackgroundScheduler()
        backup_scheduler.start()
    except Exception as e:
        print(f"Erreur Backup Scheduler: {e}")

# ==============================================================================
# LOGIQUE DE MAINTENANCE DES SIGNATURES (GEMINI SIDE-CAR)
# ==============================================================================

def load_sig_config():
    """Charge la configuration de maintenance depuis le JSON."""
    if os.path.exists(SIG_CONFIG_FILE):
        try:
            with open(SIG_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return DEFAULT_SIG_CONFIG.copy()

def save_sig_config(config):
    """Sauvegarde la configuration de maintenance."""
    try:
        os.makedirs(os.path.dirname(SIG_CONFIG_FILE), exist_ok=True)
        with open(SIG_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Erreur sauvegarde config signatures: {e}")

def run_signature_cleanup():
    """
    ALGORITHME DE NETTOYAGE DES SIGNATURES
    
    Pourquoi ?
    Le Pipe Gemini génère un fichier texte par conversation pour stocker l'état cognitif.
    Avec le temps, cela peut créer beaucoup de petits fichiers.
    
    Comment ?
    1. Vérification Volumétrique : On compte les fichiers. Si < file_count_trigger, on arrête.
       Cela évite de scanner les dates inutilement si le volume est faible.
    2. Vérification Temporelle : Si le seuil est dépassé, on scanne les dates de modification.
       On supprime les fichiers non touchés depuis 'retention_weeks'.
       
    Sécurité :
    L'opération est faite fichier par fichier avec gestion d'exception pour ne jamais crasher le serveur.
    """
    print(f"🔧 [Maintenance] Démarrage du nettoyage dans {SIG_DATA_DIR}...")
    config = load_sig_config()
    
    if not os.path.exists(SIG_DATA_DIR):
        print(f"⚠️ [Maintenance] Sous-dossier {SIG_DATA_DIR} introuvable. Création au cas où...")
        os.makedirs(SIG_DATA_DIR, exist_ok=True)
        return

    try:
        # 1. Analyse Volumétrique (Rapide car basée sur l'inode du dossier)
        files = [os.path.join(SIG_DATA_DIR, f) for f in os.listdir(SIG_DATA_DIR) 
                 if os.path.isfile(os.path.join(SIG_DATA_DIR, f))]
        count = len(files)
        
        # Condition de déclenchement
        threshold = config.get("file_count_trigger", 100000)
        if count < threshold:
            print(f"ℹ️ [Maintenance] Seuil non atteint ({count} < {threshold}). Pas de nettoyage.")
            return

        print(f"⚠️ [Maintenance] Seuil dépassé ({count}). Analyse de l'ancienneté...")
        
        # 2. Nettoyage par âge
        weeks = config.get("retention_weeks", 156)
        retention_seconds = weeks * 7 * 24 * 3600
        now = time.time()
        deleted = 0
        
        for fpath in files:
            try:
                mtime = os.path.getmtime(fpath)
                age = now - mtime
                
                # Suppression si trop vieux
                if age > retention_seconds:
                    os.remove(fpath)
                    deleted += 1
            except Exception as e:
                print(f"⚠️ [Maintenance] Erreur suppression {fpath}: {e}")

        print(f"✅ [Maintenance] Terminée. {deleted} signatures expirées supprimées.")
        
        # Mise à jour du timestamp de dernière exécution
        config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_sig_config(config)

    except Exception as e:
        print(f"💥 [Maintenance] Crash : {str(e)}")

# Thread de planification (Schedule Lib)
# Un thread séparé vérifie chaque minute s'il est l'heure de lancer la maintenance.
def sig_scheduler_loop():
    while True:
        schedule.run_pending()
        time.sleep(60)

def setup_sig_scheduler():
    """Configure l'heure de lancement de la tâche."""
    if not HAS_SIG_SCHEDULER: return
    config = load_sig_config()
    target_time = config.get("cleanup_hour", "03:00")
    
    schedule.clear()
    schedule.every().day.at(target_time).do(run_signature_cleanup)
    print(f"⏰ [Maintenance] Tâche planifiée pour {target_time} quotidiennement.")

if HAS_SIG_SCHEDULER:
    setup_sig_scheduler()
    t_sig = threading.Thread(target=sig_scheduler_loop, daemon=True)
    t_sig.start()

# --- UTILS & LOGIC BACKUPS (Legacy) ---

def human_size(size):
    """Convertit une taille en octets en format lisible."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def load_settings():
    defaults = {
        "auto_backup": True, "auto_cleanup": True, 
        "cleanup_mode": "count", "cleanup_value": 5, 
        "backup_time": "03:00", "interval_days": 1
    }
    if os.path.exists(SETTINGS_FILE):
        try: 
            s = json.load(open(SETTINGS_FILE))
            for k, v in defaults.items():
                if k not in s or s[k] is None: s[k] = v
            return s
        except: pass
    return defaults

def save_settings(new_settings):
    clean = load_settings()
    clean.update(new_settings)
    with open(SETTINGS_FILE, 'w') as f: json.dump(clean, f, indent=2)
    update_backup_schedule()

def get_backup_list():
    try:
        files = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
        return [{'name': os.path.basename(f), 'size': human_size(os.path.getsize(f)), 'date': datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d/%m/%Y %H:%M')} for f in files]
    except: return []

def apply_retention_policy():
    settings = load_settings()
    if not settings.get("auto_cleanup", False): return
    mode = settings.get("cleanup_mode", "count")
    try: value = int(settings.get("cleanup_value", 5))
    except: value = 5

    files = sorted(glob.glob(os.path.join(BACKUP_DIR, '*.tar.gz')), key=os.path.getmtime, reverse=True)
    
    if mode == 'count' and len(files) > value:
        for f in files[value:]:
            try: os.remove(f)
            except: pass
    elif mode == 'days':
        cutoff = time.time() - (value * 86400)
        for f in files:
            if os.path.getmtime(f) < cutoff:
                try: os.remove(f)
                except: pass

def perform_backup_task():
    if not DOCKER_AVAILABLE or not client: return
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"owui_backup_{timestamp}.tar.gz"
    filepath = os.path.join(BACKUP_DIR, filename)
    try:
        container = client.containers.get(TARGET_CONTAINER)
        container.stop()
        # Backup du dossier complet (y compris le sous-dossier signatures)
        subprocess.run(['tar', '-czf', filepath, '-C', DATA_DIR_FOR_BACKUP, '.'], check=True)
        container.start()
        apply_retention_policy()
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
        if not time_str or ":" not in time_str: time_str = "03:00"
        try:
            interval = int(settings.get("interval_days", 1))
            hour, minute = map(int, time_str.split(':'))
            now = datetime.datetime.now()
            start_date = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if start_date <= now:
                start_date += datetime.timedelta(days=1)
            backup_scheduler.add_job(perform_backup_task, 'interval', days=interval, start_date=start_date, id='auto_back')
        except Exception as e:
            print(f"Schedule Error: {e}")

try: update_backup_schedule()
except: pass

# --- ROUTES FLASK ---

@app.route('/')
def index():
    if not session.get('logged_in'): return render_template_string(HTML_LOGIN)
    
    # Calcul des stats pour l'affichage (Nb fichiers signatures, taille totale)
    sig_stats = {"count": 0, "size": "0 B"}
    if os.path.exists(SIG_DATA_DIR):
        try:
            files = [os.path.join(SIG_DATA_DIR, f) for f in os.listdir(SIG_DATA_DIR) if os.path.isfile(os.path.join(SIG_DATA_DIR, f))]
            sig_stats["count"] = len(files)
            sig_stats["size"] = human_size(sum(os.path.getsize(f) for f in files))
        except: pass

    return render_template_string(HTML_DASHBOARD, 
                                server_time=datetime.datetime.now().strftime('%H:%M:%S'),
                                settings=load_settings(),
                                sig_config=load_sig_config(),
                                sig_stats=sig_stats,
                                has_scheduler=HAS_SCHEDULER,
                                has_sig_scheduler=HAS_SIG_SCHEDULER)

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
        return redirect(url_for('index'))
    except Exception as e:
        flash(f'Échec authentification: {str(e)}', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

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
    except Exception as e:
        return jsonify({"error": str(e)})

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
    except Exception as e:
        return jsonify({"error": str(e)})

# --- ROUTES API MAINTENANCE SIGNATURES ---

@app.route('/api/signatures/stats', methods=['GET'])
def get_signature_stats():
    """API pour monitoring externe : renvoie les stats des signatures."""
    if not session.get('logged_in'): return jsonify({"error": "Auth required"}), 401
    config = load_sig_config()
    stats = {
        "config": config,
        "total_files": 0,
        "total_size_mb": 0.0,
        "status": "OK"
    }
    if os.path.exists(SIG_DATA_DIR):
        try:
            files = [os.path.join(SIG_DATA_DIR, f) for f in os.listdir(SIG_DATA_DIR) if os.path.isfile(os.path.join(SIG_DATA_DIR, f))]
            stats["total_files"] = len(files)
            stats["total_size_mb"] = round(sum(os.path.getsize(f) for f in files) / (1024 * 1024), 2)
        except Exception as e:
            stats["status"] = f"Error scanning: {str(e)}"
    else:
        stats["status"] = "Directory not found"
    return jsonify(stats)

@app.route('/settings/signatures', methods=['POST'])
def update_signature_settings():
    """Mise à jour de la configuration de nettoyage (Seuil, Rétention, Heure)."""
    if not session.get('logged_in'): return redirect(url_for('index'))
    config = load_sig_config()
    
    try:
        config["retention_weeks"] = int(request.form.get("retention_weeks", 156))
        config["file_count_trigger"] = int(request.form.get("file_count_trigger", 100000))
        config["cleanup_hour"] = request.form.get("cleanup_hour", "03:00")
        
        save_sig_config(config)
        
        if HAS_SIG_SCHEDULER:
            setup_sig_scheduler() # Replanifie avec la nouvelle heure immédiatement
            
        flash('Config maintenance signatures mise à jour.', 'success')
    except Exception as e:
        flash(f'Erreur mise à jour: {str(e)}', 'danger')
        
    return redirect(url_for('index'))

@app.route('/action/signatures/cleanup', methods=['POST'])
def force_signature_cleanup():
    """Déclenchement manuel de la maintenance (Bouton Dashboard)."""
    if not session.get('logged_in'): return redirect(url_for('index'))
    # Lancement en thread pour ne pas bloquer l'interface web pendant le scan
    threading.Thread(target=run_signature_cleanup).start()
    flash('Nettoyage des signatures lancé en arrière-plan.', 'info')
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
                    # Nettoyage avant restore
                    subprocess.run(f"rm -rf {DATA_DIR_FOR_BACKUP}/*", shell=True)
                    subprocess.run(['tar', '-xzf', p, '-C', DATA_DIR_FOR_BACKUP], check=True)
                    container.start()
                    flash('Restauration terminée.', 'success')
                except Exception as e:
                    flash(f'Erreur Restauration: {e}', 'danger')

    elif action_type == 'restart':
        cid = request.form.get('container')
        if cid and DOCKER_AVAILABLE and client:
            try:
                client.containers.get(cid).restart()
                flash('Conteneur redémarré.', 'info')
            except Exception as e:
                flash(f'Erreur: {e}', 'danger')

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

# --- HTML TEMPLATES ---

HTML_LOGIN = """
<!doctype html>
<html lang="fr" data-bs-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Connexion ECHO Admin</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { display: flex; align-items: center; justify-content: center; height: 100vh; background-color: #121212; }
        .card { width: 100%; max-width: 400px; border: 1px solid #333; }
    </style>
</head>
<body>
    <div class="card shadow-lg">
        <div class="card-body p-5">
            <h3 class="text-center mb-4">ECHO Admin</h3>
            <div class="text-center mb-3 text-muted small">Authentification Système (SSH)</div>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            <form method="POST">
                <div class="mb-3">
                    <label class="form-label">Utilisateur</label>
                    <input type="text" name="username" class="form-control" required autofocus>
                </div>
                <div class="mb-3">
                    <label class="form-label">Mot de Passe</label>
                    <input type="password" name="password" class="form-control" required>
                </div>
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
        .sig-stat-val { font-size: 1.2rem; font-weight: bold; }
    </style>
</head>
<body>
    <div id="loader">
        <div class="spinner-border text-primary" style="width: 3rem; height: 3rem;" role="status"></div>
        <h4 class="mt-3 text-light" id="loader-msg">Traitement...</h4>
    </div>

    <nav class="navbar mb-4">
        <div class="container">
            <span class="navbar-brand mb-0 h1" data-bs-toggle="tooltip" title="Heure du serveur"><i class="bi bi-cpu-fill text-primary"></i> ECHO Admin <span class="ms-3 badge bg-secondary border border-secondary text-light fw-normal" style="font-size:0.8rem">{{ server_time }}</span></span>
            <a href="/logout" class="btn btn-outline-danger btn-sm" data-bs-toggle="tooltip" title="Se déconnecter"><i class="bi bi-box-arrow-right"></i></a>
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
            <!-- GAUCHE : Gestion des Backups -->
            <div class="col-lg-8">
                <div class="card h-100">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <span><i class="bi bi-archive"></i> Sauvegardes</span>
                        <div class="d-flex gap-2">
                             <button class="btn btn-sm btn-link text-white p-0 me-2" onclick="refreshBackups()" data-bs-toggle="tooltip" title="Actualiser la liste"><i class="bi bi-arrow-repeat fs-5"></i></button>
                             <button class="btn btn-sm btn-outline-secondary" onclick="document.getElementById('uploadInput').click()" data-bs-toggle="tooltip" title="Importer un fichier .tar.gz depuis votre ordinateur"><i class="bi bi-upload"></i></button>
                             <form action="/action/backup" method="post" style="display:inline" onsubmit="showLoader('Création du backup...')">
                                <button class="btn btn-sm btn-success" data-bs-toggle="tooltip" title="Lancer une sauvegarde immédiate de l'état actuel"><i class="bi bi-plus-lg"></i> Backup</button>
                             </form>
                        </div>
                    </div>
                    <form id="uploadForm" action="/upload" method="post" enctype="multipart/form-data" class="d-none">
                        <input type="file" id="uploadInput" name="file" accept=".gz" onchange="showLoader('Upload...'); this.form.submit()">
                    </form>
                    
                    <div class="table-responsive">
                        <table class="table table-hover mb-0">
                            <thead class="table-dark"><tr><th>Fichier</th><th>Date</th><th>Taille</th><th class="text-end">Actions</th></tr></thead>
                            <tbody id="backup-list">
                                <tr><td colspan="4" class="text-center py-4 text-muted">Chargement...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- DROITE : Paramètres & Maintenance -->
            <div class="col-lg-4">
                
                <!-- AUTO-PILOT BACKUPS -->
                <div class="card mb-4">
                    <div class="card-header"><i class="bi bi-robot"></i> Auto-Pilot (Backups)</div>
                    <div class="card-body">
                        {% if not has_scheduler %}
                        <div class="alert alert-warning small">Module 'APScheduler' manquant.<br>Automatisation désactivée.</div>
                        {% endif %}
                        <form action="/settings" method="post">
                            <div class="form-check form-switch mb-3 p-2 border rounded bg-dark border-secondary">
                                <input class="form-check-input ms-0 me-2" type="checkbox" name="auto_backup" id="autoBackup" {% if settings.auto_backup %}checked{% endif %}>
                                <label class="form-check-label" for="autoBackup">Backup Auto</label>
                            </div>
                            
                            <div class="row g-2 mb-3">
                                <div class="col-6">
                                    <label class="form-label text-muted small mb-1">Jours</label>
                                    <input type="number" name="interval_days" class="form-control bg-dark text-white border-secondary" value="{{ settings.interval_days }}" min="1">
                                </div>
                                <div class="col-6">
                                    <label class="form-label text-muted small mb-1">Heure</label>
                                    <input type="time" name="backup_time" class="form-control bg-dark text-white border-secondary" value="{{ settings.backup_time }}">
                                </div>
                            </div>

                            <hr class="border-secondary my-3">
                            
                            <div class="form-check form-switch mb-2">
                                <input class="form-check-input" type="checkbox" name="auto_cleanup" id="autoCleanup" {% if settings.auto_cleanup %}checked{% endif %}>
                                <label class="form-check-label" for="autoCleanup">Nettoyage Vieux Backups</label>
                            </div>

                            <div class="input-group mb-2">
                                <select name="cleanup_mode" class="form-select form-select-sm bg-dark text-white border-secondary">
                                    <option value="count" {% if settings.cleanup_mode == 'count' %}selected{% endif %}>Garder X derniers</option>
                                    <option value="days" {% if settings.cleanup_mode == 'days' %}selected{% endif %}>Supprimer > X jours</option>
                                </select>
                            </div>
                            <div class="input-group mb-3">
                                <input type="number" name="cleanup_value" class="form-control form-control-sm bg-dark text-white border-secondary" value="{{ settings.cleanup_value }}" min="1">
                            </div>

                            <button type="submit" class="btn btn-primary w-100">Enregistrer Config Backups</button>
                        </form>
                    </div>
                </div>

                <!-- MAINTENANCE SIGNATURES -->
                <div class="card mb-4 border-info">
                    <div class="card-header text-info"><i class="bi bi-shield-check"></i> Maintenance Signatures</div>
                    <div class="card-body">
                         {% if not has_sig_scheduler %}
                        <div class="alert alert-warning small">Module 'schedule' manquant.<br>Auto-nettoyage désactivé.</div>
                        {% endif %}
                        
                        <div class="d-flex justify-content-between mb-3 text-muted small">
                            <span>Fichiers: <strong class="text-light">{{ sig_stats.count }}</strong></span>
                            <span>Taille: <strong class="text-light">{{ sig_stats.size }}</strong></span>
                        </div>
                        <div class="text-muted small mb-3">Dernier run: <span class="text-light">{{ sig_config.last_run }}</span></div>

                        <form action="/settings/signatures" method="post">
                            <div class="mb-2">
                                <label class="form-label text-muted small mb-0">Déclencheur (Nb Fichiers)</label>
                                <input type="number" name="file_count_trigger" class="form-control form-control-sm bg-dark text-white border-secondary" value="{{ sig_config.file_count_trigger }}">
                            </div>
                            <div class="row g-2 mb-3">
                                <div class="col-7">
                                    <label class="form-label text-muted small mb-0">Rétention (Semaines)</label>
                                    <input type="number" name="retention_weeks" class="form-control form-control-sm bg-dark text-white border-secondary" value="{{ sig_config.retention_weeks }}">
                                </div>
                                <div class="col-5">
                                    <label class="form-label text-muted small mb-0">Heure</label>
                                    <input type="time" name="cleanup_hour" class="form-control form-control-sm bg-dark text-white border-secondary" value="{{ sig_config.cleanup_hour }}">
                                </div>
                            </div>
                            <div class="d-flex gap-2">
                                <button type="submit" class="btn btn-sm btn-outline-info flex-grow-1">Sauver Config</button>
                            </div>
                        </form>
                        <form action="/action/signatures/cleanup" method="post" class="mt-2">
                             <button type="submit" class="btn btn-sm btn-info w-100"><i class="bi bi-stars"></i> Nettoyer Maintenant</button>
                        </form>
                    </div>
                </div>

                <!-- DOCKER SERVICES -->
                <div class="card">
                    <div class="card-header d-flex justify-content-between"><span><i class="bi bi-box-seam"></i> Services</span><button onclick="refreshContainers()" class="btn btn-sm btn-link text-white p-0" data-bs-toggle="tooltip" title="Rafraîchir l'état"><i class="bi bi-arrow-repeat"></i></button></div>
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
            window.confirmRestore = function() {
                if(confirm("RESTAURATION DESTRUCTIVE ! Confirmer ?")) { showLoader("Restauration..."); return true; } return false; 
            };

            // Fonction AJAX générique pour le debug
            async function fetchData(url) {
                console.log(`Fetching ${url}...`);
                const res = await fetch(url);
                if (!res.ok) throw new Error(`HTTP Error ${res.status}`);
                return await res.json();
            }

            async function updateStats() {
                try {
                    const d = await fetchData('/api/stats');
                    if(d.error) {
                        console.error("Stats API Error:", d.error);
                        document.getElementById('disk-text').innerHTML = `<span class="error-text">${d.error}</span>`;
                        return;
                    }
                    document.getElementById('cpu-val').innerText = d.cpu + '%'; document.getElementById('cpu-bar').style.width = d.cpu + '%';
                    document.getElementById('ram-val').innerText = d.ram + '%'; document.getElementById('ram-bar').style.width = d.ram + '%';
                    document.getElementById('disk-val').innerText = d.disk_percent + '%'; document.getElementById('disk-bar').style.width = d.disk_percent + '%';
                    document.getElementById('disk-text').innerText = `${d.disk_used} / ${d.disk_total}`;
                } catch(e) { console.error("Stats Network Error:", e); }
            }

            async function refreshBackups() {
                try {
                    const data = await fetchData('/api/backups');
                    const list = document.getElementById('backup-list');
                    list.innerHTML = '';
                    if(data.length === 0) { list.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">Vide</td></tr>'; return; }
                    
                    document.querySelectorAll('.tooltip').forEach(e => e.remove());
                    
                    data.forEach(b => {
                        list.innerHTML += `<tr>
                            <td><i class="bi bi-file-earmark-zip text-warning me-2"></i>${b.name}</td>
                            <td>${b.date}</td>
                            <td><span class="badge bg-secondary">${b.size}</span></td>
                            <td class="text-end">
                                <a href="/download/${b.name}" class="btn btn-outline-primary btn-sm" data-bs-toggle="tooltip" title="Télécharger sur votre PC"><i class="bi bi-download"></i></a>
                                <form action="/action/restore" method="post" onsubmit="return confirmRestore()" style="display:inline">
                                    <input type="hidden" name="filename" value="${b.name}">
                                    <button class="btn btn-outline-warning btn-sm mx-1" data-bs-toggle="tooltip" title="RESTAURER : Écrase la configuration !"><i class="bi bi-arrow-counterclockwise"></i></button>
                                </form>
                                <form action="/action/delete" method="post" onsubmit="return confirm('Supprimer ?')" style="display:inline">
                                    <input type="hidden" name="filename" value="${b.name}">
                                    <button class="btn btn-danger btn-sm" data-bs-toggle="tooltip" title="Supprimer définitivement"><i class="bi bi-trash3-fill"></i></button>
                                </form>
                            </td>
                        </tr>`;
                    });
                    
                    var newTooltips = [].slice.call(list.querySelectorAll('[data-bs-toggle="tooltip"]'));
                    newTooltips.map(function (el) { return new bootstrap.Tooltip(el) });
                } catch(e) { console.error("Backup Network Error:", e); }
            }

            async function refreshContainers() {
                try {
                    const data = await fetchData('/api/containers');
                    const list = document.getElementById('container-list');
                    list.innerHTML = '';
                    
                    if (data.error) {
                        list.innerHTML = `<li class="list-group-item bg-transparent text-danger small">${data.error}</li>`;
                        return;
                    }

                    document.querySelectorAll('.tooltip').forEach(e => e.remove());

                    data.forEach(c => {
                        const isUp = c.status.startsWith('Up');
                        const color = isUp ? 'bg-success' : 'bg-danger';
                        list.innerHTML += `<li class="list-group-item bg-transparent border-secondary text-light d-flex justify-content-between align-items-center">
                            <div>
                                <div class="fw-bold"><span class="status-badge ${color}"></span>${c.name}</div>
                                <div class="small text-muted" style="font-size:0.75rem">${c.status}</div>
                            </div>
                            <form action="/action/restart" method="post" onsubmit="showLoader('Redémarrage...')">
                                <input type="hidden" name="container" value="${c.id}">
                                <button class="btn btn-sm btn-outline-secondary py-0" data-bs-toggle="tooltip" title="Redémarrer le conteneur"><i class="bi bi-power"></i></button>
                            </form>
                        </li>`;
                    });
                    
                    var newTooltips = [].slice.call(list.querySelectorAll('[data-bs-toggle="tooltip"]'));
                    newTooltips.map(function (el) { return new bootstrap.Tooltip(el) });
                } catch(e) { console.error("Container Network Error:", e); }
            }

            // Init & Loop
            updateStats(); refreshBackups(); refreshContainers();
            setInterval(updateStats, 3000); 
            setInterval(refreshContainers, 5000); 
            
            // Expose
            window.refreshBackups = refreshBackups;
            window.refreshContainers = refreshContainers;
        });
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    # Mode DEBUG désactivé pour stabilité Docker (évite le crash du reloader)
    app.run(host='0.0.0.0', port=3001, debug=False, threaded=True)