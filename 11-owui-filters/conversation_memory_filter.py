"""
title: ECHO Memory Filter V2 (Base Vectorielle des Souvenirs)
author: Wilfried BARNAVON
version: 4.0
description: 4.0: Fenêtre glissante déterministe (WINDOW_SIZE + WINDOW_OVERLAP), nettoyage des messages (role+content+fichiers), prompt 100-1000 mots, suppression du déclenchement probabiliste.
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
import hashlib
import uuid

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoGeminiClient, EchoEvents, EchoAuth
from echo_constants import (
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
        WINDOW_SIZE: int = Field(default=5, ge=2, le=10,
            description="Nombre de nouveaux messages avant déclenchement de la distillation.")
        WINDOW_OVERLAP: int = Field(default=2, ge=0, le=5,
            description="Messages de la fenêtre précédente réinjectés pour continuité contextuelle.")
        
        SIMILARITY_THRESHOLD: float = Field(default=0.85, description="Seuil de similarité pour déclencher la fusion LLM (0.0 à 1.0).")
        EXACT_MATCH_THRESHOLD: float = Field(default=0.95, description="Seuil pour simple mise à jour de date (sans coût LLM).")
        
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne de Qdrant.")
        DEBUG_MEMORY: bool = Field(default=False, description="Affiche les détails de fusion et de pruning dans les logs.")

    class UserValves(BaseModel):
        ENABLE_MEMORY: bool = Field(default=True, description="🧠 Autoriser ECHO à mémoriser cette conversation dans la base vectorielle des souvenirs.")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self.auth = EchoAuth()
        self.collection_verified = False
        self.message_count = {}  # {chat_id: nb de messages vus depuis dernière distillation}
        
        # --- CONFIGURATION UI OPEN WEBUI ---
        self.toggle = True  # Affiche le switch dans le menu Intégrations (icône engrenage)
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNOSAxMmExIDEsMCwxLDAsMiwxLDEsMCwxLDAtMi0weiIvPjxwYXRoIGQ9Ik0xNSAxMmExIDEsMCwxLDAsMiwxLDEsMCwxLDAtMi0weiIvPjxwYXRoIGQ9Ik04IDE3YTUgNSAwIDAgMSAxMCAwIi8+PHBhdGggZD0iTTEyIDN2Mm0wIDE0djJtLTktOWgtMm0xNCAwaC0yIi8+PC9zdmc+"

    def _clean_messages(self, messages: List[Dict]) -> str:
        """
        Nettoyage des messages pour la distillation : role + content + fichiers joints.
        Supprime les UUIDs, metadata, timestamps et tout bruit technique.
        Gain : ~8x moins de tokens vs str(messages).
        """
        cleaned = []
        for m in messages:
            header = m.get("role", "?")
            files = m.get("files")
            if files:
                noms = ", ".join(f.get("name", "?") for f in files if isinstance(f, dict))
                if noms:
                    header += f" [📎 {noms}]"
            cleaned.append(f"{header}:\n{m.get('content', '')}")
        return "\n\n".join(cleaned)

    async def _ensure_collection(self):
        if self.collection_verified: return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}")
                if resp.status_code == 200:
                    self.collection_verified = True; return

                logger.info(f"[ECHO-MEMORY-V2] 🏗️ Création de la collection {COLLECTION_MEMORY} ({EMBEDDING_DIM_V2}d)...")
                create_payload = {"vectors": {"size": EMBEDDING_DIM_V2, "distance": "Cosine"}}
                cr = await client.put(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}", json=create_payload)
                if cr.status_code not in (200, 201):
                    logger.error(f"[ECHO-MEMORY-V2] ❌ Échec création collection ({cr.status_code}): {cr.text}")
                    return  # Ne pas valider si la création a échoué
                self.collection_verified = True
                logger.info(f"[ECHO-MEMORY-V2] ✅ Collection {COLLECTION_MEMORY} créée.")
        except Exception as e:
            logger.error(f"[ECHO-MEMORY-V2] ❌ Erreur Qdrant: {e}")

    async def _distill_and_store(self, chat_id: str, user_id: str, messages: List[Dict]):
        """Pipeline Asynchrone V4 — Fenêtre Glissante Déterministe."""
        try:
            await self._ensure_collection()
            
            # --- 1. Distillation Contextuelle (messages nettoyés) ---
            distill_prompt = (
                "Tu es l'unité de distillation contextuelle de mémoire d'ECHO. Analyse cet extrait de conversation.\n"
                "Ta mission est d'extraire les connaissances, décisions techniques ou préférences utilisateur.\n"
                "Produis un JSON STRICT avec :\n"
                "- 'summary': Résumé ultra-dense en 100 à 1000 mots MAXIMUM.\n"
                "             IMPORTANT : Ce résumé sera stocké comme un seul vecteur 1024d.\n"
                "             La densité et la précision priment sur l'exhaustivité.\n"
                "             Concentre-toi sur les faits techniques, décisions et préférences actionnables.\n"
                "- 'memory_importance': Score de 1 (Trivial) à 5 (Critique/Fondateur).\n"
                "- 'slug': Identifiant sémantique court et unique (ex: 'pref_python_format', 'archi_db_cluster').\n"
                "- 'tags': 3 à 5 tags techniques TRÈS SPÉCIFIQUES."
            )
            u_ctx = {"id": user_id}
            m_ctx = {"chat_id": chat_id}
            
            # Nettoyage : role + content + fichiers joints uniquement
            cleaned = self._clean_messages(messages)
            
            distilled = await EchoGeminiClient.call_distillation(
                distill_prompt + "\n\nCONVERSATION :\n" + cleaned,
                u_ctx, m_ctx
            )
            if not distilled or not distilled.get("summary"): return
            
            summary = distilled["summary"]
            new_memory_importance = int(distilled.get("memory_importance", distilled.get("importance", 1))) # fallback compatibilité
            new_slug = distilled.get("slug", "generic_note")
            tags = distilled.get("tags", [])
            
            # --- 2. Vectorisation Locale (BAAI/bge-m3 via echo-embedding worker) ---
            vector = await EchoGeminiClient.generate_embedding(summary, "document", u_ctx, m_ctx, title=new_slug)
            if not vector: return

            # 3. Collision Sémantique (Recherche vectorielle pure)
            async with httpx.AsyncClient(timeout=30) as client:
                search_payload = {
                    "vector": vector, "limit": 1, "with_payload": True,
                    "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}]}
                }
                resp_search = await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/search", json=search_payload)
                results = resp_search.json().get("result", [])
                
                final_summary = summary; final_slug = new_slug; final_memory_importance = new_memory_importance
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{new_slug}"))
                
                if results:
                    hit = results[0]
                    score = hit.get("score", 0)
                    old_payload = hit.get("payload", {})
                    
                    if score > self.valves.SIMILARITY_THRESHOLD:
                        point_id = hit.get("id")
                        final_slug = old_payload.get("slug", new_slug)
                        old_memory_importance = int(old_payload.get("memory_importance", old_payload.get("importance", 1))) # fallback compatibilité
                        # Gestion de l'importance des souvenirs : on conserve le score maximal (fusion préservative)
                        final_memory_importance = max(old_memory_importance, new_memory_importance)
                        
                        if score > self.valves.EXACT_MATCH_THRESHOLD:
                            final_summary = old_payload.get("summary", summary)
                        else:
                            fusion_prompt = f"FUSION DE MÉMOIRE\nAncien : {old_payload.get('summary')}\nNouveau : {summary}\nProduis un résumé unique fusionnant les deux sans perte d'information critique."
                            final_summary = await EchoGeminiClient.call_distillation(fusion_prompt, u_ctx, m_ctx, is_json=False)
                
                # 4. Enregistrement Vectoriel dans la Base des Souvenirs
                point_payload = {
                    "points": [{
                        "id": point_id, "vector": vector,
                        "payload": {
                            "user_id": user_id, "chat_id": chat_id, "timestamp": int(time.time()),
                            "memory_importance": final_memory_importance, "slug": final_slug, "tags": tags, "summary": final_summary
                        }
                    }]
                }
                await client.put(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points", json=point_payload)
                
            logger.info(f"[ECHO-MEMORY-V2] ✅ Enregistrement vectoriel '{final_slug}' (Lvl {final_memory_importance}) effectué.")

        except Exception as e:
            logger.error(f"[ECHO-MEMORY-V2] ❌ Erreur pipeline: {e}")

    async def outlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_emitter__: Optional[Any] = None) -> dict:
        """Phase Outlet : Déclenchement déterministe par fenêtre glissante."""
        if not self.user_valves.ENABLE_MEMORY or not __user__: return body

        messages = body.get("messages", [])
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
        user_id = __user__.get("id")
        if not chat_id or not user_id: return body
        
        # Compteur de messages par chat
        count = self.message_count.get(chat_id, 0) + 1
        self.message_count[chat_id] = count

        window_size = self.valves.WINDOW_SIZE
        overlap = min(self.valves.WINDOW_OVERLAP, window_size - 1)  # Clamp sécurité
        
        # Déclenchement déterministe : tous les WINDOW_SIZE nouveaux messages
        if count >= window_size and len(messages) >= window_size:
            self.message_count[chat_id] = 0
            total = window_size + overlap
            window_msgs = messages[-total:]  # Prend window_size + overlap si disponible
            
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": "🧠 Distillation contextuelle et enregistrement dans la base vectorielle...", "done": False, "hidden": not self.valves.DEBUG_MEMORY}
                })
            
            asyncio.create_task(self._distill_and_store(chat_id, user_id, window_msgs))

        return body
