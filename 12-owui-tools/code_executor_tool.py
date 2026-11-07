"""
title: ECHO Code Executor
author: Wilfried BARNAVON
version: 5.234.1.2
description: Composant système interne : ECHO Code Executor (Python & Node.js).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.234.1.2: Support du mode background=False/True (_BACKGROUND_POLLERS), ajout de control_sandbox_execution (status, listen, kill), routage d'événements echoPushMonitorEvent et alignement PAS.
# 5.233.0.1: Décoration @echo_tool sur les 2 méthodes publiques et simplification des retours wrap_tool_output.
# 5.232.5.1: Mise à jour de la docstring code_executor mentionnant les montages RO /main, /plans, /generated et /files.
# 5.232.1.1: Clarification de la docstring code_executor (runtimes interactifs echo_monitor, flux bidirectionnel UI/Sandbox, Supervisor et readme sandbox).
# 5.231.20.1: Multiplexage des exécutions concurrentes par run_id et isolation des payloads/HUDs de monitoring par chat_id.

# ECHO CONFIG NAME : ECHO Code Sandbox

import asyncio
import json
import sys
import uuid
from typing import Any, List, Literal, Optional

sys.path.append("/app/backend/echo_libs")
from echo_constants import (  # noqa: E402
    ECHO_CODING_WORKER_URL,
    ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT,
    ECHO_MAX_CODE_EXECUTION_TIMEOUT,
    ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS,
    SANDBOX_LISTEN_MAX_WAIT_SEC,
)
from echo_core import echo_tool, wrap_tool_output  # noqa: E402
from echo_events import EchoEvents  # noqa: E402
from echo_paths import sanitize_echo_id  # noqa: E402
from echo_ui import EchoUI  # noqa: E402

_BACKGROUND_POLLERS: dict[str, dict[str, Any]] = {}


def _resolve_sandbox_identity(__user__: Optional[dict], __metadata__: Optional[dict]) -> tuple[str, str]:
    uid = sanitize_echo_id((__user__ or {}).get("id") or "system", fallback="system")
    cid = sanitize_echo_id((__metadata__ or {}).get("chat_id") or "", fallback="")
    return uid, cid


async def _emit_monitor_payload(events: EchoEvents, meta: dict, cid_js: str, monitor_lib_js: str) -> None:
    msg_type = meta.get("type", "display")
    wid_js = json.dumps(str(meta.get("window_id", "default")))
    if msg_type == "event":
        data_js = json.dumps(meta.get("data"))
        req_js = json.dumps(meta.get("req_id"))
        js_code = f"""
            (async () => {{
                if (typeof window.echoPushMonitorEvent !== 'function') {{
                    {monitor_lib_js}
                }}
                window.echoPushMonitorEvent({wid_js}, {data_js}, {req_js}, {cid_js});
            }})();
        """
        await events.emit_execute(js_code)
        return

    args = ", ".join(
        json.dumps(str(meta.get(k, d)))
        for k, d in (("window_id", "default"), ("title", "Monitor"), ("width", "100%"), ("height", "400px"))
    )
    b64 = json.dumps(meta.get("html", ""))
    js_code = f"""
        (async () => {{
            if (typeof window.echoCreateFloatingMonitor !== 'function') {{
                {monitor_lib_js}
            }}
            const bin = atob({b64});
            const htmlContent = new TextDecoder('utf-8').decode(Uint8Array.from(bin, c => c.charCodeAt(0)));
            const [wid, title, width, height] = [{args}];
            window.echoCreateFloatingMonitor(wid, title, htmlContent, width, height, {cid_js});
        }})();
    """
    await events.emit_execute(js_code)


def _stop_background_poller(uid: str, cid: str, run_id: Optional[str] = None) -> None:
    prefix = f"{uid}:{cid}:"
    keys = [f"{prefix}{run_id}"] if run_id else [k for k in list(_BACKGROUND_POLLERS.keys()) if k.startswith(prefix)]
    for k in keys:
        entry = _BACKGROUND_POLLERS.pop(k, None)
        if entry and entry.get("task") and not entry["task"].done():
            entry["task"].cancel()


class Tools:
    def __init__(self):
        pass

    @echo_tool
    async def control_sandbox_execution(
        self,
        action: Literal["status", "listen", "kill"] = "status",
        run_id: Optional[str] = None,
        wait_sec: int = 0,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict | str:
        """Contrôle l'état ('status'), écoute les flux stdout/stderr ('listen') ou interrompt ('kill') les processus Sandbox de la session."""
        uid, cid = _resolve_sandbox_identity(__user__, __metadata__)
        if not cid:
            return "❌ Contexte de session manquant (chat_id)."

        sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
        clean_rid = sanitize_echo_id(run_id, fallback="") if run_id else ""

        import httpx
        try:
            if action == "kill":
                _stop_background_poller(uid, cid, clean_rid or None)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(
                        f"{sandbox_base}/kill",
                        json={"user_id": uid, "chat_id": cid, "run_id": clean_rid or None},
                    )
                data = res.json()
                if data.get("status") == "killed":
                    pids = data.get("pids") or ([data["pid"]] if "pid" in data else [])
                    rids = data.get("run_ids") or ([clean_rid] if clean_rid else [])
                    msg = f"Processus Sandbox arrêté (run_id: {', '.join(rids) or 'all'} | PID: {', '.join(str(p) for p in pids)})."
                    return wrap_tool_output(text=msg, status={"status": "killed", "run_ids": rids, "pids": pids})
                return "⚠️ Aucun processus Sandbox actif trouvé pour cette cible."

            bounded_wait = max(0, min(int(wait_sec or 0), SANDBOX_LISTEN_MAX_WAIT_SEC)) if action == "listen" else 0
            key_cursor = f"{uid}:{cid}:{clean_rid}"
            prev_entry = _BACKGROUND_POLLERS.get(key_cursor, {})
            out_off = prev_entry.get("stdout_offset", 0) if action == "listen" else 0
            err_off = prev_entry.get("stderr_offset", 0) if action == "listen" else 0

            async with httpx.AsyncClient(timeout=bounded_wait + 10.0) as client:
                if action == "listen" and not clean_rid:
                    list_res = await client.get(f"{sandbox_base}/status", params={"user_id": uid, "chat_id": cid})
                    jobs = list_res.json().get("jobs", [])
                    running = [j for j in jobs if j.get("state") == "running"]
                    if len(running) == 1:
                        clean_rid = running[0]["run_id"]
                        key_cursor = f"{uid}:{cid}:{clean_rid}"
                        prev_entry = _BACKGROUND_POLLERS.get(key_cursor, {})
                        out_off = prev_entry.get("stdout_offset", 0)
                        err_off = prev_entry.get("stderr_offset", 0)
                    elif not running:
                        return "⚠️ Aucun processus actif à écouter. Utiliser action='status' pour lister les run_id terminés."
                    else:
                        rids_str = ", ".join(j["run_id"] for j in running)
                        return f"⚠️ Plusieurs processus actifs ({rids_str}). Spécifier le paramètre run_id."

                res = await client.get(
                    f"{sandbox_base}/status",
                    params={
                        "user_id": uid,
                        "chat_id": cid,
                        "run_id": clean_rid,
                        "wait_sec": bounded_wait,
                        "stdout_offset": out_off,
                        "stderr_offset": err_off,
                    },
                )
            data = res.json()
            if not clean_rid:
                jobs = data.get("jobs", [])
                if not jobs:
                    return "Aucun processus Sandbox enregistré dans cette session."
                lines = [
                    f"- `{j['run_id']}` | Fichier: `{j['file_path']}` | État: **{j['state']}** | PID: {j['pid']} | Durée: {j['elapsed_sec']}s"
                    for j in jobs
                ]
                return wrap_tool_output(
                    text="État des processus Sandbox de la session :\n" + "\n".join(lines),
                    status={"status": "success", "jobs": jobs},
                )

            if data.get("status") == "not_found":
                return f"❌ Aucun processus trouvé pour run_id='{clean_rid}'."

            if key_cursor in _BACKGROUND_POLLERS:
                _BACKGROUND_POLLERS[key_cursor]["stdout_offset"] = data.get("stdout_offset", out_off)
                _BACKGROUND_POLLERS[key_cursor]["stderr_offset"] = data.get("stderr_offset", err_off)
                if data.get("state") != "running":
                    _stop_background_poller(uid, cid, clean_rid)

            out_chunk = data.get("stdout_chunk", "")
            err_chunk = data.get("stderr_chunk", "")
            header = (
                f"[Sandbox `{clean_rid}` | Fichier: `{data.get('file_path')}` | "
                f"État: {data.get('state')} | PID: {data.get('pid')} | Durée: {data.get('elapsed_sec')}s]"
            )
            body = out_chunk or "(Aucune nouvelle sortie stdout)"
            if err_chunk:
                body += f"\n⚠️ Stderr:\n{err_chunk}"
            return wrap_tool_output(
                text=f"{header}\n{body}",
                status={
                    "status": "success" if data.get("state") in ("running", "completed") else "error",
                    "run_id": clean_rid,
                    "state": data.get("state"),
                    "pid": data.get("pid"),
                    "exit_code": data.get("exit_code"),
                },
            )
        except Exception as e:
            return f"❌ Erreur lors du contrôle Sandbox : {str(e)}"

    @echo_tool
    async def kill_sandbox_execution(
        self,
        run_id: Optional[str] = None,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict | str:
        """Interrompt immédiatement le processus ciblé par run_id (ou tous les processus actifs) dans la Sandbox de la session."""
        return await self.control_sandbox_execution(
            action="kill", run_id=run_id, __user__=__user__, __metadata__=__metadata__
        )

    @echo_tool
    async def code_executor(
        self,
        file_path: str,
        dependencies: Optional[List[str]] = None,
        timeout_sec: int = ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT,
        fetch_ui_payload: bool = False,
        background: bool = False,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> dict | str:
        """Exécute un script Python (.py) ou Node.js (.js) isolé dans le workspace `sandbox` du Codex en mode synchrone (`background=False`) ou serveur détaché (`background=True`)."""
        dependencies = dependencies or []
        uid, cid = _resolve_sandbox_identity(__user__, __metadata__)
        if not cid:
            return "❌ Contexte de session manquant (chat_id)."

        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=cid)

        if timeout_sec > ECHO_MAX_CODE_EXECUTION_TIMEOUT:
            err_msg = f"timeout_sec ({timeout_sec}s) dépasse la limite autorisée ({ECHO_MAX_CODE_EXECUTION_TIMEOUT}s)."
            await events.status(f"Erreur: {err_msg}", done=True)
            return f"❌ {err_msg}"

        actual_timeout = timeout_sec
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        ext = file_path.split(".")[-1]
        runtime_icon = "🐍" if ext == "py" else "📦" if ext == "js" else "⚙️"
        mode_label = "arrière-plan" if background else "synchrone"
        await events.status(f"{runtime_icon} Exécution {mode_label} de {file_path} ({run_id}, Timeout: {actual_timeout}s)")

        try:
            import httpx
            cid_js = json.dumps(cid)
            ui_payload_data = None
            if fetch_ui_payload:
                try:
                    raw = await events.call_execute(
                        f"return typeof window._echoGetAggregatedPayload === 'function' ? window._echoGetAggregatedPayload({cid_js}) : null;"
                    )
                    ui_payload_data = raw if isinstance(raw, dict) else (json.loads(raw) if raw else None)
                except Exception:
                    pass

            task_audit = {"status": "running", "run_id": run_id, "polls": 0, "payloads_pushed": 0, "payloads_pulled": 0}
            sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
            abort_event = asyncio.Event()
            supervisor = {"first_seen": {}, "last_ping_values": {}, "boot_timeout": 3.0, "ping_timeout": 2.5}

            async def bidi_polling():
                offset = 0
                start_t = asyncio.get_event_loop().time()
                monitor_lib_js = EchoUI.get_floating_monitor_js()
                try:
                    async with httpx.AsyncClient() as pclient:
                        while asyncio.get_event_loop().time() - start_t < actual_timeout:
                            task_audit["polls"] += 1
                            try:
                                raw = await events.call_execute(
                                    f"return typeof window._echoGetAggregatedPayload === 'function' ? window._echoGetAggregatedPayload({cid_js}) : null;"
                                )
                                ui_data = raw if isinstance(raw, dict) else (json.loads(raw) if raw else None)
                                if ui_data:
                                    if ui_data.get("_echo_signal") == "abort":
                                        task_audit["status"] = "aborted_by_ui"
                                        abort_event.set()
                                        if background:
                                            await pclient.post(f"{sandbox_base}/kill", json={"user_id": uid, "chat_id": cid, "run_id": run_id})
                                        break

                                    pings = ui_data.get("_pings", {})
                                    now = asyncio.get_event_loop().time()
                                    if supervisor["first_seen"]:
                                        alive_windows = 0
                                        for s_wid, first_t in supervisor["first_seen"].items():
                                            current_ping = pings.get(s_wid)
                                            if current_ping is None:
                                                if now - first_t <= supervisor["boot_timeout"]:
                                                    alive_windows += 1
                                            else:
                                                last_val, last_time = supervisor["last_ping_values"].get(s_wid, (0, now))
                                                if current_ping > last_val:
                                                    supervisor["last_ping_values"][s_wid] = (current_ping, now)
                                                    alive_windows += 1
                                                elif now - last_time <= supervisor["ping_timeout"]:
                                                    alive_windows += 1
                                        if alive_windows == 0:
                                            task_audit["status"] = "all_windows_closed_or_crashed"
                                            abort_event.set()
                                            if background:
                                                await pclient.post(f"{sandbox_base}/kill", json={"user_id": uid, "chat_id": cid, "run_id": run_id})
                                            break

                                    await pclient.post(
                                        f"{sandbox_base}/update_payload",
                                        json={"user_id": uid, "chat_id": cid, "run_id": run_id, "ui_payload": ui_data},
                                        timeout=2.0,
                                    )
                                    task_audit["payloads_pushed"] += 1
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                pass

                            try:
                                pr = await pclient.get(
                                    f"{sandbox_base}/poll_monitor",
                                    params={"user_id": uid, "chat_id": cid, "run_id": run_id, "offset": offset},
                                    timeout=2.0,
                                )
                                if pr.status_code == 200:
                                    pdata = pr.json()
                                    offset = pdata.get("offset", offset)
                                    for meta in pdata.get("payloads", []):
                                        wid = str(meta.get("window_id", "default"))
                                        if meta.get("type", "display") == "display" and wid not in supervisor["first_seen"]:
                                            supervisor["first_seen"][wid] = asyncio.get_event_loop().time()
                                        await _emit_monitor_payload(events, meta, cid_js, monitor_lib_js)
                                        task_audit["payloads_pulled"] += 1
                            except Exception:
                                pass
                            await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    pass
                finally:
                    _BACKGROUND_POLLERS.pop(f"{uid}:{cid}:{run_id}", None)

            polling_task = asyncio.create_task(bidi_polling())
            detach_poller = False

            try:
                async with httpx.AsyncClient(timeout=actual_timeout + 30.0) as client:
                    http_task = asyncio.create_task(
                        client.post(
                            ECHO_CODING_WORKER_URL,
                            json={
                                "file_path": file_path,
                                "dependencies": dependencies,
                                "ui_payload": ui_payload_data,
                                "user_id": uid,
                                "chat_id": cid,
                                "run_id": run_id,
                                "timeout": actual_timeout,
                                "max_output_length": ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS,
                                "background": background,
                            },
                        )
                    )
                    done, _ = await asyncio.wait(
                        [http_task, asyncio.create_task(abort_event.wait())],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if abort_event.is_set():
                        http_task.cancel()
                        raise asyncio.CancelledError("UI requested abort")
                    response = http_task.result()

                if response.status_code == 200:
                    worker_res = response.json()
                    if worker_res.get("status") == "running" and background:
                        detach_poller = True
                        _BACKGROUND_POLLERS[f"{uid}:{cid}:{run_id}"] = {
                            "task": polling_task,
                            "stdout_offset": len(worker_res.get("output", "")),
                            "stderr_offset": 0,
                        }
                        await events.status(f"Serveur/Tâche Sandbox {run_id} actif en arrière-plan.", done=True)
                        init_out = worker_res.get("output", "")
                        msg = (
                            f"Processus Sandbox démarré en arrière-plan (`run_id`: `{run_id}`, `pid`: `{worker_res.get('pid')}`).\n"
                            f"Utiliser `control_sandbox_execution(action='listen'|'status'|'kill', run_id='{run_id}')` pour superviser."
                        )
                        if init_out:
                            msg += f"\n\nSortie initiale :\n{init_out}"
                        return wrap_tool_output(
                            text=msg,
                            status={"status": "running", "run_id": run_id, "pid": worker_res.get("pid"), "task_review": task_audit},
                        )
                    if not abort_event.is_set():
                        task_audit["status"] = "completed"
                else:
                    err_msg = f"Erreur Worker (HTTP {response.status_code})"
                    await events.status(err_msg, done=True)
                    return wrap_tool_output(text="", status={"status": "critical_error", "code": response.status_code, "error": err_msg})
            except asyncio.CancelledError:
                await events.emit_execute(
                    f"if(window._echoGetAggregatedPayload){{ const p = window._echoGetAggregatedPayload({cid_js}); if(p) p._echo_signal = null; }}"
                )
                async with httpx.AsyncClient() as client:
                    await client.post(f"{sandbox_base}/kill", json={"user_id": uid, "chat_id": cid, "run_id": run_id})
                task_audit["status"] = "killed"
                raise
            except httpx.RequestError:
                return "❌ Service Coding Worker injoignable."
            except Exception as e:
                task_audit["status"] = f"error: {str(e)}"
                worker_res = {"status": "error", "error": str(e)}
            finally:
                if not detach_poller:
                    polling_task.cancel()

            text_out = worker_res.get("output", "")
            if worker_res.get("monitor_payloads"):
                pulled_count = task_audit.get("payloads_pulled", 0)
                remaining_payloads = worker_res["monitor_payloads"][pulled_count:]
                monitor_lib_js = EchoUI.get_floating_monitor_js()
                success_count = 0
                for meta in remaining_payloads:
                    try:
                        await _emit_monitor_payload(events, meta, cid_js, monitor_lib_js)
                        success_count += 1
                    except Exception as e:
                        text_out += f"\n[Erreur Système Monitor: {str(e)}]"
                if success_count > 0:
                    s_pluriel = "s" if success_count > 1 else ""
                    text_out += f"\n[Système: ECHO Monitor a transmis {success_count} événement{s_pluriel} au navigateur en Post-Run.]"

            if worker_res.get("error"):
                text_out += f"\n\n⚠️ Erreur d'exécution :\n{worker_res['error']}"

            echo_status = {
                "status": worker_res.get("status", "success"),
                "run_id": run_id,
                "pid": worker_res.get("pid"),
                "task_review": task_audit,
            }
            if worker_res.get("error"):
                echo_status["error"] = worker_res["error"]

            await events.status("Exécution terminée.", done=True)
            return wrap_tool_output(text=text_out, status=echo_status)
        except Exception as e:
            return f"❌ Erreur Client: {str(e)}"
