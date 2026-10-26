"""
title: Edge Embedding Bridge Filter
author: ECHO Framework
author_url: https://github.com/echo-framework
version: 5.231.11.1
description: Composant système interne : Edge Embedding Bridge Filter.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.11.1: Réétagement de la priorité à 3 (après window_manager_filter à 2 et avant context_gauge_filter à 10).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.26: Ajustement du HUD WebGPU (Décalage horizontal à calc(50% + 125px) pour éviter le chevauchement avec la barre de contexte).
# 1.25: Fix HUD WebGPU (Saut de position lors du clignotement) via CSS scale indépendant.
# 1.24: Optimisation (Bypass Serveur-Side) empêchant l'injection JS sur mobile et purge du code mort associé.

import asyncio
import logging
import re
import sys
import time
from typing import Optional, Dict, Any
import httpx
from pydantic import BaseModel, Field

if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_constants import (
    DEFAULT_EDGE_EMBEDDING_TIMEOUT,
    ECHO_EMBEDDING_BASE_URL,
    ECHO_EDGE_ONNX_REPO,
    ECHO_EDGE_HUD_TITLE,
    ECHO_EDGE_MODEL_CACHE_VERSION,
    ECHO_EDGE_DTYPE,
    ECHO_EDGE_MIN_BUFFER_BYTES,
    ECHO_MOBILE_UA_PATTERN,
)
from echo_events import EchoEvents
from echo_ui import EchoUI

SCRIPT_VERSION = "5.231.11.1"
_MOBILE_UA_REGEX = re.compile(ECHO_MOBILE_UA_PATTERN, re.IGNORECASE)
logger = logging.getLogger("ECHO-EDGE-BRIDGE")


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=3, hidden=True, description="Priorité d'exécution (3 = après window_manager_filter à 2 et avant context_gauge_filter à 10).")
        ENABLE_EDGE_EMBEDDING: bool = Field(
            default=True,
            description="Activer le pont Edge Embedding (nécessite BunkerWeb ou un proxy WSS configuré)"
        )
        WAIT_FOR_EDGE_EMBEDDING: bool = Field(
            default=True,
            description="Bloque le flux jusqu'à l'initialisation du Edge Embedding."
        )
        EDGE_EMBEDDING_TIMEOUT: int = Field(
            default=DEFAULT_EDGE_EMBEDDING_TIMEOUT,
            description="Délai maximum d'attente (en secondes) avant de forcer le repli sur le CPU."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTMgMmwyIDYuNW0tNyAwaDIuNW0tMyA3LjVsMi41LTdtMyA3bC0yLjUtN20tNiA0aDEwbTEgMmEyIDIgMCAwIDEgMiAydjZhMiAyIDAgMCAxLTIgMmgtMTJhMiAyIDAgMCAxLTItMnYtNmEyIDIgMCAwIDEgMi0yaDEyIi8+PC9zdmc+"

    async def inlet(
        self,
        body: dict,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[dict] = None,
        __request__: Optional[Any] = None,
        __metadata__: Optional[Dict] = None,
    ) -> dict:
        """
        Intercepte la requête entrante.
        Injecte le pont JavaScript factorisé (EchoUI) avec validation WebGPU stricte en amont.
        Bloque jusqu'à la préparation ou au constat d'incompatibilité du modèle WebGPU.
        """
        if not self.valves.ENABLE_EDGE_EMBEDDING or not __event_emitter__:
            return body

        # --- BYPASS SERVEUR-SIDE POUR MOBILES ---
        if __request__:
            user_agent = __request__.headers.get("user-agent", "")
            if _MOBILE_UA_REGEX.search(user_agent):
                logger.info("📱 Bypass Mobile (Server-Side) : Pont WebGPU ignoré pour ce client.")
                return body

        user_id = __user__.get("id", "anonymous") if __user__ else "anonymous"

        # Génération factorisée du script JS via EchoUI
        js_code = EchoUI.get_edge_embedding_bridge_js(
            script_version=SCRIPT_VERSION,
            target_repo=ECHO_EDGE_ONNX_REPO,
            hud_title=ECHO_EDGE_HUD_TITLE,
            model_cache_version=ECHO_EDGE_MODEL_CACHE_VERSION,
            target_dtype=ECHO_EDGE_DTYPE,
            min_buffer_bytes=ECHO_EDGE_MIN_BUFFER_BYTES,
        )

        events = EchoEvents(__event_emitter__)
        await events.emit_execute(js_code)

        if self.valves.WAIT_FOR_EDGE_EMBEDDING:
            timeout = self.valves.EDGE_EMBEDDING_TIMEOUT
            logger.info(f"⏳ Attente de la préparation Edge WebGPU pour l'utilisateur {user_id}...")
            start_time = time.time()

            async with httpx.AsyncClient() as client:
                while time.time() - start_time < timeout:
                    try:
                        resp = await client.get(
                            f"{ECHO_EMBEDDING_BASE_URL}/internal/edge-status",
                            params={"user_id": user_id},
                            timeout=2.0,
                        )
                        if resp.status_code == 200:
                            status = resp.json().get("status")
                            if status == "ready":
                                logger.info(f"✅ WebGPU Ready détecté pour {user_id}. Poursuite du flux.")
                                break
                            elif status == "incompatible":
                                logger.warning(f"⚠️ WebGPU Incompatible détecté pour {user_id}. Repli CPU immédiat.")
                                break
                            elif status == "connecting":
                                pass
                            elif status == "unknown" and (time.time() - start_time > 5):
                                logger.warning(f"⚠️ Aucun signe de vie du pont Edge après 5s pour {user_id}. Annulation de l'attente.")
                                break
                    except Exception:
                        pass

                    await asyncio.sleep(0.25)
                else:
                    logger.error(f"❌ Timeout ({timeout}s) atteint en attendant WebGPU pour {user_id}. Repli CPU par défaut.")

        return body
