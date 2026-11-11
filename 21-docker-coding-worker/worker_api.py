"""
================================================================================
MODULE : ECHO CODE WORKER API
VERSION : 6.4.0.1
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-11-11

================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.4.0.1: Serveur PTY WebSocket (/api/terminals), endpoints config/openapi et factorisation bwrap.
# 5.234.1.3: Bind-mount --ro-bind /proc /proc (retrait --proc et --unshare-pid) pour Docker imbriqué.
# 5.232.5.1: Montage bwrap en lecture seule (--ro-bind-try) des workspaces Codex (/main, /plans, /generated).
# 5.232.1.2: Unification SSOT de sandbox-agent-readme.md et suppression définitive du .txt redondant.
# 5.232.1.1: Prise en charge du double montage bwrap sandbox-agent-readme (.md et .txt).

import asyncio
import json
import logging
import logging.config
import orjson
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from flask import Flask, jsonify, request  # pyright: ignore[reportMissingImports]
from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.routing import Mount, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn

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


def _build_base_bwrap_cmd(
    safe_uid: str,
    safe_cid: str,
    sandbox_dir: str,
    sandbox_tmp: str,
    files_dir: str | None = None,
    global_files_dir: str | None = None,
    deps_dir: str | None = None,
) -> list[str]:
    """Construit la base immuable des arguments bubblewrap partagée entre batch et PTY."""
    cmd = [
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
    ]
    if files_dir:
        cmd.extend(["--ro-bind-try", files_dir, "/files"])
    if global_files_dir:
        cmd.extend(["--ro-bind-try", global_files_dir, global_files_dir])
    if sandbox_dir:
        codex_root = os.path.dirname(sandbox_dir)
        for ro_ws in ("main", "plans", "generated"):
            cmd.extend(["--ro-bind-try", os.path.join(codex_root, ro_ws), f"/{ro_ws}"])
    if deps_dir:
        cmd.extend(["--bind-try", deps_dir, "/.deps"])
    return cmd


def _build_interactive_bwrap_cmd(
    safe_uid: str,
    safe_cid: str,
    sandbox_dir: str,
    deps_dir: str | None = None,
) -> list[str]:
    """Construit la commande bwrap pour le shell interactif PTY XTerminal."""
    sandbox_tmp = os.path.join(sandbox_dir, ".tmp")
    os.makedirs(sandbox_tmp, exist_ok=True)

    files_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/files" if sandbox_dir else None
    global_files_dir = f"/app/backend/data/users/{safe_uid}/files" if sandbox_dir else None
    if deps_dir is None and sandbox_dir:
        deps_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/dependencies"

    cmd = _build_base_bwrap_cmd(
        safe_uid=safe_uid,
        safe_cid=safe_cid,
        sandbox_dir=sandbox_dir,
        sandbox_tmp=sandbox_tmp,
        files_dir=files_dir,
        global_files_dir=global_files_dir,
        deps_dir=deps_dir,
    )

    python_paths = ["/echo_libs_sandbox"] + (["/.deps/python"] if deps_dir else [])
    node_paths = (["/.deps/node/node_modules"] if deps_dir else []) + ["/echo_libs_sandbox", "/usr/lib/node_modules"]

    cmd.extend([
        "--setenv", "HOME", "/sandbox",
        "--setenv", "TERM", "xterm-256color",
        "--setenv", "PYTHONPATH", ":".join(python_paths),
        "--setenv", "NODE_PATH", ":".join(node_paths),
        "--setenv", "ECHO_SESSION_USER", safe_uid,
        "--setenv", "ECHO_SESSION_CHAT", safe_cid,
    ])

    shell_bin = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
    cmd.extend([shell_bin, "--norc", "-i"])
    return cmd


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
                subprocess.run(
                    ["python", "-m", "pip", "install", "--target", python_deps_dir] + dependencies,
                    capture_output=True,
                    text=True,
                )
            elif is_node:
                node_deps_dir = os.path.join(deps_dir, "node")
                os.makedirs(node_deps_dir, exist_ok=True)
                os.chmod(node_deps_dir, 0o777)
                subprocess.run(
                    ["npm", "install", "--prefix", node_deps_dir] + dependencies,
                    capture_output=True,
                    text=True,
                )

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

    bwrap_cmd = _build_base_bwrap_cmd(
        safe_uid=safe_uid,
        safe_cid=safe_cid,
        sandbox_dir=sandbox_dir,
        sandbox_tmp=sandbox_tmp,
        files_dir=files_dir,
        global_files_dir=global_files_dir,
        deps_dir=deps_dir,
    )
    bwrap_cmd.extend([
        "--setenv", "PYTHONUNBUFFERED", "1",
        "--setenv", "ECHO_RUN_ID", str(run_id),
        "--setenv", "ECHO_MONITOR_FILE", mon_fname,
        "--setenv", "ECHO_UI_PAYLOAD_FILE", pay_fname,
        "--setenv", "ECHO_UI_INBOX_FILE", inb_fname,
    ])

    if is_python:
        python_paths = ["/echo_libs_sandbox"] + (["/.deps/python"] if deps_dir else [])
        bwrap_cmd.extend(["--setenv", "PYTHONPATH", ":".join(python_paths), "python", f"/sandbox/{file_path}"])
    else:
        node_paths = (
            (["/.deps/node/node_modules"] if deps_dir else []) + ["/echo_libs_sandbox", "/usr/lib/node_modules"]
        )
        bwrap_cmd.extend(["--setenv", "NODE_PATH", ":".join(node_paths), "node", f"/sandbox/{file_path}"])

    with _JOBS_LOCK:
        _prune_finished_jobs_unlocked(safe_uid, safe_cid)
        prefix = f"{safe_uid}:{safe_cid}:"
        active_chat = sum(1 for k, j in _JOBS_REGISTRY.items() if k.startswith(prefix) and j["state"] == "running")
        active_global = sum(1 for j in _JOBS_REGISTRY.values() if j["state"] == "running")
        if active_chat >= MAX_CONCURRENT_JOBS_PER_CHAT:
            return None, {
                "status": "error",
                "error": f"Quota dépassé : {active_chat}/{MAX_CONCURRENT_JOBS_PER_CHAT} processus actifs session.",
            }
        if active_global >= MAX_CONCURRENT_JOBS_GLOBAL:
            return None, {
                "status": "error",
                "error": f"Quota global dépassé : {active_global}/{MAX_CONCURRENT_JOBS_GLOBAL} processus actifs.",
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
        t_out = threading.Thread(
            target=_stream_reader,
            args=(proc.stdout, stdout_buf, out_sz, job_lock, max_output_length, True),
            daemon=True,
        )
        t_err = threading.Thread(
            target=_stream_reader,
            args=(proc.stderr, stderr_buf, err_sz, job_lock, max_output_length, False),
            daemon=True,
        )
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


# ================================================================================
# GESTIONNAIRE DE SESSIONS PTY INTERACTIVES (XTerminal.svelte)
# ================================================================================

class TerminalSession:
    def __init__(self, session_id: str, user_id: str, chat_id: str, sandbox_dir: str):
        self.id = session_id
        self.user_id = user_id
        self.chat_id = chat_id
        self.sandbox_dir = sandbox_dir
        self.created_at = time.time()
        self.cursor = 0
        self.max_buffer = 65536
        self.buffer = bytearray()
        self.truncated = False
        self.lock = threading.Lock()
        self.out_queue = queue.Queue()
        self.alive = True
        self.is_pty = False
        self.master_fd = None
        self.proc = None
        self.cols = 80
        self.rows = 24

        self._start()

    def _start(self):
        has_bwrap = shutil.which("bwrap") is not None
        has_pty = False
        try:
            import pty
            has_pty = hasattr(pty, "openpty") and hasattr(os, "fork")
        except ImportError:
            has_pty = False

        if has_bwrap and has_pty:
            import fcntl
            import pty
            import struct
            import termios

            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd
            self.is_pty = True
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))
            except Exception:
                pass

            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            cmd = _build_interactive_bwrap_cmd(self.user_id, self.chat_id, self.sandbox_dir)
            self.proc = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave_fd)
        else:
            # Fallback portable multi-plateforme (tests locaux .venv Windows ou hôte sans bwrap)
            if os.name == "nt":
                cmd = ["cmd.exe", "/q"]
            else:
                shell_bin = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
                cmd = [shell_bin, "-i"]

            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.sandbox_dir if os.path.isdir(self.sandbox_dir) else None,
                bufsize=0,
            )

        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

    def _reader_loop(self):
        try:
            if self.is_pty and self.master_fd is not None:
                import select
                while self.alive:
                    r, _, _ = select.select([self.master_fd], [], [], 0.1)
                    if r:
                        try:
                            chunk = os.read(self.master_fd, 4096)
                            if not chunk:
                                break
                            self._handle_output(chunk)
                        except OSError:
                            break
                    if self.proc.poll() is not None:
                        try:
                            while True:
                                chunk = os.read(self.master_fd, 4096)
                                if not chunk:
                                    break
                                self._handle_output(chunk)
                        except OSError:
                            pass
                        break
            else:
                while self.alive:
                    chunk = self.proc.stdout.read(512)
                    if not chunk:
                        break
                    self._handle_output(chunk)
        except Exception as e:
            logger.debug(f"Terminal reader loop ended: {e}")
        finally:
            self.alive = False
            self.out_queue.put(b"")

    def _handle_output(self, chunk: bytes):
        with self.lock:
            self.cursor += len(chunk)
            self.buffer.extend(chunk)
            if len(self.buffer) > self.max_buffer:
                self.buffer = self.buffer[-self.max_buffer:]
                self.truncated = True
        self.out_queue.put(chunk)

    def write_input(self, data: bytes | str):
        if not self.is_alive():
            return
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            if self.is_pty and self.master_fd is not None:
                os.write(self.master_fd, data)
            elif self.proc and self.proc.stdin:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
        except Exception as e:
            logger.warning(f"Terminal write_input error: {e}")

    def resize(self, cols: int, rows: int):
        self.cols = cols
        self.rows = rows
        if self.is_pty and self.master_fd is not None:
            try:
                import fcntl
                import struct
                import termios
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            except Exception as e:
                logger.debug(f"Terminal resize failed: {e}")

    def is_alive(self) -> bool:
        return self.alive and self.proc is not None and self.proc.poll() is None

    def terminate(self):
        self.alive = False
        if self.proc:
            _kill_process_group(self.proc)
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except Exception:
                pass
            self.master_fd = None
        self.out_queue.put(b"")


class TerminalSessionManager:
    def __init__(self):
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def create_session(self, user_id: str, chat_id: str) -> TerminalSession:
        safe_uid = _sanitize_id(user_id)
        safe_cid = _sanitize_id(chat_id, default="")
        sandbox_dir = _get_sandbox_dir(safe_uid, safe_cid)
        if not sandbox_dir:
            sandbox_dir = os.path.join(tempfile.gettempdir(), "echo_terminals", safe_uid, safe_cid or "general")
        os.makedirs(sandbox_dir, exist_ok=True)

        session_id = f"term_{uuid.uuid4().hex[:12]}"
        session = TerminalSession(
            session_id=session_id,
            user_id=safe_uid,
            chat_id=safe_cid,
            sandbox_dir=sandbox_dir,
        )
        with self._lock:
            self._prune_sessions()
            self._sessions[session_id] = session
        logger.info(f"🖥️ Terminal session CREATED | ID: {session_id} | User: {safe_uid} | Chat: {safe_cid}")
        return session

    def get_session(self, session_id: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def terminate_session(self, session_id: str):
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            session.terminate()
            logger.info(f"🛑 Terminal session TERMINATED | ID: {session_id}")

    def _prune_sessions(self):
        now = time.time()
        to_del = []
        for sid, s in self._sessions.items():
            if not s.is_alive() and (now - s.created_at > 300):
                to_del.append(sid)
        for sid in to_del:
            del self._sessions[sid]


TERMINAL_MANAGER = TerminalSessionManager()


# ================================================================================
# ROUTES FLASK REST EXISTANTES & TERMINAL
# ================================================================================

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
                f.write(orjson.dumps(ui_payload).decode("utf-8"))
            os.replace(tmp, tgt)
        except Exception:
            return jsonify({"status": "error"}), 500
    return jsonify({"status": "ok"})


@app.route('/health')
def health():
    """Healthcheck pour Docker Compose (orchestration séquentielle)."""
    return jsonify({"status": "ready"})


@app.route('/api/config', methods=['GET'])
def terminal_config():
    """Configuration déclarative des fonctionnalités requise par Open WebUI."""
    return jsonify({
        "features": {
            "terminal": True,
            "files": False,
            "desktop": False,
            "browser": False,
        }
    })


@app.route('/openapi.json', methods=['GET'])
def openapi_spec():
    """Spécification OpenAPI minimale pour découverte et connexion Open WebUI."""
    return jsonify({
        "openapi": "3.1.0",
        "info": {
            "title": "ECHO Coding Sandbox Terminal",
            "version": "6.4.0.1",
        },
        "paths": {},
    })


@app.route('/api/terminals', methods=['POST'])
def create_terminal():
    """Crée une session terminal interactive PTY."""
    req_json = request.json or {}
    user_id = request.headers.get("X-User-Id") or req_json.get("user_id") or "system"
    chat_id = (
        request.headers.get("X-Session-Id")
        or request.headers.get("X-Terminal-Context-Id")
        or req_json.get("chat_id")
        or ""
    )
    session = TERMINAL_MANAGER.create_session(user_id=user_id, chat_id=chat_id)
    return jsonify({"id": session.id})


@app.route('/api/terminals/<session_id>', methods=['GET'])
def get_terminal(session_id):
    """Vérifie l'existence et l'état d'une session terminal."""
    session = TERMINAL_MANAGER.get_session(session_id)
    if not session:
        return jsonify({"error": "Terminal session not found"}), 404
    return jsonify({
        "id": session.id,
        "chat_id": session.chat_id,
        "alive": session.is_alive(),
    })


