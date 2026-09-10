"""
================================================================================
MODULE : ECHO PYTHON WORKER API
VERSION : 2.4 (orjson integration)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-09-10

CHANGELOG 2.4 :
- Remplacement du module json par orjson pour de meilleures performances (lecture binaire de logging.json).
CHANGELOG 2.3 :
- Nettoyage des imports (PEP8) et placement de la docstring en tête de fichier.
CHANGELOG 2.2 :
- Retrait de l'isolation réseau (--unshare-net) pour permettre l'usage de requests/pandas.
CHANGELOG 2.1 :
- Durcissement de Bubblewrap : exécution en tant que nobody (UID/GID 65534) et isolation complète (--unshare-all).
CHANGELOG 2.0 :
- Moteur Bubblewrap : Isolation absolue avec dossiers `workspace` (RW) et `inputs` (RO).
CHANGELOG 1.8 :
- FIX: Ajout d'un filtre de logs limitant l'affichage des requêtes /health (1/5min).
CHANGELOG 1.6 :
- Correction d'un risque de deadlock IPC (utilisation de queue.get avec timeout au lieu de p.join bloquant).
CHANGELOG 1.5 :
- Omission du champ 'error' quand stderr est vide (alignement standard ECHO).
CHANGELOG 1.4 :
- Ajout de GET /health pour l'orchestration séquentielle Docker Compose.
CHANGELOG 1.3 :
- Migrated to orjson and pybase64 for consistency across the framework.
CHANGELOG 1.2 :
- Ajout du logging de l'ID utilisateur (X-OpenWebUI-User-Id).
- Maintien du mode 'threaded' pour le parallélisme.
================================================================================
"""

import logging
import logging.config
import multiprocessing
import orjson
import os
import queue
import subprocess
import tempfile
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

def run_isolated_process(code, result_queue, target_dir, files_dir, timeout_sec):
    try:
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
            workspace = target_dir
        else:
            workspace = tempfile.mkdtemp()
            
        script_path = os.path.join(workspace, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        # Isolation Absolue Bubblewrap
        bwrap_cmd = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/bin", "/bin",
            "--dev", "/dev",
            "--proc", "/proc",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--unshare-user", "--uid", "65534", "--gid", "65534", # Exécute en tant qu'utilisateur "nobody"
            "--bind", workspace, "/workspace", # <- Dossier Sandbox (RW)
            "--chdir", "/workspace"
        ]

        # Montage des uploads utilisateurs en LECTURE SEULE
        if files_dir and os.path.exists(files_dir):
            bwrap_cmd.extend(["--ro-bind", files_dir, "/inputs"])

        bwrap_cmd.extend(["python", "/workspace/script.py"])

        result = {'status': 'success', 'output': ''}
        try:
            proc = subprocess.run(
                bwrap_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_sec
            )
            
            # Troncature Anti-OOM (Max 1 Mo)
            result['output'] = proc.stdout[:1024 * 1024]
            if len(proc.stdout) > 1024 * 1024:
                result['output'] += "\n[Avertissement ECHO : Sortie tronquée à 1 Mo]"
                
            if proc.stderr:
                result['error'] = proc.stderr[:1024 * 1024]
                result['status'] = 'error'
            elif proc.returncode != 0:
                result['error'] = result.get('error', '') + f"\nProcess exited with code {proc.returncode}"
                result['status'] = 'error'

        except subprocess.TimeoutExpired as e:
            result['status'] = 'error'
            result['error'] = f"Timeout ({e.timeout}s) dépassé."
            if e.stdout: result['output'] = e.stdout.decode()[:1024 * 1024]
            
        result_queue.put(result)
    except Exception as e:
        result_queue.put({'status': 'critical_error', 'error': f"Worker System Error: {str(e)}"})

@app.route('/execute', methods=['POST'])
def execute_code():
    data = request.json
    code = data.get('code', '')
    timeout = data.get('timeout', 30)
    
    user_id = data.get('user_id', 'system')
    chat_id = data.get('chat_id')
    
    logger.info(f"🚀 Execution START | User: {user_id} | Chat: {chat_id} | Timeout: {timeout}s")

    target_dir = None
    files_dir = None
    if user_id != 'system' and chat_id:
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
        target_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/codex/sandbox"
        files_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/files"

    # Création d'un processus OS distinct
    q_result = multiprocessing.Queue()
    p = multiprocessing.Process(target=run_isolated_process, args=(code, q_result, target_dir, files_dir, timeout))
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
