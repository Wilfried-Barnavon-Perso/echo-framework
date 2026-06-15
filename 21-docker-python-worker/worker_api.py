from flask import Flask, request, jsonify # pyright: ignore[reportMissingImports]
import sys, io, contextlib, traceback, multiprocessing, tempfile, os, queue
import logging
import orjson as json
import pybase64 as base64

"""
================================================================================
MODULE : ECHO PYTHON WORKER API
VERSION : 1.6 (DATA SCIENCE HEADLESS)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-06-15

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

# Configuration des logs pour voir qui fait quoi dans la console Docker
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

def run_isolated_process(code, result_queue):
    """
    Exécute le code dans un processus séparé et un dossier temporaire.
    Totalement isolé des autres requêtes.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            os.chdir(temp_dir)
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            result = {'status': 'success', 'output': ''}
            
            try:
                # Contexte d'exécution vierge
                execution_context = {'__name__': '__main__'}
                with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                    exec(code, execution_context)
                
                result['output'] = stdout_capture.getvalue()
                stderr_output = stderr_capture.getvalue()
                if stderr_output:
                    result['error'] = stderr_output
                    result['status'] = 'error'
                    
            except Exception:
                result['status'] = 'critical_error'
                result['error'] = traceback.format_exc()
            
            result_queue.put(result)
        except Exception as e:
            result_queue.put({'status': 'critical_error', 'error': f"Worker System Error: {str(e)}"})

@app.route('/execute', methods=['POST'])
def execute_code():
    data = request.json
    code = data.get('code', '')
    timeout = data.get('timeout', 30)
    
    # Récupération de l'identité propagée par l'outil Python Code Executor (v137.0)
    # Permet de tracer qui consomme des ressources
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    
    logger.info(f"🚀 Execution START | User: {user_id} | Timeout: {timeout}s | Code Len: {len(code)}")

    # Création d'un processus OS distinct (Vrai parallélisme + Isolation mémoire)
    q_result = multiprocessing.Queue()
    p = multiprocessing.Process(target=run_isolated_process, args=(code, q_result))
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
