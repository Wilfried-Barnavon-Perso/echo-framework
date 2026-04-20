"""
title: ECHO Proactive Memory Arsenal
author: Wilfried BARNAVON
version: 3.2
description: 3.2: Harmonisation de la résilience (KeySwitch 2, Retries 5) pour la distillation et l'ancrage.
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
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, EchoGeminiClient
from echo_constants import (
    ECHO_USER_AGENT, GOOGLE_API_BASE_URL,
    MODEL_DISTILLATION, MODEL_EMBEDDING, 
    EMBEDDING_DIM_V2, COLLECTION_MEMORY,
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
        self.auth = EchoAuth()

    async def list_memory_topics(
        self,
        scope: str = "global",
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> str:
        """
        Récupère la liste des sujets et concepts actuellement stockés dans votre mémoire organique.
        Utile pour faire un état des lieux de vos connaissances avant de décider d'en oublier ou d'en approfondir.
        
        :param scope: 'global' pour toute la mémoire, 'conversation' pour les souvenirs liés à ce chat.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return "❌ Erreur : Utilisateur non identifié."

        user_id = __user__.get("id")
        await events.status("🧠 Consultation de l'index de la mémoire...")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Scroll pour récupérer les payloads (slugs, tags, importance)
                filter_must = [{"key": "user_id", "match": {"value": user_id}}]
                # Note: Le filtrage par chat_id n'est pas implémenté ici pour rester simple, 
                # mais pourrait l'être si besoin.

                scroll_payload = {
                    "filter": {"must": filter_must},
                    "limit": 100,
                    "with_payload": ["slug", "tags", "importance", "timestamp"]
                }
                
                resp = await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/scroll", json=scroll_payload)
                if resp.status_code != 200:
                    error_msg = resp.text
                    return f"❌ Erreur Qdrant ({resp.status_code}) : {error_msg}"

                points = resp.json().get("result", {}).get("points", [])
                if not points:
                    return "Votre mémoire organique est actuellement vide ou aucun souvenir ne correspond à ce périmètre."

                # Agrégation par slug pour éviter les doublons visuels
                topics = {}
                for p in points:
                    payload = p.get("payload", {})
                    slug = payload.get("slug", "Note_sans_nom")
                    if slug not in topics:
                        topics[slug] = {
                            "importance": payload.get("importance", 1),
                            "tags": payload.get("tags", []),
                            "last_update": payload.get("timestamp", 0)
                        }

                # Formatage Markdown
                md = "### 📚 Index de votre Mémoire Organique\n\n"
                imp_labels = {1: "🟢", 2: "🔵", 3: "🟡", 4: "🟠", 5: "🔴"}
                
                for slug, info in sorted(topics.items()):
                    label = imp_labels.get(info['importance'], "⚪")
                    ts = time.strftime('%Y-%m-%d', time.localtime(info['last_update']))
                    tags_str = f" `{', '.join(info['tags'])}`" if info['tags'] else ""
                    md += f"- {label} **{slug}** | *MàJ: {ts}*{tags_str}\n"

                await events.status("🧠 Index récupéré.", done=True)
                return md

        except Exception as e:
            logger.error(f"[ECHO-MEMORY] Erreur list_topics: {e}")
            return f"❌ Erreur lors de la récupération de l'index : {str(e)}"

    async def memorize_that(
        self,
        fact: str,
        importance: int = 1,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> str:
        """
        Enregistre un fait, une décision ou une préférence précise dans votre mémoire organique.
        L'utilisateur recevra une demande de confirmation avant que la donnée ne soit scellée.
        
        :param fact: Le contenu textuel précis à mémoriser.
        :param importance: Niveau d'importance de 1 (Trivial) à 5 (Axiome/Critique).
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return "❌ Erreur : Utilisateur non identifié."

        user_id = __user__.get("id")
        api_keys = self.auth.get_api_keys(user_id)
        if not api_keys:
            return "❌ Clé API Google requise."

        await events.status(f"🧠 Distillation du souvenir : '{fact[:30]}...'")

        try:
            # 1. Distillation via Gemini Flash (pour Slug et Tags)
            distill_prompt = (
                "Analyse ce fait et produis un JSON court :\n"
                "- 'slug': nom technique court (ex: 'pref_python_lint').\n"
                "- 'tags': 2 ou 3 tags techniques spécifiques.\n"
                f"Contenu : {fact}"
            )
            distill_data = await EchoGeminiClient.call(
                keys=api_keys, 
                target_model=MODEL_DISTILLATION, 
                payload={
                    "contents": [{"role": "user", "parts": [{"text": distill_prompt}]}],
                    "generationConfig": {"response_mime_type": "application/json"}
                },
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=self.valves.MAX_RETRIES
            )
            distilled = json.loads(distill_data["candidates"][0]["content"]["parts"][0]["text"])
            slug = distilled.get("slug", f"note_{int(time.time())}")
            tags = distilled.get("tags", [])

            # 2. Vectorisation
            embed_data = await EchoGeminiClient.embed(
                keys=api_keys, 
                model=MODEL_EMBEDDING, 
                content={"parts": [{"text": f"title: {slug} | text: {fact}"}]},
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=self.valves.MAX_RETRIES
            )
            vector = embed_data["embedding"]["values"]

            # 3. Stockage (Collision sémantique gérée nativement par l'ID UUID5 du slug)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{slug}"))
            
            async with httpx.AsyncClient(timeout=30) as client:
                point_payload = {
                    "points": [{
                        "id": point_id, "vector": vector,
                        "payload": {
                            "user_id": user_id, "timestamp": int(time.time()),
                            "importance": importance, "slug": slug, "tags": tags, "summary": fact
                        }
                    }]
                }
                await client.put(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points", json=point_payload)

            await events.status(f"✅ Souvenir '{slug}' scellé avec succès.", done=True)
            return f"Le fait a été mémorisé avec succès sous l'identifiant '{slug}' (Niveau {importance})."

        except Exception as e:
            logger.error(f"[ECHO-MEMORY] Erreur memorize_that: {e}")
            return f"❌ Erreur lors de la mémorisation : {str(e)}"

    async def prepare_forget_memory(
        self,
        topic_query: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> str:
        """
        Recherche et prépare la suppression de souvenirs liés à un sujet. 
        Cette fonction ne supprime rien, elle identifie les cibles pour validation par l'utilisateur.
        
        :param topic_query: Le sujet ou les mots-clés des souvenirs à identifier.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return "❌ Erreur : Utilisateur non identifié."

        user_id = __user__.get("id")
        api_keys = self.auth.get_api_keys(user_id)
        if not api_keys:
            return "❌ Clé API Google requise."

        await events.status(f"🧠 Recherche des souvenirs liés à '{topic_query}'...")

        try:
            # 1. Vectorisation de la requête
            embed_data = await EchoGeminiClient.embed(
                keys=api_keys, 
                model=MODEL_EMBEDDING, 
                content={"parts": [{"text": f"task: search result | query: {topic_query}"}]},
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=self.valves.MAX_RETRIES
            )
            query_vector = embed_data["embedding"]["values"]

            # 2. Recherche Qdrant
            async with httpx.AsyncClient(timeout=30) as client:
                search_payload = {
                    "vector": query_vector, "limit": 5, "with_payload": True,
                    "score_threshold": self.valves.SIMILARITY_THRESHOLD,
                    "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}]}
                }
                resp = await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/search", json=search_payload)
                results = resp.json().get("result", [])

            if not results:
                return f"Aucun souvenir pertinent n'a été trouvé concernant '{topic_query}'."

            # 3. Formatage pour le LLM et l'Utilisateur
            targets = []
            md_list = "### ⚠️ Souvenirs identifiés pour suppression :\n\n"
            for hit in results:
                p = hit.get("payload", {})
                slug = p.get("slug", "Inconnu")
                targets.append(slug)
                md_list += f"- **{slug}** (Confiance: {hit.get('score', 0):.2f})\n  > _{p.get('summary', '')[:100]}..._\n"

            instruction = (
                f"\n**INSTRUCTION POUR L'IA :** Affiche la liste ci-dessus à l'utilisateur. "
                f"Demande-lui explicitement s'il confirme vouloir OUBLIER ces éléments. "
                f"S'il accepte, tu devras appeler l'outil `execute_forget_memory` avec la liste suivante : {json.dumps(targets).decode('utf-8')}."
            )
            
            await events.status("🧠 Analyse terminée.", done=True)
            return md_list + instruction

        except Exception as e:
            logger.error(f"[ECHO-MEMORY] Erreur prepare_forget: {e}")
            return f"❌ Erreur lors de l'identification : {str(e)}"

    async def execute_forget_memory(
        self,
        slugs: List[str],
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> str:
        """
        Supprime définitivement une liste de souvenirs de la mémoire organique.
        L'utilisateur recevra une fenêtre de confirmation native avant l'exécution.
        
        :param slugs: Liste des identifiants (slugs) exacts à supprimer.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return "❌ Erreur : Utilisateur non identifié."

        user_id = __user__.get("id")
        await events.status(f"🧹 Suppression de {len(slugs)} souvenirs...")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                delete_payload = {
                    "filter": {
                        "must": [
                            {"key": "user_id", "match": {"value": user_id}},
                            {"key": "slug", "match": {"any": slugs}}
                        ]
                    }
                }
                resp = await client.post(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete", json=delete_payload)
                
                if resp.status_code == 200:
                    await events.status("🧠 Oubli effectué.", done=True)
                    return f"Succès : Les souvenirs suivants ont été purgés : {', '.join(slugs)}."
                else:
                    return f"❌ Échec de la suppression Qdrant : {resp.text}"

        except Exception as e:
            logger.error(f"[ECHO-MEMORY] Erreur execute_forget: {e}")
            return f"❌ Erreur lors de la suppression : {str(e)}"
