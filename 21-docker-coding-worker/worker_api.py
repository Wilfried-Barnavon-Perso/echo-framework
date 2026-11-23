# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
================================================================================
MODULE : ECHO CODE WORKER API
VERSION : 6.19.0.1
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-11-23

================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.19.0.1: Support du répertoire de travail scopé sub_sid (/sandbox/<sub_sid>) sous Bubblewrap.
# 6.17.4.1: Purge /ws/canvas et relais BidiRouter, ajout webhook interne sandbox_event HMAC et durcissement proxy HTTP/WS B2 (Option B).
# 6.16.0.3: CanvasRunHub AF_UNIX, /ws/canvas/{run_id}, proxy HTTP/WS B2 (/app_proxy, /ws/app) et purge monitor_payloads/flag test port.
# 6.15.2.1: Long-polling /poll_monitor (wait), purge TTL/lifetime jobs terminés, contrôle /kill par run_id et payloads.
# 6.13.1.1: Exposition de job_state, exit_code et alive dans GET /poll_monitor.

import asyncio
import json
import logging
import logging.config
import orjson
import os
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlencode
import uuid

from flask import Flask, jsonify, request  # pyright: ignore[reportMissingImports]
import httpx
from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.responses import Response, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect
import uvicorn

try:
    import websockets
    from websockets.exceptions import ConnectionClosed as _WsConnectionClosed
except ImportError:
    websockets = None
    _WsConnectionClosed = Exception

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
DEFAULT_BG_IDLE_TTL_SEC = float(os.environ.get("SANDBOX_BG_IDLE_TTL_SEC", 1800.0))
DEFAULT_BG_MAX_LIFETIME_SEC = float(os.environ.get("SANDBOX_BG_MAX_LIFETIME_SEC", 14400.0))
ECHO_WEBUI_INTERNAL_URL = os.environ.get("ECHO_WEBUI_INTERNAL_URL", "http://echo-webui:8080").rstrip("/")

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


def _notify_webui_sandbox_event(callback_token: str, payload: dict) -> None:
    """Émet de manière non-bloquante un événement sandbox vers le backend Open WebUI."""
    token = str(callback_token or "").strip()
    if not token or not isinstance(payload, dict):
        return
    url = f"{ECHO_WEBUI_INTERNAL_URL}/echo/v1/wm/internal/sandbox_event"
    body = orjson.dumps(payload)

    def _send():
        try:
            with httpx.Client(timeout=2.5) as client:
                client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Echo-Callback-Token": token,
                    },
                )
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True, name="echo-sandbox-cb").start()


def _allocate_ephemeral_port() -> int:
    for _ in range(16):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            if port != 5000:
                return port
    return 0


