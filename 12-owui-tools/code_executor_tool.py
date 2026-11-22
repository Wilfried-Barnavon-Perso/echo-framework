# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Code Executor
author: Wilfried BARNAVON
version: 6.17.4.1
description: Composant système interne : ECHO Code Executor (Python, Node.js, Bash & SSH).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.17.4.1: Propagation du jeton HMAC sandbox_callback_token lors de l'exécution et alignement Option B (sans Pacemaker WS/BidiRouter).
# 6.16.0.2: Retrait bidi_polling/poller, raccordement /wait_ready, offsets WM, abort léger et garde expose_app(background=True).
# 6.15.2.1: Durcissement Option A : garde pré-vol serve_forever(), handshake ready/crash, boucle poller réveillée par wake_event, arrêt sur unknown x3 et cleanup scopé par run_id.
# 6.13.1.2: Fiabilisation Pacemaker bidi_polling (constantes SSOT, peek/ack scopé par window_id, push delta-only, grâce _is_closed, arrêt auto sur fin de job et broadcast_canvas_status).
# 6.6.30.1: Isolation par sub_sid (ECHO_SUBAGENT_CONTEXT) des jobs, pollers et actions status/listen/kill.

# ECHO CONFIG NAME : ECHO Code Sandbox

import asyncio
import json
import os
import re
import sys
import uuid
from typing import Any, List, Literal, Optional

