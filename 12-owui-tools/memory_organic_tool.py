"""
title: ECHO Organic Memory Retrieval
author: Wilfried BARNAVON
version: 1.0
description: Outil de rappel sémantique (RAG) pour la mémoire organique ECHO.
"""

from typing import Optional, List, Any, Dict
import json
import os
import sys
import httpx
import logging
import time

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-MEMORY-TOOL")

class Tools:
    def __init__(self):
        self.auth = EchoAuth()
        self.qdrant_url = "http://echo-qdrant:6333"
        self.collection_name = "echo_memory"

    async def recall_memories(
        self,
        query: str,
        limit: int = 5,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """
        Recherche sémantique dans la mémoire organique de l'utilisateur.
        Utilise cette fonction pour retrouver des informations sur vos échanges passés, 
        des décisions techniques ou des préférences utilisateur mémorisées.
        
        :param query: La question ou le sujet à rechercher dans la mémoire.
        :param limit: Nombre maximum de souvenirs à récupérer (défaut: 5).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        
        if not __user__ or not __user__.get("id"):
            return wrap_tool_output(text="❌ Erreur : Utilisateur non identifié.")

        user_id = __user__.get("id")
        
        try:
            await events.status(f"🧠 Recherche dans la mémoire organique : '{query}'...")
            
            # 1. Récupération du token
            token = await self.auth.refresh_google_token(user_id)
            if not token:
                return wrap_tool_output(text="❌ Erreur d'authentification Google.")

            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}

            # 2. Vectorisation de la requête (Embedding)
            embed_url = f"{GOOGLE_API_BASE_URL}:embedContent?model=models/gemini-embedding-2-preview"
            payload_embed = {
                "content": {"parts": [{"text": query}]}
            }
            
            async with httpx.AsyncClient() as client:
                resp_embed = await client.post(embed_url, headers=headers, json=payload_embed, timeout=30)
                if resp_embed.status_code != 200:
                    logger.error(f"[ECHO-MEMORY-TOOL] Erreur Embedding: {resp_embed.text}")
                    return wrap_tool_output(text="❌ Impossible de vectoriser la recherche.")
                
                query_vector = resp_embed.json()["embedding"]["values"]

                # 3. Recherche dans Qdrant avec filtre strict sur user_id
                search_payload = {
                    "vector": query_vector,
                    "limit": limit,
                    "with_payload": True,
                    "score_threshold": 0.45, # Seuil de confiance minimal
                    "filter": {
                        "must": [
                            {"key": "user_id", "match": {"value": user_id}}
                        ]
                    }
                }
                
                resp_qdrant = await client.post(
                    f"{self.qdrant_url}/collections/{self.collection_name}/points/search",
                    json=search_payload,
                    timeout=20
                )
                
                if resp_qdrant.status_code != 200:
                    logger.error(f"[ECHO-MEMORY-TOOL] Erreur Qdrant: {resp_qdrant.text}")
                    return wrap_tool_output(text="❌ Erreur lors de l'accès à la base de données vectorielle.")
                
                results = resp_qdrant.json().get("result", [])
                
                if not results:
                    return wrap_tool_output(
                        text="Je n'ai trouvé aucun souvenir pertinent dans ma mémoire organique pour cette recherche.",
                        status={"status": "empty"}
                    )

                # 4. Formatage de la réponse pour le modèle IA
                memory_md = "## 🧠 Souvenirs Récupérés\n\nVoici les informations pertinentes retrouvées dans ma mémoire organique :\n\n"
                
                for hit in results:
                    p = hit.get("payload", {})
                    score = hit.get("score", 0)
                    ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(p.get('timestamp', time.time())))
                    
                    memory_md += f"### 📌 [{p.get('memory_type', 'INFO')}] (Confiance: {score:.2f})\n"
                    memory_md += f"**Date :** {ts}\n"
                    if p.get('tags'):
                        memory_md += f"**Tags :** {', '.join(p.get('tags'))}\n"
                    memory_md += f"**Résumé :** {p.get('summary', 'Pas de résumé disponible.')}\n\n"
                    memory_md += "---\n\n"

                await events.status("🧠 Mémoire organique consultée avec succès.", done=True)
                return wrap_tool_output(
                    text=memory_md,
                    status={"status": "success", "count": len(results)}
                )

        except Exception as e:
            logger.error(f"[ECHO-MEMORY-TOOL] ❌ Erreur critique : {e}")
            return wrap_tool_output(text=f"❌ Une erreur système est survenue lors de la recherche mémoire : {str(e)}")