class CanvasRunHub:
    """
    Bus IPC mémoire + serveur Socket Unix (AF_UNIX / repli AF_INET) par run_id
    entre le bwrap Sandbox et le Hub Worker (display, expose_app, window_closed).
    """

    _HEADER_STRUCT = struct.Struct(">II")

    def __init__(self, run_id, user_id="", chat_id="", is_background=False, callback_token=""):
        self.run_id = str(run_id)
        self.user_id = str(user_id or "")
        self.chat_id = str(chat_id or "")
        self.is_background = bool(is_background)
        self.callback_token = str(callback_token or "").strip()
        self.safe_rid = _sanitize_id(self.run_id, default=uuid.uuid4().hex[:8])
        self.ipc_dir = os.path.join(tempfile.gettempdir(), "echo_ipc", self.safe_rid)
        self.app_sock_dir = os.path.join(self.ipc_dir, "app")
        self.sock_path = None
        self.sandbox_sock_env = "/run/echo_ipc.sock"
        self.is_unix_sock = False
        self.allocated_port = None
        self.job_pgid = None

        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.frames = {}
        self.app_targets = {}
        self._verified_tcp_inodes = set()
        self.closed_windows = set()

        self.ready_or_display_event = threading.Event()

        self._job_ref = None
        self._server_sock = None
        self._active_client = None
        self._closed = False
        self._job_state = "running"
        self._exit_code = None

    def attach_job(self, job):
        with self.lock:
            self._job_ref = job

    def _touch_activity(self, ts=None):
        job = self._job_ref
        if job is not None:
            now = ts if ts is not None else time.time()
            with job["lock"]:
                job["last_activity_at"] = now

    def start(self):
        os.makedirs(self.ipc_dir, exist_ok=True)
        try:
            os.chmod(self.ipc_dir, 0o755)
        except OSError:
            pass
        os.makedirs(self.app_sock_dir, exist_ok=True)
        try:
            os.chmod(self.app_sock_dir, 0o777)
        except OSError:
            pass
        if not self.allocated_port:
            self.allocated_port = _allocate_ephemeral_port()

        if hasattr(socket, "AF_UNIX") and os.environ.get("ECHO_IPC_FORCE_TCP") != "1":
            sock_path = os.path.join(self.ipc_dir, "ipc.sock")
            if os.path.exists(sock_path):
                try:
                    os.remove(sock_path)
                except OSError:
                    pass
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sock_path)
            os.chmod(sock_path, 0o666)
            srv.listen(8)
            self.sock_path = sock_path
            self.sandbox_sock_env = "/run/echo_ipc.sock"
            self.is_unix_sock = True
        else:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(8)
            port = srv.getsockname()[1]
            self.sock_path = None
            self.sandbox_sock_env = f"tcp://127.0.0.1:{port}"
            self.is_unix_sock = False

        self._server_sock = srv
        t = threading.Thread(target=self._accept_loop, daemon=True, name=f"canvas-ipc-{self.safe_rid}")
        t.start()

    @staticmethod
    def _recv_exact(conn, n):
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = conn.recv(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @classmethod
    def _encode_uds_frame(cls, meta, binary=b""):
        meta_bytes = orjson.dumps(meta)
        bin_bytes = bytes(binary) if binary else b""
        return cls._HEADER_STRUCT.pack(len(meta_bytes), len(bin_bytes)) + meta_bytes + bin_bytes

    def _accept_loop(self):
        srv = self._server_sock
        while srv is not None and not self._closed:
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            with self.write_lock:
                self._active_client = conn
            with self.lock:
                closed_snap = list(self.closed_windows)
            for wid in closed_snap:
                self._send_frame_to_conn(conn, {"type": "window_closed", "window_id": wid})
            t = threading.Thread(
                target=self._client_reader,
                args=(conn,),
                daemon=True,
                name=f"canvas-cli-{self.safe_rid}",
            )
            t.start()

    def _send_frame_to_conn(self, conn, meta, binary=b""):
        frame = self._encode_uds_frame(meta, binary)
        with self.write_lock:
            if conn is None or self._closed:
                return False
            try:
                conn.sendall(frame)
                return True
            except OSError:
                if self._active_client is conn:
                    self._active_client = None
                try:
                    conn.close()
                except OSError:
                    pass
                return False

    def send_to_sandbox(self, meta, binary=b""):
        if not isinstance(meta, dict):
            return False
        with self.write_lock:
            conn = self._active_client
        if conn is None:
            return False
        return self._send_frame_to_conn(conn, meta, binary)

    def close_windows(self, window_ids):
        if isinstance(window_ids, str):
            wids = [window_ids]
        else:
            wids = [str(w) for w in (window_ids or []) if w]
        if not wids:
            wids = ["default"]
        for wid in wids:
            with self.lock:
                self.closed_windows.add(wid)
            self.send_to_sandbox({"type": "window_closed", "window_id": wid}, b"")

    def _client_reader(self, conn):
        try:
            while not self._closed:
                header = self._recv_exact(conn, self._HEADER_STRUCT.size)
                if header is None:
                    break
                meta_len, bin_len = self._HEADER_STRUCT.unpack(header)
                meta_raw = self._recv_exact(conn, meta_len) if meta_len > 0 else b"{}"
                if meta_raw is None:
                    break
                bin_raw = self._recv_exact(conn, bin_len) if bin_len > 0 else b""
                if bin_len > 0 and bin_raw is None:
                    break
                try:
                    meta = orjson.loads(meta_raw)
                except Exception:
                    continue
                if not isinstance(meta, dict):
                    continue

                mtype = meta.get("type")
                wid = str(meta.get("window_id") or "default")
                if mtype == "display":
                    html_str = bin_raw.decode("utf-8", errors="replace") if bin_raw else (meta.get("html") or "")
                    title = meta.get("title", "ECHO Sandbox Monitor")
                    width = meta.get("width", "100%")
                    height = meta.get("height", "400px")
                    ts = time.time()
                    with self.lock:
                        self.frames[wid] = {
                            "id": wid,
                            "title": title,
                            "width": width,
                            "height": height,
                            "html": html_str,
                            "mode": "srcdoc",
                            "window_id": wid,
                            "updated_at": ts,
                        }
                        frame_obj = dict(self.frames[wid])
                    self._touch_activity(ts)
                    self.ready_or_display_event.set()
                    if self.is_background and self.callback_token:
                        _notify_webui_sandbox_event(
                            self.callback_token,
                            {
                                "event": "frame_upsert",
                                "user_id": self.user_id,
                                "chat_id": self.chat_id,
                                "run_id": self.run_id,
                                "frame": frame_obj,
                            },
                        )
                elif mtype == "expose_app":
                    raw_target = str(meta.get("target") or "").strip()
                    resolved_target = None
                    if raw_target.startswith("uds:"):
                        raw_path = raw_target[4:].strip()
                        sock_name = os.path.basename(raw_path)
                        if sock_name and sock_name not in (".", ".."):
                            safe_uds_path = os.path.join(self.app_sock_dir, sock_name)
                            resolved_target = ("uds", safe_uds_path)
                    elif raw_target.startswith("tcp:"):
                        try:
                            port = int(raw_target[4:].strip())
                        except (TypeError, ValueError):
                            port = 0
                        if 1024 <= port <= 65535 and port != 5000:
                            resolved_target = ("tcp", port)
                    if resolved_target is None:
                        continue
                    title = meta.get("title", "Application Sandbox")
                    width = meta.get("width", "100%")
                    height = meta.get("height", "600px")
                    ts = time.time()
                    with self.lock:
                        self.app_targets[wid] = resolved_target
                        self.frames[wid] = {
                            "id": wid,
                            "title": title,
                            "width": width,
                            "height": height,
                            "html": "",
                            "mode": "app_proxy",
                            "window_id": wid,
                            "updated_at": ts,
                        }
                        frame_obj = dict(self.frames[wid])
                    self._touch_activity(ts)
                    self.ready_or_display_event.set()
                    if self.is_background and self.callback_token:
                        _notify_webui_sandbox_event(
                            self.callback_token,
                            {
                                "event": "frame_upsert",
                                "user_id": self.user_id,
                                "chat_id": self.chat_id,
                                "run_id": self.run_id,
                                "frame": frame_obj,
                            },
                        )
        except OSError:
            pass
        finally:
            with self.write_lock:
                if self._active_client is conn:
                    self._active_client = None
            try:
                conn.close()
            except OSError:
                pass

    def get_frames_list(self):
        with self.lock:
            return list(self.frames.values())

    def mark_job_finished(self, job_state, exit_code):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            self._job_state = job_state
            self._exit_code = exit_code
            window_ids = list(self.frames.keys())
        self.ready_or_display_event.set()
        if self.callback_token and (self.is_background or window_ids):
            _notify_webui_sandbox_event(
                self.callback_token,
                {
                    "event": "run_finished",
                    "user_id": self.user_id,
                    "chat_id": self.chat_id,
                    "run_id": self.run_id,
                    "state": job_state,
                    "exit_code": exit_code,
                    "window_ids": window_ids,
                },
            )

        with self.write_lock:
            if self._active_client is not None:
                try:
                    self._active_client.close()
                except OSError:
                    pass
                self._active_client = None
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        shutil.rmtree(self.ipc_dir, ignore_errors=True)


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
    hub = job.get("canvas_hub")
    if not job.get("background"):
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            with job["lock"]:
                if job["state"] == "running":
                    job["state"] = "timeout"
                    job["error_msg"] = f"Timeout ({timeout_sec}s) dépassé."
    else:
        while True:
            try:
                proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                now = time.time()
                should_kill = False
                with job["lock"]:
                    if job["state"] != "running":
                        break
                    if now - job["last_activity_at"] >= job["idle_ttl_sec"]:
                        job["state"] = "timeout"
                        job["error_msg"] = f"Serveur arrêté après inactivité ({int(job['idle_ttl_sec'])}s)."
                        should_kill = True
                    elif now - job["started_at"] >= job["max_lifetime_sec"]:
                        job["state"] = "timeout"
                        job["error_msg"] = f"Durée maximale serveur dépassée ({int(job['max_lifetime_sec'])}s)."
                        should_kill = True

                if should_kill:
                    _kill_process_group(proc)
                    break

    for t in (job.get("t_out"), job.get("t_err")):
        if t:
            t.join(timeout=2.0)

    rc = proc.poll()

    with job["lock"]:
        job["exit_code"] = rc
        job["ended_at"] = time.time()
        if job["state"] == "running":
            stderr_str = "".join(job["stderr_buf"])
            if stderr_str or (rc is not None and rc != 0):
                job["state"] = "error"
            else:
                job["state"] = "completed"
        final_state = job["state"]

    if hub is not None:
        hub.mark_job_finished(final_state, rc)

    job["done_event"].set()


def _build_job_response(job):
    hub = job.get("canvas_hub")
    canvas_frames = hub.get_frames_list() if hub is not None else []
    ui_ready = bool(canvas_frames)

    with job["lock"]:
        stdout_str = "".join(job["stdout_buf"])
        stderr_str = "".join(job["stderr_buf"])
        state = job["state"]
        rc = job["exit_code"]
        err_msg = job.get("error_msg") or ""

    if state == "running":
        return {
            "status": "running",
            "state": "running",
            "run_id": job["run_id"],
            "pid": job["pid"],
            "file_path": job["file_path"],
            "output": stdout_str,
            "canvas_frames": canvas_frames,
            "ui_ready": ui_ready,
        }

    status_val = "success" if state == "completed" else ("killed" if state == "killed" else "error")
    res = {
        "status": status_val,
        "state": state,
        "run_id": job["run_id"],
        "pid": job["pid"],
        "exit_code": rc,
        "output": stdout_str,
        "canvas_frames": canvas_frames,
        "ui_ready": ui_ready,
    }
    if stderr_str:
        res["error"] = stderr_str if not err_msg else f"{err_msg}\n{stderr_str}"
    elif err_msg:
        res["error"] = err_msg
    elif rc is not None and rc != 0 and state != "killed":
        res["error"] = f"Process exited with code {rc}"
    return res


_BWRAP_PASSWD_PATH = os.path.join(tempfile.gettempdir(), "echo_bwrap_passwd")
_BWRAP_GROUP_PATH = os.path.join(tempfile.gettempdir(), "echo_bwrap_group")


def _ensure_bwrap_identity_files() -> None:
    """Crée les fichiers passwd/group minimaux pour résoudre uid=65534 (nobody) dans bwrap."""
    if not os.path.exists(_BWRAP_PASSWD_PATH):
        with open(_BWRAP_PASSWD_PATH, "w", encoding="utf-8") as f:
            f.write("root:x:0:0:root:/root:/bin/bash\n")
            f.write("nobody:x:65534:65534:ECHO Sandbox:/sandbox:/bin/bash\n")
    if not os.path.exists(_BWRAP_GROUP_PATH):
        with open(_BWRAP_GROUP_PATH, "w", encoding="utf-8") as f:
            f.write("root:x:0:\n")
            f.write("nogroup:x:65534:\n")


def _build_base_bwrap_cmd(
    safe_uid: str,
    safe_cid: str,
    sandbox_dir: str,
    sandbox_tmp: str,
    files_dir: str | None = None,
    global_files_dir: str | None = None,
    deps_dir: str | None = None,
    browserviews_dir: str | None = None,
    workdir_subpath: str = "",
) -> list[str]:
    """Construit la base immuable des arguments bubblewrap partagée entre batch et PTY."""
    _ensure_bwrap_identity_files()
    chdir_target = f"/sandbox/{workdir_subpath}" if workdir_subpath else "/sandbox"
    cmd = [
        "bwrap",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/bin", "/bin",
        "--dev", "/dev",
        "--ro-bind", "/proc", "/proc",
        "--ro-bind-try", "/lib64", "/lib64",
        "--ro-bind-try", _BWRAP_PASSWD_PATH, "/etc/passwd",
        "--ro-bind-try", _BWRAP_GROUP_PATH, "/etc/group",
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
        "--chdir", chdir_target,
    ]
    if files_dir:
        cmd.extend(["--ro-bind-try", files_dir, "/files"])
    if browserviews_dir:
        cmd.extend(["--ro-bind-try", browserviews_dir, "/browserviews"])
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
    browserviews_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/browserviews" if sandbox_dir else None
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
        browserviews_dir=browserviews_dir,
    )

    python_paths = ["/echo_libs_sandbox"] + (["/.deps/python"] if deps_dir else [])
    node_paths = (["/.deps/node/node_modules"] if deps_dir else []) + ["/echo_libs_sandbox", "/usr/lib/node_modules"]
    default_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    extra_bins = "/.deps/python/bin:/.deps/node/.bin"

    cmd.extend([
        "--setenv", "HOME", "/sandbox",
        "--setenv", "TERM", "xterm-256color",
        "--setenv", "PS1", "sandbox:\\w\\$ ",
        "--setenv", "PATH", f"{extra_bins}:{default_path}",
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
    timeout_sec, max_output_length,
    browserviews_dir: str | None = None,
    background: bool = False,
    idle_ttl_sec: float = DEFAULT_BG_IDLE_TTL_SEC,
    max_lifetime_sec: float = DEFAULT_BG_MAX_LIFETIME_SEC,
    callback_token: str = "",
    sub_sid: str = "",
):
    if not sandbox_dir:
        return None, {"status": "critical_error", "error": "Espace d'exécution non défini."}

    os.makedirs(sandbox_dir, exist_ok=True)
    safe_sub_sid = _sanitize_id(sub_sid, default="") if sub_sid else ""
    if safe_sub_sid:
        os.makedirs(os.path.join(sandbox_dir, safe_sub_sid), exist_ok=True)
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
    is_shell = file_path.endswith(".sh")
    if not (is_python or is_node or is_shell):
        return None, {"status": "error", "error": "Extension non supportée. Seuls .py, .js et .sh sont acceptés."}

    if is_python:
        lint_cmd = ["python", "-m", "py_compile", abs_file_path]
    elif is_node:
        lint_cmd = ["node", "-c", abs_file_path]
    else:
        lint_cmd = ["/bin/bash", "-n", abs_file_path]
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

    hub = CanvasRunHub(
        run_id,
        user_id=safe_uid,
        chat_id=safe_cid,
        is_background=background,
        callback_token=callback_token,
    )
    hub.start()

    bwrap_cmd = _build_base_bwrap_cmd(
        safe_uid=safe_uid,
        safe_cid=safe_cid,
        sandbox_dir=sandbox_dir,
        sandbox_tmp=sandbox_tmp,
        files_dir=files_dir,
        global_files_dir=global_files_dir,
        deps_dir=deps_dir,
        browserviews_dir=browserviews_dir,
        workdir_subpath=safe_sub_sid,
    )
    if hub.is_unix_sock and hub.sock_path:
        bwrap_cmd.extend([
            "--dir", "/run",
            "--bind", hub.sock_path, "/run/echo_ipc.sock",
            "--dir", "/run/echo_app",
            "--bind", hub.app_sock_dir, "/run/echo_app",
        ])
    default_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    extra_bins = "/.deps/python/bin:/.deps/node/.bin"
    bwrap_cmd.extend([
        "--setenv", "PYTHONUNBUFFERED", "1",
        "--setenv", "PATH", f"{extra_bins}:{default_path}",
        "--setenv", "ECHO_RUN_ID", str(run_id),
        "--setenv", "ECHO_IPC_SOCK", hub.sandbox_sock_env,
        "--setenv", "ECHO_APP_UDS", "/run/echo_app/http.sock",
        "--setenv", "ECHO_APP_PORT", str(hub.allocated_port),
    ])

    if is_python:
        python_paths = ["/echo_libs_sandbox"] + (["/.deps/python"] if deps_dir else [])
        bwrap_cmd.extend(["--setenv", "PYTHONPATH", ":".join(python_paths), "python", f"/sandbox/{file_path}"])
    elif is_node:
        node_paths = (
            (["/.deps/node/node_modules"] if deps_dir else []) + ["/echo_libs_sandbox", "/usr/lib/node_modules"]
        )
        bwrap_cmd.extend(["--setenv", "NODE_PATH", ":".join(node_paths), "node", f"/sandbox/{file_path}"])
    else:
        bwrap_cmd.extend(["/bin/bash", f"/sandbox/{file_path}"])

    with _JOBS_LOCK:
        _prune_finished_jobs_unlocked(safe_uid, safe_cid)
        prefix = f"{safe_uid}:{safe_cid}:"
        active_chat = sum(1 for k, j in _JOBS_REGISTRY.items() if k.startswith(prefix) and j["state"] == "running")
        active_global = sum(1 for j in _JOBS_REGISTRY.values() if j["state"] == "running")
        if active_chat >= MAX_CONCURRENT_JOBS_PER_CHAT:
            hub.mark_job_finished("error", None)
            return None, {
                "status": "error",
                "error": f"Quota dépassé : {active_chat}/{MAX_CONCURRENT_JOBS_PER_CHAT} processus actifs session.",
            }
        if active_global >= MAX_CONCURRENT_JOBS_GLOBAL:
            hub.mark_job_finished("error", None)
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
        hub.job_pgid = proc.pid
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
            "background": bool(background),
            "callback_token": str(callback_token or "").strip(),
            "idle_ttl_sec": max(30.0, float(idle_ttl_sec)),
            "max_lifetime_sec": max(60.0, float(max_lifetime_sec)),
            "last_activity_at": time.time(),
            "exit_code": None,
            "error_msg": "",
            "stdout_buf": stdout_buf,
            "stderr_buf": stderr_buf,
            "canvas_hub": hub,
            "sandbox_dir": sandbox_dir,
            "lock": job_lock,
            "done_event": threading.Event(),
            "t_out": t_out,
            "t_err": t_err,
        }
        hub.attach_job(job)
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

            def _attach_ctty():
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            self.proc = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=_attach_ctty,
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

