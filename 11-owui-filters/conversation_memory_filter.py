"""
title: ECHO Organic Memory Filter V2
author: Wilfried BARNAVON
version: 2.3
description: 2.3: Affinement des tags (discrimination renforcée) pour une purge granulaire fiable.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict, Union
import orjson as json
import os
import sys
import asyncio
import logging
import time
import httpx
import random
import hashlib
import uuid

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoGeminiClient, EchoEvents
from echo_constants import (
    ECHO_USER_AGENT, GOOGLE_API_BASE_URL, 
    MODEL_DISTILLATION, MODEL_EMBEDDING, 
    EMBEDDING_DIM_V2, COLLECTION_MEMORY
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-MEMORY-V2")

class Filter:
    # Priorité élevée pour s'exécuter en fin de chaîne (Outlet)
    priority: int = 100

    class Valves(BaseModel):
        TRIGGER_PROBABILITY: float = Field(default=0.1, description="Probabilité (0.0 à 1.0) de déclenchement à chaque fin de message.")
        RECOVERY_FACTOR: float = Field(default=1.3, description="Facteur de recouvrement de la fenêtre de messages (1.3 = 30% d'overlap).")
        FORCE_TRIGGER_THRESHOLD: int = Field(default=20, description="Force la mémorisation si aucun déclenchement après N messages.")
        
        SIMILARITY_THRESHOLD: float = Field(default=0.85, description="Seuil de similarité pour déclencher la fusion LLM (0.0 à 1.0).")
        EXACT_MATCH_THRESHOLD: float = Field(default=0.95, description="Seuil pour simple mise à jour de date (sans coût LLM).")
        
        TTL_LVL_1: int = Field(default=30, description="Rétention (jours) Niveau 1 (Trivial/Éphémère).")
        TTL_LVL_2: int = Field(default=60, description="Rétention (jours) Niveau 2.")
        TTL_LVL_3: int = Field(default=180, description="Rétention (jours) Niveau 3.")
        TTL_LVL_4: int = Field(default=365, description="Rétention (jours) Niveau 4.")
        TTL_LVL_5: int = Field(default=540, description="Rétention (jours) Niveau 5 (Axiome/Critique).")
        
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne de Qdrant.")
        DEBUG_MEMORY: bool = Field(default=False, description="Affiche les détails de fusion et de pruning dans les logs.")

    class UserValves(BaseModel):
        ENABLE_MEMORY: bool = Field(default=True, description="🧠 Autoriser ECHO à mémoriser cette conversation pour enrichir ma mémoire organique.")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self.auth = EchoAuth()
        self.collection_verified = False
        self.last_triggered_count = {} 
        
        # --- CONFIGURATION UI OPEN WEBUI ---
        self.toggle = True  # Affiche le switch dans le menu Intégrations (icône engrenage)
        # Icône SVG : Cerveau avec circuit (Cognition)
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNOSAxMmExIDEsMCwxLDAsMiwxLDEsMCwxLDAtMi0weiIvPjxwYXRoIGQ9Ik0xNSAxMmExIDEsMCwxLDAsMiwxLDEsMCwxLDAtMi0weiIvPjxwYXRoIGQ9Ik04IDE3YTUgNSAwIDAgMSAxMCAwIi8+PHBhdGggZD0iTTEyIDN2Mm0wIDE0djJtLTktOWgtMm0xNCAwaC0yIi8+PC9zdmc+"

    async def _ensure_collection(self):
        if self.collection_verified: return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}")
                if resp.status_code == 200:
                    self.collection_verified = True; return
                
                logger.info(f"[ECHO-MEMORY-V2] 🏗️ Création de la collection {COLLECTION_MEMORY} ({EMBEDDING_DIM_V2}d)...")
                create_payload = {"vectors": {"size": EMBEDDING_DIM_V2, "distance": "Cosine"}}
                await client.put(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}", json=create_payload)
                self.collection_verified = True
        except Exception as e:
            logger.error(f"[ECHO-MEMORY-V2] ❌ Erreur Qdrant: {e}")

    async def _get_distilled_json(self, messages: List[Dict], api_keys: List[str]) -> Optional[Dict]:
        """Distillation multimodale via Gemini 2.5 Flash."""
        distill_prompt = (
            "Tu es l'unité de distillation de mémoire d'ECHO. Analyse cet extrait de conversation.\n"
            "Ta mission est d'extraire les connaissances, décisions techniques ou préférences utilisateur.\n"
            "Produis un JSON STRICT avec :\n"
            "- 'summary': Résumé ultra-dense et technique (sans fioriture).\n"
            "- 'importance': Score de 1 (Trivial) à 5 (Critique/Fondateur).\n"
            "- 'slug': Identifiant sémantique court et unique (ex: 'pref_python_format', 'archi_db_cluster').\n"
            "- 'tags': 3 à 5 tags techniques TRÈS SPÉCIFIQUES.\n"
            "IMPORTANT : Interdiction d'utiliser des tags génériques (ex: 'IA', 'technique', 'informatique', 'user', 'preference').\n"
            "Chaque tag doit être discriminant et lié au sujet réel (ex: 'astrophysique', 'react_hooks', 'docker_security')."
        )

        parts = [{"text": distill_prompt}]
        for m in messages:
            role = m.get('role', 'user').upper()
            content = m.get('content', '')
            if isinstance(content, list):
                for p in content:
                    if isinstance(p, dict):
                        if 'text' in p: parts.append({"text": f"{role}: {p['text']}"})
                        elif 'inline_data' in p: parts.append({"inline_data": p['inline_data']})
            else:
                parts.append({"text": f"{role}: {content}"})

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"}
        }

        try:
            data = await EchoGeminiClient.call(keys=api_keys, target_model=MODEL_DISTILLATION, payload=payload)
            content_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(content_text)
        except Exception as e:
            logger.error(f"[ECHO-MEMORY-V2] ❌ Erreur Distillation: {e}")
            return None

    async def _distill_and_store(self, chat_id: str, user_id: str, messages: List[Dict], api_keys: List[str]):
        """Pipeline Asynchrone V2."""
        try:
            await self._ensure_collection()
            
            # 1. Distillation
            distilled = await self._get_distilled_json(messages, api_keys)
            if not distilled or not distilled.get("summary"): return
            
            summary = distilled["summary"]
            importance = int(distilled.get("importance", 1))
            slug = distilled.get("slug", "generic_note")
            
            # 2. Vectorisation V2
            embed_data = await EchoGeminiClient.embed(
                keys=api_keys, 
                model=MODEL_EMBEDDING, 
                content={"parts": [{"text": f"title: {slug} | text: {summary}"}]}
            )
            vector = embed_data["embedding"]["values"]

            # 3. Collision & Fusion
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{slug}"))
            
            async with httpx.AsyncClient(timeout=30) as client:
                search_payload = {
                    "vector": vector, "limit": 1, "with_payload": True,
                    "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}, {"key": "slug", "match": {"value": slug}}]}
                }
                resp_search = await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/search", json=search_payload)
                results = resp_search.json().get("result", [])
                
                final_summary = summary
                if results:
                    hit = results[0]
                    score = hit.get("score", 0)
                    old_payload = hit.get("payload", {})
                    
                    if score > self.valves.EXACT_MATCH_THRESHOLD:
                        if self.valves.DEBUG_MEMORY: logger.info(f"[ECHO-MEMORY-V2] 🔄 Doublon détecté pour '{slug}'. Rafraîchissement date.")
                    elif score > self.valves.SIMILARITY_THRESHOLD:
                        if self.valves.DEBUG_MEMORY: logger.info(f"[ECHO-MEMORY-V2] 🧩 Fusion sémantique pour '{slug}'...")
                        fusion_prompt = f"Fusionne ces deux résumés techniques en un seul paragraphe cohérent et à jour :\n1. {old_payload.get('summary')}\n2. {summary}"
                        fusion_data = await EchoGeminiClient.call(keys=api_keys, target_model=MODEL_DISTILLATION, payload={"contents": [{"role":"user", "parts":[{"text": fusion_prompt}]}]})
                        final_summary = fusion_data["candidates"][0]["content"]["parts"][0]["text"]
                
                # 4. Insertion/Update
                point_payload = {
                    "points": [{
                        "id": point_id, "vector": vector,
                        "payload": {
                            "user_id": user_id, "chat_id": chat_id, "timestamp": int(time.time()),
                            "importance": importance, "slug": slug, "tags": distilled.get("tags", []), "summary": final_summary
                        }
                    }]
                }
                await client.put(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points", json=point_payload)
                
            logger.info(f"[ECHO-MEMORY-V2] ✅ Souvenir '{slug}' (Lvl {importance}) mémorisé.")

        except Exception as e:
            logger.error(f"[ECHO-MEMORY-V2] ❌ Erreur Pipeline: {e}")

    async def outlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_emitter__: Optional[Any] = None) -> dict:
        """Phase Outlet : Déclenchement de la mémorisation après la réponse de l'IA."""
        # On vérifie la UserValve de souveraineté
        if not self.user_valves.ENABLE_MEMORY or not __user__:
            return body

        messages = body.get("messages", [])
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
        user_id = __user__.get("id")
        
        # Hard Limit logic
        count = self.last_triggered_count.get(chat_id, 0) + 1
        self.last_triggered_count[chat_id] = count

        triggered = (random.random() < self.valves.TRIGGER_PROBABILITY) or (count >= self.valves.FORCE_TRIGGER_THRESHOLD)
        
        if triggered and len(messages) >= 4:
            self.last_triggered_count[chat_id] = 0
            api_keys = self.auth.get_api_keys(user_id)
            if api_keys:
                # Fenêtre de recouvrement sémantique
                window_size = int(self.valves.RECOVERY_FACTOR / self.valves.TRIGGER_PROBABILITY)
                window_msgs = messages[-window_size:]
                if self.valves.DEBUG_MEMORY:
                    logger.info(f"[ECHO-MEMORY-V2] 🧠 Déclenchement (Fenêtre: {len(window_msgs)} msgs)")
                
                # Feedback visuel (Optionnel, masqué si hidden=True)
                if __event_emitter__:
                    await __event_emitter__({
                        "type": "status",
                        "data": {"description": "🧠 Consolidation de la mémoire organique...", "done": False, "hidden": not self.valves.DEBUG_MEMORY}
                    })
                
                asyncio.create_task(self._distill_and_store(chat_id, user_id, window_msgs, api_keys))

        return body
