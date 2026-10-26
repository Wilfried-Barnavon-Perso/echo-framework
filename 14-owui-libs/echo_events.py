# -*- coding: utf-8 -*-
"""
title: ECHO Echo Events
author: Wilfried BARNAVON
version: 5.231.5.1
description: Gestionnaire des événements WebSocket et UI. Intègre le protocole UCTP (Universal Chunked Transfer Protocol).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.5.1: Correction du Fast-Path Codex dans trigger_action (multi-chat switch_chat, target_chat_id, commit et dé-minimisation).
# 5.231.1.0: Double transport non-destructif (Socket.IO + SSE EchoWindowManager) dédupliqué par exec_id pour préserver WebGPU et les appels RPC multi-chats.
# 5.230.1.1: Introspection automatique du contexte de closure Open WebUI et routage multi-chats/multi-navigateurs via EchoWindowManager.
# 5.228.12.1: Factorisation de resolve_live_session et wait_hud_action dans EchoEvents pour la résilience RPC post-standby de tous les HUDs.
# 5.228.2.1: Vérification de la vivacité de window.echoCodexResolve dans le fast-path de trigger_action pour ré-amorcer le HUD Codex après standby.
import asyncio
from typing import Any, Optional
import uuid
import orjson as json
from echo_constants import ECHO_UCTP_CHUNK_SIZE
try:
    from echo_window_manager import (
        get_window_manager,
        extract_closure_context,
        register_echo_wm_routes,
    )
except ImportError:
    get_window_manager = None
    extract_closure_context = None
    register_echo_wm_routes = None


class EchoEvents:
    def __init__(
        self,
        emitter: Any = None,
        caller: Any = None,
        user_id: str = "",
        chat_id: str = "",
        message_id: str = "",
        session_id: str = "",
    ):
        self.emitter = emitter
        self.caller = caller
        if register_echo_wm_routes is not None:
            register_echo_wm_routes()
        ctx = {}
        if extract_closure_context is not None:
            ctx = extract_closure_context(caller) or extract_closure_context(emitter) or {}
        self.user_id = user_id or ctx.get("user_id", "")
        self.chat_id = chat_id or ctx.get("chat_id", "")
        self.message_id = message_id or ctx.get("message_id", "")
        self.session_id = session_id or ctx.get("session_id", "")

    async def emit(self, event_type: str, data: dict):
        if self.emitter:
            try:
                await self.emitter({"type": event_type, "data": data})
            except Exception as e:
                print(f"[EchoEvents] Emit Error: {e}")

    async def status(self, description: str, done: bool = False, hidden: bool = False):
        await self.emit("status", {"description": description, "done": done, "hidden": hidden})

    async def toast(self, content: str, level: str = "info", title: str = "ECHO"):
        await self.emit("notification", {"type": level, "content": content})

    async def call(self, event_type: str, data: dict) -> Any:
        if self.caller:
            try:
                return await self.caller({"type": event_type, "data": data})
            except Exception as e:
                print(f"[EchoEvents] Call Error: {e}")
        return None

    async def input(self, title: str, message: str, placeholder: str = "", type: str = "text") -> Optional[str]:
        return await self.call("input", {"title": title, "message": message, "placeholder": placeholder, "type": type})

    async def confirm(self, title: str, message: str) -> bool:
        res = await self.call("confirmation", {"title": title, "message": message})
        if isinstance(res, dict) and "error" in res:
            return False
        return res is True

    def _wrap_wm_js(self, code_str: str, exec_id: str, mode: str = "emit") -> str:
        """Encapsule le code JS pour exécution tracée et dédupliquée via window.EchoWM avec fallback natif."""
        cid_json = json.dumps(self.chat_id or "").decode("utf-8")
        eid_json = json.dumps(exec_id).decode("utf-8")
        code_json = json.dumps(code_str).decode("utf-8")
        mode_json = json.dumps(mode).decode("utf-8")
        if mode == "call":
            return (
                f"if (window.EchoWM && typeof window.EchoWM.runInChatScope === 'function') {{\n"
                f"    return await window.EchoWM.runInChatScope({cid_json}, {eid_json}, {code_json}, {mode_json}, false);\n"
                f"}}\n"
                f"{code_str}"
            )
        return (
            f"(async () => {{\n"
            f"    if (window.EchoWM && typeof window.EchoWM.runInChatScope === 'function') {{\n"
            f"        await window.EchoWM.runInChatScope({cid_json}, {eid_json}, {code_json}, {mode_json}, false);\n"
            f"        return;\n"
            f"    }}\n"
            f"    const AsyncFunction = Object.getPrototypeOf(async function(){{}}).constructor;\n"
            f"    await (new AsyncFunction({code_json}))();\n"
            f"}})();"
        )

    def _build_uctp_frames(self, code_str: str, exec_id: str, mode: str) -> tuple[str, list[str], str]:
        """Construit les trames JS de fragmentation UCTP (setup, chunks, final_exec) factorisées pour 'emit' et 'call'."""
        var_id = uuid.uuid4().hex
        var_name = "window._uctp_" + var_id
        loader_id = "uctp-loader-" + var_id
        cid_json = json.dumps(self.chat_id or "").decode("utf-8")
        eid_json = json.dumps(exec_id).decode("utf-8")
        mode_json = json.dumps(mode).decode("utf-8")

        setup_body = f"""
            window.{var_name} = '';
            if (!document.getElementById('{loader_id}')) {{
                const loader = document.createElement('div');
                loader.id = '{loader_id}';
                loader.style.cssText = 'position:fixed;bottom:20px;right:20px;background:rgba(15,23,42,0.9);'
                    + 'border:1px solid rgba(56,189,248,0.3);border-radius:8px;padding:12px;display:flex;'
                    + 'flex-direction:column;align-items:center;gap:8px;z-index:999999;'
                    + 'box-shadow:0 10px 15px -3px rgba(0,0,0,0.5);backdrop-filter:blur(8px);font-family:system-ui;';
                loader.innerHTML = `
                    <div style="display:flex;align-items:center;justify-content:center;margin-bottom:6px;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#38bdf8"
                             stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                             style="animation: spin 1s linear infinite;">
                            <polyline points="23 4 23 10 17 10"></polyline>
                            <polyline points="1 20 1 14 7 14"></polyline>
                            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                        </svg>
                    </div>
                    <div style="width:30px;height:2px;background:rgba(255,255,255,0.1);border-radius:2px;overflow:hidden;">
                        <div id="{loader_id}-bar" style="width:0%;height:100%;background:#38bdf8;transition:width 0.1s linear;"></div>
                    </div>
                    <style>@keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}</style>
                `;
                document.body.appendChild(loader);
            }}
        """
        setup_js = f"(async () => {{{setup_body}}})();" if mode == "emit" else f"{setup_body}\nreturn true;"

        chunks_js: list[str] = []
        total_chunks = (len(code_str) + ECHO_UCTP_CHUNK_SIZE - 1) // ECHO_UCTP_CHUNK_SIZE
        for i in range(total_chunks):
            start_idx = i * ECHO_UCTP_CHUNK_SIZE
            escaped = json.dumps(code_str[start_idx: start_idx + ECHO_UCTP_CHUNK_SIZE]).decode("utf-8")
            percent = int(((i + 1) / total_chunks) * 100)
            chunk_body = f"""
                window.{var_name} += {escaped};
                const bar = document.getElementById('{loader_id}-bar');
                if (bar) bar.style.width = '{percent}%';
                await new Promise(r => setTimeout(r, 5));
            """
            chunks_js.append(f"(async () => {{{chunk_body}}})();" if mode == "emit" else f"{chunk_body}\nreturn true;")

        final_body = f"""
            const loader = document.getElementById('{loader_id}');
            if (loader) loader.remove();
            const code = window.{var_name};
            delete window.{var_name};
            if (window.EchoWM && typeof window.EchoWM.runInChatScope === 'function') {{
                return await window.EchoWM.runInChatScope({cid_json}, {eid_json}, code, {mode_json}, false);
            }}
            const AsyncFunction = Object.getPrototypeOf(async function(){{}}).constructor;
            return await (new AsyncFunction(code))();
        """
        final_js = f"(async () => {{{final_body}}})();" if mode == "emit" else final_body
        return setup_js, chunks_js, final_js

    async def emit_execute(self, code_str: str):
        """ Émet et exécute du JS avec fragmentation automatique Python -> JS (UCTP) et diffusion multi-chats/multi-navigateurs """
        exec_id = uuid.uuid4().hex
        if get_window_manager is not None:
            wm = get_window_manager()
            wm.register_exec(exec_id, self.user_id, self.chat_id, code_str)
            if wm.has_active_clients(self.user_id):
                await wm.emit_js(
                    user_id=self.user_id,
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    code=code_str,
                    exec_id=exec_id,
                )
        if not self.emitter:
            return
        try:
            if len(code_str) > ECHO_UCTP_CHUNK_SIZE:
                setup_js, chunks_js, final_exec = self._build_uctp_frames(code_str, exec_id, "emit")
                await self.emitter({"type": "execute", "data": {"code": setup_js}})
                for chunk_js in chunks_js:
                    await self.emitter({"type": "execute", "data": {"code": chunk_js}})
                await self.emitter({"type": "execute", "data": {"code": final_exec}})
            else:
                await self.emitter({"type": "execute", "data": {"code": self._wrap_wm_js(code_str, exec_id, "emit")}})
        except Exception as e:
            print(f"[EchoEvents] Emit Execute Error: {e}")

    async def _call_via_socket_caller(self, code_str: str, exec_id: str, timeout: Optional[float] = None) -> Any:
        """Exécute un appel JS via le caller Socket.IO natif (avec support UCTP et traçage EchoWM)."""
        if len(code_str) > ECHO_UCTP_CHUNK_SIZE:
            setup_js, chunks_js, final_exec = self._build_uctp_frames(code_str, exec_id, "call")
            await self.caller({"type": "execute", "data": {"code": setup_js}})
            for chunk_js in chunks_js:
                await self.caller({"type": "execute", "data": {"code": chunk_js}})
            coro = self.caller({"type": "execute", "data": {"code": final_exec}})
            res = await asyncio.wait_for(coro, timeout=timeout) if (timeout is not None and timeout > 0) else await coro
        else:
            wrapped = self._wrap_wm_js(code_str, exec_id, "call")
            coro = self.caller({"type": "execute", "data": {"code": wrapped}})
            res = await asyncio.wait_for(coro, timeout=timeout) if (timeout is not None and timeout > 0) else await coro

        # UCTP Bidi Réception (JS -> Python)
        if isinstance(res, dict) and res.get("action") == "__chunked_payload__":
            buffer = [res.get("data", "")]
            total = res.get("total_chunks", 1)

            for _ in range(1, total):
                coro_next = self.caller({"type": "execute", "data": {"code": res.get("wait_code")}})
                next_res = await asyncio.wait_for(coro_next, timeout=timeout) if (timeout is not None and timeout > 0) else await coro_next
                if isinstance(next_res, dict) and next_res.get("action") == "__chunked_payload__":
                    buffer.append(next_res.get("data", ""))
                else:
                    break  # Rupture de protocole

            full_str = "".join(buffer)
            buffer.clear()
            try:
                return json.loads(full_str)
            except Exception as e:
                print(f"[EchoEvents UCTP] Decode error: {e}")
                return None

        return res

    async def call_execute(self, code_str: str, timeout: Optional[float] = None) -> Any:
        """ Émet du JS, attend un retour, gère la fragmentation Bidi UCTP et le double transport Socket.IO + SSE """
        if "return " not in code_str:
            code_str += "\nreturn true;"

        exec_id = uuid.uuid4().hex
        wm = get_window_manager() if get_window_manager is not None else None
        if wm is not None:
            wm.register_exec(exec_id, self.user_id, self.chat_id, code_str)

        has_wm = wm is not None and wm.has_active_clients(self.user_id)

        if has_wm and not self.caller:
            wm_ok, wm_res = await wm.call_js(
                user_id=self.user_id,
                chat_id=self.chat_id,
                message_id=self.message_id,
                code=code_str,
                timeout=timeout,
                exec_id=exec_id,
            )
            return wm_res if wm_ok else None

        if not self.caller:
            return None

        if has_wm:
            wm_task = asyncio.create_task(
                wm.call_js(
                    user_id=self.user_id,
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    code=code_str,
                    timeout=timeout,
                    exec_id=exec_id,
                )
            )
            caller_task = asyncio.create_task(
                self._call_via_socket_caller(code_str, exec_id, timeout=timeout)
            )
            try:
                done, pending = await asyncio.wait(
                    {wm_task, caller_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if caller_task in done and not caller_task.cancelled() and caller_task.exception() is None:
                    res = caller_task.result()
                    if not isinstance(res, dict) or "error" not in res:
                        for p in pending:
                            p.cancel()
                        return res

                if wm_task in done and not wm_task.cancelled() and wm_task.exception() is None:
                    wm_ok, wm_res = wm_task.result()
                    if wm_ok:
                        for p in pending:
                            p.cancel()
                        return wm_res

                # Attendre la tâche restante si la première n'a pas abouti
                for p in pending:
                    try:
                        rem = await p
                        if p is wm_task:
                            wm_ok, wm_res = rem
                            if wm_ok:
                                return wm_res
                        else:
                            return rem
                    except Exception:
                        pass
                return None
            except asyncio.TimeoutError:
                wm_task.cancel()
                caller_task.cancel()
                print(f"[EchoEvents] Call Execute Timeout ({timeout}s)")
                return None
            except Exception as e:
                wm_task.cancel()
                caller_task.cancel()
                print(f"[EchoEvents] Call Execute Error: {e}")
                return None

        try:
            return await self._call_via_socket_caller(code_str, exec_id, timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[EchoEvents] Call Execute Timeout ({timeout}s)")
            return None
        except Exception as e:
            print(f"[EchoEvents] Call Execute Error: {e}")
            return None

    async def wait_hud_action(
        self, resolve_var: str, ready_fn: str = "", timeout: Optional[float] = None
    ) -> Any:
        """Attend la prochaine action RPC du HUD et signale au frontend que le backend écoute."""
        ready_call = (
            f" if (typeof window.{ready_fn} === 'function') window.{ready_fn}();"
            if ready_fn
            else ""
        )
        wait_code = f"return new Promise(r => {{ window.{resolve_var} = r;{ready_call} }});"
        return await self.call_execute(wait_code, timeout=timeout)

    @classmethod
    async def resolve_live_session(
        cls,
        user_id: str,
        chat_id: str = "",
        message_id: str = "",
        hud_id: str = "",
        probe_expr: str = "true",
        retries: int = 3,
        delay: float = 1.5,
    ) -> Optional[tuple["EchoEvents", Any]]:
        """Résout dynamiquement la session Socket.IO active de l'utilisateur possédant le HUD ouvert."""
        for attempt in range(max(1, retries)):
            try:
                from open_webui.socket.main import (
                    get_session_ids_by_user_id,
                    SESSION_POOL,
                    get_event_call,
                    get_event_emitter,
                )
                sids = get_session_ids_by_user_id(user_id)
                if sids:
                    sids_sorted = sorted(
                        sids,
                        key=lambda s: (SESSION_POOL.get(s) or {}).get("last_seen_at", 0),
                        reverse=True,
                    )
                    if hud_id:
                        cid_check = (
                            f" && (el.getAttribute('data-echo-chat-id') || el.getAttribute('data-chat-id')) === '{chat_id}'"
                            if chat_id
                            else ""
                        )
                        probe_js = (
                            f"const el = document.getElementById('{hud_id}');"
                            f"return (el{cid_check}) ? ({probe_expr}) : false;"
                        )
                    else:
                        probe_js = f"return ({probe_expr});"

                    for sid in sids_sorted:
                        req_info = {
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "session_id": sid,
                            "user_id": user_id,
                        }
                        caller = await get_event_call(req_info)
                        emitter = await get_event_emitter(req_info)
                        candidate_events = cls(emitter, caller)
                        probe_res = await candidate_events.call_execute(probe_js, timeout=3.0)
                        if probe_res is not None and probe_res is not False:
                            return candidate_events, probe_res
            except Exception as e:
                print(f"[EchoEvents] resolve_live_session error: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
        return None

    async def trigger_action(self, action_id: str, chat_id: str, message_id: str, model_id: str, session_id: str, payload: dict = None) -> Any:
        """
        Déclenche une action native Open WebUI de manière asynchrone via l'API.
        Bypass Svelte. Variables de contexte injectées de manière absolue.
        """
        if not action_id:
            return False

        payload_js = json.dumps(payload or {}).decode("utf-8")
        js_payload = f"""
        (async () => {{
            try {{
                const token = localStorage.token ? localStorage.token.replace(/^"|"$/g, '') : '';
                const extraPayload = {payload_js};

                // Fast-path ECHO Codex : si le HUD est déjà ouvert dans le DOM pour CE chat_id et que sa boucle RPC est active,
                // réveiller directement la boucle existante sans recréer l'action ni réinjecter le DOM.
                const codexEl = document.getElementById('echo-codex-hud');
                const isSameChat = codexEl && codexEl.getAttribute('data-chat-id') === '{chat_id}';
                const hasCodexBridge = typeof window.sendCodexAction === 'function' && typeof window.echoCodexResolve === 'function';
                if ('{action_id}' === 'echo_codex_action' && isSameChat && hasCodexBridge) {{
                    const target = window._echoCodexTarget || (extraPayload && (extraPayload.target_file || extraPayload.file) ? extraPayload : null);
                    window._echoCodexTarget = null;
                    if (target) {{
                        const targetCid = target.target_chat_id || '{chat_id}';
                        const reqFile = target.target_file || target.file || '';
                        const reqWs = target.workspace || 'main';
                        const reqCommit = target.commit_hash || target.commit || null;
                        const actionName = (target.target_chat_id && target.target_chat_id !== '{chat_id}') ? 'switch_chat' : 'switch_workspace';
                        window.sendCodexAction({{
                            action: actionName,
                            target_chat_id: targetCid,
                            chat_id: targetCid,
                            workspace: reqWs,
                            target_file: reqFile,
                            current_file: reqFile,
                            commit_hash: reqCommit
                        }});
                    }} else {{
                        window.sendCodexAction({{ action: 'ping' }});
                    }}
                    if (codexEl) codexEl.classList.remove('is-minimized');
                    return true;
                }}

                const response = await fetch(`/api/chat/actions/{action_id}`, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${{token}}`
                    }},
                    body: JSON.stringify({{
                        chat_id: '{chat_id}',
                        model: '{model_id}',
                        id: '{message_id}',
                        session_id: '{session_id}',
                        messages: [],
                        ...extraPayload
                    }})
                }});

                if (!response.ok) {{
                    const errText = await response.text();
                    console.error(`ECHO Action Error [{action_id}]:`, errText);
                    return false;
                }}
                return true;
            }} catch (e) {{
                console.error(`ECHO Action Exception [{action_id}]:`, e);
                return false;
            }}
        }})();
        """
        return await self.call_execute(js_payload, timeout=10.0)
