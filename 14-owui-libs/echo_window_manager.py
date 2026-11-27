# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Window Manager
author: Wilfried BARNAVON
version: 6.24.6.1
description: Gestionnaire de fenêtres et d'exécutions JS agnostique, multi-chats et multi-navigateurs.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.24.6.1: Ajout de broadcast_terminal_state (echo_terminal_state) pour la diffusion temps réel des sessions terminales.
# 6.23.4.1: Ajout de broadcast_active_model (echo_active_model) et de l'action RPC get_active_model pour la pastille Écho v6.
# 6.21.9.1: Ajout de notify_cognition_update (bus WS echo_cognition_update), capture de _loop dans subscribe et cache _chat_owner_cache.
# 6.20.3.1: Ajout de broadcast_wait_timer (echo_wait_timer) pour la diffusion temps réel du minuteur vers le bus WM.
# 6.17.4.1: Suppression relais WS Canvas et file d'événements, ajout jetons HMAC sandbox_event et préservation src app_proxy dans upsert_canvas_frame.

import asyncio
import base64
from contextlib import closing
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from typing import Any, Dict, Optional, Set, Tuple

from echo_constants import ECHO_BASE_DATA_DIR, ECHO_UCTP_CHUNK_SIZE, ECHO_USERS_ROOT
try:
    from echo_constants import ECHO_CODING_WORKER_BASE_URL, ECHO_CODING_WORKER_URL
except ImportError:
    ECHO_CODING_WORKER_URL = "http://echo-coding-worker:5000/execute"
    ECHO_CODING_WORKER_BASE_URL = "http://echo-coding-worker:5000"
from echo_paths import sanitize_echo_id
from echo_state_manager import EchoStateManager

_OWUI_DB_PATH = os.path.join(ECHO_BASE_DATA_DIR, "webui.db")
_EMAIL_TO_UID_CACHE: Dict[str, str] = {}


def extract_closure_context(fn: Any) -> Dict[str, str]:
    """Extrait automatiquement (user_id, chat_id, message_id, session_id) depuis la closure Open WebUI."""
    if not fn:
        return {}
    closure = getattr(fn, "__closure__", None)
    code_obj = getattr(fn, "__code__", None)
    if not closure or not code_obj:
        return {}
    freevars = getattr(code_obj, "co_freevars", ())
    result: Dict[str, str] = {}
    for idx, cell in enumerate(closure):
        try:
            val = cell.cell_contents
        except ValueError:
            continue
        var_name = freevars[idx] if idx < len(freevars) else ""
        if isinstance(val, dict):
            for k in ("user_id", "chat_id", "message_id", "session_id"):
                if k in val and val[k] is not None and not result.get(k):
                    result[k] = str(val[k])
        elif isinstance(val, str) and var_name in ("user_id", "chat_id", "message_id", "session_id"):
            if val and not result.get(var_name):
                result[var_name] = val
    return result


