"""
title: ECHO Python Code Executor
author: Wilfried BARNAVON
version: 6.14
description: Composant système interne : ECHO Python Code Executor.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.10: Délégation de la gestion du timeout (ECHO_MAX_CODE_EXECUTION_TIMEOUT) au modèle.
# 6.9: Refonte asynchrone via httpx, sécurisation de la sandbox et gestion multi-workspaces.
# 6.8: Mise à jour sémantique de la docstring (explicitation de l'interdiction de génération UI).
# 6.7: Précision de la version (Python 3.14) et rappel d'isolation dans la docstring.
# 6.6: Nettoyage du code : suppression des imports inutilisés (PEP8).
# 6.5: Ajout de l'argument __metadata__ dans l'interface de l'outil pour assurer la compatibilité OWUI.

# ECHO CONFIG NAME : ECHO Python Sandbox

import sys
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_events import EchoEvents
from echo_constants import ECHO_CODING_WORKER_URL, ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT, ECHO_MAX_CODE_EXECUTION_TIMEOUT

class Tools:
    def __init__(self):
        pass

    async def execute_python(
        self,
        code: str,
        timeout_sec: int = ECHO_DEFAULT_CODE_EXECUTION_TIMEOUT,
        __user__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        __metadata__: dict = None
    ) -> str:
        """
        Permet au modèle d'exécuter du code (Python, etc.) dans une Sandbox isolée.
        Le script s'exécute avec les accès stricts suivants :
        - '/ro_user_files' : Dossier en Lecture seule contenant les fichiers (pièces jointes) du Registre.
        - '/ro_user_edits' : Dossier en Lecture seule contenant le code versionné (Codex workspace main).
        - '/workspace' : Espace d'écriture persistant visible dans le Codex (workspace sandbox).
        
        Args:
            code (str): Le code source complet à exécuter.
            timeout_sec (int): Délai maximum accordé au script. Une fois ce délai strictement 
                               dépassé, l'environnement d'exécution éphémère (la Sandbox) 
                               disparaît intégralement, entraînant la destruction instantanée 
                               de tous les processus et variables en mémoire.
        
        Note à l'Orchestrateur : Si la tâche de développement ou d'exécution est complexe, il est vivement recommandé de confier l'utilisation de cet outil à un sous-agent spécialisé.
        """
        __user__ = __user__ or {}
        __metadata__ = __metadata__ or {}

        events = EchoEvents(__event_emitter__, __event_call__)
        
        if timeout_sec > ECHO_MAX_CODE_EXECUTION_TIMEOUT:
            err_msg = f"timeout_sec ({timeout_sec}s) dépasse la limite autorisée ({ECHO_MAX_CODE_EXECUTION_TIMEOUT}s)."
            await events.status(f"Erreur: {err_msg}", done=True)
            return wrap_tool_output(text="", status={"status": "error", "error": err_msg}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        
        actual_timeout = timeout_sec
        
        await events.status(f"🐍 Exécution de code en cours... (Timeout: {actual_timeout}s)")

        try:
            import httpx
            async with httpx.AsyncClient(timeout=actual_timeout + 2.0) as client:
                response = await client.post(
                    ECHO_CODING_WORKER_URL,
                    json={
                        "code": code,
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