def _lookup_job(safe_uid: str, safe_cid: str, run_id: str = "", prune: bool = False):
    """Recherche centralisée d'un job par (user_id, chat_id, run_id) ou liste des jobs de la session si run_id vide."""
    with _JOBS_LOCK:
        if prune and safe_uid and safe_cid:
            _prune_finished_jobs_unlocked(safe_uid, safe_cid)
        if not run_id:
            prefix = f"{safe_uid}:{safe_cid}:"
            return [j for k, j in _JOBS_REGISTRY.items() if k.startswith(prefix)]
        if safe_uid and safe_cid:
            return _JOBS_REGISTRY.get(f"{safe_uid}:{safe_cid}:{run_id}")
        for candidate in _JOBS_REGISTRY.values():
            if candidate["run_id"] != run_id:
                continue
            if safe_uid and candidate["user_id"] != safe_uid:
                continue
            if safe_cid and candidate["chat_id"] != safe_cid:
                continue
            return candidate
    return None


@app.route('/execute', methods=['POST'])
def execute_code():
    data = request.json or {}
    file_path = data.get('file_path', '')
    dependencies = data.get('dependencies') or []
    callback_token = str(data.get("callback_token") or "").strip()
    timeout = int(data.get('timeout', 30))
    max_output_length = int(data.get('max_output_length', 131072))
    background = bool(data.get('background', False))
    try:
        idle_ttl_sec = float(data.get('idle_ttl_sec', DEFAULT_BG_IDLE_TTL_SEC))
    except (TypeError, ValueError):
        idle_ttl_sec = DEFAULT_BG_IDLE_TTL_SEC
    try:
        max_lifetime_sec = float(data.get('max_lifetime_sec', DEFAULT_BG_MAX_LIFETIME_SEC))
    except (TypeError, ValueError):
        max_lifetime_sec = DEFAULT_BG_MAX_LIFETIME_SEC
    run_id = _sanitize_id(data.get('run_id') or f"run_{uuid.uuid4().hex[:8]}")
    safe_uid = _sanitize_id(data.get('user_id', 'system'))
    safe_cid = _sanitize_id(data.get('chat_id'), default="")
    safe_sub_sid = _sanitize_id(data.get("sub_sid"), default="") if data.get("sub_sid") else ""

    logger.info(
        f"🚀 Execution START | User: {safe_uid} | Chat: {safe_cid} | "
        f"Run: {run_id} | File: {file_path} | Bg: {background} | Timeout: {timeout}s"
    )

    sandbox_dir = _get_sandbox_dir(safe_uid, safe_cid)
    files_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/files" if sandbox_dir else None
    browserviews_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/browserviews" if sandbox_dir else None
    global_files_dir = f"/app/backend/data/users/{safe_uid}/files" if sandbox_dir else None
    deps_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/dependencies" if sandbox_dir else None

    job, err = _start_sandbox_job(
        safe_uid, safe_cid, run_id, file_path, dependencies,
        sandbox_dir, files_dir, global_files_dir, deps_dir,
        timeout, max_output_length,
        browserviews_dir=browserviews_dir,
        background=background,
        idle_ttl_sec=idle_ttl_sec,
        max_lifetime_sec=max_lifetime_sec,
        callback_token=callback_token,
        sub_sid=safe_sub_sid,
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

    if not run_id:
        jobs = _lookup_job(safe_uid, safe_cid, "", prune=True)
        jobs_summary = []
        for j in jobs:
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
    job = _lookup_job(safe_uid, safe_cid, run_id, prune=True)

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
        hub = j.get("canvas_hub")
        if hub is not None:
            hub.mark_job_finished(j["state"], j["exit_code"])

    if killed_pids:
        return jsonify({"status": "killed", "pid": killed_pids[0], "pids": killed_pids, "run_ids": killed_runs})
    return jsonify({"status": "not_found"})


@app.route('/wait_ready', methods=['GET'])
def wait_ready():
    safe_uid = _sanitize_id(request.args.get('user_id', 'system'))
    safe_cid = _sanitize_id(request.args.get('chat_id'), default="")
    safe_rid = _sanitize_id(request.args.get('run_id'), default="")
    try:
        wait_sec = max(0.0, min(float(request.args.get('timeout', 8.0)), 15.0))
    except (TypeError, ValueError):
        wait_sec = 8.0

    job = _lookup_job(safe_uid, safe_cid, safe_rid) if safe_rid else None

    if job is None:
        return jsonify({
            "status": "not_found",
            "job_state": "not_found",
            "exit_code": None,
            "alive": False,
            "ui_ready": False,
            "canvas_frames": [],
        })

    hub = job.get("canvas_hub")
    with job["lock"]:
        is_running = (job["state"] == "running")
    if is_running and hub is not None and wait_sec > 0.0:
        hub.ready_or_display_event.wait(timeout=wait_sec)

    with job["lock"]:
        job_state = job["state"]
        exit_code = job["exit_code"]

    frames = hub.get_frames_list() if hub is not None else []
    return jsonify({
        "status": "ok",
        "job_state": job_state,
        "exit_code": exit_code,
        "alive": job_state == "running",
        "ui_ready": bool(frames),
        "canvas_frames": frames,
    })


@app.route('/canvas_signal', methods=['POST'])
def canvas_signal():
    data = request.get_json(silent=True) or {}
    safe_uid = _sanitize_id(data.get('user_id', 'system'))
    safe_cid = _sanitize_id(data.get('chat_id'), default="")
    safe_rid = _sanitize_id(data.get('run_id'), default="")
    raw_closed = data.get('window_closed')
    try:
        grace = max(0.0, float(data.get('grace_sec', 3.0)))
    except (TypeError, ValueError):
        grace = 3.0

    job = _lookup_job(safe_uid, safe_cid, safe_rid) if safe_rid else None

    if job is None:
        return jsonify({"status": "not_found", "run_id": safe_rid})

    hub = job.get("canvas_hub")
    if hub is not None:
        with job["lock"]:
            job["last_activity_at"] = time.time()

        if raw_closed:
            wids = [raw_closed] if isinstance(raw_closed, str) else [str(w) for w in raw_closed if w]
            if wids:
                hub.close_windows(wids)
                with hub.lock:
                    known_wids = set(hub.frames.keys())
                    all_closed = (len(known_wids) == 0) or known_wids.issubset(hub.closed_windows)
                if all_closed:
                    def _grace_killer(target_job, wait_grace):
                        target_job["done_event"].wait(timeout=min(3.0, wait_grace))
                        with target_job["lock"]:
                            still_running = (target_job["state"] == "running")
                            if still_running:
                                target_job["state"] = "killed"
                                target_job["error_msg"] = "Fenêtres Canvas fermées par l'utilisateur."
                        if still_running:
                            _kill_process_group(target_job["proc"])

                    threading.Thread(
                        target=_grace_killer,
                        args=(job, grace),
                        daemon=True,
                        name=f"canvas-grace-{safe_rid}",
                    ).start()

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
    req_json = request.get_json(silent=True) or {}
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


def _normalize_ssh_private_key(raw_key: str) -> str:
    """Normalise une clé privée PEM/OpenSSH (CRLF, \\n littéraux et découpage 64 car./ligne)."""
    cleaned = raw_key.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n").strip()
    match = re.match(
        r"^(-----BEGIN [A-Z0-9 ]+-----)\s*(.*?)\s*(-----END [A-Z0-9 ]+-----)$",
        cleaned,
        re.DOTALL,
    )
    if not match:
        return cleaned + "\n"
    header, body, footer = match.groups()
    body_compact = re.sub(r"\s+", "", body)
    wrapped = "\n".join(body_compact[i:i + 64] for i in range(0, len(body_compact), 64))
    return f"{header}\n{wrapped}\n{footer}\n"


@app.route('/ssh', methods=['POST'])
def execute_ssh():
    """Exécute une commande SSH isolée dans la sandbox bwrap."""
    data = request.get_json(silent=True) or {}
    host = str(data.get("host") or "").strip()
    user = str(data.get("user") or "").strip()
    command = str(data.get("command") or "").strip()
    private_key = str(data.get("private_key") or "")
    password = str(data.get("password") or "")

    if not host or not user or not command:
        return jsonify({"status": "error", "error": "Champs requis : host, user, command."}), 400

    safe_ident_re = re.compile(r"^[A-Za-z0-9._:@-]+$")
    if (
        host.startswith("-") or user.startswith("-")
        or not safe_ident_re.match(host) or not safe_ident_re.match(user)
    ):
        return jsonify({"status": "error", "error": "Paramètre host ou user invalide."}), 400

    try:
        port = int(data.get("port", 22))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "Port SSH invalide."}), 400
    if not (1 <= port <= 65535):
        return jsonify({"status": "error", "error": "Port SSH hors plage (1-65535)."}), 400

    try:
        timeout = max(5, min(int(data.get("timeout", 60)), 900))
    except (TypeError, ValueError):
        timeout = 60

    try:
        max_out = max(1000, int(data.get("max_output_length", 131072)))
    except (TypeError, ValueError):
        max_out = 131072

    safe_uid = _sanitize_id(data.get("user_id", "system"))
    safe_cid = _sanitize_id(data.get("chat_id"), default="")
    sandbox_dir = _get_sandbox_dir(safe_uid, safe_cid)
    files_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/files" if safe_cid else None
    global_files_dir = f"/app/backend/data/users/{safe_uid}/files" if safe_cid else None
    browserviews_dir = f"/app/backend/data/users/{safe_uid}/chats/{safe_cid}/browserviews" if safe_cid else None

    ssh_tmp_dir = tempfile.mkdtemp(prefix="echo_ssh_")
    try:
        os.chmod(ssh_tmp_dir, 0o755)
        if not sandbox_dir:
            sandbox_dir = os.path.join(ssh_tmp_dir, "sandbox")
        os.makedirs(sandbox_dir, exist_ok=True)
        sandbox_tmp = os.path.join(sandbox_dir, ".tmp")
        os.makedirs(sandbox_tmp, exist_ok=True)

        bwrap_cmd = _build_base_bwrap_cmd(
            safe_uid=safe_uid,
            safe_cid=safe_cid,
            sandbox_dir=sandbox_dir,
            sandbox_tmp=sandbox_tmp,
            files_dir=files_dir,
            global_files_dir=global_files_dir,
            browserviews_dir=browserviews_dir,
        )
        bwrap_cmd.extend(["--setenv", "HOME", "/sandbox"])

        ssh_args = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=/sandbox/.ssh_known_hosts",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-p", str(port),
        ]

        if private_key.strip():
            key_file = os.path.join(ssh_tmp_dir, "id_key")
            with open(key_file, "w", encoding="utf-8") as kf:
                kf.write(_normalize_ssh_private_key(private_key))
            os.chmod(key_file, 0o600)
            bwrap_cmd.extend(["--ro-bind-try", ssh_tmp_dir, "/ssh_auth"])
            ssh_args.extend(["-i", "/ssh_auth/id_key", "-o", "IdentitiesOnly=yes"])
            if password:
                bwrap_cmd.extend(["--setenv", "SSHPASS", password])
                ssh_args = ["sshpass", "-e", "-P", "assphrase"] + ssh_args
            else:
                ssh_args.extend(["-o", "BatchMode=yes"])
        elif password:
            bwrap_cmd.extend(["--setenv", "SSHPASS", password])
            ssh_args.extend([
                "-o", "PreferredAuthentications=password,keyboard-interactive",
                "-o", "PubkeyAuthentication=no",
            ])
            ssh_args = ["sshpass", "-e"] + ssh_args
        else:
            ssh_args.extend(["-o", "BatchMode=yes"])

        ssh_args.extend([f"{user}@{host}", command])
        bwrap_cmd.extend(ssh_args)

        proc = subprocess.run(bwrap_cmd, capture_output=True, text=True, timeout=timeout)
        stdout_str = (proc.stdout or "")[:max_out]
        stderr_str = (proc.stderr or "")[:max_out]
        return jsonify({
            "status": "success" if proc.returncode == 0 else "error",
            "exit_code": proc.returncode,
            "output": stdout_str,
            "error": stderr_str,
        })
    except subprocess.TimeoutExpired as te:
        out_partial = (
            te.stdout.decode("utf-8", errors="replace")
            if isinstance(te.stdout, bytes)
            else (te.stdout or "")
        )
        return jsonify({
            "status": "timeout",
            "exit_code": -1,
            "output": out_partial[:max_out],
            "error": f"Timeout SSH dépassé ({timeout}s).",
        })
    except Exception as e:
        return jsonify({"status": "error", "error": f"Erreur exécution SSH : {str(e)}"}), 500
    finally:
        shutil.rmtree(ssh_tmp_dir, ignore_errors=True)


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

    init_data = {}
    try:
        init_raw = await websocket.receive_text()
        parsed_init = json.loads(init_raw)
        if isinstance(parsed_init, dict):
            init_data = parsed_init
            if init_data.get("chat_id"):
                session.chat_id = init_data["chat_id"]
    except Exception:
        pass

    # Confirmer la liaison au chat
    await websocket.send_text(json.dumps({"type": "chat", "chat_id": session.chat_id}))

    # Envoyer le buffer accumulé si présent (sans doublon avec out_queue)
    with session.lock:
        buffered = bytes(session.buffer)
        curr_cursor = session.cursor
        is_trunc = session.truncated
        while not session.out_queue.empty():
            try:
                session.out_queue.get_nowait()
            except queue.Empty:
                break

    try:
        client_cursor = int(init_data.get("cursor") or 0) if init_data.get("resume") else 0
    except (TypeError, ValueError):
        client_cursor = 0

    if client_cursor > 0 and curr_cursor >= client_cursor:
        delta = curr_cursor - client_cursor
        truncated_for_client = delta > len(buffered)
        to_send = buffered[-delta:] if delta > 0 else b""
    else:
        truncated_for_client = is_trunc
        to_send = buffered

    if to_send:
        await websocket.send_text(
            json.dumps({"type": "output", "end": curr_cursor, "truncated": truncated_for_client})
        )
        await websocket.send_bytes(to_send)

    stop_event = asyncio.Event()

    async def _reader():
        loop = asyncio.get_running_loop()
        while not stop_event.is_set():
            def get_chunk():
                try:
                    return session.out_queue.get(timeout=0.1)
                except queue.Empty:
                    return None
            chunk = await loop.run_in_executor(None, get_chunk)
            if chunk == b"":
                break
            if chunk is None:
                if not session.is_alive() and session.out_queue.empty():
                    break
                continue
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
        if not session.is_alive():
            try:
                await websocket.close(code=1000)
            except Exception:
                pass


