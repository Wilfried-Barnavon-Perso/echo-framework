# -*- coding: utf-8 -*-
"""
title: ECHO Echo Events
author: Wilfried BARNAVON
version: 2.0
description: Gestionnaire des événements WebSocket et UI. Intègre le protocole UCTP (Universal Chunked Transfer Protocol).
"""
from typing import Any, Optional
import uuid
import orjson as json
from echo_constants import ECHO_UCTP_CHUNK_SIZE


class EchoEvents:
    def __init__(self, emitter: Any = None, caller: Any = None):
        self.emitter = emitter
        self.caller = caller

    async def emit(self, event_type: str, data: dict):
        if self.emitter:
            try:
                await self.emitter({"type": event_type, "data": data})
            except Exception as e:
                print(f"[EchoEvents] Emit Error: {e}")

    async def status(self, description: str, done: bool = False, hidden: bool = False):
        await self.emit("status", {"description": description, "done": done, "hidden": hidden})

    async def toast(self, content: str, level: str = "info", title: str = "ECHO"):
        await self.emit("toast", {"title": title, "message": content, "type": level})

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
        return bool(res)

    async def emit_execute(self, code_str: str):
        """ Émet et exécute du JS avec fragmentation automatique Python -> JS (UCTP) """
        if not self.emitter:
            return
        try:
            if len(code_str) > ECHO_UCTP_CHUNK_SIZE:
                var_id = uuid.uuid4().hex
                var_name = "window._uctp_" + var_id
                loader_id = "uctp-loader-" + var_id

                setup_js = f"""
                window.{var_name} = '';
                if (!document.getElementById('{loader_id}')) {{
                    const loader = document.createElement('div');
                    loader.id = '{loader_id}';
                    loader.style.cssText = 'position:fixed;bottom:20px;right:20px;background:rgba(15,23,42,0.9);border:1px solid rgba(56,189,248,0.3);border-radius:8px;padding:12px;display:flex;flex-direction:column;align-items:center;gap:8px;z-index:999999;box-shadow:0 10px 15px -3px rgba(0,0,0,0.5);backdrop-filter:blur(8px);font-family:system-ui;';
                    loader.innerHTML = `
                        <div style="display:flex;align-items:center;gap:10px;">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;">
                                <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                                <path d="M12 2a10 10 0 0 1 10 10"></path>
                            </svg>
                            <span style="color:#e2e8f0;font-size:12px;font-weight:500;">ECHO UCTP</span>
                        </div>
                        <div style="width:100%;height:2px;background:rgba(255,255,255,0.1);border-radius:2px;overflow:hidden;margin-top:2px;">
                            <div id="{loader_id}-bar" style="width:0%;height:100%;background:#38bdf8;transition:width 0.1s linear;"></div>
                        </div>
                        <style>@keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}</style>
                    `;
                    document.body.appendChild(loader);
                }}
                return true;
                """
                await self.emitter({"type": "execute", "data": {"code": setup_js}})

                total_chunks = (len(code_str) + ECHO_UCTP_CHUNK_SIZE - 1) // ECHO_UCTP_CHUNK_SIZE
                for i in range(total_chunks):
                    start_idx = i * ECHO_UCTP_CHUNK_SIZE
                    chunk = code_str[start_idx: start_idx + ECHO_UCTP_CHUNK_SIZE]
                    escaped = json.dumps(chunk).decode("utf-8")
                    percent = int(((i + 1) / total_chunks) * 100)

                    chunk_js = f"""
                    window.{var_name} += {escaped};
                    const bar = document.getElementById('{loader_id}-bar');
                    if (bar) bar.style.width = '{percent}%';
                    await new Promise(r => setTimeout(r, 5)); // Force un rafraîchissement DOM (Yield Event Loop)
                    return true;
                    """
                    await self.emitter({"type": "execute", "data": {"code": chunk_js}})

                final_exec = f"""
                const loader = document.getElementById('{loader_id}');
                if (loader) loader.remove();

                const code = window.{var_name};
                delete window.{var_name};
                const AsyncFunction = Object.getPrototypeOf(async function(){{}}).constructor;
                return await (new AsyncFunction(code))();
                """
                await self.emitter({"type": "execute", "data": {"code": final_exec}})
            else:
                await self.emitter({"type": "execute", "data": {"code": code_str}})
        except Exception as e:
            print(f"[EchoEvents] Emit Execute Error: {e}")

    async def call_execute(self, code_str: str) -> Any:
        """ Émet du JS, attend un retour, gère la fragmentation Bidi UCTP (Python <-> JS) """
        if not self.caller:
            return None
        try:
            if "return " not in code_str:
                code_str += "\nreturn true;"

            if len(code_str) > ECHO_UCTP_CHUNK_SIZE:
                var_id = uuid.uuid4().hex
                var_name = "window._uctp_" + var_id
                loader_id = "uctp-loader-" + var_id

                setup_js = f"""
                window.{var_name} = '';
                if (!document.getElementById('{loader_id}')) {{
                    const loader = document.createElement('div');
                    loader.id = '{loader_id}';
                    loader.style.cssText = 'position:fixed;bottom:20px;right:20px;background:rgba(15,23,42,0.9);border:1px solid rgba(56,189,248,0.3);border-radius:8px;padding:12px;display:flex;flex-direction:column;align-items:center;gap:8px;z-index:999999;box-shadow:0 10px 15px -3px rgba(0,0,0,0.5);backdrop-filter:blur(8px);font-family:system-ui;';
                    loader.innerHTML = `
                        <div style="display:flex;align-items:center;gap:10px;">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation: spin 1s linear infinite;">
                                <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
                                <path d="M12 2a10 10 0 0 1 10 10"></path>
                            </svg>
                            <span style="color:#e2e8f0;font-size:12px;font-weight:500;">ECHO UCTP</span>
                        </div>
                        <div style="width:100%;height:2px;background:rgba(255,255,255,0.1);border-radius:2px;overflow:hidden;margin-top:2px;">
                            <div id="{loader_id}-bar" style="width:0%;height:100%;background:#38bdf8;transition:width 0.1s linear;"></div>
                        </div>
                        <style>@keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}</style>
                    `;
                    document.body.appendChild(loader);
                }}
                return true;
                """
                await self.caller({"type": "execute", "data": {"code": setup_js}})

                total_chunks = (len(code_str) + ECHO_UCTP_CHUNK_SIZE - 1) // ECHO_UCTP_CHUNK_SIZE
                for i in range(total_chunks):
                    start_idx = i * ECHO_UCTP_CHUNK_SIZE
                    chunk = code_str[start_idx: start_idx + ECHO_UCTP_CHUNK_SIZE]
                    escaped = json.dumps(chunk).decode("utf-8")
                    percent = int(((i + 1) / total_chunks) * 100)

                    chunk_js = f"""
                    window.{var_name} += {escaped};
                    const bar = document.getElementById('{loader_id}-bar');
                    if (bar) bar.style.width = '{percent}%';
                    await new Promise(r => setTimeout(r, 5)); // Force un rafraîchissement DOM (Yield Event Loop)
                    return true;
                    """
                    await self.caller({"type": "execute", "data": {"code": chunk_js}})

                final_exec = f"""
                const loader = document.getElementById('{loader_id}');
                if (loader) loader.remove();

                const code = window.{var_name};
                delete window.{var_name};
                const AsyncFunction = Object.getPrototypeOf(async function(){{}}).constructor;
                return await (new AsyncFunction(code))();
                """
                res = await self.caller({"type": "execute", "data": {"code": final_exec}})
            else:
                res = await self.caller({"type": "execute", "data": {"code": code_str}})

            # UCTP Bidi Réception (JS -> Python)
            if isinstance(res, dict) and res.get("action") == "__chunked_payload__":
                buffer = [res.get("data", "")]
                total = res.get("total_chunks", 1)

                for _ in range(1, total):
                    next_res = await self.caller({"type": "execute", "data": {"code": res.get("wait_code")}})
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
        except Exception as e:
            print(f"[EchoEvents] Call Execute Error: {e}")
            return None
