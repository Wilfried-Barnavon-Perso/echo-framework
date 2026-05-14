"""
title: ECHO Organic Memory Tool
author: Wilfried BARNAVON
version: 2.5
description: 2.5: Migration vers EchoGeminiClient factorisé (Embedding v2).
"""

from typing import Optional, List, Any, Dict
import orjson as json
import os
import sys
import logging
import time
import httpx
from pydantic import BaseModel, Field

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_constants import (
    MODEL_EMBEDDING, COLLECTION_MEMORY,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-MEMORY-TOOL-V2")

class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        RECALL_TIMEOUT: int = Field(default=30, description="Délai d'attente maximum (secondes) pour l'embedding de recherche.")
        SCORE_THRESHOLD: float = Field(default=0.45, description="Seuil de confiance minimal (0.0 à 1.0).")
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne de Qdrant.")

    def __init__(self):
        self.valves = self.Valves()

    async def recall_memories(
        self,
        query: str,
        limit: int = 5,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """Recherche sémantique factorisée (v2.5)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        await events.status(f"🧠 Recherche sémantique v2...")
        try:
            # Vectorisation Factorisée (Gemini Embedding 2)
            vector = await EchoGeminiClient.generate_embedding(query, "query", __user__, __metadata__)
            if not vector: return wrap_tool_output(text="❌ Échec vectorisation.", status={"status": "error"})

            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                search_payload = {
                    "vector": vector, "limit": limit, "with_payload": True,
                    "filter": {"must": [{"key": "user_id", "match": {"value": __user__.get("id")}}]}
                }
                resp = await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/search", json=search_payload)
                if resp.status_code != 200: return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                results = resp.json().get("result", [])
                
            if not results: return wrap_tool_output(text="Aucun souvenir trouvé.", status={"status": "success", "results": []})

            md = "### 🧠 Souvenirs retrouvés\n\n"
            for r in results:
                if r["score"] < self.valves.SCORE_THRESHOLD: continue
                p = r["payload"]
                md += f"- **{p.get('slug', 'Note')}** (Score: {r['score']:.2f})\n  > {p.get('summary', '')}\n\n"

            await events.status(f"🧠 Recherche terminée.", done=True)
            return wrap_tool_output(text=md, status={"status": "success"})
        except Exception as e: return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})
