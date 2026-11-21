# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO - ECHO MONITOR
VERSION : 6.16.0.2
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.16.0.2: Bus AF_UNIX/TCP (ECHO_IPC_SOCK), BidiRouter événementiel et expose_app/get_app_uds/get_app_port/wait_until_closed (Niveau B2).
# 6.15.2.1: Écriture thread-safe (_MONITOR_WRITE_LOCK), push_ready(), pool ThreadPoolExecutor borné, __echo_ping__.
# 5.234.1.1: Isolation IPC par env vars et ajout de push_event, get_ui_requests et BidiRouter.
# 5.226.18.0: Alignement sur le versioning global ECHO.
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import os
import socket
import struct
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Set

_DEFAULT_IPC_SOCK = "/run/echo_ipc.sock"
_HEADER_STRUCT = struct.Struct(">II")

_IPC_WRITE_LOCK = threading.Lock()
_IPC_COND = threading.Condition()

_SOCK: Optional[socket.socket] = None
_CONNECTED_TARGET: Optional[str] = None
_CONN_GEN: int = 0
_SOCKET_CLOSED: bool = False

_UI_PAYLOADS: Dict[str, Any] = {}
_CLOSED_WINDOWS: Set[str] = set()
_RPC_QUEUE: Deque[dict] = deque()


def _get_ipc_target() -> str:
    return os.environ.get("ECHO_IPC_SOCK") or _DEFAULT_IPC_SOCK


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    chunks: List[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _handle_incoming_message(msg: dict, bin_bytes: Optional[bytes]) -> None:
    msg_type = msg.get("type")
    wid = msg.get("window_id") or "default"
    with _IPC_COND:
        if msg_type == "payload_sync":
            payload = msg.get("payload")
            _UI_PAYLOADS[wid] = payload
            if isinstance(payload, dict) and payload.get("_is_closed"):
                _CLOSED_WINDOWS.add(wid)
            _IPC_COND.notify_all()
        elif msg_type == "window_closed":
            _CLOSED_WINDOWS.add(wid)
            existing = _UI_PAYLOADS.get(wid)
            if not isinstance(existing, dict):
                _UI_PAYLOADS[wid] = {"_is_closed": True}
            else:
                existing["_is_closed"] = True
            _IPC_COND.notify_all()
        elif msg_type == "rpc":
            _RPC_QUEUE.append({
                "window_id": wid,
                "req_id": msg.get("req_id"),
                "action": msg.get("action"),
                "payload": msg.get("payload"),
                "binary": bin_bytes,
            })
            _IPC_COND.notify_all()


def _reader_loop(sock: socket.socket, conn_gen: int) -> None:
    try:
        while True:
            header = _recv_exact(sock, _HEADER_STRUCT.size)
            if header is None:
                break
            meta_len, bin_len = _HEADER_STRUCT.unpack(header)
            meta_raw = _recv_exact(sock, meta_len) if meta_len > 0 else b"{}"
            if meta_raw is None:
                break
            bin_raw = _recv_exact(sock, bin_len) if bin_len > 0 else None
            if bin_len > 0 and bin_raw is None:
                break
            try:
                msg = json.loads(meta_raw.decode("utf-8"))
                if isinstance(msg, dict):
                    _handle_incoming_message(msg, bin_raw)
            except Exception:
                pass
    except OSError:
        pass
    finally:
        with _IPC_COND:
            if _CONN_GEN == conn_gen:
                _SOCKET_CLOSED_SET_AND_NOTIFY(sock)


def _SOCKET_CLOSED_SET_AND_NOTIFY(sock: socket.socket) -> None:
    global _SOCK, _SOCKET_CLOSED
    _SOCKET_CLOSED = True
    if _SOCK is sock:
        _SOCK = None
    try:
        sock.close()
    except OSError:
        pass
    _IPC_COND.notify_all()


def _open_socket(target: str) -> socket.socket:
    if target.startswith("tcp://"):
        host_port = target[6:]
        host, port_str = host_port.rsplit(":", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, int(port_str)))
        return sock
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(target)
    return sock


