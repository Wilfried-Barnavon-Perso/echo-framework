from flask import Flask, request, jsonify # pyright: ignore[reportMissingImports]
import sys, io, contextlib, traceback, multiprocessing, tempfile, os
"""
================================================================================
MODULE : ECHO PYTHON WORKER API
VERSION : v1.1 (Sandbox Core)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-01-15
================================================================================
"""

app = Flask(__name__)

def run_isolated_process(code, result_queue):
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            os.chdir(temp_dir)
            stdout_capture = io.StringIO()
            stderr_capture = io.StringIO()
            result = {'status': 'success', 'output': '', 'error': ''}
            try:
                execution_context = {'__name__': '__main__'}
                with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                    exec(code, execution_context)
                result['output'] = stdout_capture.getvalue()
                result['error'] = stderr_capture.getvalue()
                result['status'] = 'error' if result['error'] else 'success'
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
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=run_isolated_process, args=(code, queue))
    p.start()
    p.join(timeout=timeout)
    if p.is_alive():
        p.terminate(); p.join()
        return jsonify({'status': 'error', 'error': f'Timeout ({timeout}s).'})
    if not queue.empty(): return jsonify(queue.get())
    else: return jsonify({'status': 'error', 'error': 'Crash silencieux du processus.'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)