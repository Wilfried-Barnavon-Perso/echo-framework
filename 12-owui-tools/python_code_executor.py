"""
title: ECHO Python Code Executor
author: Wilfried BARNAVON
version: 6.3
description: 6.0: Validation analytique par calcul empirique. Capacité graphique retirée (Headless).
             6.1: Alignement du status sur le standard wrap_tool_output ECHO.
             6.2: Correction d'un bug d'import (ECHO_PYTHON_WORKER_URL) et unbound local error (text_out).
             6.3: Nettoyage sémantique de la docstring (Retrait de la mention PRAF).
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
        """Exécution isolée de code Python (Worker distant). Idéal pour validation analytique (math, dates, data). Aucun accès direct aux fichiers locaux (injecter les données textuellement). IMPLIQUE Python 3."""
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

                await events.status("Exécution terminée.", done=True)
                return wrap_tool_output(text=text_out, status=echo_status)
            else:
                err_msg = f"Erreur Worker (HTTP {response.status_code})"
                await events.status(f"❌ {err_msg}", done=True)
                return wrap_tool_output(text=err_msg, status={"status": "critical_error", "code": response.status_code})

        except requests.exceptions.ConnectionError:
            return wrap_tool_output(text="❌ Service Python Worker injoignable.", status={"status": "error"})
        except Exception as e: 
            return wrap_tool_output(text=f"❌ Erreur Client: {str(e)}", status={"status": "error", "error": str(e)})
