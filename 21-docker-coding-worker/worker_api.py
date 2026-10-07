"""
================================================================================
MODULE : ECHO CODE WORKER API
VERSION : 3.7 (Hotfix NameError & Flake8)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-10-07

CHANGELOG 3.7 :
- Hotfix NameError : Déplacement de la purge ECHO Monitor et création du fichier payload avant bwrap.
- Fix Flake8 : Suppression du préfixe f-string inutile.
CHANGELOG 3.6 :
- Baisse de la troncature Anti-OOM (via paramètre dynamique max_output_length ou 131Ko) pour protéger le contexte LLM.
CHANGELOG 3.5 :
- Implémentation du Passe-Plat multiplexé (Frontend vers Sandbox) avec double purge de sécurité du fichier .echo_ui_payload.json
CHANGELOG 3.4 :
- Fix NameError critique : remplacement de json.loads par orjson.loads pour le Monitor Payload.
CHANGELOG 3.3 :
- Implémentation du Multiplexage (Multi-fenêtrage JSONL) pour ECHO Monitor.
- Ajout d'une double purge de sécurité (Pre-Run / Post-Run) du fichier .echo_monitor.jsonl pour isoler les sandbox entre deux exécutions.
CHANGELOG 3.2 :
- Ajout du montage de /echo_libs_sandbox et /sandbox-agent-readme.txt pour l'utilisation d'ECHO Monitor.
CHANGELOG 3.1 :
- Alignement sémantique des montages Bwrap sur /sandbox, /main et /files.
CHANGELOG 3.0 :
- Refonte majeure : Transformation du Python Worker en Code Worker multi-langage (Python 3.14 + NodeJS 22).
- Le script n'est plus transmis à la volée, mais exécuté depuis un fichier physique préalablement enregistré dans la Sandbox du Codex.
- Intégration d'un Pre-execution Linting (py_compile, node -c) pour rejeter immédiatement les erreurs de syntaxe.
CHANGELOG 2.8 :
- Renommage de /inputs vers /ro_user_files (plus sémantique).
- Ajout du montage du dépôt Codex en lecture seule vers /ro_user_edits pour permettre au script d'exécuter le code généré.
CHANGELOG 2.7 :
- Remplacement du tmpfs en RAM par un bind-mount physique vers `.tmp` dans le workspace pour prévenir les attaques de type OOM-DoS (Saturation RAM).
================================================================================
"""

import logging
import logging.config
import multiprocessing
import orjson
import os
import queue
import subprocess
import time

from flask import Flask, jsonify, request  # pyright: ignore[reportMissingImports]

# Configuration des logs pour voir qui fait quoi dans la console Docker

if os.path.exists('/app/logging.json'):
    with open('/app/logging.json', 'rb') as f:
        logging.config.dictConfig(orjson.loads(f.read()))
else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RateLimitHealthCheckFilter(logging.Filter):
    def __init__(self, rate_limit_seconds=300):
        super().__init__()
        self.rate_limit_seconds = rate_limit_seconds
        self.last_logged = 0

    def filter(self, record):
        try:
            msg = record.getMessage()
            if "GET /health" in msg:
                now = time.time()
                if now - self.last_logged >= self.rate_limit_seconds:
                    self.last_logged = now
                    return True
                return False
        except Exception:
            pass
        return True

logging.getLogger("werkzeug").addFilter(RateLimitHealthCheckFilter())

app = Flask(__name__)

