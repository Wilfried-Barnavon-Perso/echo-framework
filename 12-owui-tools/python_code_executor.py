"""
title: ECHO Python Code Executor
author: Wilfried BARNAVON
version: 6.8
description: Composant système interne : ECHO Python Code Executor.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.7: Mise à jour sémantique de la docstring (explicitation de l'interdiction de génération UI).
# 6.6: Précision de la version (Python 3.14) et rappel d'isolation dans la docstring.
# 6.5: Nettoyage du code : suppression des imports inutilisés (PEP8).
# 6.4: Ajout de l'argument __metadata__ dans l'interface de l'outil pour assurer la compatibilité OWUI.
# 6.3: Nettoyage sémantique de la docstring (Retrait de la mention PRAF).

# ECHO CONFIG NAME : ECHO Python Sandbox

import sys
from pydantic import BaseModel, Field
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_events import EchoEvents
from echo_constants import ECHO_CODING_WORKER_URL

class Tools:
    class Valves(BaseModel):
        TIMEOUT: int = Field(default=30, description="Délai d'attente maximum pour l'exécution (secondes).")

    def __init__(self):
        self.valves = self.Valves()

    async def execute_python(
        self,
        code: str,
        __user__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        __metadata__: dict = None
    ) -> str:
        """
        Permet au modèle d'exécuter du code (Python, etc.) dans une Sandbox isolée.
        Le script s'exécute avec les accès stricts suivants :
        - '/workspace' : Dossier en Lecture/Écriture pour générer et manipuler des fichiers.
        - '/inputs' : Dossier en Lecture seule contenant les fichiers fournis par l'utilisateur.
        
        Note à l'Orchestrateur : Si la tâche de développement ou d'exécution est complexe, il est vivement recommandé de confier l'utilisation de cet outil à un sous-agent spécialisé.
        """
        __user__ = __user__ or {}
        __metadata__ = __metadata__ or {}

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status("🐍 Exécution de code en cours...")

        try:
            import httpx
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
                response = await client.post(
                    ECHO_CODING_WORKER_URL,
                    json={
                        "code": code,
                        "user_id": __user__.get("id", "system"),
                        "chat_id": __metadata__.get("chat_id"),
                        "timeout": self.valves.TIMEOUT
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
                await events.status(f"❌ {err_msg}", done=True)
                return wrap_tool_output(text=err_msg, status={"status": "critical_error", "code": response.status_code}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

        except httpx.RequestError as e:
            return wrap_tool_output(text="❌ Service Coding Worker injoignable.", status={"status": "error"}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e: 
            return wrap_tool_output(text=f"❌ Erreur Client: {str(e)}", status={"status": "error", "error": str(e)}, user_id=__user__.get("id", "system"), chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
