"""
title: ECHO Code Executor
author: Wilfried BARNAVON
version: 7.10
description: Composant système interne : ECHO Code Executor (Python & Node.js).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 7.10: Implémentation du Supervisor Pattern & JS Heartbeat (fermeture/crash automatique).
# 7.9: Implémentation du Bidi Polling asynchrone (Temps Réel UI<->Sandbox) et outil kill_sandbox_execution.
# 7.8: Injection dynamique de la constante ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS au worker pour l'anti-OOM.
# 7.7: Implémentation du canal passe-plat descendant asynchrone via `fetch_ui_payload` (UI -> Sandbox) sans impact LLM.
# 7.6: Encapsulation du code JS (ECHO Monitor) en IIFE asynchrone pour isolation (correction return global via eval).
# 7.5: Fix Monitor - Injection de EchoUI.get_floating_monitor_js() (fonction absente côté navigateur), sérialisation JSON des paramètres JS.
# 7.3: Allègement de la docstring via intégration de la documentation /sandbox-agent-readme.txt.

# ECHO CONFIG NAME : ECHO Code Sandbox

from echo_constants import ECHO_CODING_WORKER_URL, ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT, ECHO_MAX_CODE_EXECUTION_TIMEOUT
from echo_constants import ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS
from echo_ui import EchoUI
from echo_events import EchoEvents
from echo_core import wrap_tool_output
import sys
import json
import asyncio
from typing import Any, Optional, List

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")


class Tools:
    def __init__(self):
        pass

    async def kill_sandbox_execution(
        self,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None
    ) -> str:
        """
        Permet au modèle d'interrompre de force l'exécution en cours d'une Sandbox.
        Détruit immédiatement le processus distant orphelin ou bloqué associé à la session courante.
        """
        __user__ = __user__ or {}
        __metadata__ = __metadata__ or {}
        uid = __user__.get("id", "system")
        cid = __metadata__.get("chat_id")
        url = ECHO_CODING_WORKER_URL.replace("/execute", "/kill")
        
        import httpx
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json={"user_id": uid, "chat_id": cid})
            
        data = res.json()
        if data.get("status") == "killed":
            return wrap_tool_output(text=f"Processus Sandbox (PID {data.get('pid')}) détruit avec succès.", user_id=uid, chat_id=cid, metadata=__metadata__)
        return wrap_tool_output(text="Aucune exécution Sandbox active trouvée pour cette session.", user_id=uid, chat_id=cid, metadata=__metadata__)

    async def code_executor(
        self,
        file_path: str,
        dependencies: Optional[List[str]] = None,
        timeout_sec: int = ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT,
        fetch_ui_payload: bool = False,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        __metadata__: Optional[dict] = None
    ) -> str:
        """
        Permet au modèle d'exécuter du code (Python ou Node.js) dans une Sandbox isolée.
        Le modèle DOIT STRICTEMENT écrire le code dans le workspace `sandbox` du Codex avant d'appeler cet outil.
        L'exécution est déterminée par l'extension du fichier (.py ou .js).

        Permet de lancer un calcul avec un pont bidirectionnel temps-réel. La Sandbox et le navigateur (UI) s'échangent des données à la volée.
        Le retour de l'outil inclura un objet `task_review` détaillant la télémétrie asynchrone de l'exécution (statut final, nombre de cycles UI synchronisés).

        S'il y a des dépendances externes requises non pré-installées, l'agent DOIT les lister dans l'argument `dependencies` (ex: ["requests", "pandas"] ou ["axios", "express"]).

        L'utilisation de la librairie d'interface "ECHO Monitor" et la liste exhaustive des dépendances pré-installées
        sont documentées dans le fichier en lecture seule `/sandbox-agent-readme.txt` (lisible depuis la Sandbox).

        Le script s'exécute avec les accès stricts suivants :
        - '/files' : Dossier en Lecture seule contenant les fichiers (pièces jointes) du Registre.
        - '/main' : Dossier en Lecture seule contenant le code versionné (Codex workspace main).
        - '/sandbox' : Espace d'écriture persistant visible dans le Codex (workspace sandbox).

        Args:
            file_path (str): Le chemin relatif du fichier à exécuter dans la sandbox (ex: "script.py" ou "dossier/app.js").
            dependencies (list[str]): Liste des paquets pip/npm additionnels à installer avant l'exécution.
            timeout_sec (int): Délai maximum accordé au script.
            fetch_ui_payload (bool): Initialise le payload avec les données UI avant l'exécution (le flux temps-réel prend ensuite le relais).
        """
        __user__ = __user__ or {}
        __metadata__ = __metadata__ or {}
        dependencies = dependencies or []

        events = EchoEvents(__event_emitter__, __event_call__)

        if timeout_sec > ECHO_MAX_CODE_EXECUTION_TIMEOUT:
            err_msg = f"timeout_sec ({timeout_sec}s) dépasse la limite autorisée ({ECHO_MAX_CODE_EXECUTION_TIMEOUT}s)."
            await events.status(f"Erreur: {err_msg}", done=True)
            return wrap_tool_output(text="", status={"status": "error", "error": err_msg}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

        actual_timeout = timeout_sec

        ext = file_path.split('.')[-1]
        runtime_icon = "🐍" if ext == "py" else "📦" if ext == "js" else "⚙️"
        await events.status(f"{runtime_icon} Exécution de {file_path} en cours... (Timeout: {actual_timeout}s)")

        try:
            import httpx
            
            ui_payload_data = None
            if fetch_ui_payload:
                try:
                    raw = await events.call_execute("return typeof window._echoGetAggregatedPayload === 'function' ? window._echoGetAggregatedPayload() : null;")
                    ui_payload_data = raw if isinstance(raw, dict) else (json.loads(raw) if raw else None)
                except Exception:
                    pass
                    
            task_audit = {"status": "running", "polls": 0, "payloads_pushed": 0, "payloads_pulled": 0}
            sandbox_base = ECHO_CODING_WORKER_URL.replace("/execute", "")
            uid = __user__.get("id", "system")
            cid = __metadata__.get("chat_id")
            
            import uuid
            run_id = str(uuid.uuid4())
            abort_event = asyncio.Event()
            
            supervisor = {
                "first_seen": {},
                "last_ping_values": {},
                "boot_timeout": 3.0,
                "ping_timeout": 2.5
            }
            
            async def bidi_polling():
                offset = 0
                start_t = asyncio.get_event_loop().time()
                monitor_lib_js = EchoUI.get_floating_monitor_js()
                try:
                    async with httpx.AsyncClient() as pclient:
                        while asyncio.get_event_loop().time() - start_t < actual_timeout:
                            task_audit["polls"] += 1
                            # 1. Descente Multiplexée
                            try:
                                raw = await events.call_execute("return typeof window._echoGetAggregatedPayload === 'function' ? window._echoGetAggregatedPayload() : null;")
                                ui_data = raw if isinstance(raw, dict) else (json.loads(raw) if raw else None)
                                if ui_data:
                                    if ui_data.get("_echo_signal") == "abort":
                                        task_audit["status"] = "aborted_by_ui"
                                        abort_event.set()
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
                                                else:
                                                    if now - last_time <= supervisor["ping_timeout"]:
                                                        alive_windows += 1
                                        if alive_windows == 0:
                                            task_audit["status"] = "all_windows_closed_or_crashed"
                                            abort_event.set()
                                            break
                                            
                                    await pclient.post(f"{sandbox_base}/update_payload", json={"user_id": uid, "chat_id": cid, "run_id": run_id, "ui_payload": ui_data}, timeout=2.0)
                                    task_audit["payloads_pushed"] += 1
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                pass

                            # 2. Remontée Temps Réel
                            try:
                                pr = await pclient.get(f"{sandbox_base}/poll_monitor", params={"user_id": uid, "chat_id": cid, "offset": offset}, timeout=2.0)
                                if pr.status_code == 200:
                                    pdata = pr.json()
                                    offset = pdata.get("offset", offset)
                                    payloads_list = pdata.get("payloads", [])
                                    for meta in payloads_list:
                                        wid = str(meta.get("window_id", "default"))
                                        if wid not in supervisor["first_seen"]:
                                            supervisor["first_seen"][wid] = asyncio.get_event_loop().time()
                                            
                                        args = ", ".join(json.dumps(str(meta.get(k, d))) for k, d in (
                                            ("window_id", "default"), ("title", "Monitor"),
                                            ("width", "100%"), ("height", "400px")
                                        ))
                                        b64 = json.dumps(meta.get('html', ''))
                                        js_code = f'''
                                            (async () => {{
                                                if (typeof window.echoCreateFloatingMonitor !== 'function') {{
                                                    {monitor_lib_js}
                                                }}
                                                const bin = atob({b64});
                                                const htmlContent = new TextDecoder('utf-8').decode(Uint8Array.from(bin, c => c.charCodeAt(0)));
                                                const [wid, title, width, height] = [{args}];
                                                window.echoCreateFloatingMonitor(wid, title, htmlContent, width, height);
                                            }})();
                                        '''
                                        await events.emit_execute(js_code)
                                        task_audit["payloads_pulled"] += 1
                            except Exception:
                                pass
                            await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    pass

            polling_task = asyncio.create_task(bidi_polling())

            try:
                # Ajout d'une marge substantielle (ex: 30s) pour couvrir le temps d'installation des dépendances dynamiques
                async with httpx.AsyncClient(timeout=actual_timeout + 30.0) as client:
                    http_task = asyncio.create_task(client.post(
                        ECHO_CODING_WORKER_URL,
                        json={
                            "file_path": file_path,
                            "dependencies": dependencies,
                            "ui_payload": ui_payload_data,
                            "user_id": uid,
                            "chat_id": cid,
                            "run_id": run_id,
                            "timeout": actual_timeout,
                            "max_output_length": ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS
                        }
                    ))
                    
                    done, pending = await asyncio.wait(
                        [http_task, asyncio.create_task(abort_event.wait())],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    if abort_event.is_set():
                        http_task.cancel()
                        raise asyncio.CancelledError("UI requested abort")
                        
                    response = http_task.result()
                    
                if response.status_code == 200:
                    worker_res = response.json()
                    if not abort_event.is_set():
                        task_audit["status"] = "completed"
                else:
                    err_msg = f"Erreur Worker (HTTP {response.status_code})"
                    await events.status(err_msg, done=True)
                    return wrap_tool_output(text="", status={"status": "critical_error", "code": response.status_code, "error": err_msg}, user_id=uid, chat_id=cid, metadata=__metadata__)
            except asyncio.CancelledError:
                # Interception du Stop Generating (Utilisateur) ou Abort (Navigateur)
                await events.emit_execute("if(window._echoGetAggregatedPayload){ window._echoGetAggregatedPayload()._echo_signal = null; }")
                async with httpx.AsyncClient() as client:
                    await client.post(f"{sandbox_base}/kill", json={"user_id": uid, "chat_id": cid, "run_id": run_id})
                task_audit["status"] = "killed"
                worker_res = {"status": "error", "error": "Execution aborted by UI or User."}
                raise
            except httpx.RequestError:
                return wrap_tool_output(text="", status={"status": "error", "error": "Service Coding Worker injoignable."}, user_id=uid, chat_id=cid, metadata=__metadata__)
            except Exception as e:
                task_audit["status"] = f"error: {str(e)}"
                worker_res = {"status": "error", "error": str(e)}
            finally:
                polling_task.cancel()

            text_out = worker_res.get("output", "")

            # Interception POST-RUN du Multiplexage ECHO Monitor
            if worker_res.get('monitor_payloads'):
                pulled_count = task_audit.get("payloads_pulled", 0)
                remaining_payloads = worker_res['monitor_payloads'][pulled_count:]
                
                monitor_lib_js = EchoUI.get_floating_monitor_js()
                success_count = 0
                for meta in remaining_payloads:
                    try:
                        # json.dumps : échappement JS sûr (quotes, backslashes, retours ligne)
                        args = ", ".join(json.dumps(str(meta.get(k, d))) for k, d in (
                            ("window_id", "default"), ("title", "Monitor"),
                            ("width", "100%"), ("height", "400px")
                        ))
                        b64 = json.dumps(meta.get('html', ''))
                        js_code = f'''
                            (async () => {{
                                if (typeof window.echoCreateFloatingMonitor !== 'function') {{
                                    {monitor_lib_js}
                                }}
                                const bin = atob({b64});
                                const htmlContent = new TextDecoder('utf-8').decode(Uint8Array.from(bin, c => c.charCodeAt(0)));
                                const [wid, title, width, height] = [{args}];
                                window.echoCreateFloatingMonitor(wid, title, htmlContent, width, height);
                            }})();
                        '''
                        await events.emit_execute(js_code)
                        success_count += 1
                    except Exception as e:
                        text_out += f"\n[Erreur Système Monitor: {str(e)}]"

                if success_count > 0:
                    s_pluriel = "s" if success_count > 1 else ""
                    text_out += f"\n[Système: ECHO Monitor a transmis {success_count} interface{s_pluriel} au navigateur en Post-Run.]"

            if worker_res.get("error"):
                text_out += f"\n\n⚠️ Erreur d'exécution :\n{worker_res['error']}"

            echo_status = {"status": worker_res.get("status", "success"), "task_review": task_audit}
            if worker_res.get("error"):
                echo_status["error"] = worker_res["error"]

            await events.status("Exécution terminée.", done=True)
            return wrap_tool_output(text=text_out, status=echo_status, user_id=uid, chat_id=cid, metadata=__metadata__)

        except Exception as e:
            return wrap_tool_output(text="", status={"status": "error", "error": f"Erreur Client: {str(e)}"}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
