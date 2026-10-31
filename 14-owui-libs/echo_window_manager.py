# -*- coding: utf-8 -*-
"""
title: ECHO Window Manager
author: Wilfried BARNAVON
version: 5.231.39.1
description: Gestionnaire de fenêtres et d'exécutions JS agnostique, multi-chats et multi-navigateurs.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.39.1: Synchronisation de activeChatId à l'insertion sur route '/' en nouvelle session et bump de version EchoWM.
# 5.231.13.1: Résolution du ca_model_id actif et inclusion de calculate_context_delta dans get_chat_snapshot pour l'hydratation à froid.
# 5.231.12.1: Enrichissement de context_stats avec get_ui_quota_snapshot et affichage de la jauge à froid dès les quotas disponibles dans hydrateChatState.
# 5.231.10.1: Intégration de context_stats dans get_chat_snapshot et hydratation automatique echoRenderContextGauge dans hydrateChatState.
# 5.231.6.1: Intégration du pont universel EchoCodexUI.get_codex_bridge_js dans get_full_loader_bundle_js (/static/loader.js).

import asyncio
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import orjson as json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from echo_constants import ECHO_AUDIO_PREFIX, ECHO_USERS_ROOT
from echo_state_manager import EchoStateManager

WM_PREFIX = f"{ECHO_AUDIO_PREFIX}/wm"
wm_router = APIRouter(prefix=WM_PREFIX, tags=["echo_wm"])
wm_static_router = APIRouter(tags=["echo_wm_static"])

WM_SCRIPT_VERSION = "5.231.39.1"
LOADER_MARKER_START = "/* === ECHO_WM_START === */"
LOADER_MARKER_END = "/* === ECHO_WM_END === */"
WM_STATE_KEY = "wm_state"
MAX_TRACKED_WINDOWS_PER_CHAT = 32
MAX_KV_ENTRIES_PER_CHAT = 128


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


def resolve_wm_user_id(request: Optional[Request] = None) -> str:
    """Résout l'identifiant utilisateur depuis le JWT Bearer, le cookie token, le query param ou le disque."""
    if request is not None:
        token = ""
        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip().strip('"')
        if not token:
            token = (request.cookies.get("token") or "").strip().strip('"')
        if not token:
            token = (request.query_params.get("token") or "").strip().strip('"')

        if token:
            try:
                from open_webui.utils.auth import decode_token
                decoded = decode_token(token)
                if isinstance(decoded, dict) and decoded.get("id"):
                    return str(decoded["id"])
            except Exception:
                pass

        for hdr in ("x-openwebui-user-id", "x-webui-user", "x-user-id", "user-id"):
            val = request.headers.get(hdr)
            if val and val.strip():
                return val.strip()

    try:
        if os.path.exists(ECHO_USERS_ROOT):
            candidates = [
                d for d in os.listdir(ECHO_USERS_ROOT)
                if os.path.isdir(os.path.join(ECHO_USERS_ROOT, d)) and not d.startswith(".")
            ]
            for candidate in candidates:
                if os.path.exists(os.path.join(ECHO_USERS_ROOT, candidate, "identity.db")):
                    return candidate
            if candidates:
                return candidates[0]
    except Exception:
        pass
    return "system"


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

    def _load_chat_state(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        if not chat_id:
            return {"windows": {}, "kv": {}}
        uid = user_id or "system"
        key = (uid, chat_id)
        if key in self._chat_states:
            return self._chat_states[key]

        state = {"windows": {}, "kv": {}}
        try:
            sm = EchoStateManager(uid, chat_id)
            raw = sm.get_session_setting(WM_STATE_KEY)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    state["windows"] = parsed.get("windows") if isinstance(parsed.get("windows"), dict) else {}
                    state["kv"] = parsed.get("kv") if isinstance(parsed.get("kv"), dict) else {}
        except Exception as e:
            print(f"[EchoWindowManager] Load state error ({chat_id}): {e}")

        self._chat_states[key] = state
        return state

    def _save_chat_state(self, user_id: str, chat_id: str) -> None:
        if not chat_id:
            return
        uid = user_id or "system"
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
        delivered = await self.broadcast(
            user_id,
            {
                "type": "wm_execute",
                "mode": "emit",
                "exec_id": eid,
                "chat_id": chat_id or "",
                "message_id": message_id or "",
                "code": code,
            },
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
            delivered = await self.broadcast(
                user_id,
                {
                    "type": "wm_execute",
                    "mode": "call",
                    "exec_id": eid,
                    "chat_id": chat_id or "",
                    "message_id": message_id or "",
                    "code": code,
                },
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
            if effective_cid and code and window_ids:
                state = self._load_chat_state(user_id, effective_cid)
                wins = state.setdefault("windows", {})
                now = time.time()
                for wid in window_ids:
                    if not wid or str(wid).startswith("uctp-loader-"):
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
                    if action == "window_mounted" or not w_entry.get("mount_code"):
                        w_entry["mount_code"] = code
                        w_entry["update_code"] = ""
                        w_entry["update_codes"] = []
                    else:
                        if code != w_entry.get("mount_code"):
                            w_entry["update_code"] = code
                            uc_list = w_entry.get("update_codes")
                            if not isinstance(uc_list, list):
                                uc_list = []
                            if not uc_list or uc_list[-1] != code:
                                uc_list.append(code)
                            w_entry["update_codes"] = uc_list[-16:]
                    wins[wid] = w_entry
                self._save_chat_state(user_id, effective_cid)
            return {"status": "ok"}

        if action == "window_closed":
            window_id = str(payload.get("window_id") or "")
            if chat_id and window_id:
                state = self._load_chat_state(user_id, chat_id)
                wins = state.get("windows", {})
                if window_id in wins:
                    wins[window_id]["is_open"] = False
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

        return {"status": "ignored"}

    def get_chat_snapshot(self, user_id: str, chat_id: str) -> Dict[str, Any]:
        """Retourne l'état complet (KV, fenêtres ouvertes, appels RPC en attente et stats contexte) pour un chat."""
        state = self._load_chat_state(user_id, chat_id)
        open_windows = [
            w for w in state.get("windows", {}).values()
            if isinstance(w, dict) and w.get("is_open") and w.get("mount_code")
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
            sm = EchoStateManager(user_id or "system", chat_id)
            context_stats = sm.get_last_context_stats() or {}
            prev_stats = sm.get_previous_context_stats()
            from echo_auth import EchoAuth
            from echo_protocol import get_ca_model_id
            from echo_ui import EchoUI
            last_model = sm.get_last_active_model()
            ca_model_id = get_ca_model_id(last_model) if last_model else None
            quota_snap = EchoAuth(user_id or "system").get_ui_quota_snapshot(ca_model_id)
            if quota_snap:
                context_stats.update(quota_snap)
            context_stats["delta"] = EchoUI.calculate_context_delta(context_stats, prev_stats)
        except Exception:
            pass

        return {
            "chat_id": chat_id,
            "kv": state.get("kv", {}),
            "windows": open_windows,
            "pending_calls": pending,
            "context_stats": context_stats,
        }


_WM_SINGLETON: Optional[EchoWindowManager] = None


def get_window_manager() -> EchoWindowManager:
    global _WM_SINGLETON
    if _WM_SINGLETON is None:
        _WM_SINGLETON = EchoWindowManager()
    return _WM_SINGLETON


def get_client_loader_js() -> str:
    """Génère le script client global agnostique window.EchoWM injecté dans /static/loader.js et via window_manager_filter."""
    return r"""
(function() {
    if (window.EchoWM && window.EchoWM._version === '5.231.39.1') {
        if (typeof window.EchoWM.connectSSE === 'function') window.EchoWM.connectSSE();
        if (typeof window.EchoWM.onRouteChanged === 'function') window.EchoWM.onRouteChanged();
        return;
    }

    const clientId = (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : ('wm-' + Math.random().toString(36).slice(2) + Date.now());

    const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
    const rawSetItem = Storage.prototype.setItem;
    const rawRemoveItem = Storage.prototype.removeItem;
    const rawAppendChild = Element.prototype.appendChild;
    const rawInsertBefore = Element.prototype.insertBefore;

    function extractChatIdFromUrl(pathname) {
        const p = pathname || (typeof location !== 'undefined' ? location.pathname : '');
        const m = p.match(/\/c\/([a-zA-Z0-9_-]+)/);
        return m ? m[1] : '';
    }

    function getAuthToken() {
        try {
            const raw = localStorage.getItem('token') || '';
            return raw.replace(/^"|"$/g, '');
        } catch (e) {
            return '';
        }
    }

    function getElementChatId(el) {
        if (!el || typeof el.getAttribute !== 'function') return '';
        return el.getAttribute('data-echo-chat-id') || el.getAttribute('data-chat-id') || '';
    }

    function isGlobalElement(el) {
        if (!el || el.nodeType !== 1) return true;
        const tag = (el.tagName || '').toUpperCase();
        if (tag === 'STYLE' || tag === 'SCRIPT' || tag === 'LINK') return true;
        if (el.getAttribute('data-echo-scope') === 'global') return true;
        const id = el.id || '';
        if (id.startsWith('uctp-loader-') || id.startsWith('echo-edge-hud') || id === 'echo-hud-container' || id === 'echo-app-drawer-container') return true;
        if (el.classList && el.classList.contains('echo-tooltip-floating')) return true;
        return false;
    }

    function isWaitRpcCode(code) {
        if (!code || typeof code !== 'string') return false;
        return /new\s+Promise\s*\(\s*r\s*=>\s*\{\s*window\./.test(code);
    }

    const EchoWM = {
        _version: '5.231.39.1',
        clientId: clientId,
        activeChatId: extractChatIdFromUrl(),
        currentExecContext: null,
        suppressCloseReport: false,
        suppressKvIntercept: false,
        isReplaying: false,
        hydratedChats: {},
        hydratingPromises: {},
        executedIds: {},
        executedPromises: {},
        kvFlushTimers: {},
        kvPending: {},
        sse: null,
        getElementChatId: getElementChatId,

        postRpc: async function(payload) {
            try {
                const token = getAuthToken();
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;
                await fetch('/echo/v1/wm/rpc', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify(Object.assign({ client_id: clientId }, payload))
                });
            } catch (e) {}
        },

        queueKvSync: function(chatId, key, value) {
            if (!chatId || !key || EchoWM.suppressKvIntercept) return;
            if (!EchoWM.kvPending[chatId]) EchoWM.kvPending[chatId] = {};
            EchoWM.kvPending[chatId][key] = value;
            if (EchoWM.kvFlushTimers[chatId]) clearTimeout(EchoWM.kvFlushTimers[chatId]);
            EchoWM.kvFlushTimers[chatId] = setTimeout(() => {
                const entries = EchoWM.kvPending[chatId];
                delete EchoWM.kvPending[chatId];
                delete EchoWM.kvFlushTimers[chatId];
                if (entries && Object.keys(entries).length > 0) {
                    EchoWM.postRpc({ action: 'kv_sync', chat_id: chatId, entries: entries });
                }
            }, 250);
        },

        getGeometryKey: function(hud) {
            const cid = getElementChatId(hud) || EchoWM.activeChatId || 'global';
            const wid = (hud && hud.id) || 'default';
            return 'echo_geom_' + cid + '_' + wid;
        },

        loadGeometry: function(hud) {
            try {
                const key = EchoWM.getGeometryKey(hud);
                const raw = localStorage.getItem(key);
                return raw ? JSON.parse(raw) : null;
            } catch (e) {
                return null;
            }
        },

        saveGeometry: function(hud, bounds, ctrl) {
            try {
                if (!hud || !hud.id) return;
                const cid = getElementChatId(hud) || EchoWM.activeChatId || '';
                const key = EchoWM.getGeometryKey(hud);
                const data = JSON.stringify({
                    x: bounds.x,
                    y: bounds.y,
                    w: bounds.w,
                    h: bounds.h,
                    tx: bounds.tx,
                    isMinimized: !!(ctrl && ctrl.isMinimized),
                    isFullscreen: !!(ctrl && ctrl.isFullscreen)
                });
                rawSetItem.call(localStorage, key, data);
                if (cid) EchoWM.queueKvSync(cid, key, data);
            } catch (e) {}
        },

        resetWindowMinimized: function(hud) {
            try {
                if (!hud || !hud.id) return;
                const key = EchoWM.getGeometryKey(hud);
                const raw = localStorage.getItem(key);
                if (raw) {
                    const parsed = JSON.parse(raw);
                    if (parsed && parsed.isMinimized) {
                        parsed.isMinimized = false;
                        const data = JSON.stringify(parsed);
                        rawSetItem.call(localStorage, key, data);
                        const cid = getElementChatId(hud) || EchoWM.activeChatId || '';
                        if (cid) EchoWM.queueKvSync(cid, key, data);
                    }
                }
            } catch (e) {}
        },

        applyParkingForElement: function(el) {
            if (!el || el.nodeType !== 1) return;
            const cid = getElementChatId(el);
            if (!cid) return;
            if (!el.getAttribute('data-echo-chat-id')) {
                el.setAttribute('data-echo-chat-id', cid);
            }
            const active = EchoWM.activeChatId;
            if (active && cid === active) {
                if (el.getAttribute('data-echo-parked') === '1') {
                    const prev = el.getAttribute('data-echo-prev-display');
                    const restoreDisplay = (prev && prev !== 'none') ? prev : 'flex';
                    el.style.removeProperty('display');
                    el.style.setProperty('display', restoreDisplay);
                    el.setAttribute('data-echo-parked', '0');
                    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
                        window.requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
                    }
                }
            } else {
                if (el.getAttribute('data-echo-parked') !== '1') {
                    const cur = (el.style.getPropertyValue ? el.style.getPropertyValue('display') : '') || el.style.display;
                    if (cur && cur !== 'none') {
                        el.setAttribute('data-echo-prev-display', cur);
                    }
                    el.setAttribute('data-echo-parked', '1');
                }
                el.style.setProperty('display', 'none', 'important');
            }
        },

        syncAllWindowsVisibility: function() {
            if (!document.body) return;
            const nodes = document.body.querySelectorAll('[data-echo-chat-id], [data-chat-id]');
            nodes.forEach(el => EchoWM.applyParkingForElement(el));
        },

        tagInsertedElement: function(el) {
            if (!el || el.nodeType !== 1 || isGlobalElement(el)) return;
            const ctx = EchoWM.currentExecContext;
            const explicitCid = getElementChatId(el);
            const cid = explicitCid || (ctx && ctx.chatId) || '';
            if (!cid) return;
            if (!EchoWM.activeChatId && !extractChatIdFromUrl()) {
                EchoWM.activeChatId = cid;
            }

            if (!el.id) {
                const baseId = (ctx && ctx.execId) ? ctx.execId.slice(0, 10) : Math.random().toString(36).slice(2, 10);
                el.id = 'echo-auto-win-' + baseId;
            }
            el.setAttribute('data-echo-chat-id', cid);
            if (!el.getAttribute('data-chat-id')) {
                el.setAttribute('data-chat-id', cid);
            }
            if (ctx && ctx.mountedIds) {
                ctx.mountedIds.add(el.id);
            }
            EchoWM.applyParkingForElement(el);
        },

        runInChatScope: async function(chatId, execId, code, mode, isReplay) {
            if (execId && !isReplay) {
                if (EchoWM.executedPromises[execId]) {
                    return await EchoWM.executedPromises[execId];
                }
                EchoWM.executedIds[execId] = Date.now();
            }

            const execTask = async () => {
                const waitRpc = isWaitRpcCode(code);
                const prevCtx = EchoWM.currentExecContext;
                const prevReplay = EchoWM.isReplaying;
                const ctx = {
                    chatId: chatId || '',
                    execId: execId || '',
                    mountedIds: new Set(),
                    updatedIds: new Set()
                };
                if (!waitRpc) {
                    EchoWM.currentExecContext = ctx;
                    EchoWM.suppressCloseReport = true;
                    if (isReplay) EchoWM.isReplaying = true;
                }

                let mutObs = null;
                if (!waitRpc && document.body && typeof MutationObserver !== 'undefined') {
                    mutObs = new MutationObserver((mutations) => {
                        for (const m of mutations) {
                            let node = m.target;
                            while (node && node.parentElement && node.parentElement !== document.body) {
                                node = node.parentElement;
                            }
                            if (node && node.parentElement === document.body && node.id && !isGlobalElement(node)) {
                                const nodeCid = getElementChatId(node);
                                if (!ctx.mountedIds.has(node.id) && (!chatId || !nodeCid || nodeCid === chatId)) {
                                    if (chatId && !nodeCid) {
                                        node.setAttribute('data-echo-chat-id', chatId);
                                        node.setAttribute('data-chat-id', chatId);
                                        EchoWM.applyParkingForElement(node);
                                    }
                                    ctx.updatedIds.add(node.id);
                                }
                            }
                        }
                    });
                    mutObs.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
                }

                let result = null;
                try {
                    const fn = new AsyncFunction(code);
                    result = await fn();
                } catch (err) {
                    console.error('[EchoWM] Execution error:', err);
                } finally {
                    if (mutObs) mutObs.disconnect();
                    if (!waitRpc) {
                        EchoWM.currentExecContext = prevCtx;
                        EchoWM.isReplaying = prevReplay;
                        setTimeout(() => { EchoWM.suppressCloseReport = false; }, 0);
                        EchoWM.syncAllWindowsVisibility();
                    }
                }

                if (!isReplay && !waitRpc && chatId) {
                    const liveMounted = Array.from(ctx.mountedIds).filter(id => {
                        const el = document.getElementById(id);
                        return el && !isGlobalElement(el);
                    });
                    const liveUpdated = Array.from(ctx.updatedIds).filter(id => {
                        const el = document.getElementById(id);
                        return el && !ctx.mountedIds.has(id) && !isGlobalElement(el);
                    });

                    if (liveMounted.length > 0) {
                        EchoWM.postRpc({
                            action: 'window_mounted',
                            chat_id: chatId,
                            exec_id: execId,
                            window_ids: liveMounted
                        });
                    } else if (liveUpdated.length > 0) {
                        EchoWM.postRpc({
                            action: 'window_updated',
                            chat_id: chatId,
                            exec_id: execId,
                            window_ids: liveUpdated
                        });
                    }
                }

                if (mode === 'call' && execId) {
                    await EchoWM.postRpc({
                        action: 'rpc_result',
                        chat_id: chatId,
                        exec_id: execId,
                        result: result
                    });
                }
                return result;
            };

            if (execId && !isReplay) {
                const p = execTask();
                EchoWM.executedPromises[execId] = p;
                try {
                    return await p;
                } finally {
                    const t = setTimeout(() => { delete EchoWM.executedPromises[execId]; }, 60000);
                    if (t && typeof t.unref === 'function') t.unref();
                }
            }
            return await execTask();
        },

        hydrateChatState: async function(chatId) {
            if (!chatId) return;
            if (EchoWM.hydratingPromises[chatId]) {
                return await EchoWM.hydratingPromises[chatId];
            }
            const task = (async () => {
                try {
                    const token = getAuthToken();
                    const headers = {};
                    if (token) headers['Authorization'] = 'Bearer ' + token;
                    const res = await fetch('/echo/v1/wm/chat/' + encodeURIComponent(chatId), { headers: headers });
                    if (!res.ok) return;
                    const snapshot = await res.json();

                    if (snapshot.kv && typeof snapshot.kv === 'object') {
                        EchoWM.suppressKvIntercept = true;
                        try {
                            for (const [k, v] of Object.entries(snapshot.kv)) {
                                if (v !== null && v !== undefined) {
                                    rawSetItem.call(localStorage, k, String(v));
                                }
                            }
                        } finally {
                            EchoWM.suppressKvIntercept = false;
                        }
                    }

                    if (Array.isArray(snapshot.windows)) {
                        for (const win of snapshot.windows) {
                            if (!win || !win.window_id || !win.is_open) continue;
                            const existing = document.getElementById(win.window_id);
                            const existingCid = getElementChatId(existing);
                            if (!existing || (existingCid && existingCid !== chatId)) {
                                if (win.mount_code) {
                                    await EchoWM.runInChatScope(chatId, 'replay-mount-' + win.window_id, win.mount_code, 'emit', true);
                                }
                                const uCodes = Array.isArray(win.update_codes) && win.update_codes.length > 0
                                    ? win.update_codes
                                    : (win.update_code ? [win.update_code] : []);
                                for (let i = 0; i < uCodes.length; i++) {
                                    if (uCodes[i]) {
                                        await EchoWM.runInChatScope(chatId, 'replay-upd-' + win.window_id + '-' + i, uCodes[i], 'emit', true);
                                    }
                                }
                            }
                        }
                    }

                    if (Array.isArray(snapshot.pending_calls)) {
                        for (const pc of snapshot.pending_calls) {
                            if (pc && pc.exec_id && pc.code && !EchoWM.executedIds[pc.exec_id]) {
                                EchoWM.runInChatScope(chatId, pc.exec_id, pc.code, 'call', false);
                            }
                        }
                    }

                    if (snapshot.context_stats && (snapshot.context_stats.totalTokenCount > 0 || snapshot.context_stats.quota_fraction != null)) {
                        if (typeof window.echoRenderContextGauge === 'function') {
                            window.echoRenderContextGauge(Object.assign({ chat_id: chatId }, snapshot.context_stats));
                        }
                    } else {
                        const g = document.getElementById('echo-nav-context-hud-wrapper');
                        if (g) g.remove();
                    }

                    EchoWM.syncAllWindowsVisibility();
                } catch (e) {
                    console.warn('[EchoWM] Hydrate error:', e);
                }
            })();
            EchoWM.hydratingPromises[chatId] = task;
            try {
                return await task;
            } finally {
                delete EchoWM.hydratingPromises[chatId];
            }
        },

        onRouteChanged: function() {
            const nextCid = extractChatIdFromUrl();
            const changed = nextCid !== EchoWM.activeChatId;
            EchoWM.activeChatId = nextCid;
            EchoWM.syncAllWindowsVisibility();
            if (nextCid && (changed || !EchoWM.hydratedChats[nextCid])) {
                EchoWM.hydratedChats[nextCid] = true;
                EchoWM.hydrateChatState(nextCid);
            }
        },

        connectSSE: function() {
            if (EchoWM.sse && EchoWM.sse.readyState !== 2) return;
            const token = getAuthToken();
            const url = '/echo/v1/wm/stream?client_id=' + encodeURIComponent(clientId)
                + (token ? '&token=' + encodeURIComponent(token) : '');
            try {
                const es = new EventSource(url, { withCredentials: true });
                EchoWM.sse = es;
                es.onmessage = async (ev) => {
                    try {
                        const msg = JSON.parse(ev.data);
                        if (!msg || !msg.type) return;

                        if (msg.type === 'wm_execute') {
                            await EchoWM.runInChatScope(msg.chat_id || '', msg.exec_id || '', msg.code || '', msg.mode || 'emit', false);
                        } else if (msg.type === 'wm_window_closed') {
                            const el = msg.window_id ? document.getElementById(msg.window_id) : null;
                            if (el) {
                                EchoWM.suppressCloseReport = true;
                                try { el.remove(); } finally { EchoWM.suppressCloseReport = false; }
                            }
                        } else if (msg.type === 'wm_kv_sync' && msg.entries) {
                            EchoWM.suppressKvIntercept = true;
                            try {
                                for (const [k, v] of Object.entries(msg.entries)) {
                                    if (v === null || v === undefined) rawRemoveItem.call(localStorage, k);
                                    else rawSetItem.call(localStorage, k, String(v));
                                }
                            } finally {
                                EchoWM.suppressKvIntercept = false;
                            }
                        }
                    } catch (e) {}
                };
                es.onerror = () => {
                    try { es.close(); } catch (e) {}
                    EchoWM.sse = null;
                    setTimeout(() => EchoWM.connectSSE(), 3000);
                };
            } catch (e) {
                setTimeout(() => EchoWM.connectSSE(), 3000);
            }
        }
    };

    Element.prototype.appendChild = function(node) {
        const res = rawAppendChild.call(this, node);
        if (this === document.body && node && node.nodeType === 1) {
            EchoWM.tagInsertedElement(node);
        }
        return res;
    };

    Element.prototype.insertBefore = function(node, refNode) {
        const res = rawInsertBefore.call(this, node, refNode);
        if (this === document.body && node && node.nodeType === 1) {
            EchoWM.tagInsertedElement(node);
        }
        return res;
    };

    Storage.prototype.setItem = function(key, value) {
        rawSetItem.call(this, key, value);
        if (this === localStorage && !EchoWM.suppressKvIntercept && typeof key === 'string') {
            if (key.startsWith('echo_') || key.startsWith('nav_state_')) {
                const cid = (EchoWM.currentExecContext && EchoWM.currentExecContext.chatId) || EchoWM.activeChatId || '';
                if (cid) EchoWM.queueKvSync(cid, key, String(value));
            }
        }
    };

    const origPushState = history.pushState;
    const origReplaceState = history.replaceState;
    history.pushState = function() {
        const r = origPushState.apply(this, arguments);
        EchoWM.onRouteChanged();
        return r;
    };
    history.replaceState = function() {
        const r = origReplaceState.apply(this, arguments);
        EchoWM.onRouteChanged();
        return r;
    };
    window.addEventListener('popstate', () => EchoWM.onRouteChanged());

    function attachBodyObserver() {
        if (!document.body || typeof MutationObserver === 'undefined') return;
        const bodyObs = new MutationObserver((mutations) => {
            for (const m of mutations) {
                if (m.addedNodes) {
                    m.addedNodes.forEach(node => {
                        if (node && node.nodeType === 1) {
                            EchoWM.tagInsertedElement(node);
                        }
                    });
                }
                if (m.removedNodes && !EchoWM.suppressCloseReport && !EchoWM.currentExecContext) {
                    m.removedNodes.forEach(node => {
                        if (node && node.nodeType === 1 && node.id && !isGlobalElement(node)) {
                            const cid = getElementChatId(node);
                            const parked = node.getAttribute('data-echo-parked') === '1';
                            if (cid && !parked) {
                                EchoWM.postRpc({
                                    action: 'window_closed',
                                    chat_id: cid,
                                    window_id: node.id
                                });
                            }
                        }
                    });
                }
            }
        });
        bodyObs.observe(document.body, { childList: true });
    }

    window.EchoWM = EchoWM;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            attachBodyObserver();
            EchoWM.connectSSE();
            EchoWM.onRouteChanged();
        });
    } else {
        attachBodyObserver();
        EchoWM.connectSSE();
        EchoWM.onRouteChanged();
    }
})();
"""


def get_full_loader_bundle_js() -> str:
    """Assemble le bundle complet (ESM Loader + primitives EchoUI + pont Codex + runtime window.EchoWM) isolé de crossorigin='use-credentials'."""
    parts = []
    try:
        from echo_ui import EchoUI
        parts.append(EchoUI.get_esm_loader_js().strip())
        parts.append(EchoUI.get_hud_geometry_js().strip())
        parts.append(EchoUI.get_hud_rpc_bridge_js().strip())
        parts.append(EchoUI.get_context_gauge_renderer_js().strip())
    except Exception:
        pass
    try:
        from echo_codex_ui import EchoCodexUI
        parts.append(EchoCodexUI.get_codex_bridge_js().strip())
    except Exception:
        pass
    parts.append(get_client_loader_js().strip())
    raw_bundle = "\n".join(parts)
    encoded = json.dumps(raw_bundle).decode("utf-8")
    return (
        "(function(){\n"
        f"    const code = {encoded};\n"
        "    if (typeof document !== 'undefined' && (document.head || document.documentElement)) {\n"
        "        const s = document.createElement('script');\n"
        "        s.textContent = code;\n"
        "        (document.head || document.documentElement).appendChild(s);\n"
        "        s.remove();\n"
        "    } else {\n"
        "        (0, eval)(code);\n"
        "    }\n"
        "})();"
    )


def inject_client_loader() -> bool:
    """Injecte de manière idempotente le runtime window.EchoWM dans tous les fichiers static/loader.js candidats."""
    candidate_paths = []
    try:
        from open_webui.env import STATIC_DIR, FRONTEND_BUILD_DIR
        if STATIC_DIR:
            candidate_paths.append(os.path.join(str(STATIC_DIR), "loader.js"))
        if FRONTEND_BUILD_DIR:
            candidate_paths.append(os.path.join(str(FRONTEND_BUILD_DIR), "static", "loader.js"))
    except Exception:
        pass
    for fallback_path in ("/app/backend/open_webui/static/loader.js", "/app/build/static/loader.js"):
        if fallback_path not in candidate_paths:
            candidate_paths.append(fallback_path)

    bundle_js = get_full_loader_bundle_js()
    block = f"\n{LOADER_MARKER_START}\n{bundle_js}\n{LOADER_MARKER_END}\n"

    injected_any = False
    for path in candidate_paths:
        try:
            parent = os.path.dirname(path)
            if not os.path.isdir(parent):
                continue
            existing = ""
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    existing = f.read()

            if LOADER_MARKER_START in existing and LOADER_MARKER_END in existing:
                s_idx = existing.index(LOADER_MARKER_START)
                e_idx = existing.index(LOADER_MARKER_END) + len(LOADER_MARKER_END)
                updated = existing[:s_idx].rstrip() + block + existing[e_idx:].lstrip()
            else:
                updated = existing.rstrip() + block

            if updated != existing:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(updated)
            injected_any = True
        except Exception as e:
            print(f"[EchoWindowManager] Loader injection warning ({path}): {e}")
    return injected_any


@wm_static_router.get("/static/loader.js")
async def wm_serve_loader_js():
    """Sert dynamiquement /static/loader.js sans cache navigateur pour garantir la présence de window.EchoWM dès le chargement initial."""
    content = get_full_loader_bundle_js()
    return Response(
        content=content,
        media_type="text/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@wm_router.get("/stream")
async def wm_sse_stream(request: Request, client_id: str = ""):
    user_id = resolve_wm_user_id(request)
    cid = client_id or uuid.uuid4().hex
    wm = get_window_manager()
    queue = wm.subscribe(user_id, cid)

    async def event_generator():
        try:
            init_payload = json.dumps({"type": "wm_connected", "client_id": cid, "user_id": user_id}).decode("utf-8")
            yield f"data: {init_payload}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    payload = json.dumps(msg).decode("utf-8")
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            wm.unsubscribe(user_id, cid)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@wm_router.get("/chat/{chat_id}")
async def wm_get_chat_state(chat_id: str, request: Request):
    user_id = resolve_wm_user_id(request)
    wm = get_window_manager()
    snapshot = wm.get_chat_snapshot(user_id, chat_id)
    return JSONResponse(content=snapshot)


@wm_router.post("/rpc")
async def wm_post_rpc(request: Request):
    user_id = resolve_wm_user_id(request)
    try:
        raw = await request.body()
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {}
    wm = get_window_manager()
    res = await wm.handle_rpc(user_id, payload)
    return JSONResponse(content=res)


_ROUTES_REGISTERED = False


def register_echo_wm_routes(force: bool = False) -> bool:
    """Enregistre de manière idempotente les routes FastAPI /echo/v1/wm/* et /static/loader.js."""
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED and not force:
        return True
    try:
        inject_client_loader()
        if "open_webui.main" not in sys.modules:
            try:
                import open_webui.main as owui_main
            except ImportError:
                return False
        else:
            import open_webui.main as owui_main

        app = getattr(owui_main, "app", None)
        if not app or not hasattr(app, "router"):
            return False

        filtered_routes = [
            r for r in app.router.routes
            if not getattr(r, "path", "").startswith(WM_PREFIX)
            and getattr(r, "path", "") != "/static/loader.js"
        ]
        app.router.routes = filtered_routes

        for route in reversed(wm_static_router.routes):
            app.router.routes.insert(0, route)
        for route in reversed(wm_router.routes):
            app.router.routes.insert(0, route)

        _ROUTES_REGISTERED = True
        print("[ECHO Window Manager] Pont multi-chats et multi-navigateurs (/echo/v1/wm + /static/loader.js) enregistré.")
        return True
    except Exception as e:
        print(f"[ECHO Window Manager] Avertissement enregistrement routes : {e}")
        return False
