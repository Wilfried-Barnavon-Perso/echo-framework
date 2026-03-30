"""
title: ECHO Memory Search
author: ECHO Framework
version: 1.9
description: 1.9: Migrated to Google AI Studio API Key authentication and standard Gemini Embedding.
"""

import sys
import httpx
import orjson as json
from typing import Optional, Any

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL

QDRANT_URL = "http://echo-qdrant:6333"
COLLECTION = "echo_knowledge"

class Tools:
    def __init__(self):
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
        
        # Récupération de la clé API
        api_key = self.auth.get_api_key(user_id)
        if not api_key: 
            return wrap_tool_output(text="❌ Configuration ECHO Requise : Aucune clé API Google AI Studio trouvée.", status={"status": "error"})

        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
        
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Vectorisation (Embedding AI Studio)
            url_embed = f"{GOOGLE_API_BASE_URL}/models/text-embedding-004:embedContent?key={api_key}"
            try:
                payload_embed = {
                    "model": "models/text-embedding-004",
                    "content": {"parts": [{"text": query}]}
                }
                resp_embed = await client.post(url_embed, headers=headers, content=json.dumps(payload_embed))
                if resp_embed.status_code != 200:
                    return wrap_tool_output(text="❌ Impossible de vectoriser la recherche (AI Studio).")
                
                vector = resp_embed.json()["embedding"]["values"]
            except Exception as e: 
                return wrap_tool_output(text=f"❌ Erreur Vectorisation : {str(e)}", status={"status": "error"})

            # 2. Recherche Qdrant
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
