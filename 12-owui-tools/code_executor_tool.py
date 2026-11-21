"""
title: ECHO Code Executor
author: Wilfried BARNAVON
version: 6.15.2.1
description: Composant système interne : ECHO Code Executor (Python, Node.js, Bash & SSH).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.15.2.1: Durcissement Option A : garde pré-vol serve_forever(), handshake ready/crash, boucle poller réveillée par wake_event, arrêt sur unknown x3 et cleanup scopé par run_id.
# 6.13.1.2: Fiabilisation Pacemaker bidi_polling (constantes SSOT, peek/ack scopé par window_id, push delta-only, grâce _is_closed, arrêt auto sur fin de job et broadcast_canvas_status).
# 6.6.30.1: Isolation par sub_sid (ECHO_SUBAGENT_CONTEXT) des jobs, pollers et actions status/listen/kill.
# 6.6.21.1: Support Bash (.sh) dans code_executor et ajout de ssh_executor branché sur l'Identity Vault.
# 6.6.16.1: Raccordement natif de echo_monitor à l'onglet echo-canvas via EchoWindowManager (broadcast_canvas_frame, broadcast_canvas_event, consume_canvas_ui_payload) et suppression des appels emit_execute/call_execute legacy.

# ECHO CONFIG NAME : ECHO Code Sandbox

import asyncio
import base64
import json
import os
import re
import sys
import time
import uuid
from typing import Any, List, Literal, Optional

sys.path.append("/app/backend/echo_libs")
from echo_constants import (  # noqa: E402
    CODEX_DIR_NAME,
    CODEX_WORKSPACE_SANDBOX,
    ECHO_CANVAS_BOOT_TIMEOUT_SEC,
    ECHO_CANVAS_GRACEFUL_CLOSE_SEC,
    ECHO_CANVAS_IDLE_TTL_SEC,
    ECHO_CANVAS_MAX_LIFETIME_SEC,
    ECHO_CANVAS_PING_TIMEOUT_SEC,
    ECHO_CANVAS_POLL_WAIT_SEC,
    ECHO_CANVAS_READY_TIMEOUT_SEC,
    ECHO_CANVAS_UNKNOWN_GRACE_POLLS,
    ECHO_CODING_WORKER_URL,
    ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT,
    ECHO_MAX_CODE_EXECUTION_TIMEOUT,
    ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS,
    ECHO_SUBAGENT_CONTEXT,
    SANDBOX_LISTEN_MAX_WAIT_SEC,
)
from echo_core import echo_tool, wrap_tool_output  # noqa: E402
from echo_events import EchoEvents  # noqa: E402
from echo_paths import get_echo_session_path, sanitize_echo_id  # noqa: E402
from echo_state_manager import EchoStateManager  # noqa: E402
from echo_window_manager import get_window_manager  # noqa: E402


def _resolve_sandbox_identity(__user__: Optional[dict], __metadata__: Optional[dict]) -> tuple[str, str, str]:
    uid = sanitize_echo_id((__user__ or {}).get("id") or "system", fallback="system")
    cid = sanitize_echo_id((__metadata__ or {}).get("chat_id") or "", fallback="")
    sub_sid = sanitize_echo_id((ECHO_SUBAGENT_CONTEXT.get() or {}).get("sub_sid") or "", fallback="")
    return uid, cid, sub_sid


def _inspect_sandbox_script(uid: str, cid: str, file_path: str) -> tuple[bool, bool]:
    """Inspecte le script sandbox en lecture confinée et détecte (uses_echo_monitor, calls_serve_forever)."""
    try:
        session_dir = get_echo_session_path(uid, cid)
        sandbox_dir = os.path.abspath(os.path.join(session_dir, CODEX_DIR_NAME, CODEX_WORKSPACE_SANDBOX))
        rel = str(file_path or "").lstrip("/\\")
        target_path = os.path.abspath(os.path.join(sandbox_dir, rel))
        if target_path != sandbox_dir and not target_path.startswith(sandbox_dir + os.sep):
            return (False, False)
        if not os.path.isfile(target_path):
            return (False, False)
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        stripped = re.sub(r"(?m)(?:#|//).*$", "", content)
        return ("echo_monitor" in stripped, "serve_forever(" in stripped)
    except Exception:
        return (False, False)


async def _emit_monitor_payload(uid: str, cid: str, meta: dict, run_id: str = "") -> None:
    wm = get_window_manager()
    msg_type = meta.get("type", "display")
    wid = str(meta.get("window_id", "default"))
    if msg_type == "ready":
        cstate = wm._get_canvas_state(uid, cid)
        frame = cstate["frames"].get(wid)
        if isinstance(frame, dict) and (not run_id or not frame.get("run_id") or frame.get("run_id") == run_id):
            frame["ready"] = True
            frame["updated_at"] = time.time()
        return

    if msg_type == "event":
        await wm.broadcast_canvas_event(
            user_id=uid,
            chat_id=cid,
            window_id=wid,
            data=meta.get("data"),
            req_id=meta.get("req_id"),
        )
        return

    b64_html = meta.get("html") or ""
    try:
        html_content = base64.b64decode(b64_html).decode("utf-8", errors="replace") if b64_html else ""
    except Exception:
        html_content = str(b64_html)

    await wm.broadcast_canvas_frame(
        user_id=uid,
        chat_id=cid,
        frame_data={
            "id": wid,
            "title": str(meta.get("title") or "Monitor"),
            "width": str(meta.get("width") or "100%"),
            "height": str(meta.get("height") or "400px"),
            "html": html_content,
        },
        auto_open=True,
        run_id=run_id,
    )


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
        uid, cid, sub_sid = _resolve_sandbox_identity(__user__, __metadata__)
        if not cid:
            return "❌ Contexte de session manquant (chat_id)."

        wm = get_window_manager()
        sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
        clean_rid = sanitize_echo_id(run_id, fallback="") if run_id else ""
        if sub_sid and clean_rid and not wm.is_run_owned_by_subagent(uid, cid, clean_rid, sub_sid):
            return f"❌ Accès refusé : le processus `{clean_rid}` n'appartient pas au sous-agent `{sub_sid}`."

        import httpx
        try:
            if action == "kill":
                if sub_sid and not clean_rid:
                    prefix = f"{uid}:{cid}:"
                    candidate_rids = set(wm.list_sandbox_pollers(uid, cid).keys())
                    for k in list(wm._sandbox_run_owners.keys()):
                        if k.startswith(prefix):
                            candidate_rids.add(k[len(prefix):])
                    owned_rids = sorted(rid for rid in candidate_rids if wm.is_run_owned_by_subagent(uid, cid, rid, sub_sid))
                    wm.stop_sandbox_poller(uid, cid, None, sub_sid=sub_sid)
                    if not owned_rids:
                        return "⚠️ Aucun processus Sandbox actif trouvé pour ce sous-agent."
                    killed_rids: list[str] = []
                    pids: list[Any] = []
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        for rid in owned_rids:
                            res = await client.post(
                                f"{sandbox_base}/kill",
                                json={"user_id": uid, "chat_id": cid, "run_id": rid},
                            )
                            data = res.json()
                            if data.get("status") == "killed":
                                killed_rids.extend(data.get("run_ids") or [rid])
                                pids.extend(data.get("pids") or ([data["pid"]] if "pid" in data else []))
                            wm.pop_sandbox_run_owner(uid, cid, rid)
                    if killed_rids:
                        msg = f"Processus Sandbox arrêté (run_id: {', '.join(killed_rids)} | PID: {', '.join(str(p) for p in pids)})."
                        return wrap_tool_output(text=msg, status={"status": "killed", "run_ids": killed_rids, "pids": pids})
                    return "⚠️ Aucun processus Sandbox actif trouvé pour ce sous-agent."

                wm.stop_sandbox_poller(uid, cid, clean_rid or None, sub_sid=sub_sid)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(
                        f"{sandbox_base}/kill",
                        json={"user_id": uid, "chat_id": cid, "run_id": clean_rid or None},
                    )
                data = res.json()
                wm.pop_sandbox_run_owner(uid, cid, clean_rid or None)
                if data.get("status") == "killed":
                    pids = data.get("pids") or ([data["pid"]] if "pid" in data else [])
                    rids = data.get("run_ids") or ([clean_rid] if clean_rid else [])
                    msg = f"Processus Sandbox arrêté (run_id: {', '.join(rids) or 'all'} | PID: {', '.join(str(p) for p in pids)})."
                    return wrap_tool_output(text=msg, status={"status": "killed", "run_ids": rids, "pids": pids})
                return "⚠️ Aucun processus Sandbox actif trouvé pour cette cible."

            bounded_wait = max(0, min(int(wait_sec or 0), SANDBOX_LISTEN_MAX_WAIT_SEC)) if action == "listen" else 0
            prev_entry = wm.get_sandbox_poller(uid, cid, clean_rid) or {}
            out_off = prev_entry.get("stdout_offset", 0) if action == "listen" else 0
            err_off = prev_entry.get("stderr_offset", 0) if action == "listen" else 0

            async with httpx.AsyncClient(timeout=bounded_wait + 10.0) as client:
                if action == "listen" and not clean_rid:
                    list_res = await client.get(f"{sandbox_base}/status", params={"user_id": uid, "chat_id": cid})
                    jobs = list_res.json().get("jobs", [])
                    running = [
                        j for j in jobs
                        if j.get("state") == "running"
                        and wm.is_run_owned_by_subagent(uid, cid, j.get("run_id", ""), sub_sid)
                    ]
                    if len(running) == 1:
                        clean_rid = running[0]["run_id"]
                        prev_entry = wm.get_sandbox_poller(uid, cid, clean_rid) or {}
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
                if sub_sid:
                    jobs = [j for j in jobs if wm.is_run_owned_by_subagent(uid, cid, j.get("run_id", ""), sub_sid)]
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

            poller_entry = wm.get_sandbox_poller(uid, cid, clean_rid)
            if poller_entry is not None:
                poller_entry["stdout_offset"] = data.get("stdout_offset", out_off)
                poller_entry["stderr_offset"] = data.get("stderr_offset", err_off)
                if data.get("state") != "running":
                    wm.stop_sandbox_poller(uid, cid, clean_rid)

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
        """Exécute un script Python (.py), Node.js (.js) ou Bash (.sh) isolé dans le workspace `sandbox` du Codex en mode synchrone (`background=False`) ou serveur détaché (`background=True`)."""
        dependencies = dependencies or []
        uid, cid, sub_sid = _resolve_sandbox_identity(__user__, __metadata__)
        if not cid:
            return "❌ Contexte de session manquant (chat_id)."

        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=cid)

        if timeout_sec > ECHO_MAX_CODE_EXECUTION_TIMEOUT:
            err_msg = f"timeout_sec ({timeout_sec}s) dépasse la limite autorisée ({ECHO_MAX_CODE_EXECUTION_TIMEOUT}s)."
            await events.status(f"Erreur: {err_msg}", done=True)
            return f"❌ {err_msg}"

        uses_echo_monitor, calls_serve_forever = await asyncio.to_thread(
            _inspect_sandbox_script, uid, cid, file_path
        )
        if not background and calls_serve_forever:
            return (
                f"❌ Blocage prévenu : le script '{file_path}' invoque serve_forever() (serveur persistant). "
                "L'exécution synchrone (background=False) est interdite pour un serveur afin de ne pas bloquer la session. "
                f"Rappeler code_executor(file_path='{file_path}', background=True)."
            )

        actual_timeout = timeout_sec
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        wm = get_window_manager()
        wm.set_sandbox_run_owner(uid, cid, run_id, sub_sid)
        ext = file_path.split(".")[-1]
        runtime_icon = "🐍" if ext == "py" else "📦" if ext == "js" else "🐚" if ext == "sh" else "⚙️"
        mode_label = "arrière-plan" if background else "synchrone"
        await events.status(f"{runtime_icon} Exécution {mode_label} de {file_path} ({run_id}, Timeout: {actual_timeout}s)")

        try:
            import httpx
            wm.clear_canvas_signal(uid, cid)
            ui_payload_data = wm.consume_canvas_ui_payload(uid, cid) if fetch_ui_payload else None

            task_audit = {"status": "running", "run_id": run_id, "polls": 0, "payloads_pushed": 0, "payloads_pulled": 0}
            run_result_state: dict[str, Any] = {"state": "running", "exit_code": None}
            sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
            abort_event = asyncio.Event()
            wake_event = asyncio.Event()
            ready_event = asyncio.Event()
            display_event = asyncio.Event()
            job_done_event = asyncio.Event()
            stop_polling_event = asyncio.Event()
            supervisor = {
                "first_seen": {},
                "last_ping_values": {},
                "boot_timeout": ECHO_CANVAS_BOOT_TIMEOUT_SEC,
                "ping_timeout": ECHO_CANVAS_PING_TIMEOUT_SEC,
                "graceful_close": ECHO_CANVAS_GRACEFUL_CLOSE_SEC,
                "closing_since": None,
            }

            async def bidi_polling():
                start_t = asyncio.get_running_loop().time()
                status_notified = False
                current_task = asyncio.current_task()
                poller_entry: dict[str, Any] = {
                    "task": current_task,
                    "wake_event": wake_event,
                    "window_ids": set(),
                    "stdout_offset": 0,
                    "stderr_offset": 0,
                    "sub_sid": sub_sid,
                }
                wm.register_sandbox_poller(uid, cid, run_id, poller_entry)

                async def _inbox_pusher(pclient: httpx.AsyncClient) -> None:
                    last_pushed_sig: Optional[str] = None
                    try:
                        while not stop_polling_event.is_set():
                            try:
                                await asyncio.wait_for(wake_event.wait(), timeout=2.0)
                            except asyncio.TimeoutError:
                                pass
                            if stop_polling_event.is_set():
                                break
                            wake_event.clear()
                            task_audit["polls"] += 1
                            try:
                                owned_wids = set(supervisor["first_seen"].keys())
                                ui_data, ack_counts = wm.peek_canvas_ui_payload(
                                    uid, cid, window_ids=owned_wids, run_id=run_id
                                )
                                if ui_data.get("_echo_signal") == "abort":
                                    task_audit["status"] = "aborted_by_ui"
                                    run_result_state["state"] = "killed"
                                    abort_event.set()
                                    stop_polling_event.set()
                                    if background:
                                        await pclient.post(
                                            f"{sandbox_base}/kill",
                                            json={"user_id": uid, "chat_id": cid, "run_id": run_id},
                                        )
                                    break

                                now = asyncio.get_running_loop().time()
                                if owned_wids:
                                    closed_wids = set(ui_data.get("_closed_windows") or [])
                                    if owned_wids.issubset(closed_wids):
                                        if supervisor["closing_since"] is None:
                                            supervisor["closing_since"] = now
                                        if now - supervisor["closing_since"] >= supervisor["graceful_close"]:
                                            task_audit["status"] = "all_windows_closed"
                                            run_result_state["state"] = "killed"
                                            abort_event.set()
                                            stop_polling_event.set()
                                            if background:
                                                await pclient.post(
                                                    f"{sandbox_base}/kill",
                                                    json={"user_id": uid, "chat_id": cid, "run_id": run_id},
                                                )
                                            break
                                    else:
                                        supervisor["closing_since"] = None
                                        if not background:
                                            pings = ui_data.get("_pings", {})
                                            alive_windows = 0
                                            for s_wid in owned_wids - closed_wids:
                                                first_t = supervisor["first_seen"][s_wid]
                                                current_ping = pings.get(s_wid)
                                                if current_ping is None:
                                                    if now - first_t <= supervisor["boot_timeout"]:
                                                        alive_windows += 1
                                                else:
                                                    last_val, last_time = supervisor["last_ping_values"].get(
                                                        s_wid, (0, now)
                                                    )
                                                    if current_ping > last_val:
                                                        supervisor["last_ping_values"][s_wid] = (current_ping, now)
                                                        alive_windows += 1
                                                    elif now - last_time <= supervisor["ping_timeout"]:
                                                        alive_windows += 1
                                            if alive_windows == 0:
                                                task_audit["status"] = "all_windows_closed_or_crashed"
                                                run_result_state["state"] = "killed"
                                                abort_event.set()
                                                stop_polling_event.set()
                                                break

                                if ui_data:
                                    payload_only = {
                                        k: v for k, v in ui_data.items() if k not in ("_pings", "_inbox_events")
                                    }
                                    payload_sig = json.dumps(payload_only, sort_keys=True, default=str)
                                    if bool(ack_counts) or (bool(payload_only) and payload_sig != last_pushed_sig):
                                        upd_res = await pclient.post(
                                            f"{sandbox_base}/update_payload",
                                            json={
                                                "user_id": uid,
                                                "chat_id": cid,
                                                "run_id": run_id,
                                                "ui_payload": ui_data,
                                            },
                                            timeout=2.0,
                                        )
                                        if upd_res.status_code == 200:
                                            wm.ack_canvas_inbox(uid, cid, ack_counts)
                                            last_pushed_sig = payload_sig
                                            task_audit["payloads_pushed"] += 1
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                pass
                    finally:
                        stop_polling_event.set()

                async def _monitor_reader(pclient: httpx.AsyncClient) -> None:
                    offset = 0
                    seen_running = False
                    unknown_streak = 0
                    try:
                        while not stop_polling_event.is_set():
                            elapsed = asyncio.get_running_loop().time() - start_t
                            if not background and elapsed >= actual_timeout:
                                break
                            try:
                                pr = await pclient.get(
                                    f"{sandbox_base}/poll_monitor",
                                    params={
                                        "user_id": uid,
                                        "chat_id": cid,
                                        "run_id": run_id,
                                        "offset": offset,
                                        "wait_sec": ECHO_CANVAS_POLL_WAIT_SEC,
                                    },
                                    timeout=ECHO_CANVAS_POLL_WAIT_SEC + 3.0,
                                )
                                if pr.status_code == 200:
                                    pdata = pr.json()
                                    offset = pdata.get("offset", offset)
                                    job_state = pdata.get("job_state", "unknown")
                                    job_rc = pdata.get("exit_code")
                                    if job_state == "running":
                                        seen_running = True
                                    if job_state != "unknown":
                                        unknown_streak = 0

                                    for meta in pdata.get("payloads", []):
                                        wid = str(meta.get("window_id", "default"))
                                        mtype = meta.get("type", "display")
                                        if mtype == "display":
                                            if wid not in supervisor["first_seen"]:
                                                supervisor["first_seen"][wid] = asyncio.get_running_loop().time()
                                                poller_entry["window_ids"].add(wid)
                                            display_event.set()
                                        elif mtype == "ready":
                                            ready_event.set()
                                        await _emit_monitor_payload(uid, cid, meta, run_id=run_id)
                                        task_audit["payloads_pulled"] += 1

                                    if job_state == "unknown":
                                        elapsed = asyncio.get_running_loop().time() - start_t
                                        if seen_running or elapsed > ECHO_CANVAS_READY_TIMEOUT_SEC:
                                            unknown_streak += 1
                                            if unknown_streak >= ECHO_CANVAS_UNKNOWN_GRACE_POLLS:
                                                run_result_state["state"] = "error"
                                                task_audit["status"] = "unknown_worker_lost"
                                                stop_polling_event.set()
                                                break
                                    elif job_state not in ("running", "unknown"):
                                        run_result_state["state"] = job_state
                                        run_result_state["exit_code"] = job_rc
                                        task_audit["status"] = job_state
                                        stop_polling_event.set()
                                        break
                                else:
                                    await asyncio.sleep(0.5)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                await asyncio.sleep(0.5)
                    finally:
                        stop_polling_event.set()
                        wake_event.set()

                pusher_task: Optional[asyncio.Task] = None
                reader_task: Optional[asyncio.Task] = None
                try:
                    async with httpx.AsyncClient() as pclient:
                        pusher_task = asyncio.create_task(_inbox_pusher(pclient))
                        reader_task = asyncio.create_task(_monitor_reader(pclient))
                        await asyncio.gather(pusher_task, reader_task)
                except asyncio.CancelledError:
                    pass
                finally:
                    for sub_t in (pusher_task, reader_task):
                        if sub_t is not None and not sub_t.done():
                            sub_t.cancel()
                    job_done_event.set()
                    if supervisor["first_seen"] and not status_notified:
                        status_notified = True
                        term_state = run_result_state["state"]
                        if term_state == "running":
                            elapsed = asyncio.get_running_loop().time() - start_t
                            term_state = "timeout" if (not background and elapsed >= actual_timeout) else "completed"
                        wids = list(supervisor["first_seen"].keys())
                        try:
                            await wm.broadcast_canvas_status(
                                uid,
                                cid,
                                wids,
                                server_status=term_state,
                                run_id=run_id,
                                exit_code=run_result_state["exit_code"],
                            )
                        except Exception:
                            pass
                        wm.cleanup_canvas_run(uid, cid, wids, run_id=run_id)
                    wm.pop_sandbox_poller(uid, cid, run_id, expected_task=current_task)

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
                                "idle_ttl_sec": ECHO_CANVAS_IDLE_TTL_SEC,
                                "max_lifetime_sec": ECHO_CANVAS_MAX_LIFETIME_SEC,
                            },
                        )
                    )
                    abort_wait_task = asyncio.create_task(abort_event.wait())
                    done, _ = await asyncio.wait(
                        [http_task, abort_wait_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if abort_event.is_set():
                        http_task.cancel()
                        raise asyncio.CancelledError("UI requested abort")
                    if not abort_wait_task.done():
                        abort_wait_task.cancel()
                    response = http_task.result()

                if response.status_code == 200:
                    worker_res = response.json()
                    if worker_res.get("status") == "running" and background:
                        poller_entry = wm.get_sandbox_poller(uid, cid, run_id)
                        if poller_entry is not None:
                            poller_entry["stdout_offset"] = len(worker_res.get("output", ""))

                        if uses_echo_monitor:
                            t_ready = asyncio.create_task(ready_event.wait())
                            t_done = asyncio.create_task(job_done_event.wait())
                            _, pending_probe = await asyncio.wait(
                                [t_ready, t_done],
                                timeout=ECHO_CANVAS_READY_TIMEOUT_SEC,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for p_task in pending_probe:
                                p_task.cancel()
                            if (
                                not ready_event.is_set()
                                and not job_done_event.is_set()
                                and not calls_serve_forever
                                and display_event.is_set()
                            ):
                                try:
                                    await asyncio.wait_for(job_done_event.wait(), timeout=0.6)
                                except asyncio.TimeoutError:
                                    pass
                        else:
                            try:
                                await asyncio.wait_for(job_done_event.wait(), timeout=0.5)
                            except asyncio.TimeoutError:
                                pass

                        if job_done_event.is_set() and run_result_state["state"] in ("error", "timeout", "killed"):
                            err_status_data: dict[str, Any] = {}
                            try:
                                async with httpx.AsyncClient(timeout=5.0) as st_client:
                                    st_res = await st_client.get(
                                        f"{sandbox_base}/status",
                                        params={"user_id": uid, "chat_id": cid, "run_id": run_id},
                                    )
                                    if st_res.status_code == 200:
                                        err_status_data = st_res.json()
                            except Exception:
                                pass
                            stderr_chunk = err_status_data.get("stderr_chunk", "")
                            stdout_chunk = err_status_data.get("stdout_chunk", "")
                            rc_val = err_status_data.get("exit_code", run_result_state["exit_code"])
                            await events.status(f"Échec au démarrage ({run_id})", done=True)
                            err_parts = [
                                f"❌ Échec du processus Sandbox au démarrage (`run_id`: `{run_id}`, "
                                f"`state`: `{run_result_state['state']}`, `exit_code`: `{rc_val}`)."
                            ]
                            if stderr_chunk:
                                err_parts.append(f"Stderr :\n{stderr_chunk}")
                            if stdout_chunk:
                                err_parts.append(f"Stdout :\n{stdout_chunk}")
                            return wrap_tool_output(
                                text="\n\n".join(err_parts),
                                status={
                                    "status": "error",
                                    "run_id": run_id,
                                    "state": run_result_state["state"],
                                    "exit_code": rc_val,
                                    "pid": worker_res.get("pid"),
                                },
                            )

                        detach_poller = True
                        wids_list = list(supervisor["first_seen"].keys())
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
                            status={
                                "status": "running",
                                "run_id": run_id,
                                "pid": worker_res.get("pid"),
                                "ui_ready": ready_event.is_set(),
                                "window_ids": wids_list,
                                "idle_ttl_sec": ECHO_CANVAS_IDLE_TTL_SEC,
                                "task_review": task_audit,
                            },
                        )
                    w_status = worker_res.get("status", "success")
                    run_result_state["state"] = "completed" if w_status == "success" else str(w_status)
                    run_result_state["exit_code"] = worker_res.get("exit_code")
                    if not abort_event.is_set():
                        task_audit["status"] = "completed"
                else:
                    run_result_state["state"] = "error"
                    err_msg = f"Erreur Worker (HTTP {response.status_code})"
                    await events.status(err_msg, done=True)
                    return wrap_tool_output(text="", status={"status": "critical_error", "code": response.status_code, "error": err_msg})
            except asyncio.CancelledError:
                run_result_state["state"] = "killed"
                wm.clear_canvas_signal(uid, cid)
                async with httpx.AsyncClient() as client:
                    await client.post(f"{sandbox_base}/kill", json={"user_id": uid, "chat_id": cid, "run_id": run_id})
                task_audit["status"] = "killed"
                raise
            except httpx.RequestError:
                run_result_state["state"] = "error"
                return "❌ Service Coding Worker injoignable."
            except Exception as e:
                run_result_state["state"] = "error"
                task_audit["status"] = f"error: {str(e)}"
                worker_res = {"status": "error", "error": str(e)}
            finally:
                if not detach_poller:
                    polling_task.cancel()

            text_out = worker_res.get("output", "")
            if worker_res.get("monitor_payloads"):
                pulled_count = task_audit.get("payloads_pulled", 0)
                remaining_payloads = worker_res["monitor_payloads"][pulled_count:]
                success_count = 0
                post_display_wids: list[str] = []
                for meta in remaining_payloads:
                    try:
                        await _emit_monitor_payload(uid, cid, meta, run_id=run_id)
                        if meta.get("type", "display") == "display":
                            p_wid = str(meta.get("window_id", "default"))
                            if p_wid not in post_display_wids:
                                post_display_wids.append(p_wid)
                        success_count += 1
                    except Exception as e:
                        text_out += f"\n[Erreur Système Monitor: {str(e)}]"
                if post_display_wids:
                    try:
                        await wm.broadcast_canvas_status(
                            uid,
                            cid,
                            post_display_wids,
                            server_status=run_result_state["state"],
                            run_id=run_id,
                            exit_code=run_result_state["exit_code"],
                        )
                    except Exception:
                        pass
                    wm.cleanup_canvas_run(uid, cid, post_display_wids, run_id=run_id)
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

    @echo_tool
    async def ssh_executor(
        self,
        account_id: str,
        command: str,
        timeout_sec: int = 60,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        __metadata__: Optional[dict] = None,
    ) -> dict | str:
        """Exécute une commande sur un serveur SSH configuré dans l'Identity Vault sans exposer ses secrets."""
        uid, cid, _ = _resolve_sandbox_identity(__user__, __metadata__)
        if not cid:
            return "❌ Contexte de session manquant (chat_id)."

        clean_acc = (account_id or "").strip()
        clean_cmd = (command or "").strip()
        if not clean_acc or not clean_cmd:
            return "❌ Les paramètres 'account_id' et 'command' sont obligatoires."

        if not (1 <= timeout_sec <= ECHO_MAX_CODE_EXECUTION_TIMEOUT):
            return (
                f"❌ timeout_sec ({timeout_sec}s) doit être compris "
                f"entre 1 et {ECHO_MAX_CODE_EXECUTION_TIMEOUT}s."
            )

        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=cid)

        def _load_ssh_creds() -> str:
            return EchoStateManager(user_id=uid).get_vault_credential("ssh", clean_acc)

        raw_creds = await asyncio.to_thread(_load_ssh_creds)
        if not raw_creds:
            return (
                f"❌ Identifiants SSH introuvables pour '{clean_acc}'. "
                "Vérifier les comptes disponibles avec list_identities(service=\"ssh\")."
            )

        try:
            creds = json.loads(raw_creds)
        except Exception:
            return f"❌ Configuration SSH invalide (JSON corrompu) pour '{clean_acc}'."

        host = str(creds.get("host") or "").strip()
        user = str(creds.get("user") or "").strip()
        if not host or not user:
            return f"❌ Configuration SSH incomplète pour '{clean_acc}' ('host' et 'user' requis)."

        try:
            port = int(creds.get("port") or 22)
        except (TypeError, ValueError):
            port = 22

        password = str(creds.get("password") or "")
        private_key = str(creds.get("private_key") or "")

        await events.status(f"🔐 Exécution SSH sur '{clean_acc}' ({user}@{host}:{port})...")
        sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")

        try:
            import httpx
            async with httpx.AsyncClient(timeout=float(timeout_sec) + 10.0) as client:
                response = await client.post(
                    f"{sandbox_base}/ssh",
                    json={
                        "user_id": uid,
                        "chat_id": cid,
                        "host": host,
                        "port": port,
                        "user": user,
                        "password": password,
                        "private_key": private_key,
                        "command": clean_cmd,
                        "timeout": timeout_sec,
                    },
                )

            if response.status_code != 200:
                err_msg = f"Erreur Worker SSH (HTTP {response.status_code}) : {response.text[:300]}"
                await events.status(err_msg, done=True)
                return wrap_tool_output(
                    text="",
                    status={"status": "error", "account_id": clean_acc, "exit_code": -1, "error": err_msg},
                )

            res_data = response.json()
            status_val = res_data.get("status", "error")
            exit_code = res_data.get("exit_code", -1)
            text_out = res_data.get("output") or ""
            err_out = res_data.get("error") or ""
            if err_out:
                text_out = f"{text_out}\n\n⚠️ Erreur SSH :\n{err_out}".strip()

            await events.status(f"Exécution SSH terminée (code {exit_code}).", done=True)
            return wrap_tool_output(
                text=text_out,
                status={
                    "status": "success" if status_val == "success" else "error",
                    "account_id": clean_acc,
                    "exit_code": exit_code,
                },
            )
        except httpx.RequestError as e:
            err_msg = f"Service Coding Worker injoignable : {str(e)}"
            await events.status(err_msg, done=True)
            return f"❌ {err_msg}"
        except Exception as e:
            err_msg = f"Erreur Client SSH : {str(e)}"
            await events.status(err_msg, done=True)
            return f"❌ {err_msg}"
