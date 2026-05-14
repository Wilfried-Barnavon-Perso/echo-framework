"""
title: ECHO Maps Grounding
author: Wilfried BARNAVON
version: 12.14
description: 12.14: Correction de syntaxe (IndentationError).
"""

import orjson as json
import sys
import os
from typing import Optional, Any, Tuple, Union
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_ui import EchoUI
from echo_constants import (
    MODEL_LITE, MODEL_ROUTING, ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    TEMP_DEFAULT, TOP_P_DEFAULT
)


class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        MAPS_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour la recherche Maps.")

    def __init__(self):
        self.valves = self.Valves()

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
        Recherche des lieux, commerces ou itinéraires, affiche une carte via Google Maps.
        Affiche une interface visuelle interactive à l'utilisateur et fournit les détails textuels au modèle.
        
        NOTE : Cet outil utilise une délégation cognitive (MODEL_LITE). En cas d'erreur de quota (429) 
        ou d'indisponibilité, le Modèle doit signaler cette limitation technique à l'utilisateur.
        """
        user_id = __user__.get("id", "system")
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"🗺️ Exploration Maps : {query}...")

        payload = {
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "tools": [{"googleMaps": {"enableWidget": True}}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "MINIMAL"}}
        }
        
        if latitude is not None and longitude is not None:
            payload["toolConfig"] = {"retrievalConfig": {"latLng": {"latitude": float(latitude), "longitude": float(longitude)}}}

        try:
            data = await EchoGeminiClient.call(
                target_model=MODEL_LITE, 
                payload=payload,
                user_id=user_id,
                threshold=self.valves.KEY_SWITCH_THRESHOLD, 
                max_retries=self.valves.MAX_RETRIES,
                events=events, 
                timeout=self.valves.MAPS_TIMEOUT
            )
            
            candidates = data.get("candidates", [])
            if not candidates:
                return wrap_tool_output(text="⚠️ Google Maps n'a renvoyé aucun résultat pour cette recherche ou cet itinéraire. Veuillez préciser votre demande.", status={"status": "no_results"})

            cand = candidates[0]
            full_text = ""
            if "content" in cand:
                for p in cand["content"].get("parts", []):
                    if "text" in p: full_text += p["text"]

            if not full_text:
                return wrap_tool_output(text="⚠️ Recherche Maps complétée mais aucune information textuelle n'a été fournie par l'API.", status={"status": "empty_response"})


            await events.status("Carte interactive prête.", done=True)

            # --- RÉUSSITE : RÉPONSE RICH UI (GOOGLE MAPS EMBED) ---
            response = EchoUI.map_viewer(query=query, title=f"ECHO Maps : {query}")

            # --- CONTEXTE POUR LE LLM (STRUCTURE) ---
            return response, wrap_tool_output(text=full_text)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Maps: {str(e)}", status={"status": "error"})
