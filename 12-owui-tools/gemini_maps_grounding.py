"""
title: ECHO Google Maps Grounding
author: Wilfried BARNAVON
version: 12.30
description: 12.30: Specialized tool for native Google Maps grounding (places, reviews, transit). Removed general web search to favor sovereign alternatives.
"""

import orjson as json
import httpx
import sys
import os
from typing import Optional, Any
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL, MODEL_LITE

class Tools:
    class Valves(BaseModel):
        GEMINI_FLASH_MODEL: str = Field(default=MODEL_LITE)

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def _call_gemini_api(self, payload: dict, api_key: str, url_suffix: str = "generateContent") -> dict:
        """Appel générique à l'API Gemini (Non-Streaming) pour le grounding natif."""
        url = f"{GOOGLE_API_BASE_URL}/models/{self.valves.GEMINI_FLASH_MODEL}:{url_suffix}?key={api_key}"
        headers = {
            "x-goog-api-key": api_key, 
            "Content-Type": "application/json", 
            "User-Agent": ECHO_USER_AGENT
        }
        async with httpx.AsyncClient(timeout=120, http2=True) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"API Maps Error {resp.status_code}: {resp.text}")
            return resp.json()

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

        api_key = self.auth.get_api_key(__user__.get("id"))
        if not api_key: 
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
            data = await self._call_gemini_api(payload, api_key)
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
