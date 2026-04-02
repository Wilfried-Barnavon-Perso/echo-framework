"""
title: ECHO Memory Search
author: ECHO Framework
version: 2.0
description: 2.0: Integrated Centralized EchoGeminiClient for multi-key resilience.
"""

import sys
import orjson as json
from pydantic import BaseModel, Field
from typing import Optional, Any

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL

QDRANT_URL = "http://echo-qdrant:6333"
COLLECTION = "echo_knowledge"

class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=3, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        KNOWLEDGE_TIMEOUT: int = Field(default=30, description="Délai d'attente maximum (secondes) pour l'embedding de recherche.")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def search_knowledge_base(
        self, 
        query: str, 
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Recherche sémantique dans la base de connaissance partagée ECHO.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id"): 
            return wrap_tool_output(text="❌ Erreur : Utilisateur non identifié.", status={"status": "error"})

        user_id = __user__.get("id")
        await events.status(f"📚 Recherche dans la base de connaissance : '{query}'...")
        
        # Récupération des clés API (v5.94+)
        api_keys = self.auth.get_api_keys(user_id)
        if not api_keys: 
            return wrap_tool_output(text="❌ Configuration ECHO Requise : Aucune clé API Google AI Studio trouvée.", status={"status": "error"})

        try:
            # 1. Vectorisation (Embedding AI Studio via EchoGeminiClient)
            embed_data = await EchoGeminiClient.embed(
                keys=api_keys,
                model="text-embedding-004",
                content={"parts": [{"text": query}]},
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                events=events,
                timeout=self.valves.KNOWLEDGE_TIMEOUT
            )
            vector = embed_data["embedding"]["values"]
        except Exception as e: 
            return wrap_tool_output(text=f"❌ Erreur Vectorisation : {str(e)}", status={"status": "error"})

        # 2. Recherche Qdrant
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                payload_qdrant = {
                    "vector": vector, 
                    "limit": 5, 
                    "with_payload": True, 
                    "score_threshold": 0.4
                }
                resp_qdrant = await client.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", content=json.dumps(payload_qdrant))
                if resp_qdrant.status_code != 200:
                    return wrap_tool_output(text="❌ Erreur d'accès à la base vectorielle.")
                    
                results = resp_qdrant.json().get("result", [])
            except Exception as e: 
                return wrap_tool_output(text=f"❌ Erreur Qdrant : {str(e)}", status={"status": "error"})

        if not results: 
            return wrap_tool_output(text="Aucun résultat pertinent trouvé dans la base de connaissance.", status={"status": "empty"})
        
        md = "📚 **Résultats de la Base de Connaissance :**\n\n"
        for hit in results:
            p = hit.get('payload', {})
            md += f"- **{p.get('source','Document inconnu')}** (Confiance: {hit['score']:.2f})\n"
            if p.get('text'):
                snippet = p['text'][:200] + "..." if len(p['text']) > 200 else p['text']
                md += f"  > {snippet}\n"
        
        await events.status("📚 Recherche terminée.", done=True)
        return wrap_tool_output(text=md, status={"status": "success", "count": len(results)})