@app.route('/api/terminals/<session_id>', methods=['DELETE'])
def delete_terminal(session_id):
    """Termine une session terminal."""
    TERMINAL_MANAGER.terminate_session(session_id)
    return jsonify({"status": "terminated", "id": session_id})


# ================================================================================
# ENDPOINT WEBSOCKET ASGI & WRAPPER STARLETTE
# ================================================================================

async def ws_terminal_endpoint(websocket: WebSocket):
    """Endpoint WebSocket bidirectionnel pour le streaming PTY XTerminal."""
    await websocket.accept()
    session_id = websocket.path_params.get("session_id", "")
    session = TERMINAL_MANAGER.get_session(session_id)
    if not session or not session.is_alive():
        await websocket.close(code=4404)
        return

    try:
        init_raw = await websocket.receive_text()
        init_data = json.loads(init_raw)
        if init_data.get("chat_id"):
            session.chat_id = init_data["chat_id"]
    except Exception:
        pass

    # Confirmer la liaison au chat
    await websocket.send_text(json.dumps({"type": "chat", "chat_id": session.chat_id}))

    # Envoyer le buffer accumulé si présent
    with session.lock:
        buffered = bytes(session.buffer)
        curr_cursor = session.cursor
        is_trunc = session.truncated

    if buffered:
        await websocket.send_text(json.dumps({"type": "output", "end": curr_cursor, "truncated": is_trunc}))
        await websocket.send_bytes(buffered)

    stop_event = asyncio.Event()

    async def _reader():
        loop = asyncio.get_running_loop()
        while not stop_event.is_set() and session.is_alive():
            def get_chunk():
                try:
                    return session.out_queue.get(timeout=0.1)
                except queue.Empty:
                    return None
            chunk = await loop.run_in_executor(None, get_chunk)
            if chunk == b"":
                break
            if chunk and not stop_event.is_set():
                try:
                    await websocket.send_text(
                        json.dumps({"type": "output", "end": session.cursor, "truncated": False})
                    )
                    await websocket.send_bytes(chunk)
                except Exception:
                    break

    async def _writer():
        try:
            while not stop_event.is_set():
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if "bytes" in msg and msg["bytes"]:
                    session.write_input(msg["bytes"])
                elif "text" in msg and msg["text"]:
                    try:
                        payload = json.loads(msg["text"])
                        p_type = payload.get("type")
                        if p_type == "resize":
                            session.resize(int(payload.get("cols", 80)), int(payload.get("rows", 24)))
                        elif p_type == "ping":
                            await websocket.send_text(json.dumps({"type": "pong"}))
                        elif p_type == "chat":
                            c_id = payload.get("chat_id")
                            if c_id:
                                session.chat_id = c_id
                            await websocket.send_text(json.dumps({"type": "chat", "chat_id": session.chat_id}))
                        elif p_type in ("ack", "activity", "auth"):
                            pass
                    except Exception as e:
                        logger.debug(f"Terminal WS JSON error: {e}")
        except Exception:
            pass
        finally:
            stop_event.set()

    tasks = [asyncio.create_task(_reader()), asyncio.create_task(_writer())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (asyncio.CancelledError, WebSocketDisconnect):
        pass
    finally:
        stop_event.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


asgi_app = Starlette(
    routes=[
        WebSocketRoute("/api/terminals/{session_id}", ws_terminal_endpoint),
        Mount("/", app=WSGIMiddleware(app)),
    ]
)


if __name__ == '__main__':
    uvicorn.run(asgi_app, host='0.0.0.0', port=5000)
