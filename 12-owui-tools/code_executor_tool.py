"""
title: ECHO Code Executor
author: Wilfried BARNAVON
version: 7.2
description: Composant système interne : ECHO Code Executor (Python & Node.js).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 7.2: Alignement sémantique des montages Bwrap dans la docstring (/sandbox, /main, /files).
# 7.1: Correction typage (Optional/List) pour Pydantic V2 et renommage de execute_code en code_executor.
# 7.0: Refonte Multi-langage (Python 3.14 + Node 22). Exécution stricte depuis un fichier du Codex (sandbox). Ajout de la gestion des dépendances (dependencies).
# 6.10: Délégation de la gestion du timeout (ECHO_MAX_CODE_EXECUTION_TIMEOUT) au modèle.
# 6.9: Refonte asynchrone via httpx, sécurisation de la sandbox et gestion multi-workspaces.
# 6.8: Mise à jour sémantique de la docstring (explicitation de l'interdiction de génération UI).
# 6.7: Précision de la version (Python 3.14) et rappel d'isolation dans la docstring.

# ECHO CONFIG NAME : ECHO Code Sandbox

import sys
from typing import Any, Optional, List

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_events import EchoEvents
from echo_constants import ECHO_CODING_WORKER_URL, ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT, ECHO_MAX_CODE_EXECUTION_TIMEOUT

class Tools:
    def __init__(self):
        pass

    async def code_executor(
        self,
        file_path: str,
        dependencies: Optional[List[str]] = None,
        timeout_sec: int = ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT,
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
        
        Dépendances pré-installées (inutile de les redemander) :
        - Python 3.14 : requests, httpx, pandas, numpy, scipy, beautifulsoup4, lxml, Pillow, pydantic, sqlalchemy, openpyxl, PyPDF2
        - Node.js 22.x : axios, cheerio, js-yaml, lodash, papaparse, mathjs, zod, dotenv, moment
        
        Le script s'exécute avec les accès stricts suivants :
        - '/files' : Dossier en Lecture seule contenant les fichiers (pièces jointes) du Registre.
        - '/main' : Dossier en Lecture seule contenant le code versionné (Codex workspace main).
        - '/sandbox' : Espace d'écriture persistant visible dans le Codex (workspace sandbox).
        
        Args:
            file_path (str): Le chemin relatif du fichier à exécuter dans la sandbox (ex: "script.py" ou "dossier/app.js").
            dependencies (list[str]): Liste des paquets pip/npm additionnels à installer avant l'exécution.
            timeout_sec (int): Délai maximum accordé au script.
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
            # Ajout d'une marge substantielle (ex: 30s) pour couvrir le temps d'installation des dépendances dynamiques
            async with httpx.AsyncClient(timeout=actual_timeout + 30.0) as client:
                response = await client.post(
                    ECHO_CODING_WORKER_URL,
                    json={
                        "file_path": file_path,
                        "dependencies": dependencies,
                        "user_id": __user__.get("id", "system"),
                        "chat_id": __metadata__.get("chat_id"),
                        "timeout": actual_timeout
                    }
                )
            
            if response.status_code == 200:
                worker_res = response.json()
                text_out = worker_res.get("output", "")
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

        except httpx.RequestError as e:
            return wrap_tool_output(text="", status={"status": "error", "error": "Service Coding Worker injoignable."}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e: 
            return wrap_tool_output(text="", status={"status": "error", "error": f"Erreur Client: {str(e)}"}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
