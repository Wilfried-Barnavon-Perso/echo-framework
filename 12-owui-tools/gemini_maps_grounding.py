"""
title: ECHO Google Maps Grounding
author: Wilfried BARNAVON
version: 12.51
description: 12.51: Fix retour contexte (string pure) pour map_viewer.
"""

import orjson as json
import sys
import os
from typing import Optional, Any, Tuple, Union
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, EchoGeminiClient, EchoRichUI
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL, MODEL_LITE

class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=3, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAPS_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour la recherche Maps.")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def search_maps(
        self,
        query: str,
        latitude: float = None,
        longitude: float = None,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> Union[dict, Tuple[HTMLResponse, dict]]:
        """
        Recherche des lieux, commerces ou itinéraires via Google Maps.
        Affiche une interface visuelle interactive et fournit les détails textuels au modèle.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"🗺️ Exploration Maps : {query}...")

        api_keys = self.auth.get_api_keys(__user__.get("id"))
        if not api_keys: 
            return wrap_tool_output(text="❌ Configuration ECHO Requise : Aucune clé API Google AI Studio trouvée.", status={"status": "error"})

        payload = {
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "tools": [{"googleMaps": {"enableWidget": True}}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "MINIMAL"}}
        }
        
        if latitude is not None and longitude is not None:
            payload["toolConfig"] = {"retrievalConfig": {"latLng": {"latitude": float(latitude), "longitude": float(longitude)}}}

        try:
            data = await EchoGeminiClient.call(
                keys=api_keys, target_model=MODEL_LITE, payload=payload,
                threshold=self.valves.KEY_SWITCH_THRESHOLD, events=events, timeout=self.valves.MAPS_TIMEOUT
            )
            
            cand = data.get("candidates", [])[0]
            full_text = ""
            if "content" in cand:
                for p in cand["content"].get("parts", []):
                    if "text" in p: full_text += p["text"]
            
            await events.status("Carte interactive prête.", done=True)

            # --- RÉUSSITE : RÉPONSE RICH UI (GOOGLE MAPS EMBED) ---
            response = EchoRichUI.map_viewer(query=query, title=f"ECHO Maps : {query}")

            # --- CONTEXTE POUR LE LLM (STRING PURE) ---
            # En renvoyant une string, le LLM reçoit directement le contenu à traiter.
            return response, full_text

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Maps: {str(e)}", status={"status": "error"})
