# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO - ECHO MONITOR
VERSION : 5.234.1.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.234.1.1: Isolation IPC par variables d'environnement (ECHO_MONITOR_FILE, ECHO_UI_PAYLOAD_FILE, ECHO_UI_INBOX_FILE) et ajout de push_event, get_ui_requests et BidiRouter.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import base64
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

_INBOX_OFFSETS: Dict[str, int] = {}


def _get_monitor_path() -> str:
    filename = os.environ.get("ECHO_MONITOR_FILE", ".echo_monitor.jsonl")
    return filename if os.path.isabs(filename) else f"/sandbox/{filename}"


def _get_payload_path() -> str:
    filename = os.environ.get("ECHO_UI_PAYLOAD_FILE", ".echo_ui_payload.json")
    return filename if os.path.isabs(filename) else f"/sandbox/{filename}"


def _get_inbox_path() -> str:
    filename = os.environ.get("ECHO_UI_INBOX_FILE", ".echo_ui_inbox.jsonl")
    return filename if os.path.isabs(filename) else f"/sandbox/{filename}"


def display(html_content: str, title: str = "ECHO Sandbox Monitor", width: str = "100%", height: str = "400px", window_id: str = "default"):
    """
    Transmet une interface HTML (base64) à Open WebUI via le canal dédié JSONL.
    Prend en charge le multi-fenêtrage via l'identifiant 'window_id'.
    """
    payload_html = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')
    meta = {
        "type": "display",
        "window_id": window_id,
        "title": title,
        "width": width,
        "height": height,
        "html": payload_html
    }

    with open(_get_monitor_path(), 'a', encoding='utf-8') as f:
        f.write(json.dumps(meta) + "\n")


def push_event(data: Any, window_id: str = "default", req_id: Optional[str] = None) -> None:
    """
    Pousse un événement JSON temps réel vers l'Iframe de la fenêtre 'window_id'
    sans recharger ni recréer le DOM de l'Iframe.
    """
    meta = {
        "type": "event",
        "window_id": window_id,
        "req_id": req_id,
        "data": data
    }
    with open(_get_monitor_path(), 'a', encoding='utf-8') as f:
        f.write(json.dumps(meta) + "\n")


def get_ui_payload(window_id: str = "default") -> Optional[dict]:
    """
    Récupère le payload massif (json/base64) injecté par le Frontend.
    Multiplexé : Retourne uniquement les données associées à 'window_id'.
    """
    path = _get_payload_path()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.loads(f.read())
                return data.get(window_id) if isinstance(data, dict) else None
        except Exception:
            pass
    return None


def get_ui_requests(window_id: Optional[str] = None) -> List[dict]:
    """
    Lit de manière incrémentale les nouvelles requêtes RPC émises par l'Iframe UI
    depuis le canal d'entrée JSONL sans perte lors d'appels rapprochés.
    """
    path = _get_inbox_path()
    if not os.path.exists(path):
        return []
    cursor_key = window_id or "__all__"
    offset = _INBOX_OFFSETS.get(cursor_key, 0)
    events: List[dict] = []
    try:
        with open(path, 'rb') as f:
            f.seek(offset)
            content = f.read()
        last_nl = content.rfind(b'\n')
        if last_nl != -1:
            valid = content[:last_nl + 1]
            _INBOX_OFFSETS[cursor_key] = offset + len(valid)
            for line in valid.decode('utf-8', errors='replace').splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        if window_id is None or item.get("window_id", "default") == window_id:
                            events.append(item)
                except Exception:
                    pass
    except Exception:
        pass
    return events


def is_window_closed(window_id: str = "default") -> bool:
    """
    Indique si l'interface (ECHO Monitor) a été fermée par l'utilisateur.
    Utile pour interrompre proprement un script synchrone ou asynchrone.
    """
    payload = get_ui_payload(window_id)
    if payload and isinstance(payload, dict):
        return payload.get("_is_closed", False)
    return False


class BidiRouter:
    """
    Routeur RPC bidirectionnel permettant d'exposer des routes dans la Sandbox
    et de répondre en temps réel aux requêtes de l'Iframe sans rechargement DOM.
    """
    def __init__(self, window_id: str = "default"):
        self.window_id = window_id
        self._routes: Dict[str, Callable[[Any], Any]] = {}

    def route(self, action: str):
        def decorator(fn: Callable[[Any], Any]):
            self._routes[action] = fn
            return fn
        return decorator

    def dispatch_once(self) -> int:
        reqs = get_ui_requests(self.window_id)
        handled = 0
        for req in reqs:
            action = req.get("action")
            req_id = req.get("req_id")
            payload = req.get("payload")
            if action in self._routes:
                try:
                    res = self._routes[action](payload)
                    push_event({"status": "ok", "action": action, "result": res}, window_id=self.window_id, req_id=req_id)
                except Exception as exc:
                    push_event({"status": "error", "action": action, "error": str(exc)}, window_id=self.window_id, req_id=req_id)
                handled += 1
        return handled

    def serve_forever(self, poll_interval: float = 0.2):
        while not is_window_closed(self.window_id):
            self.dispatch_once()
            time.sleep(poll_interval)