def _resolve_user_id_by_email(email: str) -> Optional[str]:
    """Résout un email (ex: X-Webui-User) en UUID utilisateur Open WebUI via webui.db en lecture seule."""
    clean_email = (email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return None
    cached = _EMAIL_TO_UID_CACHE.get(clean_email)
    if cached:
        return cached
    if os.path.isfile(_OWUI_DB_PATH):
        try:
            with closing(sqlite3.connect(f"file:{_OWUI_DB_PATH}?mode=ro", uri=True, timeout=2.0)) as conn:
                row = conn.execute("SELECT id FROM user WHERE lower(email) = ?", (clean_email,)).fetchone()
                if row and row[0]:
                    uid = str(row[0])
                    _EMAIL_TO_UID_CACHE[clean_email] = uid
                    return uid
        except Exception:
            pass
    return None


def _find_existing_chat_owner(chat_id: str) -> Optional[str]:
    """Recherche l'identifiant utilisateur possédant déjà la session.db de ce chat_id sur disque ou dans webui.db."""
    safe_cid = sanitize_echo_id(chat_id)
    if not safe_cid or safe_cid == "default":
        return None
    try:
        if os.path.exists(ECHO_USERS_ROOT):
            for d in os.listdir(ECHO_USERS_ROOT):
                if d.startswith(".") or d == "system":
                    continue
                if os.path.isfile(os.path.join(ECHO_USERS_ROOT, d, "chats", safe_cid, "session.db")):
                    return d
    except Exception:
        pass
    if os.path.isfile(_OWUI_DB_PATH):
        try:
            with closing(sqlite3.connect(f"file:{_OWUI_DB_PATH}?mode=ro", uri=True, timeout=2.0)) as conn:
                row = conn.execute("SELECT user_id FROM chat WHERE id = ?", (chat_id,)).fetchone()
                if row and row[0]:
                    return str(row[0])
        except Exception:
            pass
    return None


class EchoWindowManager:
    """Orchestrateur serveur agnostique des fenêtres et exécutions JS multi-chats et multi-navigateurs."""

    def __init__(self):
        # user_id -> { client_id: asyncio.Queue }
        self._subscribers: Dict[str, Dict[str, asyncio.Queue]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._chat_owner_cache: Dict[str, str] = {}
        # exec_id -> { "future": Future, "user_id": str, "chat_id": str, "message_id": str, "code": str, "created_at": float }
        self._pending_calls: Dict[str, Dict[str, Any]] = {}
        # exec_id -> { "user_id": str, "chat_id": str, "code": str, "created_at": float }
        self._recent_execs: Dict[str, Dict[str, Any]] = {}
        # Etat Canvas bidirectionnel par (user_id, chat_id)
        self._canvas_ui_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Sessions navigateur actives par (user_id, chat_id) -> session_id
        self._active_browser_sessions: Dict[Tuple[str, str], str] = {}
        self._browser_session_events: Dict[Tuple[str, str], asyncio.Event] = {}
        # Propriétaires & offsets de lecture stdout/stderr Sandbox par f"{uid}:{cid}:{run_id}"
        self._sandbox_run_owners: Dict[str, str] = {}
        self._sandbox_stream_offsets: Dict[str, Tuple[int, int]] = {}
        self._revoked_app_runs: Set[str] = set()
        raw_secret = (
            os.environ.get("WEBUI_SECRET_KEY")
            or os.environ.get("ECHO_VAULT_ENCRYPTION_KEY")
            or secrets.token_hex(32)
        )
        self._app_ticket_secret: bytes = hashlib.sha256(str(raw_secret).encode("utf-8")).digest()

    def issue_app_capability_ticket(
        self,
        user_id: str,
        chat_id: str,
        run_id: str,
        window_id: str = "default",
        ttl_sec: int = 14400,
    ) -> str:
        """Émet un capability ticket HMAC-SHA256 sans cookie pour /echo/v1/wm/app/{ticket}."""
        owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        rid = str(run_id or "")
        wid = str(window_id or "default")
        if rid:
            self._revoked_app_runs.discard(rid)
        payload = {
            "u": owner_uid,
            "c": chat_id or "",
            "r": rid,
            "w": wid,
            "e": int(time.time() + ttl_sec),
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
        sig_bytes = hmac.new(self._app_ticket_secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig_bytes).decode("ascii").rstrip("=")
        return f"v1.{payload_b64}.{sig_b64}"

    def verify_app_capability_ticket(self, ticket: str) -> Optional[Dict[str, str]]:
        """Vérifie la signature HMAC, l'expiration et la non-révocation d'un ticket app_proxy."""
        if not isinstance(ticket, str) or not ticket:
            return None
        parts = ticket.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return None
        _, payload_b64, sig_b64 = parts
        if not payload_b64 or not sig_b64:
            return None
        try:
            expected_bytes = hmac.new(self._app_ticket_secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        except Exception:
            return None
        expected_b64 = base64.urlsafe_b64encode(expected_bytes).decode("ascii").rstrip("=")
        if not hmac.compare_digest(sig_b64, expected_b64):
            return None
        try:
            padded = payload_b64 + ("=" * (-len(payload_b64) % 4))
            data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        exp = data.get("e")
        if not isinstance(exp, (int, float)) or exp <= time.time():
            return None
        uid = str(data.get("u") or "")
        cid = str(data.get("c") or "")
        rid = str(data.get("r") or "")
        wid = str(data.get("w") or "default")
        if not rid or rid in self._revoked_app_runs:
            return None
        cstate = self._canvas_ui_state.get((uid, cid))
        if cstate and isinstance(cstate.get("closed"), dict) and cstate["closed"].get(wid):
            return None
        return {"user_id": uid, "chat_id": cid, "run_id": rid, "window_id": wid}

    def revoke_app_run_tickets(self, run_id: str) -> None:
        """Révoque immédiatement tous les capability tickets associés à un run_id."""
        rid = str(run_id or "")
        if not rid:
            return
        if len(self._revoked_app_runs) >= 5000:
            self._revoked_app_runs.pop()
        self._revoked_app_runs.add(rid)

    def _sandbox_callback_digest(self, user_id: str, chat_id: str) -> str:
        """Calcule la signature HMAC-SHA256 base64url authentifiant un callback worker -> WebUI."""
        owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        safe_uid = sanitize_echo_id(owner_uid, fallback="system")
        safe_cid = sanitize_echo_id(chat_id, fallback="")
        msg = f"cb:{safe_uid}:{safe_cid}".encode("utf-8")
        sig = hmac.new(self._app_ticket_secret, msg, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")

    def issue_sandbox_callback_token(self, user_id: str, chat_id: str) -> str:
        """Émet un jeton HMAC-SHA256 pour authentifier POST /echo/v1/wm/internal/sandbox_event."""
        return self._sandbox_callback_digest(user_id, chat_id)

    def verify_sandbox_callback_token(self, token: str, user_id: str, chat_id: str) -> bool:
        """Vérifie en temps constant le jeton HMAC-SHA256 d'un webhook sandbox."""
        if not isinstance(token, str) or not token:
            return False
        expected = self._sandbox_callback_digest(user_id, chat_id)
        return hmac.compare_digest(token.strip(), expected)

    def _get_canvas_state(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        """Retourne ou initialise le registre d'état Canvas pour (user_id, chat_id)."""
        uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        key = (uid, chat_id or "")
        state = self._canvas_ui_state.get(key)
        if state is None:
            state = {"frames": {}, "closed": {}}
            self._canvas_ui_state[key] = state
        return state

    def has_active_clients(self, user_id: str = "") -> bool:
        """Indique si au moins un navigateur SSE est connecté pour l'utilisateur (ou globalement)."""
        if user_id and user_id in self._subscribers and len(self._subscribers[user_id]) > 0:
            return True
        for subs in self._subscribers.values():
            if len(subs) > 0:
                return True
        return False

    def subscribe(self, user_id: str, client_id: str) -> asyncio.Queue:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        uid = user_id or "system"
        if uid not in self._subscribers:
            self._subscribers[uid] = {}
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[uid][client_id] = q
        return q

    def unsubscribe(self, user_id: str, client_id: str) -> None:
        uid = user_id or "system"
        if uid in self._subscribers:
            self._subscribers[uid].pop(client_id, None)
            if not self._subscribers[uid]:
                self._subscribers.pop(uid, None)

    async def broadcast(self, user_id: str, event: Dict[str, Any], exclude_client_id: str = "") -> int:
        """Diffuse un événement SSE à tous les navigateurs connectés de l'utilisateur."""
        delivered = 0
        target_maps = []
        if user_id and user_id in self._subscribers and self._subscribers[user_id]:
            target_maps.append(self._subscribers[user_id])
        else:
            target_maps.extend(self._subscribers.values())

        for sub_map in target_maps:
            for cid, q in list(sub_map.items()):
                if exclude_client_id and cid == exclude_client_id:
                    continue
                try:
                    if q.full():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(event)
                    delivered += 1
                except Exception:
                    pass
        return delivered

    async def open_tab(
        self,
        user_id: str,
        chat_id: str,
        kind: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Demande l'ouverture ou l'activation d'un onglet ECHO côté client via SSE."""
        await self.broadcast(
            user_id,
            {
                "type": "echo_open_tab",
                "chat_id": chat_id,
                "kind": kind,
                "payload": payload or {},
            },
        )

    async def broadcast_terminal_state(
        self,
        user_id: str,
        chat_id: str,
        terminal_data: Dict[str, Any],
        action: str = "updated",
    ) -> None:
        """Diffuse en temps réel sur le bus WM l'état d'une session terminale (opened, lock_changed, closed)."""
        await self.broadcast(
            user_id,
            {
                "type": "echo_terminal_state",
                "chat_id": chat_id,
                "action": action,
                "terminal": terminal_data or {},
            },
        )

    async def broadcast_browser_frame(
        self,
        user_id: str,
        chat_id: str,
        frame_data: Dict[str, Any],
    ) -> None:
        """Diffuse une capture ou trame live du navigateur Playwright côté client via SSE."""
        await self.broadcast(
            user_id,
            {
                "type": "echo_browser_frame",
                "chat_id": chat_id,
                **frame_data,
            },
        )

    async def broadcast_wait_timer(
        self,
        user_id: str,
        chat_id: str,
        duration_sec: int,
        active: bool = True,
    ) -> None:
        """Diffuse l'état d'un temporisateur wait_timer côté client via le bus WM."""
        await self.broadcast(
            user_id,
            {
                "type": "echo_wait_timer",
                "chat_id": chat_id,
                "duration_sec": max(0, int(duration_sec)),
                "started_at_ms": int(time.time() * 1000),
                "active": bool(active),
            },
        )

    def notify_cognition_update(self, chat_id: str, seq: int = 0, user_id: str = "") -> None:
        """Notifie de manière thread-safe les navigateurs abonnés d'une mise à jour cognitive."""
        if not self._subscribers:
            return
        safe_cid = sanitize_echo_id(chat_id)
        if not safe_cid or safe_cid == "default":
            return
        owner_uid = user_id or self._chat_owner_cache.get(safe_cid) or ""
        if not owner_uid:
            owner_uid = _find_existing_chat_owner(chat_id) or ""
            if owner_uid:
                if len(self._chat_owner_cache) > 256:
                    self._chat_owner_cache.pop(next(iter(self._chat_owner_cache)), None)
                self._chat_owner_cache[safe_cid] = owner_uid
        if owner_uid:
            sub_map = self._subscribers.get(owner_uid)
        elif len(self._subscribers) == 1:
            sub_map = next(iter(self._subscribers.values()))
        else:
            return
        if not sub_map:
            return
        event = {"type": "echo_cognition_update", "chat_id": safe_cid, "seq": int(seq)}

        def _push() -> None:
            for q in list(sub_map.values()):
                try:
                    if q.full():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(event)
                except Exception:
                    pass

        if self._loop is not None and not self._loop.is_closed():
            try:
                curr_loop = asyncio.get_running_loop()
            except RuntimeError:
                curr_loop = None
            if curr_loop is self._loop:
                _push()
            else:
                self._loop.call_soon_threadsafe(_push)
        else:
            try:
                _push()
            except Exception:
                pass

    def register_browser_session(self, user_id: str, chat_id: str, session_id: str) -> None:
        """Enregistre la session Playwright active pour (user_id, chat_id) et réveille les attentes."""
        raw_uid = user_id or "system"
        cid = chat_id or ""
        owner_uid = self._resolve_owner_uid(raw_uid, cid) if cid else raw_uid
        keys = [(owner_uid, cid)]
        if (raw_uid, cid) not in keys:
            keys.append((raw_uid, cid))
        for key in keys:
            self._active_browser_sessions[key] = session_id
            ev = self._browser_session_events.get(key)
            if ev is None:
                ev = asyncio.Event()
                self._browser_session_events[key] = ev
            ev.set()

    def unregister_browser_session(self, user_id: str, chat_id: str, session_id: str = "") -> None:
        """Désinscrit la session Playwright active pour (user_id, chat_id)."""
        raw_uid = user_id or "system"
        cid = chat_id or ""
        owner_uid = self._resolve_owner_uid(raw_uid, cid) if cid else raw_uid
        keys = [(owner_uid, cid)]
        if (raw_uid, cid) not in keys:
            keys.append((raw_uid, cid))
        for key in keys:
            current = self._active_browser_sessions.get(key)
            if not session_id or current == session_id:
                self._active_browser_sessions.pop(key, None)
                ev = self._browser_session_events.get(key)
                if ev is not None:
                    ev.clear()

    def get_active_browser_session(self, user_id: str, chat_id: str) -> Optional[str]:
        """Retourne le session_id navigateur enregistré pour (user_id, chat_id) ou par chat_id."""
        raw_uid = user_id or "system"
        cid = chat_id or ""
        sid = self._active_browser_sessions.get((raw_uid, cid))
        if sid:
            return sid
        if cid:
            owner_uid = self._resolve_owner_uid(raw_uid, cid)
            sid = self._active_browser_sessions.get((owner_uid, cid))
            if sid:
                return sid
            for (_, k_cid), val in self._active_browser_sessions.items():
                if k_cid == cid and val:
                    return val
        return None

    async def wait_for_browser_session(
        self,
        user_id: str,
        chat_id: str,
        timeout: float = 15.0,
    ) -> Optional[str]:
        """Attend l'enregistrement d'une session navigateur active pour (user_id, chat_id)."""
        sid = self.get_active_browser_session(user_id, chat_id)
        if sid:
            return sid
        key = (user_id or "system", chat_id or "")
        ev = self._browser_session_events.get(key)
        if ev is None:
            ev = asyncio.Event()
            self._browser_session_events[key] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self.get_active_browser_session(user_id, chat_id)

    async def _fire_and_forget_sandbox_kill(self, uid: str, cid: str, run_id: str) -> None:
        """Envoie un /kill asynchrone sans bloquer lors du remplacement d'une frame Canvas."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(
                    f"{ECHO_CODING_WORKER_BASE_URL}/kill",
                    json={"user_id": uid, "chat_id": cid, "run_id": run_id},
                )
        except Exception:
            pass

    async def _fire_and_forget_canvas_signal(self, uid: str, cid: str, run_id: str, body: Dict[str, Any]) -> None:
        """Pousse un signal Canvas (/canvas_signal) vers CanvasRunHub sans bloquer le routeur RPC."""
        try:
            import httpx
            payload = {"user_id": uid, "chat_id": cid, "run_id": run_id, **body}
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(f"{ECHO_CODING_WORKER_BASE_URL}/canvas_signal", json=payload)
        except Exception:
            pass

    def set_sandbox_run_owner(self, uid: str, cid: str, run_id: str, sub_sid: str) -> None:
        """Enregistre le propriétaire sub_sid d'un run_id Sandbox (FIFO borné à 1000 entrées)."""
        key = f"{uid}:{cid}:{run_id}"
        while len(self._sandbox_run_owners) >= 1000:
            self._sandbox_run_owners.pop(next(iter(self._sandbox_run_owners)))
        self._sandbox_run_owners[key] = sub_sid

    def pop_sandbox_run_owner(self, uid: str, cid: str, run_id: Optional[str] = None) -> None:
        """Supprime l'enregistrement de propriété d'un ou de tous les runs d'une session."""
        if run_id:
            self._sandbox_run_owners.pop(f"{uid}:{cid}:{run_id}", None)
            return
        prefix = f"{uid}:{cid}:"
        for k in [k for k in list(self._sandbox_run_owners.keys()) if k.startswith(prefix)]:
            self._sandbox_run_owners.pop(k, None)

    def is_run_owned_by_subagent(self, uid: str, cid: str, run_id: str, sub_sid: str) -> bool:
        """Vérifie si un run_id appartient au sous-agent sub_sid."""
        if not sub_sid:
            return True
        return self._sandbox_run_owners.get(f"{uid}:{cid}:{run_id}") == sub_sid

    def get_sandbox_stream_offsets(self, uid: str, cid: str, run_id: str) -> Tuple[int, int]:
        """Retourne les offsets (stdout_offset, stderr_offset) mémorisés pour un run Sandbox."""
        return self._sandbox_stream_offsets.get(f"{uid}:{cid}:{run_id}", (0, 0))

    def set_sandbox_stream_offsets(self, uid: str, cid: str, run_id: str, out_off: int, err_off: int) -> None:
        """Mémorise les offsets (stdout_offset, stderr_offset) pour control_sandbox_execution(action='listen')."""
        key = f"{uid}:{cid}:{run_id}"
        while len(self._sandbox_stream_offsets) >= 1000:
            self._sandbox_stream_offsets.pop(next(iter(self._sandbox_stream_offsets)))
        self._sandbox_stream_offsets[key] = (int(out_off), int(err_off))

    def clear_sandbox_stream_offsets(self, uid: str, cid: str, run_id: Optional[str] = None) -> None:
        """Supprime les offsets mémorisés d'un ou de tous les runs d'une session."""
        if run_id:
            self._sandbox_stream_offsets.pop(f"{uid}:{cid}:{run_id}", None)
            return
        prefix = f"{uid}:{cid}:"
        for k in [k for k in list(self._sandbox_stream_offsets.keys()) if k.startswith(prefix)]:
            self._sandbox_stream_offsets.pop(k, None)

    def upsert_canvas_frame(
        self,
        user_id: str,
        chat_id: str,
        frame_data: Any = None,
        run_id: str = "",
        *,
        frame_id: Optional[str] = None,
        html: Optional[str] = None,
        title: Optional[str] = None,
        width: Optional[str] = None,
        height: Optional[str] = None,
        src: Optional[str] = None,
        server_status: Optional[str] = None,
        mode: Optional[str] = None,
        exit_code: Any = None,
        ready: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Insère ou met à jour de manière idempotente une frame Canvas dans _canvas_ui_state (Contrat C3)."""
        if isinstance(frame_data, dict):
            data = dict(frame_data)
        elif isinstance(frame_data, str) and frame_data:
            data = {"id": frame_data}
        else:
            data = {}

        if frame_id is not None:
            data["id"] = frame_id
        if html is not None:
            data["html"] = html
        if title is not None:
            data["title"] = title
        if width is not None:
            data["width"] = width
        if height is not None:
            data["height"] = height
        if src is not None:
            data["src"] = src
        if server_status is not None:
            data["server_status"] = server_status
        if mode is not None and mode != "":
            data["mode"] = mode
        if exit_code is not None:
            data["exit_code"] = exit_code
        if ready is not None:
            data["ready"] = ready

        owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        cstate = self._get_canvas_state(user_id, chat_id)
        wid = str(data.get("id") or data.get("window_id") or "default")
        prev = cstate["frames"].get(wid) if isinstance(cstate.get("frames"), dict) else None
        new_rid = str(run_id or data.get("run_id") or (prev or {}).get("run_id") or "")

        terminal_states = ("completed", "killed", "error", "timeout")
        if isinstance(prev, dict):
            prev_rid = str(prev.get("run_id") or "")
            if (
                prev_rid
                and new_rid
                and prev_rid != new_rid
                and prev.get("server_status") not in terminal_states
            ):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._fire_and_forget_sandbox_kill(owner_uid, chat_id, prev_rid))
                except RuntimeError:
                    pass

        resolved_status = str(data.get("server_status") or (prev or {}).get("server_status") or "running")
        is_active = resolved_status not in terminal_states
        if is_active:
            cstate["closed"].pop(wid, None)

        resolved_mode = str(data.get("mode") or (prev or {}).get("mode") or "")
        if "html" in data and data["html"] is not None:
            html_val = str(data["html"])
        else:
            html_val = str((prev or {}).get("html") or "")

        src_val = ""
        if resolved_mode == "app_proxy":
            prev_src = str((prev or {}).get("src") or "")
            if is_active and new_rid:
                reused = False
                prefix = "/echo/v1/wm/app/"
                if prev_src.startswith(prefix):
                    existing_ticket = prev_src[len(prefix):].split("/", 1)[0]
                    claims = self.verify_app_capability_ticket(existing_ticket)
                    if (
                        claims is not None
                        and claims.get("run_id") == new_rid
                        and claims.get("window_id") == wid
                    ):
                        src_val = prev_src
                        reused = True
                if not reused:
                    ticket = self.issue_app_capability_ticket(owner_uid, chat_id, new_rid, wid)
                    src_val = f"/echo/v1/wm/app/{ticket}/"
                html_val = ""
            else:
                src_val = str(data.get("src") or prev_src or "")
                html_val = ""
        else:
            src_val = ""

        resolved_exit = data["exit_code"] if "exit_code" in data else (prev or {}).get("exit_code")
        resolved_ready = bool(data.get("ready", (prev or {}).get("ready", False)))
        frame_entry = {
            "id": wid,
            "title": str(data.get("title") or (prev or {}).get("title") or "Monitor"),
            "html": html_val,
            "src": src_val,
            "mode": resolved_mode,
            "width": str(data.get("width") or (prev or {}).get("width") or "100%"),
            "height": str(data.get("height") or (prev or {}).get("height") or "400px"),
            "run_id": new_rid,
            "server_status": resolved_status,
            "exit_code": resolved_exit,
            "ready": resolved_ready,
            "updated_at": time.time(),
        }
        cstate["frames"][wid] = frame_entry
        return frame_entry

    async def reconcile_canvas_frames(self, user_id: str, chat_id: str) -> None:
        """Réconcilie l'état des frames Canvas actives avec le Worker Sandbox."""
        try:
            owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
            cstate = self._canvas_ui_state.get((owner_uid, chat_id or ""))
            if not cstate or not isinstance(cstate.get("frames"), dict):
                return
            terminal_states = ("completed", "killed", "error", "timeout")
            active_frames = [
                dict(f) for f in cstate["frames"].values()
                if isinstance(f, dict)
                and f.get("run_id")
                and f.get("server_status") not in terminal_states
            ]
            if not active_frames:
                return

            import httpx
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(
                    f"{ECHO_CODING_WORKER_BASE_URL}/status",
                    params={"user_id": owner_uid, "chat_id": chat_id},
                )
            if res.status_code != 200:
                return
            jobs = res.json().get("jobs", [])
            jobs_by_id = {str(j.get("run_id")): j for j in jobs if isinstance(j, dict) and j.get("run_id")}
            for entry in active_frames:
                rid = str(entry.get("run_id") or "")
                if not rid:
                    continue
                job = jobs_by_id.get(rid)
                if job is None:
                    self.revoke_app_run_tickets(rid)
                    self.upsert_canvas_frame(owner_uid, chat_id, entry, run_id=rid, server_status="error")
                elif job.get("state") not in ("running", "unknown"):
                    self.revoke_app_run_tickets(rid)
                    self.upsert_canvas_frame(
                        owner_uid,
                        chat_id,
                        entry,
                        run_id=rid,
                        server_status=str(job.get("state") or "completed"),
                        exit_code=job.get("exit_code"),
                    )
                elif entry.get("mode") == "app_proxy":
                    self.upsert_canvas_frame(owner_uid, chat_id, entry, run_id=rid)
        except Exception:
            pass

    async def broadcast_canvas_frame(
        self,
        user_id: str,
        chat_id: str,
        frame_data: Any = None,
        auto_open: bool = True,
        run_id: str = "",
        *,
        frame_id: Optional[str] = None,
        html: Optional[str] = None,
        title: Optional[str] = None,
        width: Optional[str] = None,
        height: Optional[str] = None,
        src: Optional[str] = None,
        server_status: Optional[str] = None,
        mode: Optional[str] = None,
        exit_code: Any = None,
        ready: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Enregistre une frame Canvas et la diffuse via WS/SSE vers l'onglet natif echo-canvas."""
        owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        frame_entry = self.upsert_canvas_frame(
            user_id,
            chat_id,
            frame_data,
            run_id=run_id,
            frame_id=frame_id,
            html=html,
            title=title,
            width=width,
            height=height,
            src=src,
            server_status=server_status,
            mode=mode,
            exit_code=exit_code,
            ready=ready,
        )
        target_uid = owner_uid if owner_uid != "system" else user_id
        await self.broadcast(
            target_uid,
            {
                "type": "window_mounted",
                "chat_id": chat_id,
                "auto": auto_open,
                **frame_entry,
            },
        )
        return frame_entry

    async def broadcast_canvas_status(
        self,
        user_id: str,
        chat_id: str,
        window_ids: Any,
        server_status: str,
        run_id: str = "",
        exit_code: Optional[int] = None,
    ) -> None:
        """Met à jour le statut serveur des frames Canvas ciblées et le diffuse."""
        if isinstance(window_ids, (list, tuple, set)):
            wids = [str(w) for w in window_ids if w]
        elif window_ids:
            wids = [str(window_ids)]
        else:
            wids = []
        owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        cstate = self._get_canvas_state(user_id, chat_id)
        now = time.time()
        for wid in wids:
            entry = cstate["frames"].get(wid)
            if isinstance(entry, dict) and (not run_id or not entry.get("run_id") or entry.get("run_id") == run_id):
                entry["server_status"] = server_status
                entry["exit_code"] = exit_code
                entry["updated_at"] = now
        if run_id and server_status in ("completed", "killed", "error", "timeout"):
            self.revoke_app_run_tickets(run_id)
        target_uid = owner_uid if owner_uid != "system" else user_id
        await self.broadcast(
            target_uid,
            {
                "type": "echo_canvas_status",
                "chat_id": chat_id,
                "window_ids": wids,
                "run_id": run_id,
                "server_status": server_status,
                "exit_code": exit_code,
            },
        )

    def cleanup_canvas_run(
        self,
        user_id: str,
        chat_id: str,
        window_ids: Any,
        run_id: str = "",
    ) -> None:
        """Nettoie les marqueurs de fermeture et tickets associés aux fenêtres d'un run."""
        if isinstance(window_ids, (list, tuple, set)):
            wids = [str(w) for w in window_ids if w]
        elif window_ids:
            wids = [str(window_ids)]
        else:
            wids = []
        cstate = self._get_canvas_state(user_id, chat_id)
        if run_id:
            self.revoke_app_run_tickets(run_id)
        for wid in wids:
            if run_id:
                current_frame = cstate["frames"].get(wid)
                if not isinstance(current_frame, dict) or current_frame.get("run_id", "") in ("", run_id):
                    cstate["closed"].pop(wid, None)
            else:
                cstate["closed"].pop(wid, None)

    async def _kill_sandbox_async(self, uid: str, cid: str = "", run_id: Optional[str] = None) -> None:
        """Envoie un POST /kill asynchrone (timeout 3.0s) vers le Coding Worker."""
        try:
            import httpx
            payload: Dict[str, Any] = {
                "user_id": sanitize_echo_id(uid, fallback="system"),
                "chat_id": sanitize_echo_id(cid, fallback=""),
            }
            if run_id:
                payload["run_id"] = sanitize_echo_id(run_id, fallback="")
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(f"{ECHO_CODING_WORKER_BASE_URL}/kill", json=payload)
        except Exception:
            pass

    async def cleanup_chat_canvas(self, user_id: str, chat_id: str) -> None:
        """Révoque les tickets, purge _canvas_ui_state et tue les processus Sandbox d'un chat."""
        if not chat_id:
            return
        owner_uid = self._resolve_owner_uid(user_id, chat_id)
        keys_to_pop = [
            k for k in list(self._canvas_ui_state.keys())
            if k[1] == chat_id and k[0] in (owner_uid, user_id)
        ]
        for key in keys_to_pop:
            cstate = self._canvas_ui_state.pop(key, None)
            if isinstance(cstate, dict) and isinstance(cstate.get("frames"), dict):
                for f in cstate["frames"].values():
                    if isinstance(f, dict) and f.get("run_id"):
                        self.revoke_app_run_tickets(str(f["run_id"]))
        self.pop_sandbox_run_owner(owner_uid, chat_id)
        self.clear_sandbox_stream_offsets(owner_uid, chat_id)
        if user_id and user_id != owner_uid:
            self.pop_sandbox_run_owner(user_id, chat_id)
            self.clear_sandbox_stream_offsets(user_id, chat_id)
        await self._kill_sandbox_async(owner_uid, chat_id)

    async def cleanup_user_canvas(self, user_id: str) -> None:
        """Révoque tous les tickets, purge _canvas_ui_state et tue tous les processus Sandbox d'un utilisateur."""
        uid = user_id or "system"
        if "@" in uid:
            uid = _resolve_user_id_by_email(uid) or uid
        chat_ids: Set[str] = set()
        for key in [k for k in list(self._canvas_ui_state.keys()) if k[0] == uid]:
            if key[1]:
                chat_ids.add(key[1])
            cstate = self._canvas_ui_state.pop(key, None)
            if isinstance(cstate, dict) and isinstance(cstate.get("frames"), dict):
                for f in cstate["frames"].values():
                    if isinstance(f, dict) and f.get("run_id"):
                        self.revoke_app_run_tickets(str(f["run_id"]))
        for reg in (self._sandbox_run_owners, self._sandbox_stream_offsets):
            prefix = f"{uid}:"
            for k in [k for k in list(reg.keys()) if k.startswith(prefix)]:
                parts = k.split(":", 2)
                if len(parts) >= 2 and parts[1]:
                    chat_ids.add(parts[1])
                reg.pop(k, None)
        for cid in sorted(chat_ids):
            await self._kill_sandbox_async(uid, cid)

    def _resolve_owner_uid(self, user_id: str, chat_id: str) -> str:
        """Garantit la correspondance exacte (user_id, chat_id) avec la session existante."""
        uid = user_id or "system"
        if "@" in uid:
            uid = _resolve_user_id_by_email(uid) or "system"
        if not chat_id:
            return uid
        if uid != "system":
            sm = EchoStateManager(uid, chat_id)
            if os.path.isfile(sm.db_path):
                return uid
        owner = _find_existing_chat_owner(chat_id)
        return owner or uid

    def register_exec(self, exec_id: str, user_id: str, chat_id: str, code: str) -> None:
        now = time.time()
        if len(self._recent_execs) > 256:
            cutoff = now - 600
            for eid in [k for k, v in self._recent_execs.items() if v.get("created_at", 0) < cutoff]:
                self._recent_execs.pop(eid, None)
            if len(self._recent_execs) > 256:
                oldest = sorted(self._recent_execs.keys(), key=lambda k: self._recent_execs[k].get("created_at", 0))
                for eid in oldest[:64]:
                    self._recent_execs.pop(eid, None)

        self._recent_execs[exec_id] = {
            "user_id": user_id or "system",
            "chat_id": chat_id or "",
            "code": code,
            "created_at": now,
        }

    async def _broadcast_execute(
        self,
        user_id: str,
        mode: str,
        eid: str,
        chat_id: str,
        message_id: str,
        code: str,
    ) -> int:
        """Diffuse une exécution JS sur le flux SSE avec fragmentation automatique UCTP au-delà de 256 Ko."""
        if len(code) <= ECHO_UCTP_CHUNK_SIZE:
            return await self.broadcast(
                user_id,
                {
                    "type": "wm_execute",
                    "mode": mode,
                    "exec_id": eid,
                    "chat_id": chat_id or "",
                    "message_id": message_id or "",
                    "code": code,
                },
            )
        total_chunks = (len(code) + ECHO_UCTP_CHUNK_SIZE - 1) // ECHO_UCTP_CHUNK_SIZE
        delivered = 0
        for i in range(total_chunks):
            start_idx = i * ECHO_UCTP_CHUNK_SIZE
            chunk_str = code[start_idx: start_idx + ECHO_UCTP_CHUNK_SIZE]
            delivered = await self.broadcast(
                user_id,
                {
                    "type": "wm_uctp_chunk",
                    "exec_id": eid,
                    "chunk_index": i,
                    "total_chunks": total_chunks,
                    "data": chunk_str,
                },
            )
        await self.broadcast(
            user_id,
            {
                "type": "wm_uctp_exec",
                "mode": mode,
                "exec_id": eid,
                "chat_id": chat_id or "",
                "message_id": message_id or "",
            },
        )
        return delivered

    async def emit_js(
        self,
        user_id: str,
        chat_id: str,
        message_id: str,
        code: str,
        exec_id: Optional[str] = None,
    ) -> bool:
        """Envoie un script JS sans attente de retour à tous les navigateurs de l'utilisateur."""
        eid = exec_id or uuid.uuid4().hex
        self.register_exec(eid, user_id, chat_id, code)
        delivered = await self._broadcast_execute(
            user_id=user_id,
            mode="emit",
            eid=eid,
            chat_id=chat_id,
            message_id=message_id,
            code=code,
        )
        return delivered > 0

    async def call_js(
        self,
        user_id: str,
        chat_id: str,
        message_id: str,
        code: str,
        timeout: Optional[float] = None,
        exec_id: Optional[str] = None,
    ) -> Tuple[bool, Any]:
        """Envoie un script JS et attend la résolution RPC depuis n'importe quel navigateur de l'utilisateur."""
        if not self.has_active_clients(user_id):
            return False, None

        eid = exec_id or uuid.uuid4().hex
        self.register_exec(eid, user_id, chat_id, code)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending_calls[eid] = {
            "future": fut,
            "user_id": user_id or "system",
            "chat_id": chat_id or "",
            "message_id": message_id or "",
            "code": code,
            "created_at": time.time(),
        }

        try:
            delivered = await self._broadcast_execute(
                user_id=user_id,
                mode="call",
                eid=eid,
                chat_id=chat_id,
                message_id=message_id,
                code=code,
            )
            if delivered == 0:
                return False, None

            if timeout is not None and timeout > 0:
                res = await asyncio.wait_for(fut, timeout=timeout)
            else:
                res = await fut
            return True, res
        except asyncio.TimeoutError:
            print(f"[EchoWindowManager] Call Execute Timeout ({timeout}s)")
            return False, None
        except Exception as e:
            print(f"[EchoWindowManager] Call Execute Error: {e}")
            return False, None
        finally:
            self._pending_calls.pop(eid, None)

    @staticmethod
    def _normalize_active_model_key(raw_model: Optional[str]) -> Optional[str]:
        """Normalise un identifiant de modèle brut vers sa clé canonique MODEL_*."""
        if not raw_model or raw_model == "aucun":
            return None
        from echo_constants import get_model_identity
        resolved = get_model_identity(str(raw_model).strip())
        return resolved if resolved and resolved != "UNKNOWN" else None

    async def broadcast_active_model(
        self, user_id: str, chat_id: str, model_key: Optional[str]
    ) -> int:
        """Diffuse le modèle cognitif actif de l'orchestrateur sur le bus WM."""
        if not chat_id:
            return 0
        normalized = self._normalize_active_model_key(model_key)
        owner_uid = self._resolve_owner_uid(user_id, chat_id)
        return await self.broadcast(
            owner_uid,
            {"type": "echo_active_model", "chat_id": chat_id, "model_key": normalized},
        )

    async def handle_rpc(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les retours RPC, le cycle de vie DOM des fenêtres et la synchronisation KV."""
        action = payload.get("action") or ""
        client_id = str(payload.get("client_id") or "")
        chat_id = str(payload.get("chat_id") or "")
        exec_id = str(payload.get("exec_id") or "")

        if action == "get_active_model":
            if not chat_id or chat_id == "_new" or chat_id.startswith("local:"):
                return {"status": "ok", "chat_id": chat_id, "model_key": None}

            def _read_model() -> Optional[str]:
                owner_uid = self._resolve_owner_uid(user_id, chat_id)
                sm = EchoStateManager(owner_uid, chat_id)
                if not os.path.isfile(sm.db_path):
                    return None
                return self._normalize_active_model_key(sm.get_last_active_model())

            model_key = await asyncio.to_thread(_read_model)
            return {"status": "ok", "chat_id": chat_id, "model_key": model_key}

        if action == "rpc_result":
            entry = self._pending_calls.get(exec_id)
            if entry:
                fut = entry.get("future")
                if fut and not fut.done():
                    fut.set_result(payload.get("result"))
                await self.broadcast(
                    user_id,
                    {"type": "wm_rpc_resolved", "exec_id": exec_id, "chat_id": chat_id},
                    exclude_client_id=client_id,
                )
            return {"status": "ok"}

        if action == "canvas_ui_event":
            if chat_id:
                owner_uid = self._resolve_owner_uid(user_id, chat_id)
                cstate = self._get_canvas_state(user_id, chat_id)
                closed_val = payload.get("window_closed")
                if closed_val:
                    closed_items = (
                        [closed_val]
                        if isinstance(closed_val, str)
                        else (closed_val if isinstance(closed_val, list) else [])
                    )
                    remove_frame = bool(payload.get("remove_frame", False))
                    by_run_id: Dict[str, list[str]] = {}
                    explicit_rid = str(payload.get("run_id") or "")
                    for cw in closed_items:
                        if cw:
                            s_wid = str(cw)
                            target_rid = explicit_rid or str((cstate["frames"].get(s_wid) or {}).get("run_id") or "")
                            cstate["closed"][s_wid] = True
                            if remove_frame:
                                cstate["frames"].pop(s_wid, None)
                            if target_rid:
                                by_run_id.setdefault(target_rid, []).append(s_wid)
                    for target_rid, wids_list in by_run_id.items():
                        self.revoke_app_run_tickets(target_rid)
                        asyncio.create_task(
                            self._fire_and_forget_canvas_signal(
                                owner_uid,
                                chat_id,
                                target_rid,
                                {"window_closed": wids_list},
                            )
                        )
            return {"status": "ok"}

        if action == "canvas_stop_run":
            if not chat_id:
                return {"status": "error", "error": "missing chat_id"}
            owner_uid = self._resolve_owner_uid(user_id, chat_id)
            cstate = self._get_canvas_state(user_id, chat_id)
            wid = str(payload.get("frame_id") or payload.get("window_id") or "default")
            target_rid = str(payload.get("run_id") or (cstate["frames"].get(wid) or {}).get("run_id") or "")
            cstate["closed"][wid] = True
            if target_rid:
                self.revoke_app_run_tickets(target_rid)
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{ECHO_CODING_WORKER_BASE_URL}/kill",
                        json={"user_id": owner_uid, "chat_id": chat_id, "run_id": target_rid or None},
                    )
            except Exception:
                pass
            await self.broadcast_canvas_status(
                owner_uid,
                chat_id,
                [wid],
                server_status="killed",
                run_id=target_rid,
            )
            return {"status": "ok", "run_id": target_rid}

        return {"status": "ignored"}

    def get_chat_snapshot(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        """Retourne l'état complet (appels RPC en attente, stats contexte et frames Canvas) pour un chat."""
        pending = []
        for eid, info in self._pending_calls.items():
            fut = info.get("future")
            if fut and not fut.done() and info.get("chat_id") == chat_id:
                pending.append({
                    "exec_id": eid,
                    "chat_id": chat_id,
                    "message_id": info.get("message_id", ""),
                    "code": info.get("code", ""),
                })

        context_stats = {}
        try:
            from echo_auth import EchoAuth
            from echo_protocol import get_ca_model_id
            from echo_ui import EchoUI

            uid = self._resolve_owner_uid(user_id, chat_id)
            sm = EchoStateManager(uid, chat_id)
            last_model = None
            prev_stats = None
            if os.path.isfile(sm.db_path):
                context_stats = sm.get_last_context_stats() or {}
                prev_stats = sm.get_previous_context_stats()
                last_model = sm.get_last_active_model()

            if os.path.isfile(sm.identity_db_path):
                ca_model_id = get_ca_model_id(last_model) if last_model else None
                quota_snap = EchoAuth(uid).get_ui_quota_snapshot(ca_model_id or "")
                if quota_snap:
                    context_stats.update(quota_snap)

            if prev_stats is not None or context_stats:
                context_stats["delta"] = EchoUI.calculate_context_delta(context_stats, prev_stats)
        except Exception:
            pass

        canvas_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        cstate = self._canvas_ui_state.get((canvas_uid, chat_id or ""))

        return {
            "chat_id": chat_id,
            "kv": {},
            "windows": [],
            "pending_calls": pending,
            "context_stats": context_stats,
            "canvas_frames": sorted(cstate["frames"].values(), key=lambda x: x.get("updated_at", 0)) if cstate and isinstance(cstate.get("frames"), dict) else [],
        }


_WM_SINGLETON: Optional[EchoWindowManager] = None


def get_window_manager() -> EchoWindowManager:
    global _WM_SINGLETON
    if _WM_SINGLETON is None:
        _WM_SINGLETON = EchoWindowManager()
    return _WM_SINGLETON
