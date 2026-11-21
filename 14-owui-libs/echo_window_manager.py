# -*- coding: utf-8 -*-
"""
title: ECHO Window Manager
author: Wilfried BARNAVON
version: 6.15.2.1
description: Gestionnaire de fenêtres et d'exécutions JS agnostique, multi-chats et multi-navigateurs.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.15.2.1: Isolation multi-run par run_id dans les frames Canvas, arrêt ciblé canvas_stop_run, réconciliation F5 reconcile_canvas_frames et réveil réactif inbox via wake_event.
# 6.14.2.1: Suppression intégrale de la persistance SQLite wm_state (_load_chat_state, _save_chat_state, _sanitize_windows_dict) et des actions RPC mortes (window_mounted, window_updated, window_closed, kv_sync).
# 6.13.1.2: Ajout du canal SSE dédié browser-live (_browser_live_subscribers, subscribe_browser_live, unsubscribe_browser_live, has_browser_live_subscribers, broadcast_browser_live).
# 6.13.1.1: Suppression du faux ping serveur, inbox Canvas scopée par window_id avec peek/ack, support echoPings batch et window_closed (_is_closed), broadcast_canvas_status, cleanup_canvas_run et exposition canvas_frames dans get_chat_snapshot.
# 6.7.0.1: Purge des payloads de fenêtres fermées (_sanitize_windows_dict), auto-guérison wm_state, bridage update_codes et suppression du code mort get_client_loader_js.

import asyncio
from contextlib import closing
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, Optional, Set, Tuple

from echo_constants import ECHO_BASE_DATA_DIR, ECHO_UCTP_CHUNK_SIZE, ECHO_USERS_ROOT
try:
    from echo_constants import ECHO_CODING_WORKER_URL
except ImportError:
    ECHO_CODING_WORKER_URL = "http://echo-coding-worker:5000/execute"
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
        # exec_id -> { "future": Future, "user_id": str, "chat_id": str, "message_id": str, "code": str, "created_at": float }
        self._pending_calls: Dict[str, Dict[str, Any]] = {}
        # exec_id -> { "user_id": str, "chat_id": str, "code": str, "created_at": float }
        self._recent_execs: Dict[str, Dict[str, Any]] = {}
        # Etat Canvas bidirectionnel par (user_id, chat_id)
        self._canvas_ui_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Abonnés live screencast navigateur par (user_id, chat_id) -> { client_id: Queue }
        self._browser_live_subscribers: Dict[Tuple[str, str], Dict[str, asyncio.Queue]] = {}
        # Superviseur pérenne Sandbox par f"{uid}:{cid}:{run_id}"
        self._sandbox_pollers: Dict[str, Dict[str, Any]] = {}
        self._sandbox_run_owners: Dict[str, str] = {}

    def _get_canvas_state(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        """Retourne ou initialise le registre d'état Canvas bidirectionnel pour (user_id, chat_id)."""
        uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        key = (uid, chat_id or "")
        state = self._canvas_ui_state.get(key)
        if state is None:
            state = {"frames": {}, "payloads": {}, "inbox": {}, "pings": {}, "closed": {}, "signal": None}
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

    def subscribe_browser_live(self, user_id: str, chat_id: str, client_id: str) -> asyncio.Queue:
        """Enregistre un flux SSE live screencast pour (user_id, chat_id)."""
        key = (user_id or "system", chat_id or "")
        sub_map = self._browser_live_subscribers.setdefault(key, {})
        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        sub_map[client_id] = q
        return q

    def unsubscribe_browser_live(self, user_id: str, chat_id: str, client_id: str) -> None:
        """Désinscrit un flux SSE live screencast."""
        key = (user_id or "system", chat_id or "")
        sub_map = self._browser_live_subscribers.get(key)
        if sub_map is not None:
            sub_map.pop(client_id, None)
            if not sub_map:
                self._browser_live_subscribers.pop(key, None)

    def has_browser_live_subscribers(self, user_id: str, chat_id: str) -> bool:
        """Indique si au moins un client écoute le live screencast pour ce chat."""
        uid = user_id or "system"
        cid = chat_id or ""
        if self._browser_live_subscribers.get((uid, cid)):
            return True
        if cid:
            for (_, k_cid), sub_map in self._browser_live_subscribers.items():
                if k_cid == cid and sub_map:
                    return True
        return False

    async def broadcast_browser_live(self, user_id: str, chat_id: str, event: Dict[str, Any]) -> int:
        """Diffuse un événement screencast live vers les abonnés du chat (drop-oldest si plein)."""
        uid = user_id or "system"
        cid = chat_id or ""
        target_maps = []
        exact = self._browser_live_subscribers.get((uid, cid))
        if exact:
            target_maps.append(exact)
        elif cid:
            for (_, k_cid), sub_map in self._browser_live_subscribers.items():
                if k_cid == cid and sub_map:
                    target_maps.append(sub_map)

        delivered = 0
        for sub_map in target_maps:
            for q in list(sub_map.values()):
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

    async def _fire_and_forget_sandbox_kill(self, uid: str, cid: str, run_id: str) -> None:
        """Envoie un /kill asynchrone sans bloquer lors du remplacement d'une frame Canvas."""
        try:
            import httpx
            sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(
                    f"{sandbox_base}/kill",
                    json={"user_id": uid, "chat_id": cid, "run_id": run_id},
                )
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
        key = f"{uid}:{cid}:{run_id}"
        poller = self._sandbox_pollers.get(key)
        if poller is not None and "sub_sid" in poller:
            return poller["sub_sid"] == sub_sid
        return self._sandbox_run_owners.get(key) == sub_sid

    def register_sandbox_poller(self, uid: str, cid: str, run_id: str, entry: Dict[str, Any]) -> None:
        """Enregistre un poller Sandbox actif dans le gestionnaire pérenne."""
        self._sandbox_pollers[f"{uid}:{cid}:{run_id}"] = entry

    def get_sandbox_poller(self, uid: str, cid: str, run_id: str) -> Optional[Dict[str, Any]]:
        """Retourne l'entrée du poller Sandbox pour (uid, cid, run_id)."""
        return self._sandbox_pollers.get(f"{uid}:{cid}:{run_id}")

    def list_sandbox_pollers(self, uid: str, cid: str) -> Dict[str, Dict[str, Any]]:
        """Liste les pollers Sandbox actifs pour une session donnée, indexés par run_id."""
        prefix = f"{uid}:{cid}:"
        return {
            k[len(prefix):]: v
            for k, v in self._sandbox_pollers.items()
            if k.startswith(prefix)
        }

    def pop_sandbox_poller(
        self,
        uid: str,
        cid: str,
        run_id: str,
        expected_task: Optional[asyncio.Task] = None,
    ) -> None:
        """Retire un poller Sandbox uniquement si expected_task est None ou correspond à l'entrée."""
        key = f"{uid}:{cid}:{run_id}"
        entry = self._sandbox_pollers.get(key)
        if entry is None:
            return
        if expected_task is None or entry.get("task") is expected_task:
            self._sandbox_pollers.pop(key, None)

    def stop_sandbox_poller(
        self,
        uid: str,
        cid: str,
        run_id: Optional[str] = None,
        sub_sid: str = "",
    ) -> list[str]:
        """Annule les pollers Sandbox ciblés et retourne la liste des run_id arrêtés."""
        prefix = f"{uid}:{cid}:"
        keys = (
            [f"{prefix}{run_id}"]
            if run_id
            else [k for k in list(self._sandbox_pollers.keys()) if k.startswith(prefix)]
        )
        stopped: list[str] = []
        for k in keys:
            rid = k[len(prefix):] if k.startswith(prefix) else k
            if sub_sid and not self.is_run_owned_by_subagent(uid, cid, rid, sub_sid):
                continue
            entry = self._sandbox_pollers.pop(k, None)
            if entry:
                stopped.append(rid)
                task = entry.get("task")
                if task and not task.done():
                    task.cancel()
        return stopped

    def wake_sandbox_pollers(
        self,
        user_id: str,
        chat_id: str,
        run_id: str = "",
        window_id: str = "",
    ) -> None:
        """Réveille les pollers Sandbox concernés via leur wake_event."""
        owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        prefix = f"{owner_uid}:{chat_id}:"
        for k, entry in list(self._sandbox_pollers.items()):
            if not k.startswith(prefix):
                continue
            rid = k[len(prefix):]
            if run_id:
                matched = rid == run_id
            elif window_id:
                wids = entry.get("window_ids")
                matched = bool(wids is not None and window_id in wids)
            else:
                matched = True
            if matched:
                wake_ev = entry.get("wake_event")
                if wake_ev is not None:
                    wake_ev.set()

    async def reconcile_canvas_frames(self, user_id: str, chat_id: str) -> None:
        """Réconcilie l'état des frames Canvas actives avec le Worker Sandbox."""
        try:
            owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
            cstate = self._canvas_ui_state.get((owner_uid, chat_id or ""))
            if not cstate or not isinstance(cstate.get("frames"), dict):
                return
            terminal_states = ("completed", "killed", "error", "timeout")
            active_frames = [
                f for f in cstate["frames"].values()
                if isinstance(f, dict)
                and f.get("run_id")
                and f.get("server_status") not in terminal_states
            ]
            if not active_frames:
                return

            import httpx
            sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
            async with httpx.AsyncClient(timeout=1.5) as client:
                res = await client.get(
                    f"{sandbox_base}/status",
                    params={"user_id": owner_uid, "chat_id": chat_id},
                )
            if res.status_code != 200:
                return
            jobs = res.json().get("jobs", [])
            jobs_by_id = {str(j.get("run_id")): j for j in jobs if isinstance(j, dict) and j.get("run_id")}
            now = time.time()
            for entry in active_frames:
                rid = str(entry.get("run_id") or "")
                if not rid:
                    continue
                job = jobs_by_id.get(rid)
                if job is None:
                    entry["server_status"] = "error"
                    entry["updated_at"] = now
                    self.stop_sandbox_poller(owner_uid, chat_id, run_id=rid)
                elif job.get("state") not in ("running", "unknown"):
                    entry["server_status"] = str(job.get("state") or "completed")
                    entry["exit_code"] = job.get("exit_code")
                    entry["updated_at"] = now
                    self.stop_sandbox_poller(owner_uid, chat_id, run_id=rid)
        except Exception:
            pass

    async def broadcast_canvas_frame(
        self,
        user_id: str,
        chat_id: str,
        frame_data: Dict[str, Any],
        auto_open: bool = True,
        run_id: str = "",
    ) -> None:
        """Enregistre une frame Canvas et la diffuse via SSE vers l'onglet natif echo-canvas."""
        owner_uid = self._resolve_owner_uid(user_id, chat_id) if chat_id else (user_id or "system")
        cstate = self._get_canvas_state(user_id, chat_id)
        wid = str(frame_data.get("id") or frame_data.get("window_id") or "default")
        new_rid = str(run_id or frame_data.get("run_id") or "")

        prev = cstate["frames"].get(wid)
        if isinstance(prev, dict):
            prev_rid = str(prev.get("run_id") or "")
            if (
                prev_rid
                and prev_rid != new_rid
                and prev.get("server_status") not in ("completed", "killed", "error", "timeout")
            ):
                self.stop_sandbox_poller(owner_uid, chat_id, run_id=prev_rid)
                cstate["inbox"].pop((wid, prev_rid), None)
                try:
                    asyncio.create_task(self._fire_and_forget_sandbox_kill(owner_uid, chat_id, prev_rid))
                except Exception:
                    pass

        frame_entry = {
            "id": wid,
            "title": str(frame_data.get("title") or "Monitor"),
            "html": str(frame_data.get("html") or ""),
            "src": str(frame_data.get("src") or ""),
            "width": str(frame_data.get("width") or "100%"),
            "height": str(frame_data.get("height") or "400px"),
            "run_id": new_rid,
            "server_status": str(frame_data.get("server_status") or "running"),
            "exit_code": frame_data.get("exit_code"),
            "ready": bool(frame_data.get("ready", False)),
            "updated_at": time.time(),
        }
        cstate["frames"][wid] = frame_entry
        cstate["pings"].pop(wid, None)
        cstate["closed"].pop(wid, None)
        await self.broadcast(
            user_id,
            {
                "type": "window_mounted",
                "chat_id": chat_id,
                "auto": auto_open,
                **frame_entry,
            },
        )

    async def broadcast_canvas_status(
        self,
        user_id: str,
        chat_id: str,
        window_ids: Any,
        server_status: str,
        run_id: str = "",
        exit_code: Optional[int] = None,
    ) -> None:
        """Met à jour le statut serveur des frames Canvas ciblées et le diffuse via SSE."""
        if isinstance(window_ids, (list, tuple, set)):
            wids = [str(w) for w in window_ids if w]
        elif window_ids:
            wids = [str(window_ids)]
        else:
            wids = []
        cstate = self._get_canvas_state(user_id, chat_id)
        now = time.time()
        for wid in wids:
            entry = cstate["frames"].get(wid)
            if isinstance(entry, dict) and (not run_id or not entry.get("run_id") or entry.get("run_id") == run_id):
                entry["server_status"] = server_status
                entry["exit_code"] = exit_code
                entry["updated_at"] = now
        await self.broadcast(
            user_id,
            {
                "type": "echo_canvas_status",
                "chat_id": chat_id,
                "window_ids": wids,
                "run_id": run_id,
                "server_status": server_status,
                "exit_code": exit_code,
            },
        )

    async def broadcast_canvas_event(
        self,
        user_id: str,
        chat_id: str,
        window_id: str,
        data: Any,
        req_id: Optional[str] = None,
    ) -> None:
        """Diffuse un événement temps réel vers une frame Canvas côté client via SSE."""
        await self.broadcast(
            user_id,
            {
                "type": "echo_canvas_event",
                "chat_id": chat_id,
                "window_id": str(window_id or "default"),
                "data": data,
                "req_id": req_id,
            },
        )

    def peek_canvas_ui_payload(
        self,
        user_id: str,
        chat_id: str,
        window_ids: Optional[Set[str]] = None,
        run_id: str = "",
    ) -> Tuple[Dict[str, Any], Dict[Any, int]]:
        """Lit les payloads UI, pings, fermetures et événements inbox Canvas sans vider la file inbox."""
        cstate = self._get_canvas_state(user_id, chat_id)
        out: Dict[str, Any] = {}
        for wid, val in cstate["payloads"].items():
            if window_ids is not None and wid not in window_ids:
                continue
            out[wid] = dict(val) if isinstance(val, dict) else val

        pings = {
            wid: ts
            for wid, ts in cstate["pings"].items()
            if window_ids is None or wid in window_ids
        }
        if pings:
            out["_pings"] = pings

        closed_wids = [
            wid
            for wid in cstate["closed"]
            if window_ids is None or wid in window_ids
        ]
        if closed_wids:
            out["_closed_windows"] = closed_wids
            for wid in closed_wids:
                existing = out.get(wid)
                if isinstance(existing, dict):
                    existing["_is_closed"] = True
                else:
                    out[wid] = {"_is_closed": True}

        if cstate["signal"] is not None:
            out["_echo_signal"] = cstate["signal"]

        inbox_events = []
        ack_counts: Dict[Any, int] = {}
        for inbox_key, q_list in cstate["inbox"].items():
            if isinstance(inbox_key, tuple) and len(inbox_key) == 2:
                wid, r_id = inbox_key
            else:
                wid, r_id = str(inbox_key), ""
            if window_ids is not None and wid not in window_ids:
                continue
            if run_id and r_id not in (run_id, ""):
                continue
            if isinstance(q_list, list) and q_list:
                inbox_events.extend(q_list)
                ack_counts[inbox_key] = len(q_list)
        if inbox_events:
            out["_inbox_events"] = inbox_events
        return out, ack_counts

    def ack_canvas_inbox(self, user_id: str, chat_id: str, ack_counts: Dict[Any, int]) -> None:
        """Acquitte les événements inbox consommés en supprimant le préfixe traité."""
        cstate = self._get_canvas_state(user_id, chat_id)
        for inbox_key, count in ack_counts.items():
            if count <= 0:
                continue
            q_list = cstate["inbox"].get(inbox_key)
            if isinstance(q_list, list):
                del q_list[:count]
                if not q_list:
                    cstate["inbox"].pop(inbox_key, None)

    def consume_canvas_ui_payload(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        """Retourne les payloads UI Canvas courants et vide atomiquement la file inbox."""
        out, ack_counts = self.peek_canvas_ui_payload(user_id, chat_id)
        if ack_counts:
            self.ack_canvas_inbox(user_id, chat_id, ack_counts)
        return out

    def cleanup_canvas_run(
        self,
        user_id: str,
        chat_id: str,
        window_ids: Any,
        run_id: str = "",
    ) -> None:
        """Nettoie les pings, inbox et marqueurs de fermeture associés aux fenêtres d'un run."""
        if isinstance(window_ids, (list, tuple, set)):
            wids = [str(w) for w in window_ids if w]
        elif window_ids:
            wids = [str(window_ids)]
        else:
            wids = []
        cstate = self._get_canvas_state(user_id, chat_id)
        for wid in wids:
            if run_id:
                cstate["inbox"].pop((wid, run_id), None)
                current_frame = cstate["frames"].get(wid)
                if not isinstance(current_frame, dict) or current_frame.get("run_id", "") in ("", run_id):
                    cstate["pings"].pop(wid, None)
                    cstate["closed"].pop(wid, None)
                    cstate["inbox"].pop((wid, ""), None)
                    cstate["inbox"].pop(wid, None)
            else:
                cstate["pings"].pop(wid, None)
                cstate["closed"].pop(wid, None)
                cstate["inbox"].pop(wid, None)
                for k in [
                    ik for ik in list(cstate["inbox"].keys())
                    if isinstance(ik, tuple) and len(ik) == 2 and ik[0] == wid
                ]:
                    cstate["inbox"].pop(k, None)

    def clear_canvas_signal(self, user_id: str, chat_id: str) -> None:
        """Réinitialise le signal Canvas (ex: abort) après prise en compte."""
        cstate = self._get_canvas_state(user_id, chat_id)
        cstate["signal"] = None

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

    async def handle_rpc(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les retours RPC, le cycle de vie DOM des fenêtres et la synchronisation KV."""
        action = payload.get("action") or ""
        client_id = str(payload.get("client_id") or "")
        chat_id = str(payload.get("chat_id") or "")
        exec_id = str(payload.get("exec_id") or "")

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
                cstate = self._get_canvas_state(user_id, chat_id)
                now_ms = time.time() * 1000.0
                ui_payload = payload.get("echoUIPayload")
                if isinstance(ui_payload, dict):
                    wid = str(ui_payload.get("window_id") or payload.get("frame_id") or "default")
                    cstate["payloads"][wid] = ui_payload.get("payload")
                    cstate["pings"][wid] = now_ms
                    cstate["closed"].pop(wid, None)
                    self.wake_sandbox_pollers(
                        user_id,
                        chat_id,
                        run_id=str(ui_payload.get("run_id") or payload.get("run_id") or ""),
                        window_id=wid,
                    )
                ui_request = payload.get("echoUIRequest")
                if isinstance(ui_request, dict):
                    req = dict(ui_request)
                    wid = str(req.get("window_id") or payload.get("frame_id") or "default")
                    req["window_id"] = wid
                    req_run_id = str(
                        req.get("run_id")
                        or payload.get("run_id")
                        or (cstate["frames"].get(wid) or {}).get("run_id")
                        or ""
                    )
                    q_list = cstate["inbox"].setdefault((wid, req_run_id), [])
                    q_list.append(req)
                    if len(q_list) > 256:
                        del q_list[:-256]
                    cstate["pings"][wid] = now_ms
                    cstate["closed"].pop(wid, None)
                    self.wake_sandbox_pollers(user_id, chat_id, run_id=req_run_id, window_id=wid)
                if payload.get("echoSignal") == "abort":
                    cstate["signal"] = "abort"
                    self.wake_sandbox_pollers(user_id, chat_id)
                ping_wid = payload.get("echoPing")
                if ping_wid:
                    s_wid = str(ping_wid)
                    cstate["pings"][s_wid] = now_ms
                    cstate["closed"].pop(s_wid, None)
                ping_wids = payload.get("echoPings")
                if isinstance(ping_wids, list):
                    for pw in ping_wids:
                        if pw:
                            s_wid = str(pw)
                            cstate["pings"][s_wid] = now_ms
                            cstate["closed"].pop(s_wid, None)
                closed_val = payload.get("window_closed")
                if closed_val:
                    closed_items = [closed_val] if isinstance(closed_val, str) else (closed_val if isinstance(closed_val, list) else [])
                    remove_frame = bool(payload.get("remove_frame", False))
                    for cw in closed_items:
                        if cw:
                            s_wid = str(cw)
                            target_rid = str((cstate["frames"].get(s_wid) or {}).get("run_id") or "")
                            cstate["closed"][s_wid] = now_ms
                            cstate["pings"].pop(s_wid, None)
                            if remove_frame:
                                cstate["frames"].pop(s_wid, None)
                            self.wake_sandbox_pollers(user_id, chat_id, run_id=target_rid, window_id=s_wid)
            return {"status": "ok"}

        if action == "canvas_stop_run":
            if not chat_id:
                return {"status": "error", "error": "missing chat_id"}
            owner_uid = self._resolve_owner_uid(user_id, chat_id)
            cstate = self._get_canvas_state(user_id, chat_id)
            wid = str(payload.get("frame_id") or payload.get("window_id") or "default")
            target_rid = str(payload.get("run_id") or (cstate["frames"].get(wid) or {}).get("run_id") or "")
            cstate["closed"][wid] = time.time() * 1000.0
            self.wake_sandbox_pollers(owner_uid, chat_id, run_id=target_rid, window_id=wid)
            poller = self.get_sandbox_poller(owner_uid, chat_id, target_rid) if target_rid else None
            poller_active = bool(poller and poller.get("task") and not poller["task"].done())
            if not poller_active:
                try:
                    import httpx
                    sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.post(
                            f"{sandbox_base}/kill",
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
