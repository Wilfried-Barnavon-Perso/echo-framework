# -*- coding: utf-8 -*-
"""
title: ECHO Window Manager
author: Wilfried BARNAVON
version: 6.13.1.2
description: Gestionnaire de fenêtres et d'exécutions JS agnostique, multi-chats et multi-navigateurs.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.13.1.2: Ajout du canal SSE dédié browser-live (_browser_live_subscribers, subscribe_browser_live, unsubscribe_browser_live, has_browser_live_subscribers, broadcast_browser_live).
# 6.13.1.1: Suppression du faux ping serveur, inbox Canvas scopée par window_id avec peek/ack, support echoPings batch et window_closed (_is_closed), broadcast_canvas_status, cleanup_canvas_run et exposition canvas_frames dans get_chat_snapshot.
# 6.7.0.1: Purge des payloads de fenêtres fermées (_sanitize_windows_dict), auto-guérison wm_state, bridage update_codes et suppression du code mort get_client_loader_js.
# 6.6.16.2: Ajout du bus Canvas bidirectionnel (broadcast_canvas_frame, broadcast_canvas_event, consume_canvas_ui_payload, clear_canvas_signal et RPC canvas_ui_event).
# 6.6.9.1: Suppression du loader JS/FastAPI legacy (get_full_loader_bundle_js, inject_client_loader, wm_router, wm_static_router, register_echo_wm_routes, resolve_wm_user_id).

import asyncio
from contextlib import closing
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, Optional, Set, Tuple

import orjson as json

from echo_constants import ECHO_BASE_DATA_DIR, ECHO_UCTP_CHUNK_SIZE, ECHO_USERS_ROOT
from echo_paths import sanitize_echo_id
from echo_state_manager import EchoStateManager

WM_STATE_KEY = "wm_state"
MAX_TRACKED_WINDOWS_PER_CHAT = 32
MAX_KV_ENTRIES_PER_CHAT = 128
TRANSIENT_HUD_PREFIXES = ("uctp-loader-", "echo-edge-hud", "echo-migration-hud", "echo-clone-hud")
_OWUI_DB_PATH = os.path.join(ECHO_BASE_DATA_DIR, "webui.db")
_EMAIL_TO_UID_CACHE: Dict[str, str] = {}


def _is_transient_hud_id(wid: str) -> bool:
    """Indique si l'ID DOM cible un HUD transitoire/global exclu de wm_state."""
    s_wid = str(wid or "").strip()
    return not s_wid or s_wid.startswith(TRANSIENT_HUD_PREFIXES)