def _list_listening_inodes_for_port(port: int) -> set[str]:
    inodes: set[str] = set()
    for proc_net in ("/proc/net/tcp", "/proc/net/tcp6"):
        if not os.path.exists(proc_net):
            continue
        try:
            with open(proc_net, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue
                local_addr = parts[1]
                state = parts[3].upper()
                inode = parts[9]
                if state != "0A" or inode == "0" or ":" not in local_addr:
                    continue
                try:
                    l_port = int(local_addr.rsplit(":", 1)[1], 16)
                except ValueError:
                    continue
                if l_port == port:
                    inodes.add(inode)
        except OSError:
            continue
    return inodes


def _verify_job_owns_tcp_port(hub, port: int) -> bool:
    if not isinstance(port, int) or port < 1024 or port > 65535 or port == 5000:
        return False
    if os.path.exists("/proc/net/tcp"):
        listening_inodes = _list_listening_inodes_for_port(port)
        if not listening_inodes:
            return False
        with hub.lock:
            for inode in listening_inodes:
                if (port, inode) in hub._verified_tcp_inodes:
                    return True
        target_pgid = getattr(hub, "job_pgid", None)
        if target_pgid is None:
            return False
        expected_targets = {f"socket:[{inode}]" for inode in listening_inodes}
        try:
            proc_entries = os.listdir("/proc")
        except OSError:
            return False
        for pid_str in proc_entries:
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            try:
                if os.getpgid(pid) != target_pgid:
                    continue
                fd_dir = f"/proc/{pid}/fd"
                for fd_name in os.listdir(fd_dir):
                    try:
                        link_target = os.readlink(os.path.join(fd_dir, fd_name))
                    except OSError:
                        continue
                    if link_target in expected_targets:
                        inode = link_target[8:-1]
                        with hub.lock:
                            hub._verified_tcp_inodes.add((port, inode))
                        return True
            except (OSError, ValueError, ProcessLookupError):
                continue
        return False
    return hub.allocated_port is not None and port == hub.allocated_port


def _find_job_for_proxy(run_id: str, user_id: str | None = None, chat_id: str | None = None):
    safe_rid = _sanitize_id(run_id, default="")
    safe_uid = _sanitize_id(user_id) if user_id else ""
    safe_cid = _sanitize_id(chat_id, default="") if chat_id else ""
    if not safe_rid:
        return None
    return _lookup_job(safe_uid, safe_cid, safe_rid)


def _resolve_hub_app_target(hub, window_id: str | None):
    with hub.lock:
        if window_id:
            target = hub.app_targets.get(window_id)
        elif "default" in hub.app_targets:
            target = hub.app_targets["default"]
        elif hub.app_targets:
            target = next(iter(hub.app_targets.values()))
        else:
            target = None
    if target is None:
        return None, ("App target not registered", 404)
    kind, val = target
    if kind == "tcp" and not _verify_job_owns_tcp_port(hub, val):
        return None, ("Forbidden TCP port", 403)
    return target, None


async def app_http_proxy_endpoint(request):
    """Proxifie en streaming les requêtes HTTP vers le serveur applicatif UDS/TCP du sandbox."""
    run_id = request.path_params.get("run_id", "")
    wid = request.query_params.get("__echo_wid")
    job = _find_job_for_proxy(
        run_id,
        request.query_params.get("user_id"),
        request.query_params.get("chat_id"),
    )
    if job is None or job.get("canvas_hub") is None:
        return Response("Sandbox run not found", status_code=404)

    hub = job["canvas_hub"]
    target, err = _resolve_hub_app_target(hub, wid)
    if err is not None:
        return Response(err[0], status_code=err[1])

    with job["lock"]:
        job["last_activity_at"] = time.time()

    kind, val = target
    rel_path = (request.path_params.get("path") or "").lstrip("/")
    query_items = [(k, v) for k, v in request.query_params.multi_items() if k != "__echo_wid"]
    query_str = urlencode(query_items)
    base_url = "http://localhost" if kind == "uds" else f"http://127.0.0.1:{val}"
    target_url = f"{base_url}/{rel_path}" + (f"?{query_str}" if query_str else "")

    req_hop_by_hop = {
        "host", "connection", "keep-alive", "transfer-encoding",
        "te", "trailers", "upgrade", "proxy-authenticate",
        "proxy-authorization", "proxy-connection", "content-length",
    }
    fwd_headers = [(k, v) for k, v in request.headers.items() if k.lower() not in req_hop_by_hop]
    body = await request.body()

    accept_hdr = (request.headers.get("accept") or "").lower()
    wants_sse = "text/event-stream" in accept_hdr
    proxy_timeout = httpx.Timeout(connect=10.0, write=30.0, read=None if wants_sse else 300.0, pool=10.0)
    transport = httpx.AsyncHTTPTransport(uds=val) if kind == "uds" else None
    client = httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=proxy_timeout)
    try:
        upstream_req = client.build_request(
            method=request.method,
            url=target_url,
            headers=fwd_headers,
            content=body if body else None,
        )
        upstream_resp = await client.send(upstream_req, stream=True)
    except Exception as exc:
        await client.aclose()
        return Response(f"Upstream app error: {exc}", status_code=502)

    resp_hop_by_hop = {
        "transfer-encoding", "connection", "keep-alive",
        "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
    }
    resp_headers = [
        (k, v) for k, v in upstream_resp.headers.multi_items()
        if k.lower() not in resp_hop_by_hop
    ]
    upstream_ct = (upstream_resp.headers.get("content-type") or "").lower()
    is_sse = wants_sse or ("text/event-stream" in upstream_ct)

    async def _stream_body():
        try:
            async for chunk in upstream_resp.aiter_raw():
                if chunk:
                    with job["lock"]:
                        job["last_activity_at"] = time.time()
                    yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    resp = StreamingResponse(_stream_body(), status_code=upstream_resp.status_code)
    for k, v in resp_headers:
        resp.headers.append(k, v)
    if is_sse and not any(k.lower() == "x-accel-buffering" for k, _ in resp_headers):
        resp.headers["X-Accel-Buffering"] = "no"
    return resp


