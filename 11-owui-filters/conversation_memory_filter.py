"""
title: ECHO Smart Memory Filter
author: Wilfried BARNAVON
version: 1.4
description: 1.4: Correction du pluralisme des clés API (get_api_keys).
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
import orjson as json

import os
import sys
import asyncio
import logging
import time
import httpx
import random
import hashlib


# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL, MODEL_LITE

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-MEMORY-FILTER")

class Filter:
    # Priorité élevée pour s'exécuter après les autres filtres (Outlet)
    priority: int = 100

    class Valves(BaseModel):
        ENABLE_MEMORY: bool = Field(default=True, description="Active la mémorisation automatique des conversations.")
        TRIGGER_PROBABILITY: float = Field(default=0.1, description="Probabilité (0.0 à 1.0) de déclenchement à chaque fin de message.")
        MIN_MESSAGES: int = Field(default=4, description="Nombre minimum de messages dans le chat avant d'envisager la mémorisation.")
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne du service Qdrant.")
        COLLECTION_NAME: str = Field(default="echo_memory", description="Nom de la collection vectorielle.")
        DEBUG_MODE: bool = Field(default=False)

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.collection_verified = False
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjEgMTJ2N2ExIDEgMCAwIDEtMSAxSDRhMSAxIDAgMCAxLTEtMVY1YTEgMSAwIDAgMSAxLTFoNSIvPjxwYXRoIGQ9Ik05IDEzaDVsLTUgNXYtNHoiLz48cGF0aCBkPSJNMTUgM2g2djZoLTZ6Ii8+PC9zdmc+"

    async def _ensure_collection(self):
        """Vérifie et crée la collection Qdrant si nécessaire."""
        if self.collection_verified:
            return
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.valves.QDRANT_URL}/collections/{self.valves.COLLECTION_NAME}")
                if resp.status_code == 200:
                    self.collection_verified = True
                    return
                
                # Création de la collection
                logger.info(f"[ECHO-MEMORY] Création de la collection {self.valves.COLLECTION_NAME}...")
                create_payload = {
                    "vectors": {
                        "size": 3072, # Dimension pour gemini-embedding-2-preview
                        "distance": "Cosine"
                    }
                }
                await client.put(f"{self.valves.QDRANT_URL}/collections/{self.valves.COLLECTION_NAME}", json=create_payload)
                self.collection_verified = True
        except Exception as e:
            logger.error(f"[ECHO-MEMORY] ❌ Erreur Qdrant Initialisation: {e}")

    async def _distill_and_store(self, chat_id: str, user_id: str, messages: List[Dict], api_key: str):
        """Tâche de fond : Distillation, Vectorisation et Stockage."""
        try:
            await self._ensure_collection()
            
            # 1. Distillation via Gemini Flash-Lite
            # On ne prend que le texte pour la distillation
            history_text = ""
            for m in messages:
                content = m.get('content', '')
                if isinstance(content, list):
                    # Cas multimodal : on extrait les parties texte
                    text_parts = [p.get('text', '') for p in content if isinstance(p, dict) and 'text' in p]
                    content = " ".join(text_parts)
                history_text += f"{m['role'].upper()}: {content}\n"
            
            distill_prompt = (
                "Tu es l'unité de mémoire d'ECHO. Analyse cet historique de conversation.\n"
                "Ta mission est d'extraire les connaissances techniques, les décisions ou les faits importants.\n"
                "Produis un JSON strict avec les champs suivants :\n"
                "- 'summary': Un résumé ultra-dense, factuel et technique (sans fioriture).\n"
                "- 'memory_type': Une catégorie courte et explicite (ex: ARCHITECTURE, FIX_BUG, CONFIG_SHELL).\n"
                "- 'tags': Une liste de 3 à 5 mots-clés techniques.\n\n"
                f"HISTORIQUE :\n{history_text}"
            )

            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
                "User-Agent": ECHO_USER_AGENT
            }
            
            payload_flash = {
                "contents": [{"role": "user", "parts": [{"text": distill_prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "response_mime_type": "application/json"
                }
            }
            
            # Endpoint standardisé via Google API Base URL
            flash_url = f"{GOOGLE_API_BASE_URL}/models/{MODEL_LITE}:generateContent?key={api_key}"

            async with httpx.AsyncClient(http2=True) as client:
                resp_flash = await client.post(flash_url, headers=headers, json=payload_flash, timeout=60)
                if resp_flash.status_code != 200:
                    logger.error(f"[ECHO-MEMORY] Erreur Distillation Flash: {resp_flash.text}")
                    return
                
                data = resp_flash.json()
                content_text = data["candidates"][0]["content"]["parts"][0]["text"]
                distilled_data = json.loads(content_text)
                
                summary = distilled_data.get("summary", "")
                if not summary:
                    return

                # 2. Vectorisation via Gemini Embedding-2
                embed_url = f"{GOOGLE_API_BASE_URL}/models/gemini-embedding-2-preview:embedContent?key={api_key}"
                payload_embed = {
                    "content": {"parts": [{"text": summary}]}
                }
                
                resp_embed = await client.post(embed_url, headers=headers, json=payload_embed, timeout=30)
                if resp_embed.status_code != 200:
                    logger.error(f"[ECHO-MEMORY] Erreur Embedding: {resp_embed.text}")
                    return
                
                vector = resp_embed.json()["embedding"]["values"]
                
                # 3. Stockage dans Qdrant
                # ID déterministe pour éviter les doublons de mémorisation sur une même version du chat
                point_id = hashlib.md5(f"{chat_id}_{summary[:100]}".encode()).hexdigest()
                
                point_payload = {
                    "points": [{
                        "id": point_id,
                        "vector": vector,
                        "payload": {
                            "user_id": user_id,
                            "chat_id": chat_id,
                            "timestamp": int(time.time()),
                            "memory_type": distilled_data.get("memory_type", "GENERIC"),
                            "tags": distilled_data.get("tags", []),
                            "summary": summary
                        }
                    }]
                }
                
                await client.put(f"{self.valves.QDRANT_URL}/collections/{self.valves.COLLECTION_NAME}/points", json=point_payload)
                logger.info(f"[ECHO-MEMORY] ✅ Souvenir sémantique mémorisé (User: {user_id}, Chat: {chat_id})")

        except Exception as e:
            logger.error(f"[ECHO-MEMORY] ❌ Erreur critique dans la tâche de mémorisation: {e}")

    async def outlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None) -> dict:
        """Phase Outlet : Déclenchement de la mémorisation après la réponse de l'IA."""
        if not self.valves.ENABLE_MEMORY or not __user__:
            return body

        messages = body.get("messages", [])
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
        
        # 1. Vérification du seuil minimal de messages
        if len(messages) < self.valves.MIN_MESSAGES:
            return body
            
        # 2. Tirage probabiliste pour éviter de saturer l'API/Base à chaque message
        if random.random() > self.valves.TRIGGER_PROBABILITY:
            return body

        # 3. Lancement asynchrone
        user_id = __user__.get("id")
        api_keys = self.auth.get_api_keys(user_id)
        api_key = api_keys[0] if api_keys else None
        
        if api_key:
            if self.valves.DEBUG_MODE:
                logger.info(f"[ECHO-MEMORY] 🧠 Analyse de mémorisation lancée pour {chat_id}")
            asyncio.create_task(self._distill_and_store(chat_id, user_id, messages, api_key))

        return body