def run_isolated_process(file_path, dependencies, result_queue, sandbox_dir, files_dir, global_files_dir, deps_dir, timeout_sec, ui_payload, max_output_length):
    try:
        if not sandbox_dir:
            result_queue.put({'status': 'critical_error', 'error': 'Espace d\'exécution non défini.'})
            return
            
        os.makedirs(sandbox_dir, exist_ok=True)
        sandbox_tmp = os.path.join(sandbox_dir, ".tmp")
        os.makedirs(sandbox_tmp, exist_ok=True)
        
        # Le dossier de dépendances doit exister pour être monté
        if deps_dir:
            os.makedirs(deps_dir, exist_ok=True)
            # Permettre à tout le monde d'écrire car Bubblewrap (nobody) y installera les libs
            os.chmod(deps_dir, 0o777)
            
        # Résolution du fichier cible dans la sandbox
        # file_path vient de l'outil et est un chemin relatif au workspace
        # On s'assure de ne pas sortir de la sandbox (path traversal)
        abs_file_path = os.path.abspath(os.path.join(sandbox_dir, file_path))
        if not abs_file_path.startswith(os.path.abspath(sandbox_dir)):
            result_queue.put({'status': 'error', 'error': 'Accès refusé : Le fichier cible est hors du workspace.'})
            return
            
        if not os.path.isfile(abs_file_path):
            result_queue.put({'status': 'error', 'error': f"Fichier cible introuvable : {file_path}"})
            return

        # =========================================================================
        # PRE-EXECUTION LINTING
        # =========================================================================
        result = {'status': 'success', 'output': ''}
        is_python = file_path.endswith('.py')
        is_node = file_path.endswith('.js')
        
        if not (is_python or is_node):
            result_queue.put({'status': 'error', 'error': 'Extension non supportée. Seuls .py et .js sont acceptés.'})
            return
            
        lint_cmd = []
        if is_python:
            lint_cmd = ["python", "-m", "py_compile", abs_file_path]
        else:
            lint_cmd = ["node", "-c", abs_file_path]
            
        try:
            lint_proc = subprocess.run(lint_cmd, capture_output=True, text=True, timeout=10)
            if lint_proc.returncode != 0:
                result['status'] = 'error'
                err_out = lint_proc.stderr if lint_proc.stderr else lint_proc.stdout
                result['error'] = f"Erreur de syntaxe (Pre-execution Linting) :\n{err_out}"
                result_queue.put(result)
                return
        except subprocess.TimeoutExpired:
            result['status'] = 'error'
            result['error'] = 'Timeout lors de la vérification syntaxique.'
            result_queue.put(result)
            return

        # =========================================================================
        # INSTALLATION DYNAMIQUE DES DEPENDANCES (DANS L'HÔTE)
        # =========================================================================
        # L'installation se fait en amont de Bwrap pour simplifier les accès réseau et droits,
        # ou elle peut se faire via subprocess classique vu qu'on a Node/Pip installés.
        # Ici on le fait via un subprocess standard avant de cloisonner avec bwrap.
        if dependencies and deps_dir:
            if is_python:
                python_deps_dir = os.path.join(deps_dir, "python")
                os.makedirs(python_deps_dir, exist_ok=True)
                os.chmod(python_deps_dir, 0o777)
                pip_cmd = ["python", "-m", "pip", "install", "--target", python_deps_dir] + dependencies
                subprocess.run(pip_cmd, capture_output=True, text=True) # Silencieux
            elif is_node:
                node_deps_dir = os.path.join(deps_dir, "node")
                os.makedirs(node_deps_dir, exist_ok=True)
                os.chmod(node_deps_dir, 0o777)
                # Installer dans node_deps_dir
                npm_cmd = ["npm", "install", "--prefix", node_deps_dir] + dependencies
                subprocess.run(npm_cmd, capture_output=True, text=True)

        # =========================================================================
        # 1. PURGE PRE-RUN ECHO Monitor & PAYLOAD
        # =========================================================================
        import uuid
        monitor_file = None
        ui_payload_file = None
        ui_payload_filename = None
        if sandbox_dir:
            monitor_file = os.path.join(sandbox_dir, ".echo_monitor.jsonl")
            if os.path.exists(monitor_file):
                try:
                    os.remove(monitor_file)
                except Exception:
                    pass

            # Pre-Run UI Payload Injection sécurisée
            if ui_payload:
                ui_payload_filename = f".echo_ui_{uuid.uuid4().hex}.json"
                ui_payload_file = os.path.join(sandbox_dir, ui_payload_filename)
                try:
                    with open(ui_payload_file, 'w', encoding='utf-8') as f:
                        f.write(orjson.dumps(ui_payload).decode('utf-8'))
                except Exception:
                    pass

        # =========================================================================
        # ISOLATION ABSOLUE BUBBLEWRAP
        # =========================================================================
        bwrap_cmd = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/bin", "/bin",
            "--dev", "/dev",
            "--ro-bind", "/proc", "/proc",
            "--ro-bind-try", "/lib64", "/lib64", # Requis pour certaines dépendances C (NumPy)
            "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf", # Requis pour la résolution DNS (Internet)
            "--ro-bind-try", "/etc/ssl/certs", "/etc/ssl/certs", # Requis pour les requêtes HTTPS (requests, axios)
            "--bind", sandbox_tmp, "/tmp", # Remplace le tmpfs en RAM pour éviter un crash OOM DoS
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--unshare-user", "--uid", "65534", "--gid", "65534", # Exécute en tant qu'utilisateur "nobody"
            "--bind", sandbox_dir, "/sandbox", # <- Dossier Sandbox (RW)
            "--ro-bind-try", "/app/echo_libs_sandbox", "/echo_libs_sandbox",
            "--ro-bind-try", "/app/sandbox-agent-readme.txt", "/sandbox-agent-readme.txt",
            "--chdir", "/sandbox"
        ]

        # Montage des uploads utilisateurs en LECTURE SEULE
        if files_dir:
            bwrap_cmd.extend(["--ro-bind-try", files_dir, "/files"])
            
        # [NEW] Montage du Vault Global (Requis pour la résolution des symlinks)
        if global_files_dir:
            bwrap_cmd.extend(["--ro-bind-try", global_files_dir, global_files_dir])
            
        # Montage du dépôt Codex (main) en LECTURE SEULE
        if sandbox_dir:
            codex_main = os.path.join(os.path.dirname(sandbox_dir), "main")
            bwrap_cmd.extend(["--ro-bind-try", codex_main, "/main"])
            
        # Montage du dossier des dépendances en LECTURE/ECRITURE
        if deps_dir:
            bwrap_cmd.extend(["--bind-try", deps_dir, "/.deps"])

        # Injecter le runtime approprié
        if ui_payload_filename:
            bwrap_cmd.extend(["--setenv", "ECHO_UI_PAYLOAD_FILE", ui_payload_filename])
            
        if is_python:
            python_paths = ["/echo_libs_sandbox"]
            if deps_dir:
                python_paths.append("/.deps/python")
            bwrap_cmd.extend(["--setenv", "PYTHONPATH", ":".join(python_paths)])
            bwrap_cmd.extend(["python", f"/sandbox/{file_path}"])
        else: # is_node
            node_paths = ["/echo_libs_sandbox", "/usr/lib/node_modules"]
            if deps_dir:
                node_paths.insert(0, "/.deps/node/node_modules")
            bwrap_cmd.extend(["--setenv", "NODE_PATH", ":".join(node_paths)])
            bwrap_cmd.extend(["node", f"/sandbox/{file_path}"])

        try:
            proc = subprocess.run(
                bwrap_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec
            )
            
            # Troncature Anti-OOM (Dynamique)
            result['output'] = proc.stdout[:max_output_length]
            if len(proc.stdout) > max_output_length:
                result['output'] += "\n[Avertissement ECHO : Sortie console tronquée à 131Ko. Privilégier echo_monitor.display() pour les payloads massifs]."
                
            if proc.stderr:
                result['error'] = proc.stderr[:max_output_length]
                result['status'] = 'error'
            elif proc.returncode != 0:
                result['error'] = result.get('error', '') + f"\nProcess exited with code {proc.returncode}"
                result['status'] = 'error'

        except subprocess.TimeoutExpired as e:
            result['status'] = 'error'
            result['error'] = f"Timeout ({e.timeout}s) dépassé."
            if e.stdout: result['output'] = e.stdout.decode()[:max_output_length]
            
        # 2. LECTURE & PURGE POST-RUN ECHO Monitor
        if monitor_file and os.path.exists(monitor_file):
            try:
                monitor_payloads = []
                with open(monitor_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            monitor_payloads.append(orjson.loads(line))
                if monitor_payloads:
                    result['monitor_payloads'] = monitor_payloads
                os.remove(monitor_file)
            except Exception as e:
                logger.error(f"Erreur lecture monitor payload: {e}")
                
        # Purge POST-RUN UI Payload
        if ui_payload_file and os.path.exists(ui_payload_file):
            try:
                os.remove(ui_payload_file)
            except Exception:
                pass
                
        result_queue.put(result)
    except Exception as e:
        result_queue.put({'status': 'critical_error', 'error': f"Worker System Error: {str(e)}"})

@app.route('/execute', methods=['POST'])
def execute_code():
    data = request.json
    file_path = data.get('file_path', '')
    dependencies = data.get('dependencies', [])
    ui_payload = data.get('ui_payload', None)
    timeout = data.get('timeout', 30)
    max_output_length = data.get('max_output_length', 131072)
    
    user_id = data.get('user_id', 'system')
    chat_id = data.get('chat_id')
    
    logger.info(f"🚀 Execution START | User: {user_id} | Chat: {chat_id} | File: {file_path} | Timeout: {timeout}s")

    sandbox_dir = None
    files_dir = None
    global_files_dir = None
    deps_dir = None
    if user_id != 'system' and chat_id:
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
        sandbox_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/codex/sandbox"
        files_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/files"
        global_files_dir = f"/app/backend/data/users/{safe_uid}/files"
        deps_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/dependencies"

    # Création d'un processus OS distinct
    q_result = multiprocessing.Queue()
    p = multiprocessing.Process(target=run_isolated_process, args=(file_path, dependencies, q_result, sandbox_dir, files_dir, global_files_dir, deps_dir, timeout, ui_payload, max_output_length))
    p.start()
    
    try:
        # On lit la queue avec un timeout. Si le buffer Base64 dépasse 64ko, 
        # le child bloquerait si le parent fait un p.join() au lieu de lire la queue (Deadlock Linux Pipe).
        res = q_result.get(timeout=timeout)
        p.join()
    except queue.Empty:
        if p.is_alive():
            p.terminate()
            p.join()
        logger.warning(f"⏰ Timeout | User: {user_id}")
        return jsonify({'status': 'error', 'error': f'Timeout ({timeout}s).'})
    
    if res is not None:
        status = res.get('status', 'unknown')
        logger.info(f"✅ Execution END | User: {user_id} | Status: {status}")
        return jsonify(res)
    else:
        logger.error(f"💥 Silent Crash | User: {user_id}")
        return jsonify({'status': 'error', 'error': 'Crash silencieux du processus.'})

@app.route('/health')
def health():
    """Healthcheck pour Docker Compose (orchestration séquentielle)."""
    return jsonify({"status": "ready"})

if __name__ == '__main__':
    # threaded=True permet de traiter les requêtes HTTP en parallèle
    app.run(host='0.0.0.0', port=5000, threaded=True)

