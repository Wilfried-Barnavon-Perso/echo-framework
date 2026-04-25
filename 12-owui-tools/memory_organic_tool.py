"""
title: ECHO Organic Memory Tool
author: Wilfried BARNAVON
version: 2.3
description: 2.3: Migration vers le Unified Auth Mesh.
"""

from typing import Optional, List, Any, Dict
import orjson as json
import os
import sys
import logging
import time
from pydantic import BaseModel, Field

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_constants import (
    ECHO_UPLOADS_DIR, ECHO_USER_DBS_DIR, ECHO_VERSION_PATH,
    GOOGLE_API_BASE_URL, ECHO_USER_AGENT, ECHO_USERS_ROOT,
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

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.qdrant_url = "http://echo-qdrant:6333"

    async def recall_memories(
        self,
        query: str,
        limit: int = 5,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """
        Recherche sémantique dans votre mémoire organique ECHO.
        Utilisez cette fonction pour retrouver des faits, des décisions techniques, 
        des préférences ou des contextes mémorisés lors de vos échanges passés.
        Indispensable pour l'application du Principe PRAF (vérification interne) 
        et du Principe PRAC (analyse rétrospective des patterns).
        
        :param query: La question ou le sujet à rechercher (ex: 'Quelles étaient mes préférences sur le tri ?').
        :param limit: Nombre maximum de souvenirs à récupérer (défaut: 5).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        
        if not __user__ or not __user__.get("id"):
            return wrap_tool_output(text="❌ Erreur : Utilisateur non identifié.")

        user_id = __user__.get("id")
        
        try:
            await events.status(f"🧠 Consultation de la mémoire organique : '{query}'...")

            # 1. Récupération du mesh d'authentification
            auth_mesh = await self.auth.get_ordered_auth_mesh(user_id)
            if not auth_mesh:
                return wrap_tool_output(text="❌ Configuration ECHO Requise : Aucune authentification Google ou Clé API trouvée.")

            # 2. Vectorisation V2 (Asymétrique Query)
            # Formatage requis par gemini-embedding-2-preview
            query_text = f"task: search result | query: {query}"

            embed_data = await EchoGeminiClient.embed(
                auth_mesh=auth_mesh,
                model=MODEL_EMBEDDING,
                content={"parts": [{"text": query_text}]},
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=self.valves.MAX_RETRIES,
                events=events,
                timeout=self.valves.RECALL_TIMEOUT
            )
            query_vector = embed_data["embedding"]["values"]

            # 3. Recherche Qdrant avec filtrage strict
            import httpx
            async with httpx.AsyncClient() as client:
                search_payload = {
                    "vector": query_vector,
                    "limit": limit,
                    "with_payload": True,
                    "score_threshold": self.valves.SCORE_THRESHOLD,
                    "filter": {
                        "must": [
                            {"key": "user_id", "match": {"value": user_id}}
                        ]
                    }
                }
                
                resp_qdrant = await client.post(
                    f"{self.qdrant_url}/collections/{COLLECTION_MEMORY}/points/search",
                    json=search_payload,
                    timeout=20
                )
                
                if resp_qdrant.status_code != 200:
                    logger.error(f"[ECHO-MEMORY-TOOL-V2] Erreur Qdrant: {resp_qdrant.text}")
                    return wrap_tool_output(text="❌ Erreur lors de l'accès à la base de données vectorielle.")
                
                results = resp_qdrant.json().get("result", [])
                
                if not results:
                    return wrap_tool_output(
                        text="Je n'ai trouvé aucun souvenir pertinent dans ma mémoire organique pour cette recherche.",
                        status={"status": "empty"}
                    )

                # 4. Formatage Markdown enrichi
                memory_md = "## 🧠 Souvenirs Organiques Retrouvés\n\n"
                
                for hit in results:
                    p = hit.get("payload", {})
                    score = hit.get("score", 0)
                    importance = p.get('importance', 1)
                    # Étoiles pour l'importance
                    stars = "⭐" * importance
                    
                    ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(p.get('timestamp', time.time())))
                    
                    memory_md += f"### 📌 {p.get('slug', 'Note')} {stars} (Confiance: {score:.2f})\n"
                    memory_md += f"**Date :** {ts}\n"
                    if p.get('tags'):
                        memory_md += f"**Tags :** `{', '.join(p.get('tags'))}`\n"
                    memory_md += f"\n> {p.get('summary', 'Pas de contenu.')}\n\n"
                    memory_md += "---\n\n"

                await events.status("🧠 Mémoire consultée avec succès.", done=True)
                return wrap_tool_output(
                    text=memory_md,
                    status={"status": "success", "count": len(results)}
                )

        except Exception as e:
            logger.error(f"[ECHO-MEMORY-TOOL-V2] ❌ Erreur : {e}")
            return wrap_tool_output(text=f"❌ Erreur système lors de la recherche mémoire : {str(e)}")
