"""
title: ECHO Proactive Memory Tool
author: Wilfried BARNAVON
version: 3.5
description: 3.5: Migration vers EchoGeminiClient factorisé (Embedding v2 & Distillation 2.5).
"""

from typing import Optional, List, Any, Dict, Union
import orjson as json
import os
import sys
import logging
import time
import uuid
import httpx
from pydantic import BaseModel, Field

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_constants import (
    MODEL_DISTILLATION, MODEL_EMBEDDING, 
    COLLECTION_MEMORY,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-PROACTIVE-MEMORY")

class Tools:
    class Valves(BaseModel):
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne de Qdrant.")
        SIMILARITY_THRESHOLD: float = Field(default=0.45, description="Seuil de confiance minimal pour la recherche.")
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        DEBUG_MODE: bool = Field(default=False, description="Affiche les détails techniques dans les logs.")

    def __init__(self):
        self.valves = self.Valves()

    async def list_memory_topics(
        self,
        scope: str = "global",
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> str:
        """Récupère la liste des sujets stockés (Fonction native préservée)."""
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"): return "❌ Erreur : Utilisateur non identifié."
        user_id = __user__.get("id")
        await events.status("🧠 Consultation de l'index de la mémoire...")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                filter_must = [{"key": "user_id", "match": {"value": user_id}}]
                scroll_payload = {"filter": {"must": filter_must}, "limit": 100, "with_payload": ["slug", "tags", "importance", "timestamp"]}
                resp = await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/scroll", json=scroll_payload)
                if resp.status_code != 200: return f"❌ Erreur Qdrant : {resp.text}"
                points = resp.json().get("result", {}).get("points", [])
                if not points: return "Votre mémoire organique est actuellement vide."
                md = "### 📚 Index de votre Mémoire Organique\n\n"
                for p in points:
                    pay = p.get("payload", {})
                    md += f"- **{pay.get('slug', 'Note')}** | {pay.get('importance', 1)} | `{', '.join(pay.get('tags', []))}`\n"
                return md
        except Exception as e: return f"❌ Erreur : {str(e)}"

    async def memorize_that(
        self,
        fact: str,
        importance: int = 1,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> str:
        """Enregistre un fait via Distillation et Embedding factorisés (v3.5)."""
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__: return "❌ Contexte manquant."
        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status(f"🧠 Distillation Factorisée...")
        try:
            distill_prompt = f"Extrais un 'slug' technique et 2-3 'tags' pour ce fait :\n{fact}"
            distilled = await EchoGeminiClient.call_distillation(distill_prompt, __user__, __metadata__)
            slug = distilled.get("slug", f"note_{uuid.uuid4().hex[:8]}")
            tags = distilled.get("tags", ["user_pref"])
            
            vector = await EchoGeminiClient.generate_embedding(fact, "document", __user__, __metadata__, title=slug)
            if not vector: return "❌ Échec vectorisation."

            point_id = str(uuid.uuid4())
            async with httpx.AsyncClient(timeout=30) as client:
                upsert_payload = {"points": [{
                    "id": point_id, "vector": vector,
                    "payload": {
                        "user_id": user_id, "chat_id": chat_id, "slug": slug, "summary": fact,
                        "importance": int(importance), "tags": tags, "timestamp": int(time.time())
                    }
                }]}
                await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/upsert", json=upsert_payload)
                return f"✅ Souvenir `{slug}` scellé."
        except Exception as e: return f"❌ Erreur : {str(e)}"