def _ensure_connected() -> socket.socket:
    global _SOCK, _CONNECTED_TARGET, _CONN_GEN, _SOCKET_CLOSED
    target = _get_ipc_target()
    with _IPC_WRITE_LOCK:
        if _SOCK is not None and _CONNECTED_TARGET == target:
            return _SOCK
        if _SOCK is not None:
            try:
                _SOCK.close()
            except OSError:
                pass
            _SOCK = None
        sock = _open_socket(target)
        _SOCK = sock
        _CONNECTED_TARGET = target
        with _IPC_COND:
            _CONN_GEN += 1
            gen = _CONN_GEN
            _SOCKET_CLOSED = False
        reader = threading.Thread(target=_reader_loop, args=(sock, gen), daemon=True, name="echo-ipc-reader")
        reader.start()
        return sock


def _send_ipc_frame(meta: dict, binary: Optional[Any] = None) -> None:
    """
    Émet une frame binaire [uint32 BE meta_len][uint32 BE bin_len][JSON meta][Raw binary]
    sur le bus IPC Socket (AF_UNIX ou TCP).
    """
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    bin_bytes = bytes(binary) if binary is not None else b""
    frame = _HEADER_STRUCT.pack(len(meta_bytes), len(bin_bytes)) + meta_bytes + bin_bytes

    sock = _ensure_connected()
    with _IPC_WRITE_LOCK:
        try:
            sock.sendall(frame)
        except OSError:
            with _IPC_COND:
                _SOCKET_CLOSED_SET_AND_NOTIFY(sock)
            raise


send_ipc_frame = _send_ipc_frame


def display(
    html_content: str,
    title: str = "ECHO Sandbox Monitor",
    width: str = "100%",
    height: str = "400px",
    window_id: str = "default",
) -> None:
    """
    Transmet une interface HTML brute (UTF-8 binaire, zéro Base64) à Open WebUI
    via le bus IPC Socket. Prend en charge le multi-fenêtrage via 'window_id'.
    """
    meta = {
        "type": "display",
        "window_id": window_id,
        "title": title,
        "width": width,
        "height": height,
    }
    _send_ipc_frame(meta, html_content.encode("utf-8"))


def push_event(
    data: Any,
    window_id: str = "default",
    req_id: Optional[str] = None,
    binary: Optional[Any] = None,
) -> None:
    """
    Pousse un événement JSON (avec charge binaire optionnelle) vers l'Iframe
    de la fenêtre 'window_id' sans recharger ni recréer le DOM.
    """
    meta = {
        "type": "event",
        "window_id": window_id,
        "req_id": req_id,
        "data": data,
    }
    _send_ipc_frame(meta, binary)


def push_ready(window_id: str = "default", actions: Optional[List[str]] = None) -> None:
    """
    Émet un événement de disponibilité RPC (ready) pour signaler à l'hôte UI
    que le routeur Bidi de la fenêtre 'window_id' écoute les requêtes.
    """
    meta = {
        "type": "ready",
        "window_id": window_id,
        "actions": list(actions or []),
    }
    _send_ipc_frame(meta, None)


def get_ui_payload(window_id: str = "default") -> Optional[dict]:
    """
    Récupère le payload injecté par le Frontend depuis le bus mémoire IPC.
    Multiplexé : Retourne uniquement les données associées à 'window_id'.
    """
    with _IPC_COND:
        payload = _UI_PAYLOADS.get(window_id)
        return payload if isinstance(payload, dict) else None


