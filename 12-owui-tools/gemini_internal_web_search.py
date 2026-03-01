"""
title: ECHO Gemini Web Search
author: Wilfried BARNAVON
version: 12.5
description: 12.5: Restored full docstrings and mission instructions (Strict Architecture).
"""

import json
import os
import httpx
import uuid
import random
import asyncio
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field

# Importations ECHO Strictes (Volume Docker)
import sys
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents
from echo_constants import ECHO_USER_AGENT, GOOGLE_SSE_URL

class Tools:
    class Valves(BaseModel):
        SEARCH_MODEL: str = Field(default="gemini-3-flash-preview", description="Modèle utilisé pour la recherche et la synthèse.")
        MAX_TOKENS: int = Field(default=65536, description="Nombre maximum de tokens en sortie.")
        DEFAULT_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH", description="Niveau de réflexion par défaut.")
        HTTP_TIMEOUT: int = Field(default=180, description="Timeout pour la recherche (secondes).")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def gemini_internal_web_search(
        self, 
        query: str, 
        location: str, 
        current_date: str, 
        current_time: str, 
        thinking_level: Optional[Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"]] = None,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        [SEARCH ENGINE] Effectue une recherche Google en temps réel pour obtenir des informations fraîches (news, météo, faits).
        Réponse EXCLUSIVEMENT en JSON structuré.

        VOTRE MISSION (Modèle Mandant) :
        1. Utilisez cet outil pour vérifier des faits ou obtenir des actualités récentes.
        2. Choisissez le 'thinking_level' : 'MINIMAL' pour une réponse rapide, 'HIGH' pour une analyse croisée de sources.
        3. Les résultats sont ancrés (Grounding) dans la recherche Google.

        :param query: La recherche à effectuer.
        :param location: Lieu de l'utilisateur (pour la pertinence locale).
        :param current_date: Date actuelle.
        :param current_time: Heure actuelle.
        :param thinking_level: (Optionnel) Niveau de réflexion souhaité pour cette recherche.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or "id" not in __user__: return json.dumps({"error": "User ID missing"})
        
        token, project_id = self.auth.get_credentials(__user__["id"])
        if not token or not project_id: return json.dumps({"error": "Google Auth failed"})

        await events.status(f"🌐 ECHO Search : {query}...")
        active_thinking = thinking_level or self.valves.DEFAULT_THINKING_LEVEL
        
        headers = {
            "Authorization": f"Bearer {token}", 
            "Content-Type": "application/json", 
            "User-Agent": ECHO_USER_AGENT,
            "x-goog-api-client": "gl-python/3.10"
        }
        
        search_schema = {
            "type": "OBJECT",
            "properties": {
                "search_metadata": {
                    "type": "OBJECT",
                    "properties": {
                        "location_used": {"type": "STRING"},
                        "sources_found": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}, "url": {"type": "STRING"}}}}
                    },
                    "required": ["location_used", "sources_found"]
                },
                "search_logic": {
                    "type": "OBJECT",
                    "properties": {
                        "search_strategy": {"type": "STRING"},
                        "grounding_confidence": {"type": "NUMBER"}
                    },
                    "required": ["search_strategy", "grounding_confidence"]
                },
                "search_payload": {
                    "type": "OBJECT",
                    "properties": {
                        "summary_fr": {"type": "STRING"},
                        "key_facts": {"type": "ARRAY", "items": {"type": "STRING"}}
                    },
                    "required": ["summary_fr", "key_facts"]
                }
            },
            "required": ["search_metadata", "search_logic", "search_payload"]
        }

        context_prompt = f"Contexte: {location}, le {current_date} à {current_time}.\nRequête: {query}\nSynthétise en français."
        payload = {
            "model": self.valves.SEARCH_MODEL,
            "project": project_id,
            "request": {
                "contents": [{"role": "user", "parts": [{"text": context_prompt}]}],
                "tools": [{"google_search": {}}, {"urlContext": {}}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": search_schema,
                    "maxOutputTokens": self.valves.MAX_TOKENS,
                    "thinkingConfig": {"includeThoughts": True, "thinkingLevel": active_thinking}
                }
            }
        }

        try:
            async with httpx.AsyncClient(timeout=self.valves.HTTP_TIMEOUT) as client:
                full_text = ""
                async with client.stream("POST", GOOGLE_SSE_URL, headers=headers, json=payload) as resp:
                    if resp.status_code != 200: return json.dumps({"error": f"API Error {resp.status_code}"})
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                                cand = data.get("response", {}).get("candidates", [])[0]
                                if "content" in cand:
                                    parts = cand["content"].get("parts", [])
                                    if parts and "text" in parts[0]: full_text += parts[0]["text"]
                            except: pass
                await events.status(f"🌐 Recherche terminée.", done=True)
                return full_text
        except Exception as e: return json.dumps({"error": "Search Exception", "details": str(e)})
