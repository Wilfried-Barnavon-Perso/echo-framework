"""
title: ECHO Python Code Executor
author: Wilfried BARNAVON
version: 6.2
description: 6.0: Validation analytique (PRAF) par calcul empirique. Capacité graphique retirée (Headless).
             6.1: Alignement du status sur le standard wrap_tool_output ECHO.
             6.2: Correction d'un bug d'import (ECHO_PYTHON_WORKER_URL) et unbound local error (text_out).
"""

# ECHO CONFIG NAME : ECHO Python Sandbox

import requests
import orjson as json
import sys
from pydantic import BaseModel, Field
from typing import Optional, Any


# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents
from echo_constants import ECHO_PYTHON_WORKER_URL

class Tools:
    class Valves(BaseModel):
        TIMEOUT: int = Field(default=30, description="Délai d'attente maximum pour l'exécution (secondes).")

    def __init__(self):
        self.valves = self.Valves()

    async def execute_python(
        self,
        code: str,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Exécute du code Python dans un environnement sandbox sécurisé et isolé pour la validation analytique (PRAF).
        Idéal pour les calculs complexes, l'analyse de données (pandas, numpy, networkx) ou la manipulation de structures JSON.
        Le code a accès à Internet mais ne partage pas ses fichiers avec les autres outils ECHO.
        NOTE : La génération de graphiques ou schémas visuels n'est pas supportée par cet outil.
        :param code: Le code Python complet à exécuter (ex: import pandas as pd; ...).
        """
        events = EchoEvents(__event_emitter__, __event_call__)

        await events.status("🐍 Exécution Python en cours...")

        try:
            response = requests.post(
                ECHO_PYTHON_WORKER_URL,
                json={"code": code},
                timeout=self.valves.TIMEOUT
            )
            
            if response.status_code == 200:
                worker_res = response.json()
                text_out = worker_res.get("output", "")
                if worker_res.get("error"):
                    text_out += f"\n\n⚠️ Erreur d'exécution :\n{worker_res['error']}"
                
                # Status ECHO propre (sans polluer avec output/error du worker)
                echo_status = {"status": worker_res.get("status", "success")}
                if worker_res.get("error"):
                    echo_status["error"] = worker_res["error"]

                # PURGE & REDIRECTION: Extraire les graphiques éventuels pour l'IA
                multiparts = []
                plots = worker_res.pop("plots", [])
                if isinstance(plots, list):
                    for plot_b64 in plots:
                        multiparts.append({"type": "media", "mime_type": "image/png", "data": plot_b64})
                
                await events.status("Exécution terminée.", done=True)
                return wrap_tool_output(text=text_out, status=echo_status, echo_tool_multiparts=multiparts)
            else:
                err_msg = f"Erreur Worker (HTTP {response.status_code})"
                await events.status(f"❌ {err_msg}", done=True)
                return wrap_tool_output(text=err_msg, status={"status": "critical_error", "code": response.status_code})

        except requests.exceptions.ConnectionError:
            return wrap_tool_output(text="❌ Service Python Worker injoignable.", status={"status": "error"})
        except Exception as e: 
            return wrap_tool_output(text=f"❌ Erreur Client: {str(e)}", status={"status": "error", "error": str(e)})