def get_ui_requests(window_id: Optional[str] = None) -> List[dict]:
    """
    Dépile de manière FIFO et thread-safe les nouvelles requêtes RPC émises par
    l'Iframe UI depuis le bus mémoire IPC.
    """
    with _IPC_COND:
        if not _RPC_QUEUE:
            return []
        if window_id is None:
            events = list(_RPC_QUEUE)
            _RPC_QUEUE.clear()
            return events
        matched: List[dict] = []
        remaining: Deque[dict] = deque()
        for item in _RPC_QUEUE:
            if (item.get("window_id") or "default") == window_id:
                matched.append(item)
            else:
                remaining.append(item)
        _RPC_QUEUE.clear()
        _RPC_QUEUE.extend(remaining)
        return matched


def _is_window_closed_locked(window_id: str) -> bool:
    if _SOCKET_CLOSED or window_id in _CLOSED_WINDOWS:
        return True
    payload = _UI_PAYLOADS.get(window_id)
    if isinstance(payload, dict) and payload.get("_is_closed"):
        return True
    return False


def is_window_closed(window_id: str = "default") -> bool:
    """
    Indique si l'interface (ECHO Monitor) a été fermée par l'utilisateur
    ou si le canal IPC Socket a atteint EOF.
    """
    with _IPC_COND:
        return _is_window_closed_locked(window_id)


def get_app_uds() -> str:
    """Retourne le chemin du socket Unix destiné au serveur applicatif sandbox."""
    return os.environ.get("ECHO_APP_UDS") or "/run/echo_app/http.sock"


def get_app_port() -> int:
    """Retourne le port TCP de repli attribué au serveur applicatif sandbox."""
    raw = os.environ.get("ECHO_APP_PORT")
    try:
        return int(raw) if raw else 8000
    except ValueError:
        return 8000


def wait_until_closed(window_id: str = "default") -> None:
    """
    Bloque de manière événementielle jusqu'à la fermeture de la fenêtre 'window_id'
    ou la fermeture du canal IPC Socket.
    """
    _ensure_connected()
    with _IPC_COND:
        while not _is_window_closed_locked(window_id):
            _IPC_COND.wait(timeout=0.5)


def _parse_app_target(target: Any) -> Optional[tuple]:
    if target is None:
        return None
    if isinstance(target, int) and not isinstance(target, bool):
        return ("tcp", int(target))
    t_str = str(target).strip()
    if t_str.startswith("tcp:"):
        return ("tcp", int(t_str[4:].strip()))
    if t_str.startswith(("http://127.0.0.1:", "https://127.0.0.1:", "http://localhost:", "https://localhost:")):
        host_part = t_str.split("://", 1)[1].split("/", 1)[0]
        return ("tcp", int(host_part.rsplit(":", 1)[1]))
    if t_str.isdigit():
        return ("tcp", int(t_str))
    if t_str.startswith("uds:"):
        return ("uds", t_str[4:].strip())
    if t_str.endswith(".sock") or "/" in t_str or os.sep in t_str:
        return ("uds", t_str)
    raise ValueError(f"Cible expose_app invalide: {target!r}")


def _probe_tcp(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _probe_uds(uds_path: str) -> bool:
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(uds_path):
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(0.5)
        sock.connect(uds_path)
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def expose_app(
    target: Any = None,
    title: str = "Application Sandbox",
    width: str = "100%",
    height: str = "600px",
    window_id: str = "default",
    health_path: str = "/",
    wait_timeout: float = 15.0,
    block: bool = True,
) -> str:
    """
    Attend que le serveur web local (UDS ou TCP) accepte les connexions,
    le déclare auprès du Hub pour montage dans l'onglet Applications, puis
    bloque optionnellement jusqu'à la fermeture de la fenêtre.
    """
    parsed = _parse_app_target(target)
    deadline = time.monotonic() + max(0.0, float(wait_timeout))
    resolved_target: Optional[str] = None
    while True:
        if parsed is None:
            uds_path = get_app_uds()
            if _probe_uds(uds_path):
                resolved_target = f"uds:{uds_path}"
                break
            port = get_app_port()
            if _probe_tcp(port):
                resolved_target = f"tcp:{port}"
                break
        elif parsed[0] == "uds":
            if _probe_uds(parsed[1]):
                resolved_target = f"uds:{parsed[1]}"
                break
        else:
            if _probe_tcp(parsed[1]):
                resolved_target = f"tcp:{parsed[1]}"
                break

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Serveur sandbox injoignable après {wait_timeout}s "
                f"(ECHO_APP_UDS={get_app_uds()}, ECHO_APP_PORT={get_app_port()}, target={target!r})."
            )
        time.sleep(0.05)

    _send_ipc_frame(
        {
            "type": "expose_app",
            "window_id": window_id,
            "title": title,
            "width": width,
            "height": height,
            "target": resolved_target,
            "health_path": health_path,
        },
        None,
    )
    push_ready(window_id, [])
    if block:
        wait_until_closed(window_id)
    return resolved_target


