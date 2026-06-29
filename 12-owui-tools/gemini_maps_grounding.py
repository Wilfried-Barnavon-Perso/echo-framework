"""
title: ECHO Maps Grounding
author: Wilfried BARNAVON
version: 13.4
description: 13.4: Fix - Intégration de TEMP_DEFAULT et TOP_P_DEFAULT dans generationConfig.
             13.3: Ajout argument optionnel print_map et lecture de _echo_suppress_map_ui pour blocage du rendu UI.
             13.2: Fix commentaires : MODEL_LITE est le plancher de la cascade (pas de fallback
             descendant possible). Aucune cascade automatique vers un modèle inférieur.
"""

import orjson as json
import sys
from typing import Optional, Any, Tuple, Union
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_ui import EchoUI
from echo_constants import (
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    THINKING_LEVEL_TOOLS, TEMP_DEFAULT, TOP_P_DEFAULT
)


class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(
            default=ECHO_API_KEY_THRESHOLD,
            description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours."
        )
        MAX_RETRIES: int = Field(
            default=ECHO_API_MAX_RETRIES,
            description="Nombre de tentatives maximum."
        )
        MAPS_TIMEOUT: int = Field(
            default=120,
            description="Délai d'attente maximum (secondes) pour la recherche Maps."
        )

    def __init__(self):
        self.valves = self.Valves()

    async def search_maps(
        self,
        query: str,
        latitude: float = None,
        longitude: float = None,
        print_map: bool = True,
        __user__: dict = {},
        __metadata__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> Union[dict, Tuple[HTMLResponse, dict]]:
        """
        Recherche géo-spatiale (lieux, itinéraires) via Google Maps. Affiche l'UI locale. Le Modèle analyse ensuite les données textuelles retournées.
        """
        user_id = __user__.get("id", "system")
        chat_id = (__metadata__ or {}).get("chat_id")
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"🗺️ Exploration Maps : {query}...")

        payload = {
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            # Grounding natif Google Maps — active l'outil googleMaps du modèle Gemini
            "tools": [{"googleMaps": {"enableWidget": True}}],
            "generationConfig": {
                "temperature": TEMP_DEFAULT,
                "topP": TOP_P_DEFAULT,
                "thinkingConfig": {"thinkingLevel": THINKING_LEVEL_TOOLS}
            }
        }

        # Contextualisation géographique si les coordonnées sont fournies
        if latitude is not None and longitude is not None:
            payload["toolConfig"] = {
                "retrievalConfig": {
                    "latLng": {
                        "latitude": float(latitude),
                        "longitude": float(longitude)
                    }
                }
            }

        try:
            # call_cascade gère : clamping politique Pipe, thinkingConfig auto.
            # MODEL_LITE est le plancher de la cascade (PRO→FLASH→LITE) : aucun fallback
            # descendant possible depuis ce niveau. Si LITE est indisponible, la cascade
            # est épuisée immédiatement. Utiliser MODEL_FLASH si un repli vers LITE est souhaité.
            data, model_key, reason = await EchoGeminiClient.call_cascade(
                target_model_key="MODEL_LITE",
                payload=payload,
                user_id=user_id,
                metadata=__metadata__,
                events=events,
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=self.valves.MAX_RETRIES,
                timeout=self.valves.MAPS_TIMEOUT,
                chat_id=chat_id,
            )

            if data is None:
                # Cascade épuisée — tous les modèles en erreur
                return wrap_tool_output(
                    text="❌ Google Maps : aucun modèle disponible (cascade épuisée). "
                         "Vérifiez vos quotas API.",
                    status={"status": "cascade_exhausted"}
                )

            candidates = data.get("candidates", [])
            if not candidates:
                return wrap_tool_output(
                    text="⚠️ Google Maps n'a renvoyé aucun résultat. Le Modèle DOIT demander à l'utilisateur de préciser sa recherche.",
                    status={"status": "no_results"}
                )

            # Extraction du texte enrichi retourné par le grounding Gemini
            cand = candidates[0]
            full_text = ""
            if "content" in cand:
                for p in cand["content"].get("parts", []):
                    if "text" in p:
                        full_text += p["text"]

            if not full_text:
                return wrap_tool_output(
                    text="⚠️ Recherche Maps complétée mais aucune information textuelle "
                         "n'a été fournie par l'API.",
                    status={"status": "empty_response"}
                )

            await events.status("Carte interactive prête.", done=True)

            # Rendu UI : embed Google Maps + contexte textuel pour le LLM
            suppress_ui = (__metadata__ or {}).get("_echo_suppress_map_ui", False)
            if print_map and not suppress_ui:
                response = EchoUI.map_viewer(query=query, title=f"ECHO Maps : {query}")
                return response, wrap_tool_output(text=full_text)
            
            return wrap_tool_output(text=full_text)

        except Exception as e:
            return wrap_tool_output(
                text=f"❌ Erreur Maps: {str(e)}",
                status={"status": "error"}
            )