async def _relay_via_websockets(websocket: WebSocket, job: dict, kind: str, val, rel_path: str, query_str: str):
    suffix = f"/{rel_path}" if rel_path else "/"
    qs = f"?{query_str}" if query_str else ""
    if kind == "uds":
        unix_conn = getattr(websockets, "unix_connect", None)
        if unix_conn is None:
            ws_asyncio = getattr(websockets, "asyncio", None)
            ws_client = getattr(ws_asyncio, "client", None) if ws_asyncio else None
            unix_conn = getattr(ws_client, "unix_connect", None) if ws_client else None
        if unix_conn is None:
            raise RuntimeError("websockets.unix_connect unavailable")
        conn_ctx = unix_conn(val, f"ws://localhost{suffix}{qs}")
    else:
        conn_ctx = websockets.connect(f"ws://127.0.0.1:{val}{suffix}{qs}")

    async with conn_ctx as upstream:
        stop_event = asyncio.Event()

        async def _c2u():
            try:
                while not stop_event.is_set():
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    with job["lock"]:
                        job["last_activity_at"] = time.time()
                    if "text" in msg and msg["text"] is not None:
                        await upstream.send(msg["text"])
                    elif "bytes" in msg and msg["bytes"] is not None:
                        await upstream.send(msg["bytes"])
            except (WebSocketDisconnect, _WsConnectionClosed, asyncio.CancelledError):
                pass
            finally:
                stop_event.set()

        async def _u2c():
            try:
                while not stop_event.is_set():
                    data = await upstream.recv()
                    with job["lock"]:
                        job["last_activity_at"] = time.time()
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(str(data))
            except (WebSocketDisconnect, _WsConnectionClosed, asyncio.CancelledError):
                pass
            finally:
                stop_event.set()

        async def _touch_loop():
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(60.0)
                    if stop_event.is_set():
                        break
                    with job["lock"]:
                        job["last_activity_at"] = time.time()
            except asyncio.CancelledError:
                pass

        tasks = [
            asyncio.create_task(_c2u()),
            asyncio.create_task(_u2c()),
            asyncio.create_task(_touch_loop()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_event.set()
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass


async def ws_app_proxy_endpoint(websocket: WebSocket):
    """Proxifie en full-duplex (TEXT/BINARY) les WebSockets vers le serveur applicatif sandbox."""
    run_id = websocket.path_params.get("run_id", "")
    wid = websocket.query_params.get("__echo_wid")
    job = _find_job_for_proxy(
        run_id,
        websocket.query_params.get("user_id"),
        websocket.query_params.get("chat_id"),
    )
    await websocket.accept()
    if job is None or job.get("canvas_hub") is None:
        try:
            await websocket.close(code=4404)
        except Exception:
            pass
        return

    hub = job["canvas_hub"]
    target, err = _resolve_hub_app_target(hub, wid)
    if err is not None:
        try:
            await websocket.close(code=4403 if err[1] == 403 else 4404)
        except Exception:
            pass
        return

    kind, val = target
    rel_path = (websocket.path_params.get("path") or "").lstrip("/")
    query_items = [(k, v) for k, v in websocket.query_params.multi_items() if k != "__echo_wid"]
    query_str = urlencode(query_items)

    try:
        if websockets is not None:
            await _relay_via_websockets(websocket, job, kind, val, rel_path, query_str)
        else:
            await websocket.close(code=1011)
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        try:
            await websocket.close(code=1000)
        except Exception:
            pass


asgi_app = Starlette(
    routes=[
        WebSocketRoute("/api/terminals/{session_id}", ws_terminal_endpoint),
        WebSocketRoute("/ws/app/{run_id}", ws_app_proxy_endpoint),
        WebSocketRoute("/ws/app/{run_id}/{path:path}", ws_app_proxy_endpoint),
        Route(
            "/app_proxy/{run_id}",
            app_http_proxy_endpoint,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
        Route(
            "/app_proxy/{run_id}/{path:path}",
            app_http_proxy_endpoint,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
        Mount("/", app=WSGIMiddleware(app)),
    ]
)


if __name__ == '__main__':
    uvicorn.run(asgi_app, host='0.0.0.0', port=5000)