class BidiRouter:
    """
    Routeur RPC bidirectionnel événementiel permettant d'exposer des routes dans
    la Sandbox et de répondre en temps réel aux requêtes de l'Iframe.
    """

    def __init__(self, window_id: str = "default", max_workers: int = 4):
        self.window_id = window_id
        self.max_workers = max(1, int(max_workers or 4))
        self._routes: Dict[str, Callable[..., Any]] = {}
        self._route_accepts_binary: Dict[str, bool] = {}

    def route(self, action: str):
        def decorator(fn: Callable[..., Any]):
            params = inspect.signature(fn).parameters.values()
            accepts_bin = (
                len(params) >= 2
                or any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            )
            self._routes[action] = fn
            self._route_accepts_binary[action] = accepts_bin
            return fn
        return decorator

    def _execute_request(self, req: dict) -> None:
        action = req.get("action")
        req_id = req.get("req_id")
        payload = req.get("payload")
        try:
            if action in self._routes:
                fn = self._routes[action]
                if self._route_accepts_binary.get(action, False):
                    res = fn(payload, req.get("binary"))
                else:
                    res = fn(payload)
                res_data = res
                res_bytes = None
                if (
                    isinstance(res, tuple)
                    and len(res) == 2
                    and isinstance(res[1], (bytes, bytearray, memoryview))
                ):
                    res_data, res_bytes = res[0], bytes(res[1])
                push_event(
                    {"status": "ok", "action": action, "result": res_data},
                    window_id=self.window_id,
                    req_id=req_id,
                    binary=res_bytes,
                )
            elif action == "__echo_ping__":
                push_event(
                    {
                        "status": "ok",
                        "action": action,
                        "result": {
                            "pong": True,
                            "window_id": self.window_id,
                            "ts": time.time(),
                            "actions": sorted(self._routes.keys()),
                        },
                    },
                    window_id=self.window_id,
                    req_id=req_id,
                )
            else:
                push_event(
                    {
                        "status": "error",
                        "action": action,
                        "error": f"Unknown RPC action: {action}",
                        "available_actions": sorted(self._routes.keys()),
                    },
                    window_id=self.window_id,
                    req_id=req_id,
                )
        except Exception as exc:
            push_event(
                {"status": "error", "action": action, "error": str(exc)},
                window_id=self.window_id,
                req_id=req_id,
            )

    def dispatch_once(self, executor: Optional[ThreadPoolExecutor] = None) -> int:
        reqs = get_ui_requests(self.window_id)
        handled = 0
        for req in reqs:
            if executor is not None:
                executor.submit(self._execute_request, req)
            else:
                self._execute_request(req)
            handled += 1
        return handled

    def serve_forever(self, poll_interval: float = 0.05):
        _ensure_connected()
        push_ready(self.window_id, sorted(self._routes.keys()))
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while True:
                self.dispatch_once(executor=executor)
                with _IPC_COND:
                    if _is_window_closed_locked(self.window_id):
                        break
                    has_pending = any(
                        (item.get("window_id") or "default") == self.window_id
                        for item in _RPC_QUEUE
                    )
                    if not has_pending:
                        _IPC_COND.wait(timeout=0.5)