def _sanitize_windows_dict(wins: Dict[str, Any]) -> bool:
    """Assainit in-place le dictionnaire des fenêtres (auto-guérison du bloat SQLite). Retourne True si modifié."""
    if not isinstance(wins, dict):
        return False
    dirty = False
    for wid in list(wins.keys()):
        w_entry = wins.get(wid)
        if _is_transient_hud_id(wid) or not isinstance(w_entry, dict):
            wins.pop(wid, None)
            dirty = True
            continue
        if not w_entry.get("is_open"):
            if w_entry.get("mount_code") or w_entry.get("update_code") or w_entry.get("update_codes"):
                w_entry["mount_code"] = ""
                w_entry["update_code"] = ""
                w_entry["update_codes"] = []
                dirty = True
            continue
        uc_list = w_entry.get("update_codes")
        if wid == "echo-nav-context-hud-wrapper":
            latest_uc = (uc_list[-1] if isinstance(uc_list, list) and uc_list else "") or w_entry.get("update_code") or ""
            if latest_uc and latest_uc != w_entry.get("mount_code"):
                w_entry["mount_code"] = latest_uc
                dirty = True
            if w_entry.get("update_code") or uc_list:
                w_entry["update_code"] = ""
                w_entry["update_codes"] = []
                dirty = True
        elif wid != "echo-codex-hud":
            if isinstance(uc_list, list) and len(uc_list) > 1:
                w_entry["update_codes"] = [uc_list[-1]]
                w_entry["update_code"] = uc_list[-1]
                dirty = True
    return dirty


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
        # Cache mémoire de l'état par (user_id, chat_id)
        self._chat_states: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Etat Canvas bidirectionnel par (user_id, chat_id)
        self._canvas_ui_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Abonnés live screencast navigateur par (user_id, chat_id) -> { client_id: Queue }
        self._browser_live_subscribers: Dict[Tuple[str, str], Dict[str, asyncio.Queue]] = {}

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

    async def broadcast_canvas_frame(
        self,
        user_id: str,
        chat_id: str,
        frame_data: Dict[str, Any],
        auto_open: bool = True,
        run_id: str = "",
    ) -> None:
        """Enregistre une frame Canvas et la diffuse via SSE vers l'onglet natif echo-canvas."""
        cstate = self._get_canvas_state(user_id, chat_id)
        wid = str(frame_data.get("id") or frame_data.get("window_id") or "default")
        frame_entry = {
            "id": wid,
            "title": str(frame_data.get("title") or "Monitor"),
            "html": str(frame_data.get("html") or ""),
            "src": str(frame_data.get("src") or ""),
            "width": str(frame_data.get("width") or "100%"),
            "height": str(frame_data.get("height") or "400px"),
            "run_id": str(run_id or frame_data.get("run_id") or ""),
            "server_status": str(frame_data.get("server_status") or "running"),
            "exit_code": frame_data.get("exit_code"),
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
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
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
        ack_counts: Dict[str, int] = {}
        for wid, q_list in cstate["inbox"].items():
            if window_ids is not None and wid not in window_ids:
                continue
            if isinstance(q_list, list) and q_list:
                inbox_events.extend(q_list)
                ack_counts[wid] = len(q_list)
        if inbox_events:
            out["_inbox_events"] = inbox_events
        return out, ack_counts

    def ack_canvas_inbox(self, user_id: str, chat_id: str, ack_counts: Dict[str, int]) -> None:
        """Acquitte les événements inbox consommés en supprimant le préfixe traité."""
        cstate = self._get_canvas_state(user_id, chat_id)
        for wid, count in ack_counts.items():
            if count <= 0:
                continue
            q_list = cstate["inbox"].get(wid)
            if isinstance(q_list, list):
                del q_list[:count]
                if not q_list:
                    cstate["inbox"].pop(wid, None)

    def consume_canvas_ui_payload(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        """Retourne les payloads UI Canvas courants et vide atomiquement la file inbox."""
        out, ack_counts = self.peek_canvas_ui_payload(user_id, chat_id)
        if ack_counts:
            self.ack_canvas_inbox(user_id, chat_id, ack_counts)
        return out

    def cleanup_canvas_run(self, user_id: str, chat_id: str, window_ids: Any) -> None:
        """Nettoie les pings, inbox et marqueurs de fermeture associés aux fenêtres d'un run."""
        if isinstance(window_ids, (list, tuple, set)):
            wids = [str(w) for w in window_ids if w]
        elif window_ids:
            wids = [str(window_ids)]
        else:
            wids = []
        cstate = self._get_canvas_state(user_id, chat_id)
        for wid in wids:
            cstate["pings"].pop(wid, None)
            cstate["inbox"].pop(wid, None)
            cstate["closed"].pop(wid, None)

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

    def _load_chat_state(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        if not chat_id:
            return {"windows": {}, "kv": {}}
        uid = self._resolve_owner_uid(user_id, chat_id)
        key = (uid, chat_id)
        if key in self._chat_states:
            return self._chat_states[key]

        state = {"windows": {}, "kv": {}}
        needs_heal_save = False
        try:
            sm = EchoStateManager(uid, chat_id)
            if not os.path.isfile(sm.db_path):
                return state
            raw = sm.get_session_setting(WM_STATE_KEY)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    state["windows"] = parsed.get("windows") if isinstance(parsed.get("windows"), dict) else {}
                    state["kv"] = parsed.get("kv") if isinstance(parsed.get("kv"), dict) else {}
                    if _sanitize_windows_dict(state["windows"]):
                        needs_heal_save = True
        except Exception as e:
            print(f"[EchoWindowManager] Load state error ({chat_id}): {e}")

        self._chat_states[key] = state
        if needs_heal_save:
            self._save_chat_state(uid, chat_id)
        return state

    def _save_chat_state(self, user_id: str, chat_id: str) -> None:
        if not chat_id:
            return
        uid = self._resolve_owner_uid(user_id, chat_id)
        key = (uid, chat_id)
        state = self._chat_states.get(key)
        if not state:
            return
        try:
            wins = state.get("windows", {})
            if len(wins) > MAX_TRACKED_WINDOWS_PER_CHAT:
                sorted_wids = sorted(wins.keys(), key=lambda w: wins[w].get("updated_at", 0))
                for wid in sorted_wids[:-MAX_TRACKED_WINDOWS_PER_CHAT]:
                    wins.pop(wid, None)

            kv = state.get("kv", {})
            if len(kv) > MAX_KV_ENTRIES_PER_CHAT:
                for k in list(kv.keys())[:-MAX_KV_ENTRIES_PER_CHAT]:
                    kv.pop(k, None)

            sm = EchoStateManager(uid, chat_id)
            if not os.path.isfile(sm.db_path):
                return
            sm.save_session_setting(WM_STATE_KEY, json.dumps(state).decode("utf-8"))
        except Exception as e:
            print(f"[EchoWindowManager] Save state error ({chat_id}): {e}")

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

        if action in ("window_mounted", "window_updated"):
            window_ids = payload.get("window_ids") or []
            if isinstance(window_ids, str):
                window_ids = [window_ids]
            rec = self._recent_execs.get(exec_id)
            code = payload.get("code") or (rec.get("code") if rec else "")
            effective_cid = chat_id or (rec.get("chat_id") if rec else "")
            effective_uid = (
                rec.get("user_id") if (rec and rec.get("user_id") and rec.get("user_id") != "system") else user_id
            )
            if effective_cid and code and window_ids:
                state = self._load_chat_state(effective_uid, effective_cid)
                wins = state.setdefault("windows", {})
                now = time.time()
                for wid in window_ids:
                    if _is_transient_hud_id(wid):
                        continue
                    w_entry = wins.get(wid) or {
                        "window_id": wid,
                        "chat_id": effective_cid,
                        "is_open": True,
                        "mount_code": "",
                        "update_code": "",
                        "update_codes": [],
                        "updated_at": now,
                    }
                    w_entry["is_open"] = True
                    w_entry["updated_at"] = now
                    if wid == "echo-nav-context-hud-wrapper" or action == "window_mounted" or not w_entry.get("mount_code"):
                        w_entry["mount_code"] = code
                        w_entry["update_code"] = ""
                        w_entry["update_codes"] = []
                    else:
                        if code != w_entry.get("mount_code"):
                            w_entry["update_code"] = code
                            if wid == "echo-codex-hud":
                                uc_list = w_entry.get("update_codes")
                                if not isinstance(uc_list, list):
                                    uc_list = []
                                if not uc_list or uc_list[-1] != code:
                                    uc_list.append(code)
                                w_entry["update_codes"] = uc_list[-16:]
                            else:
                                w_entry["update_codes"] = [code]
                    wins[wid] = w_entry
                self._save_chat_state(effective_uid, effective_cid)
            return {"status": "ok"}

        if action == "window_closed":
            window_id = str(payload.get("window_id") or "")
            if chat_id and window_id:
                state = self._load_chat_state(user_id, chat_id)
                wins = state.get("windows", {})
                if window_id in wins:
                    wins[window_id]["is_open"] = False
                    wins[window_id]["mount_code"] = ""
                    wins[window_id]["update_code"] = ""
                    wins[window_id]["update_codes"] = []
                    wins[window_id]["updated_at"] = time.time()
                    self._save_chat_state(user_id, chat_id)
                await self.broadcast(
                    user_id,
                    {"type": "wm_window_closed", "chat_id": chat_id, "window_id": window_id},
                    exclude_client_id=client_id,
                )
            return {"status": "ok"}

        if action == "kv_sync":
            entries = payload.get("entries")
            if chat_id and isinstance(entries, dict) and entries:
                state = self._load_chat_state(user_id, chat_id)
                kv = state.setdefault("kv", {})
                for k, v in entries.items():
                    if v is None:
                        kv.pop(str(k), None)
                    else:
                        kv[str(k)] = str(v)
                self._save_chat_state(user_id, chat_id)
                await self.broadcast(
                    user_id,
                    {"type": "wm_kv_sync", "chat_id": chat_id, "entries": entries},
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
                ui_request = payload.get("echoUIRequest")
                if isinstance(ui_request, dict):
                    req = dict(ui_request)
                    wid = str(req.get("window_id") or payload.get("frame_id") or "default")
                    req["window_id"] = wid
                    q_list = cstate["inbox"].setdefault(wid, [])
                    q_list.append(req)
                    if len(q_list) > 256:
                        del q_list[:-256]
                    cstate["pings"][wid] = now_ms
                    cstate["closed"].pop(wid, None)
                if payload.get("echoSignal") == "abort":
                    cstate["signal"] = "abort"
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
                            cstate["closed"][s_wid] = now_ms
                            cstate["pings"].pop(s_wid, None)
                            if remove_frame:
                                cstate["frames"].pop(s_wid, None)
            return {"status": "ok"}

        return {"status": "ignored"}

    def get_chat_snapshot(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        """Retourne l'état complet (KV, fenêtres ouvertes, appels RPC en attente et stats contexte) pour un chat."""
        state = self._load_chat_state(user_id, chat_id)
        wins_dict = state.get("windows", {})
        if _sanitize_windows_dict(wins_dict):
            self._save_chat_state(user_id, chat_id)

        open_windows = [
            w for wid, w in wins_dict.items()
            if not _is_transient_hud_id(wid) and isinstance(w, dict) and w.get("is_open") and w.get("mount_code")
        ]
        open_windows.sort(key=lambda x: x.get("updated_at", 0))

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
            "kv": state.get("kv", {}),
            "windows": open_windows,
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
