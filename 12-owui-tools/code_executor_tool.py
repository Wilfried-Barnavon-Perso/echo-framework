"""
title: ECHO Code Executor
author: Wilfried BARNAVON
version: 7.7
description: Composant système interne : ECHO Code Executor (Python & Node.js).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 7.7: Implémentation du canal passe-plat descendant asynchrone via `fetch_ui_payload` (UI -> Sandbox) sans impact LLM.
# 7.6: Encapsulation du code JS (ECHO Monitor) en IIFE asynchrone pour isolation (correction return global via eval).
# 7.5: Fix Monitor - Injection de EchoUI.get_floating_monitor_js() (fonction absente côté navigateur), sérialisation JSON des paramètres JS.
# 7.4: Intégration du Multiplexage ECHO Monitor (JSONL via UCTP emit_execute) avec fenêtrage dynamique.
# 7.3: Allègement de la docstring via intégration de la documentation /sandbox-agent-readme.txt.

# ECHO CONFIG NAME : ECHO Code Sandbox

from echo_constants import ECHO_CODING_WORKER_URL, ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT, ECHO_MAX_CODE_EXECUTION_TIMEOUT
from echo_ui import EchoUI
from echo_events import EchoEvents
from echo_core import wrap_tool_output
import sys
import json
from typing import Any, Optional, List

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")


class Tools:
    def __init__(self):
        pass

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
            fetch_ui_payload (bool): Si True, rapatrie de manière invisible les données du navigateur (images, audio) et les met à disposition du script. Lisez `/sandbox-agent-readme.txt` pour savoir comment les lire.
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
                    raw = await events.call_execute("return typeof window.getUIPayload === 'function' ? window.getUIPayload() : null;")
                    ui_payload_data = raw if isinstance(raw, dict) else json.loads(raw)
                except Exception:
                    pass
                    
            # Ajout d'une marge substantielle (ex: 30s) pour couvrir le temps d'installation des dépendances dynamiques
            async with httpx.AsyncClient(timeout=actual_timeout + 30.0) as client:
                response = await client.post(
                    ECHO_CODING_WORKER_URL,
                    json={
                        "file_path": file_path,
                        "dependencies": dependencies,
                        "ui_payload": ui_payload_data,
                        "user_id": __user__.get("id", "system"),
                        "chat_id": __metadata__.get("chat_id"),
                        "timeout": actual_timeout
                    }
                )

            if response.status_code == 200:
                worker_res = response.json()
                text_out = worker_res.get("output", "")

                # Interception du Multiplexage ECHO Monitor
                if worker_res.get('monitor_payloads'):
                    # Lazy-Loading de la définition JS du Monitor (injectée une fois par exécution)
                    monitor_lib_js = EchoUI.get_floating_monitor_js()
                    success_count = 0
                    for meta in worker_res['monitor_payloads']:
                        try:
                            # json.dumps : échappement JS sûr (quotes, backslashes, retours ligne)
                            args = ", ".join(json.dumps(str(meta.get(k, d))) for k, d in (
                                ("window_id", "default"), ("title", "Monitor"),
                                ("width", "100%"), ("height", "400px")
                            ))
                            b64 = json.dumps(meta.get('html', ''))
                            js_code = f'''
                                (async () => {{
                                    {monitor_lib_js if success_count == 0 else ""}
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
                        text_out += f"\n[Système: ECHO Monitor a transmis {success_count} interface{s_pluriel} au navigateur.]"

                if worker_res.get("error"):
                    text_out += f"\n\n⚠️ Erreur d'exécution :\n{worker_res['error']}"

                echo_status = {"status": worker_res.get("status", "success")}
                if worker_res.get("error"):
                    echo_status["error"] = worker_res["error"]

                await events.status("Exécution terminée.", done=True)
                return wrap_tool_output(text=text_out, status=echo_status, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
            else:
                err_msg = f"Erreur Worker (HTTP {response.status_code})"
                await events.status(err_msg, done=True)
                return wrap_tool_output(text="", status={"status": "critical_error", "code": response.status_code, "error": err_msg}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

        except httpx.RequestError:
            return wrap_tool_output(text="", status={"status": "error", "error": "Service Coding Worker injoignable."}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text="", status={"status": "error", "error": f"Erreur Client: {str(e)}"}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
