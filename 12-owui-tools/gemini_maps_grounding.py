"""
title: ECHO Google Maps Grounding
author: Wilfried BARNAVON
version: 12.31
description: 12.31: Integrated Centralized EchoGeminiClient for multi-key resilience.
"""

import orjson as json
import sys
import os
from typing import Optional, Any
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL, MODEL_LITE

class Tools:
    class Valves(BaseModel):
        GEMINI_FLASH_MODEL: str = Field(default=MODEL_LITE)
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
    ) -> str:
        """
        Recherche des lieux, des commerces, des avis ou des informations géographiques via Google Maps.
        Idéal pour : "Bons restaurants près de moi", "Horaires de la boulangerie X", "Itinéraire vers Y".
        
        :param query: La requête géographique précise.
        :param latitude: (Optionnel) Latitude de référence (pour 'près de moi').
        :param longitude: (Optionnel) Longitude de référence (pour 'près de moi').
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"🗺️ Recherche Google Maps : {query}...")

        api_keys = self.auth.get_api_keys(__user__.get("id"))
        if not api_keys: 
            return wrap_tool_output(text="❌ Configuration ECHO Requise : Aucune clé API Google AI Studio trouvée.", status={"status": "error"})

        payload = {
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "tools": [{"googleMaps": {"enableWidget": True}}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "MINIMAL"}}
        }
        
        if latitude is not None and longitude is not None:
            payload["toolConfig"] = {
                "retrievalConfig": {
                    "latLng": {"latitude": float(latitude), "longitude": float(longitude)}
                }
            }

        try:
            data = await EchoGeminiClient.call(
                keys=api_keys,
                target_model=self.valves.GEMINI_FLASH_MODEL,
                payload=payload,
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=3,
                events=events,
                timeout=self.valves.MAPS_TIMEOUT
            )
            
            cand = data.get("candidates", [])[0]
            full_text = ""
            if "content" in cand:
                for p in cand["content"].get("parts", []):
                    if "text" in p: full_text += p["text"]
            
            all_sources = []
            if "groundingMetadata" in cand:
                gm = cand["groundingMetadata"]
                if "groundingChunks" in gm:
                    for chunk in gm["groundingChunks"]:
                        if "maps" in chunk:
                            all_sources.append(f"- [{chunk['maps'].get('title', 'Lieu')}]({chunk['maps'].get('uri', '#')})")

            final_output = full_text.strip()
            if all_sources:
                final_output += f"\n\n📍 **Lieux trouvés sur Maps :**\n" + "\n".join(all_sources)

            await events.status("Recherche Maps terminée.", done=True)
            return wrap_tool_output(text=final_output, status={"status": "success"})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Maps: {str(e)}", status={"status": "error"})