sys.path.append("/app/backend/echo_libs")
from echo_constants import (  # noqa: E402
    CODEX_DIR_NAME,
    CODEX_WORKSPACE_SANDBOX,
    ECHO_CANVAS_IDLE_TTL_SEC,
    ECHO_CANVAS_MAX_LIFETIME_SEC,
    ECHO_CANVAS_READY_TIMEOUT_SEC,
    ECHO_CODING_WORKER_BASE_URL,
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
    """Inspecte le script sandbox en lecture confinée et détecte (uses_echo_monitor, calls_daemon)."""
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
        calls_daemon = ("expose_app(" in stripped)
        return ("echo_monitor" in stripped, calls_daemon)
    except Exception:
        return (False, False)


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
        sandbox_base = ECHO_CODING_WORKER_BASE_URL
        clean_rid = sanitize_echo_id(run_id, fallback="") if run_id else ""
        if sub_sid and clean_rid and not wm.is_run_owned_by_subagent(uid, cid, clean_rid, sub_sid):
            return f"❌ Accès refusé : le processus `{clean_rid}` n'appartient pas au sous-agent `{sub_sid}`."

        import httpx
        try:
            if action == "kill":
                if sub_sid and not clean_rid:
                    prefix = f"{uid}:{cid}:"
                    candidate_rids = {
                        k[len(prefix):]
                        for k in list(wm._sandbox_run_owners.keys())
                        if k.startswith(prefix)
                    }
                    owned_rids = sorted(rid for rid in candidate_rids if wm.is_run_owned_by_subagent(uid, cid, rid, sub_sid))
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
                            wm.clear_sandbox_stream_offsets(uid, cid, rid)
                    if killed_rids:
                        msg = f"Processus Sandbox arrêté (run_id: {', '.join(killed_rids)} | PID: {', '.join(str(p) for p in pids)})."
                        return wrap_tool_output(text=msg, status={"status": "killed", "run_ids": killed_rids, "pids": pids})
                    return "⚠️ Aucun processus Sandbox actif trouvé pour ce sous-agent."

                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.post(
                        f"{sandbox_base}/kill",
                        json={"user_id": uid, "chat_id": cid, "run_id": clean_rid or None},
                    )
                data = res.json()
                wm.pop_sandbox_run_owner(uid, cid, clean_rid or None)
                wm.clear_sandbox_stream_offsets(uid, cid, clean_rid or None)
                if data.get("status") == "killed":
                    pids = data.get("pids") or ([data["pid"]] if "pid" in data else [])
                    rids = data.get("run_ids") or ([clean_rid] if clean_rid else [])
                    msg = f"Processus Sandbox arrêté (run_id: {', '.join(rids) or 'all'} | PID: {', '.join(str(p) for p in pids)})."
                    return wrap_tool_output(text=msg, status={"status": "killed", "run_ids": rids, "pids": pids})
                return "⚠️ Aucun processus Sandbox actif trouvé pour cette cible."

            bounded_wait = max(0, min(int(wait_sec or 0), SANDBOX_LISTEN_MAX_WAIT_SEC)) if action == "listen" else 0
            out_off, err_off = wm.get_sandbox_stream_offsets(uid, cid, clean_rid) if (action == "listen" and clean_rid) else (0, 0)

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
                        out_off, err_off = wm.get_sandbox_stream_offsets(uid, cid, clean_rid)
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

            new_out_off = int(data.get("stdout_offset", out_off))
            new_err_off = int(data.get("stderr_offset", err_off))
            if data.get("state") == "running":
                if action == "listen":
                    wm.set_sandbox_stream_offsets(uid, cid, clean_rid, new_out_off, new_err_off)
            else:
                wm.clear_sandbox_stream_offsets(uid, cid, clean_rid)
                wm.pop_sandbox_run_owner(uid, cid, clean_rid)

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

        _, calls_daemon = await asyncio.to_thread(
            _inspect_sandbox_script, uid, cid, file_path
        )
        if not background and calls_daemon:
            return (
                f"❌ Blocage prévenu : le script '{file_path}' invoque echo_monitor.expose_app() "
                "(serveur persistant). L'exécution synchrone (background=False) est interdite pour un serveur afin de ne pas bloquer "
                f"la session. Rappeler code_executor(file_path='{file_path}', background=True)."
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
            sandbox_base = ECHO_CODING_WORKER_BASE_URL
            callback_token = wm.issue_sandbox_callback_token(uid, cid)
            try:
                async with httpx.AsyncClient(timeout=actual_timeout + 30.0) as client:
                    response = await client.post(
                        ECHO_CODING_WORKER_URL,
                        json={
                            "file_path": file_path,
                            "dependencies": dependencies,
                            "user_id": uid,
                            "chat_id": cid,
                            "run_id": run_id,
                            "callback_token": callback_token,
                            "timeout": actual_timeout,
                            "max_output_length": ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS,
                            "background": background,
                            "idle_ttl_sec": ECHO_CANVAS_IDLE_TTL_SEC,
                            "max_lifetime_sec": ECHO_CANVAS_MAX_LIFETIME_SEC,
                        },
                    )
            except asyncio.CancelledError:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as kclient:
                        await kclient.post(f"{sandbox_base}/kill", json={"user_id": uid, "chat_id": cid, "run_id": run_id})
                except Exception:
                    pass
                wm.pop_sandbox_run_owner(uid, cid, run_id)
                wm.clear_sandbox_stream_offsets(uid, cid, run_id)
                raise
            except httpx.RequestError:
                wm.pop_sandbox_run_owner(uid, cid, run_id)
                return "❌ Service Coding Worker injoignable."

            if response.status_code != 200:
                wm.pop_sandbox_run_owner(uid, cid, run_id)
                err_msg = f"Erreur Worker (HTTP {response.status_code})"
                await events.status(err_msg, done=True)
                return wrap_tool_output(text="", status={"status": "critical_error", "code": response.status_code, "error": err_msg})

            worker_res = response.json()
            if background and worker_res.get("status") == "running":
                init_out = worker_res.get("output", "")
                wm.set_sandbox_stream_offsets(uid, cid, run_id, len(init_out), 0)

                wdata: dict[str, Any] = {}
                try:
                    async with httpx.AsyncClient(timeout=ECHO_CANVAS_READY_TIMEOUT_SEC + 2.0) as wclient:
                        wres = await wclient.get(
                            f"{sandbox_base}/wait_ready",
                            params={
                                "user_id": uid,
                                "chat_id": cid,
                                "run_id": run_id,
                                "timeout": ECHO_CANVAS_READY_TIMEOUT_SEC,
                            },
                        )
                        if wres.status_code == 200:
                            wdata = wres.json()
                except Exception:
                    wdata = {}

                frames = wdata.get("canvas_frames") or worker_res.get("canvas_frames") or []
                frame_wids: list[str] = []
                for f in frames:
                    if isinstance(f, dict):
                        await wm.broadcast_canvas_frame(
                            user_id=uid,
                            chat_id=cid,
                            frame_data=f,
                            auto_open=True,
                            run_id=run_id,
                            mode=str(f.get("mode") or ""),
                        )
                        fwid = str(f.get("id") or f.get("window_id") or "default")
                        if fwid not in frame_wids:
                            frame_wids.append(fwid)

                job_state = wdata.get("job_state")
                if job_state not in ("running", None):
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
                    rc_val = err_status_data.get("exit_code", wdata.get("exit_code"))
                    if frame_wids:
                        await wm.broadcast_canvas_status(
                            uid,
                            cid,
                            frame_wids,
                            server_status=str(job_state),
                            run_id=run_id,
                            exit_code=rc_val,
                        )
                        wm.cleanup_canvas_run(uid, cid, frame_wids, run_id=run_id)
                    wm.pop_sandbox_run_owner(uid, cid, run_id)
                    wm.clear_sandbox_stream_offsets(uid, cid, run_id)

                    if job_state == "completed" and rc_val in (0, None):
                        await events.status("Exécution terminée.", done=True)
                        return wrap_tool_output(
                            text=stdout_chunk or init_out,
                            status={
                                "status": "success",
                                "run_id": run_id,
                                "state": "completed",
                                "exit_code": rc_val,
                                "pid": worker_res.get("pid"),
                                "window_ids": frame_wids,
                            },
                        )

                    await events.status(f"Échec au démarrage ({run_id})", done=True)
                    err_parts = [
                        f"❌ Échec du processus Sandbox au démarrage (`run_id`: `{run_id}`, "
                        f"`state`: `{job_state}`, `exit_code`: `{rc_val}`)."
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
                            "state": job_state,
                            "exit_code": rc_val,
                            "pid": worker_res.get("pid"),
                        },
                    )

                ready_wids = [str(w) for w in (wdata.get("ready_windows") or []) if w]
                wids_list = frame_wids or ready_wids
                ui_ready = bool(wdata.get("ui_ready", worker_res.get("ui_ready", False)))
                await events.status(f"Serveur/Tâche Sandbox {run_id} actif en arrière-plan.", done=True)
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
                        "ui_ready": ui_ready,
                        "window_ids": wids_list,
                        "idle_ttl_sec": ECHO_CANVAS_IDLE_TTL_SEC,
                    },
                )

            w_status = worker_res.get("status", "success")
            exit_code = worker_res.get("exit_code")
            server_status = "completed" if exit_code == 0 and w_status == "success" else str(worker_res.get("state") or "error")
            text_out = worker_res.get("output", "")
            frames = worker_res.get("canvas_frames") or []
            post_display_wids: list[str] = []
            for f in frames:
                if isinstance(f, dict):
                    try:
                        await wm.broadcast_canvas_frame(
                            user_id=uid,
                            chat_id=cid,
                            frame_data=f,
                            auto_open=True,
                            run_id=run_id,
                            mode=str(f.get("mode") or ""),
                        )
                        p_wid = str(f.get("id") or f.get("window_id") or "default")
                        if p_wid not in post_display_wids:
                            post_display_wids.append(p_wid)
                    except Exception as e:
                        text_out += f"\n[Erreur Système Monitor: {str(e)}]"
            if post_display_wids:
                try:
                    await wm.broadcast_canvas_status(
                        uid,
                        cid,
                        post_display_wids,
                        server_status=server_status,
                        run_id=run_id,
                        exit_code=exit_code,
                    )
                except Exception:
                    pass
                wm.cleanup_canvas_run(uid, cid, post_display_wids, run_id=run_id)
                s_pluriel = "s" if len(post_display_wids) > 1 else ""
                text_out += f"\n[Système: ECHO Monitor a transmis {len(post_display_wids)} vue{s_pluriel} au navigateur.]"

            wm.pop_sandbox_run_owner(uid, cid, run_id)
            wm.clear_sandbox_stream_offsets(uid, cid, run_id)

            if worker_res.get("error"):
                text_out += f"\n\n⚠️ Erreur d'exécution :\n{worker_res['error']}"

            echo_status: dict[str, Any] = {
                "status": w_status,
                "run_id": run_id,
                "pid": worker_res.get("pid"),
            }
            if worker_res.get("error"):
                echo_status["error"] = worker_res["error"]

            await events.status("Exécution terminée.", done=True)
            return wrap_tool_output(text=text_out, status=echo_status)
        except Exception as e:
            wm.pop_sandbox_run_owner(uid, cid, run_id)
            wm.clear_sandbox_stream_offsets(uid, cid, run_id)
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
