"""
================================================================================
MODULE : ECHO CODE WORKER API
VERSION : 5.234.1.3
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-11-04

================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.234.1.3: Rétablissement du bind-mount --ro-bind /proc /proc (retrait de --proc et --unshare-pid) pour compatibilité Docker imbriqué.
# 5.232.5.1: Montage Bubblewrap en lecture seule (--ro-bind-try) des workspaces Codex /main, /plans et /generated dans la Sandbox.
# 5.232.1.2: Unification SSOT de sandbox-agent-readme.md et suppression définitive du .txt redondant.
# 5.232.1.1: Prise en charge du double montage bwrap sandbox-agent-readme (.md et .txt).
# 5.231.20.1: Sérialisation par verrou de session (_SANDBOX_LOCKS) des exécutions concurrentes d'un même chat et harmonisation pid/pids sur /kill.

import logging
import logging.config
import orjson
import os
import signal
import subprocess
import threading
import time
import uuid

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

MAX_CONCURRENT_JOBS_PER_CHAT = int(os.environ.get("SANDBOX_MAX_CONCURRENT_JOBS_PER_CHAT", 4))
MAX_CONCURRENT_JOBS_GLOBAL = int(os.environ.get("SANDBOX_MAX_CONCURRENT_JOBS_GLOBAL", 8))
FINISHED_JOBS_MAX_PER_CHAT = 20
FINISHED_JOBS_TTL_SEC = 900

_JOBS_REGISTRY = {}
_JOBS_LOCK = threading.Lock()
_DEPS_LOCKS = {}
_DEPS_LOCKS_GUARD = threading.Lock()


def _sanitize_id(val, default="system"):
    if not val:
        return default
    cleaned = "".join(x for x in str(val) if x.isalnum() or x in "-_")
    return cleaned or default


def _get_session_deps_lock(user_id, chat_id):
    key = f"{user_id}:{chat_id}"
    with _DEPS_LOCKS_GUARD:
        if key not in _DEPS_LOCKS:
            _DEPS_LOCKS[key] = threading.Lock()
        return _DEPS_LOCKS[key]


def _get_sandbox_dir(user_id, chat_id):
    if user_id != 'system' and chat_id:
        safe_uid = _sanitize_id(user_id)
        safe_cid = _sanitize_id(chat_id, default="")
        if not safe_cid:
            return None
        return f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/codex/sandbox"
    return None


def _get_ipc_filenames(run_id):
    safe_rid = _sanitize_id(run_id, default="")
    if safe_rid:
        return (
            f".echo_monitor_{safe_rid}.jsonl",
            f".echo_ui_payload_{safe_rid}.json",
            f".echo_ui_inbox_{safe_rid}.jsonl",
        )
    return (".echo_monitor.jsonl", ".echo_ui_payload.json", ".echo_ui_inbox.jsonl")


def _cleanup_ipc_files(sandbox_dir, run_id):
    if not sandbox_dir:
        return
    for fname in _get_ipc_filenames(run_id):
        fpath = os.path.join(sandbox_dir, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass


def _read_monitor_file(monitor_path):
    payloads = []
    if monitor_path and os.path.exists(monitor_path):
        try:
            with open(monitor_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            payloads.append(orjson.loads(line))
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Erreur lecture monitor payload: {e}")
    return payloads


def _kill_process_group(proc):
    if not proc or proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _prune_finished_jobs_unlocked(safe_uid, safe_cid):
    now = time.time()
    prefix = f"{safe_uid}:{safe_cid}:"
    expired_keys = [
        k for k, j in _JOBS_REGISTRY.items()
        if j["state"] != "running" and j.get("ended_at") and (now - j["ended_at"] > FINISHED_JOBS_TTL_SEC)
    ]
    for k in expired_keys:
        del _JOBS_REGISTRY[k]

    chat_finished = [
        (k, j) for k, j in _JOBS_REGISTRY.items()
        if k.startswith(prefix) and j["state"] != "running"
    ]
    if len(chat_finished) > FINISHED_JOBS_MAX_PER_CHAT:
        chat_finished.sort(key=lambda item: item[1].get("ended_at") or 0.0)
        for k, _ in chat_finished[: len(chat_finished) - FINISHED_JOBS_MAX_PER_CHAT]:
            del _JOBS_REGISTRY[k]


def _stream_reader(pipe, buf_list, size_ref, lock, max_len, is_stdout=True):
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            with lock:
                rem = max_len - size_ref[0]
                if rem > 0:
                    piece = line[:rem]
                    buf_list.append(piece)
                    size_ref[0] += len(piece)
                    if len(line) > rem and is_stdout:
                        limit_kb = max_len // 1024
                        buf_list.append(
                            f"\n[Avertissement ECHO : Sortie console tronquée à {limit_kb}Ko. "
                            "Privilégier echo_monitor.display() pour les payloads massifs]."
                        )
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _job_lifecycle_watcher(job):
    proc = job["proc"]
    timeout_sec = job["timeout_sec"]
    try:
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        with job["lock"]:
            if job["state"] == "running":
                job["state"] = "timeout"
                job["error_msg"] = f"Timeout ({timeout_sec}s) dépassé."

    for t in (job.get("t_out"), job.get("t_err")):
        if t:
            t.join(timeout=2.0)

    rc = proc.poll()
    monitor_payloads = _read_monitor_file(job.get("monitor_file"))
    _cleanup_ipc_files(job.get("sandbox_dir"), job["run_id"])

    with job["lock"]:
        job["exit_code"] = rc
        job["ended_at"] = time.time()
        if monitor_payloads:
            job["monitor_payloads"] = monitor_payloads
        if job["state"] == "running":
            stderr_str = "".join(job["stderr_buf"])
            if stderr_str or (rc is not None and rc != 0):
                job["state"] = "error"
            else:
                job["state"] = "completed"

    job["done_event"].set()


def _build_job_response(job):
    with job["lock"]:
        stdout_str = "".join(job["stdout_buf"])
        stderr_str = "".join(job["stderr_buf"])
        state = job["state"]
        rc = job["exit_code"]
        err_msg = job.get("error_msg") or ""
        mon = list(job.get("monitor_payloads") or [])

    if state == "running":
        return {
            "status": "running",
            "state": "running",
            "run_id": job["run_id"],
            "pid": job["pid"],
            "file_path": job["file_path"],
            "output": stdout_str,
        }

    status_val = "success" if state == "completed" else ("killed" if state == "killed" else "error")
    res = {
        "status": status_val,
        "state": state,
        "run_id": job["run_id"],
        "pid": job["pid"],
        "exit_code": rc,
        "output": stdout_str,
    }
    if stderr_str:
        res["error"] = stderr_str if not err_msg else f"{err_msg}\n{stderr_str}"
    elif err_msg:
        res["error"] = err_msg
    elif rc is not None and rc != 0 and state != "killed":
        res["error"] = f"Process exited with code {rc}"
    if mon:
        res["monitor_payloads"] = mon
    return res


def _start_sandbox_job(
    safe_uid, safe_cid, run_id, file_path, dependencies,
    sandbox_dir, files_dir, global_files_dir, deps_dir,
    timeout_sec, ui_payload, max_output_length
):
    if not sandbox_dir:
        return None, {"status": "critical_error", "error": "Espace d'exécution non défini."}

    os.makedirs(sandbox_dir, exist_ok=True)
    sandbox_tmp = os.path.join(sandbox_dir, ".tmp")
    os.makedirs(sandbox_tmp, exist_ok=True)

    if deps_dir:
        os.makedirs(deps_dir, exist_ok=True)
        os.chmod(deps_dir, 0o777)

    abs_file_path = os.path.abspath(os.path.join(sandbox_dir, file_path))
    if not abs_file_path.startswith(os.path.abspath(sandbox_dir) + os.sep):
        return None, {"status": "error", "error": "Accès refusé : Le fichier cible est hors du workspace."}

    if not os.path.isfile(abs_file_path):
        return None, {"status": "error", "error": f"Fichier cible introuvable : {file_path}"}

    is_python = file_path.endswith(".py")
    is_node = file_path.endswith(".js")
    if not (is_python or is_node):
        return None, {"status": "error", "error": "Extension non supportée. Seuls .py et .js sont acceptés."}

    lint_cmd = ["python", "-m", "py_compile", abs_file_path] if is_python else ["node", "-c", abs_file_path]
    try:
        lint_proc = subprocess.run(lint_cmd, capture_output=True, text=True, timeout=10)
        if lint_proc.returncode != 0:
            err_out = lint_proc.stderr if lint_proc.stderr else lint_proc.stdout
            return None, {"status": "error", "error": f"Erreur de syntaxe (Pre-execution Linting) :\n{err_out}"}
    except subprocess.TimeoutExpired:
        return None, {"status": "error", "error": "Timeout lors de la vérification syntaxique."}

    if dependencies and deps_dir:
        with _get_session_deps_lock(safe_uid, safe_cid):
            if is_python:
                python_deps_dir = os.path.join(deps_dir, "python")
                os.makedirs(python_deps_dir, exist_ok=True)
                os.chmod(python_deps_dir, 0o777)
                subprocess.run(["python", "-m", "pip", "install", "--target", python_deps_dir] + dependencies, capture_output=True, text=True)
            elif is_node:
                node_deps_dir = os.path.join(deps_dir, "node")
                os.makedirs(node_deps_dir, exist_ok=True)
                os.chmod(node_deps_dir, 0o777)
                subprocess.run(["npm", "install", "--prefix", node_deps_dir] + dependencies, capture_output=True, text=True)

    mon_fname, pay_fname, inb_fname = _get_ipc_filenames(run_id)
    _cleanup_ipc_files(sandbox_dir, run_id)
    monitor_file = os.path.join(sandbox_dir, mon_fname)
    ui_payload_file = os.path.join(sandbox_dir, pay_fname)
    if ui_payload:
        try:
            with open(ui_payload_file, "w", encoding="utf-8") as f:
                f.write(orjson.dumps(ui_payload).decode("utf-8"))
        except Exception:
            pass

    bwrap_cmd = [
        "bwrap",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/bin", "/bin",
        "--dev", "/dev",
        "--ro-bind", "/proc", "/proc",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind-try", "/etc/ssl/certs", "/etc/ssl/certs",
        "--bind", sandbox_tmp, "/tmp",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--die-with-parent",
        "--unshare-user", "--uid", "65534", "--gid", "65534",
        "--bind", sandbox_dir, "/sandbox",
        "--ro-bind-try", "/app/echo_libs_sandbox", "/echo_libs_sandbox",
        "--ro-bind-try", "/app/sandbox-agent-readme.md", "/sandbox-agent-readme.md",
        "--chdir", "/sandbox",
        "--setenv", "PYTHONUNBUFFERED", "1",
        "--setenv", "ECHO_RUN_ID", str(run_id),
        "--setenv", "ECHO_MONITOR_FILE", mon_fname,
        "--setenv", "ECHO_UI_PAYLOAD_FILE", pay_fname,
        "--setenv", "ECHO_UI_INBOX_FILE", inb_fname,
    ]
    if files_dir:
        bwrap_cmd.extend(["--ro-bind-try", files_dir, "/files"])
    if global_files_dir:
        bwrap_cmd.extend(["--ro-bind-try", global_files_dir, global_files_dir])
    codex_root = os.path.dirname(sandbox_dir)
    for ro_ws in ("main", "plans", "generated"):
        bwrap_cmd.extend(["--ro-bind-try", os.path.join(codex_root, ro_ws), f"/{ro_ws}"])
    if deps_dir:
        bwrap_cmd.extend(["--bind-try", deps_dir, "/.deps"])

    if is_python:
        python_paths = ["/echo_libs_sandbox"] + (["/.deps/python"] if deps_dir else [])
        bwrap_cmd.extend(["--setenv", "PYTHONPATH", ":".join(python_paths), "python", f"/sandbox/{file_path}"])
    else:
        node_paths = (["/.deps/node/node_modules"] if deps_dir else []) + ["/echo_libs_sandbox", "/usr/lib/node_modules"]
        bwrap_cmd.extend(["--setenv", "NODE_PATH", ":".join(node_paths), "node", f"/sandbox/{file_path}"])

    with _JOBS_LOCK:
        _prune_finished_jobs_unlocked(safe_uid, safe_cid)
        prefix = f"{safe_uid}:{safe_cid}:"
        active_chat = sum(1 for k, j in _JOBS_REGISTRY.items() if k.startswith(prefix) and j["state"] == "running")
        active_global = sum(1 for j in _JOBS_REGISTRY.values() if j["state"] == "running")
        if active_chat >= MAX_CONCURRENT_JOBS_PER_CHAT:
            return None, {
                "status": "error",
                "error": f"Quota dépassé : {active_chat}/{MAX_CONCURRENT_JOBS_PER_CHAT} processus actifs dans cette session.",
            }
        if active_global >= MAX_CONCURRENT_JOBS_GLOBAL:
            return None, {
                "status": "error",
                "error": f"Quota global dépassé : {active_global}/{MAX_CONCURRENT_JOBS_GLOBAL} processus actifs sur le Worker.",
            }

        proc = subprocess.Popen(
            bwrap_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        job_lock = threading.Lock()
        stdout_buf, stderr_buf = [], []
        out_sz, err_sz = [0], [0]
        t_out = threading.Thread(target=_stream_reader, args=(proc.stdout, stdout_buf, out_sz, job_lock, max_output_length, True), daemon=True)
        t_err = threading.Thread(target=_stream_reader, args=(proc.stderr, stderr_buf, err_sz, job_lock, max_output_length, False), daemon=True)
        t_out.start()
        t_err.start()

        job = {
            "run_id": run_id,
            "user_id": safe_uid,
            "chat_id": safe_cid,
            "file_path": file_path,
            "pid": proc.pid,
            "proc": proc,
            "state": "running",
            "started_at": time.time(),
            "ended_at": None,
            "timeout_sec": timeout_sec,
            "exit_code": None,
            "error_msg": "",
            "stdout_buf": stdout_buf,
            "stderr_buf": stderr_buf,
            "monitor_payloads": [],
            "monitor_file": monitor_file,
            "sandbox_dir": sandbox_dir,
            "lock": job_lock,
            "done_event": threading.Event(),
            "t_out": t_out,
            "t_err": t_err,
        }
        _JOBS_REGISTRY[f"{safe_uid}:{safe_cid}:{run_id}"] = job

    watcher = threading.Thread(target=_job_lifecycle_watcher, args=(job,), daemon=True)
    watcher.start()
    return job, None


@app.route('/execute', methods=['POST'])
def execute_code():
    data = request.json or {}
    file_path = data.get('file_path', '')
    dependencies = data.get('dependencies') or []
    ui_payload = data.get('ui_payload', None)
    timeout = int(data.get('timeout', 30))
    max_output_length = int(data.get('max_output_length', 131072))
    background = bool(data.get('background', False))
    run_id = _sanitize_id(data.get('run_id') or f"run_{uuid.uuid4().hex[:8]}")

    safe_uid = _sanitize_id(data.get('user_id', 'system'))
    safe_cid = _sanitize_id(data.get('chat_id'), default="")

    logger.info(
        f"🚀 Execution START | User: {safe_uid} | Chat: {safe_cid} | "
        f"Run: {run_id} | File: {file_path} | Bg: {background} | Timeout: {timeout}s"
    )

    sandbox_dir = _get_sandbox_dir(safe_uid, safe_cid)
    files_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/files" if sandbox_dir else None
    global_files_dir = f"/app/backend/data/users/{safe_uid}/files" if sandbox_dir else None
    deps_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/dependencies" if sandbox_dir else None

    job, err = _start_sandbox_job(
        safe_uid, safe_cid, run_id, file_path, dependencies,
        sandbox_dir, files_dir, global_files_dir, deps_dir,
        timeout, ui_payload, max_output_length
    )
    if err is not None:
        err["run_id"] = run_id
        return jsonify(err)

    if background:
        job["done_event"].wait(timeout=0.4)
        res = _build_job_response(job)
        logger.info(f"⚡ Execution BACKGROUND | Run: {run_id} | State: {res.get('state')}")
        return jsonify(res)

    finished = job["done_event"].wait(timeout=timeout + 5.0)
    if not finished:
        _kill_process_group(job["proc"])
        job["done_event"].wait(timeout=2.0)

    res = _build_job_response(job)
    logger.info(f"✅ Execution END | Run: {run_id} | Status: {res.get('status')}")
    return jsonify(res)


@app.route('/status', methods=['GET'])
def status_sandbox():
    safe_uid = _sanitize_id(request.args.get('user_id', 'system'))
    safe_cid = _sanitize_id(request.args.get('chat_id'), default="")
    run_id = _sanitize_id(request.args.get('run_id'), default="")
    try:
        wait_sec = max(0.0, min(float(request.args.get('wait_sec', 0)), 60.0))
        stdout_offset = max(0, int(request.args.get('stdout_offset', 0)))
        stderr_offset = max(0, int(request.args.get('stderr_offset', 0)))
    except ValueError:
        wait_sec, stdout_offset, stderr_offset = 0.0, 0, 0

    prefix = f"{safe_uid}:{safe_cid}:"
    with _JOBS_LOCK:
        _prune_finished_jobs_unlocked(safe_uid, safe_cid)
        if not run_id:
            jobs_summary = []
            for k, j in _JOBS_REGISTRY.items():
                if k.startswith(prefix):
                    with j["lock"]:
                        jobs_summary.append({
                            "run_id": j["run_id"],
                            "file_path": j["file_path"],
                            "pid": j["pid"],
                            "state": j["state"],
                            "exit_code": j["exit_code"],
                            "elapsed_sec": round((j["ended_at"] or time.time()) - j["started_at"], 2),
                            "stdout_len": sum(len(s) for s in j["stdout_buf"]),
                            "stderr_len": sum(len(s) for s in j["stderr_buf"]),
                        })
            return jsonify({"status": "ok", "jobs": jobs_summary})
        job = _JOBS_REGISTRY.get(f"{prefix}{run_id}")

    if not job:
        return jsonify({"status": "not_found", "run_id": run_id})

    if wait_sec > 0 and job["state"] == "running":
        job["done_event"].wait(timeout=wait_sec)

    with job["lock"]:
        full_out = "".join(job["stdout_buf"])
        full_err = "".join(job["stderr_buf"])
        state = job["state"]
        rc = job["exit_code"]
        err_msg = job.get("error_msg") or ""

    return jsonify({
        "status": "ok",
        "run_id": job["run_id"],
        "file_path": job["file_path"],
        "pid": job["pid"],
        "state": state,
        "exit_code": rc,
        "elapsed_sec": round((job["ended_at"] or time.time()) - job["started_at"], 2),
        "stdout_chunk": full_out[stdout_offset:],
        "stderr_chunk": full_err[stderr_offset:],
        "stdout_offset": len(full_out),
        "stderr_offset": len(full_err),
        "error": err_msg,
    })


@app.route('/kill', methods=['POST'])
def kill_sandbox():
    data = request.json or {}
    safe_uid = _sanitize_id(data.get('user_id', 'system'))
    safe_cid = _sanitize_id(data.get('chat_id'), default="")
    run_id = _sanitize_id(data.get('run_id'), default="")
    prefix = f"{safe_uid}:{safe_cid}:"

    with _JOBS_LOCK:
        if run_id:
            target = _JOBS_REGISTRY.get(f"{prefix}{run_id}")
            jobs_to_kill = [target] if target else []
        else:
            jobs_to_kill = [j for k, j in _JOBS_REGISTRY.items() if k.startswith(prefix) and j["state"] == "running"]

    killed_pids = []
    killed_runs = []
    for j in jobs_to_kill:
        with j["lock"]:
            was_running = (j["state"] == "running")
            if was_running:
                j["state"] = "killed"
                j["error_msg"] = "Execution aborted by UI or User."
        if was_running:
            _kill_process_group(j["proc"])
            j["done_event"].wait(timeout=2.0)
            killed_pids.append(j["pid"])
            killed_runs.append(j["run_id"])
        _cleanup_ipc_files(j.get("sandbox_dir"), j["run_id"])

    if killed_pids:
        return jsonify({"status": "killed", "pid": killed_pids[0], "pids": killed_pids, "run_ids": killed_runs})
    return jsonify({"status": "not_found"})


@app.route('/poll_monitor', methods=['GET'])
def poll_monitor():
    user_id = request.args.get('user_id', 'system')
    chat_id = request.args.get('chat_id')
    run_id = request.args.get('run_id', '')
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0
    sandbox_dir = _get_sandbox_dir(user_id, chat_id)
    if not sandbox_dir:
        return jsonify({"payloads": [], "offset": offset})

    mon_fname, _, _ = _get_ipc_filenames(run_id)
    monitor_file = os.path.join(sandbox_dir, mon_fname)
    if not os.path.exists(monitor_file):
        return jsonify({"payloads": [], "offset": offset})

    payloads = []
    try:
        with open(monitor_file, 'rb') as f:
            f.seek(offset)
            content = f.read()

        last_newline = content.rfind(b'\n')
        if last_newline != -1:
            valid_content = content[:last_newline + 1]
            new_offset = offset + len(valid_content)
            for line in valid_content.decode('utf-8', errors='replace').strip().split('\n'):
                if line.strip():
                    try:
                        payloads.append(orjson.loads(line))
                    except Exception:
                        pass
            offset = new_offset
    except Exception:
        pass
    return jsonify({"payloads": payloads, "offset": offset})


@app.route('/update_payload', methods=['POST'])
def update_payload():
    data = request.json or {}
    ui_payload = data.get('ui_payload')
    run_id = data.get('run_id', '')
    sandbox_dir = _get_sandbox_dir(data.get('user_id', 'system'), data.get('chat_id'))
    if sandbox_dir and isinstance(ui_payload, dict):
        _, pay_fname, inb_fname = _get_ipc_filenames(run_id)
        inbox_events = ui_payload.pop('_inbox_events', None)
        if isinstance(inbox_events, list) and inbox_events:
            inb_path = os.path.join(sandbox_dir, inb_fname)
            try:
                with open(inb_path, 'a', encoding='utf-8') as f:
                    for ev in inbox_events:
                        f.write(orjson.dumps(ev).decode('utf-8') + '\n')
            except Exception:
                pass
        tmp = os.path.join(sandbox_dir, f".echo_ui.tmp.{uuid.uuid4().hex}")
        tgt = os.path.join(sandbox_dir, pay_fname)
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(orjson.dumps(ui_payload).decode('utf-8'))
            os.replace(tmp, tgt)
        except Exception:
            return jsonify({"status": "error"}), 500
    return jsonify({"status": "ok"})


@app.route('/health')
def health():
    """Healthcheck pour Docker Compose (orchestration séquentielle)."""
    return jsonify({"status": "ready"})


if __name__ == '__main__':
    # threaded=True permet de traiter les requêtes HTTP en parallèle
    app.run(host='0.0.0.0', port=5000, threaded=True)
