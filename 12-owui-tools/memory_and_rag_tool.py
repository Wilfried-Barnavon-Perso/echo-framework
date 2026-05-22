"""
title: ECHO Memory & RAG Tool
author: Wilfried BARNAVON
version: 1.5
description: 1.2: Ajout forget_memory. 1.3: RAG éphémère. 1.4: Mise à jour version. 1.5: Reranking par importance (MEMORY_IMPORTANCE_WEIGHTS) dans recall_memories.
"""

from typing import Optional, List, Any, Dict
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
    COLLECTION_MEMORY, EMBEDDING_DIM_V2,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    MEMORY_IMPORTANCE_WEIGHTS, MEMORY_IMPORTANCE_LABELS
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-MEMORY-TOOL")


class Tools:
    class Valves(BaseModel):
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne de Qdrant.")
        SCORE_THRESHOLD: float = Field(default=0.45, description="Seuil de confiance minimal pour la recherche sémantique (0.0 à 1.0).")
        RECALL_TIMEOUT: int = Field(default=30, description="Délai d'attente maximum (secondes) pour les requêtes Qdrant.")
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        DEBUG_MODE: bool = Field(default=False, description="Affiche les détails techniques dans les logs.")

    def __init__(self):
        self.valves = self.Valves()
        self._collection_verified = False

    async def _ensure_collection(self, client: httpx.AsyncClient):
        """
        Vérifie l'existence de la collection Qdrant et la crée si nécessaire.
        Ne valide le flag que si la création réussit réellement.
        """
        if self._collection_verified:
            return
        try:
            resp = await client.get(f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}")
            if resp.status_code == 404:
                logger.info(f"[ECHO-MEMORY] 🏗️ Création collection {COLLECTION_MEMORY} ({EMBEDDING_DIM_V2}d)...")
                create_payload = {"vectors": {"size": EMBEDDING_DIM_V2, "distance": "Cosine"}}
                cr = await client.put(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}",
                    json=create_payload
                )
                if cr.status_code not in (200, 201):
                    logger.error(f"[ECHO-MEMORY] ❌ Échec création collection ({cr.status_code}): {cr.text}")
                    return  # Ne pas valider si la création a échoué
                logger.info(f"[ECHO-MEMORY] ✅ Collection {COLLECTION_MEMORY} créée.")
            self._collection_verified = True
        except Exception as e:
            logger.error(f"[ECHO-MEMORY] ❌ _ensure_collection : {e}")

    # ==========================================================================
    # ÉCRITURE : Mémoriser un fait explicitement
    # ==========================================================================

    async def memorize_that(
        self,
        fact: str,
        importance: int = 1,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Enregistre un fait explicite dans la base vectorielle des souvenirs via Distillation Contextuelle et Embedding factorisés."""
        """Enregistre un fait explicitement dans la base vectorielle des souvenirs."""
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status("🧠 Distillation contextuelle et enregistrement dans la base vectorielle...")
        try:
            # Extraction slug + tags via LLM
            distill_prompt = f"Extrais un 'slug' technique et 2-3 'tags' pour ce fait :\n{fact}"
            distilled = await EchoGeminiClient.call_distillation(distill_prompt, __user__, __metadata__)
            slug = distilled.get("slug", f"note_{uuid.uuid4().hex[:8]}") if distilled else f"note_{uuid.uuid4().hex[:8]}"
            tags = distilled.get("tags", ["user_pref"]) if distilled else ["user_pref"]

            # Vectorisation
            vector = await EchoGeminiClient.generate_embedding(fact, "document", __user__, __metadata__, title=slug)
            if not vector:
                return wrap_tool_output(text="❌ Échec vectorisation.", status={"status": "error"})

            # UUIDv5 déterministe (anti-doublons)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{slug}"))

            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                upsert_payload = {"points": [{
                    "id": point_id, "vector": vector,
                    "payload": {
                        "user_id": user_id, "chat_id": chat_id, "slug": slug, "summary": fact,
                        "memory_importance": int(importance), "tags": tags, "timestamp": int(time.time())
                    }
                }]}
                resp = await client.put(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points",
                    json=upsert_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                return wrap_tool_output(text=f"✅ Souvenir `{slug}` enregistré dans la base vectorielle.", status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # LECTURE : Recherche sémantique
    # ==========================================================================

    async def recall_memories(
        self,
        query: str,
        limit: int = 5,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """Recherche sémantique dans la base vectorielle des souvenirs de l'utilisateur.
        
        Implémente un reranking par importance : score_pondéré = cos_score × MEMORY_IMPORTANCE_WEIGHTS[lvl].
        Un Axiome (lvl5, poids 1.70) remonte systématiquement même avec un score cosinus moyen.
        Over-fetch ×3 pour donner au reranking suffisamment de candidats.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        await events.status("🧠 Recherche sémantique...")
        try:
            vector = await EchoGeminiClient.generate_embedding(query, "query", __user__, __metadata__)
            if not vector:
                return wrap_tool_output(text="❌ Échec vectorisation.", status={"status": "error"})

            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                # Over-fetch ×3 pour permettre le reranking par importance.
                # Seuil Qdrant abaissé à 0.35 : les souvenirs importants (lvl4-5)
                # doivent pouvoir entrer même avec un cos_score modéré.
                search_payload = {
                    "vector": vector,
                    "limit": limit * 3,
                    "with_payload": True,
                    "score_threshold": 0.35,
                    "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}]}
                }
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/search",
                    json=search_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                candidates = resp.json().get("result", [])

            if not candidates:
                return wrap_tool_output(text="Aucun souvenir trouvé.", status={"status": "success", "results": []})

            # --- RERANKING PAR IMPORTANCE ---
            # score_pondéré = cos_score × poids_importance
            # Exemple : Axiome cos=0.60 → 0.60×1.70=1.02 > Trivial cos=0.85 → 0.85×0.55=0.47
            for r in candidates:
                imp = int(r["payload"].get("memory_importance",
                                           r["payload"].get("importance", 3)))
                r["_weighted"] = r["score"] * MEMORY_IMPORTANCE_WEIGHTS.get(imp, 1.0)

            reranked = sorted(
                [r for r in candidates if r["_weighted"] >= self.valves.SCORE_THRESHOLD],
                key=lambda x: x["_weighted"],
                reverse=True
            )[:limit]

            if not reranked:
                return wrap_tool_output(
                    text="Aucun souvenir pertinent après reranking.",
                    status={"status": "success", "results": []}
                )

            md = "🧠 **Souvenirs retrouvés**\n\n"
            for r in reranked:
                p = r["payload"]
                imp = int(p.get("memory_importance", p.get("importance", 3)))
                label = MEMORY_IMPORTANCE_LABELS.get(imp, "?")
                md += (
                    f"- **{p.get('slug', 'Note')}** "
                    f"[{label} / score: {r['_weighted']:.2f}]\n"
                    f"  > {p.get('summary', '')}\n\n"
                )

            await events.status("🧠 Recherche terminée.", done=True)
            return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # LECTURE : Index des sujets mémorisés
    # ==========================================================================

    async def list_memory_topics(
        self,
        scope: str = "global",
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Récupère l'index des sujets stockés dans la base vectorielle des souvenirs."""
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return wrap_tool_output(text="❌ Erreur : Utilisateur non identifié.", status={"status": "error"})

        user_id = __user__.get("id")
        await events.status("🧠 Consultation de l'index de la mémoire...")
        try:
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                scroll_payload = {
                    "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}]},
                    "limit": 100,
                    "with_payload": ["slug", "tags", "memory_importance", "timestamp"]
                }
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/scroll",
                    json=scroll_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})

                points = resp.json().get("result", {}).get("points", [])
                if not points:
                    return wrap_tool_output(text="Votre base vectorielle des souvenirs est actuellement vide.", status={"status": "success"})

                md = "### 📚 Index de votre Base Vectorielle des Souvenirs\n\n"
                for p in points:
                    pay = p.get("payload", {})
                    md += f"- **{pay.get('slug', 'Note')}** | Lvl {pay.get('memory_importance', pay.get('importance', 1))} | `{', '.join(pay.get('tags', []))}`\n"
                return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # SUPPRESSION : Oublier un souvenir
    # ==========================================================================

    async def forget_memory(
        self,
        slug: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Supprime un souvenir spécifique de la base vectorielle via son identifiant court (slug).
        RÈGLE CRITIQUE : Si vous ne connaissez pas le slug exact de l'information à supprimer,
        vous DEVEZ d'abord utiliser l'outil 'recall_memories' avec une requête sémantique 
        (ex: "j'aime les chats gris") pour retrouver le bon slug avant d'appeler cet outil.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return wrap_tool_output(text="❌ Erreur : Utilisateur non identifié.", status={"status": "error"})

        user_id = __user__.get("id")
        await events.status(f"🧠 Suppression du souvenir '{slug}'...")
        try:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{slug}"))

            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                delete_payload = {
                    "points": [point_id]
                }
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete",
                    json=delete_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                
                await events.status("🧠 Suppression terminée.", done=True)
                return wrap_tool_output(text=f"✅ Souvenir `{slug}` supprimé avec succès.", status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # RAG ÉPHÉMÈRE : Requête documentaire sur la session
    # ==========================================================================

    async def query_distilled_data(
        self,
        slug: str,
        query: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Recherche sémantique dans la mémoire éphémère de la session courante (ex: page web distillée).
        Ne trouve que les extraits liés au slug demandé pour le chat actuel.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status(f"🧠 Recherche dans le RAG éphémère ({slug})...")

        try:
            vector = await EchoGeminiClient.generate_embedding(query, "query", __user__, __metadata__)
            if not vector:
                return wrap_tool_output(text="❌ Échec vectorisation.", status={"status": "error"})

            from echo_constants import COLLECTION_EPHEMERAL
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                search_payload = {
                    "vector": vector, "limit": 5, "with_payload": True,
                    "filter": {
                        "must": [
                            {"key": "user_id", "match": {"value": user_id}},
                            {"key": "chat_id", "match": {"value": chat_id}},
                            {"key": "slug", "match": {"value": slug}}
                        ]
                    }
                }
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_EPHEMERAL}/points/search",
                    json=search_payload
                )
                
                if resp.status_code == 404:
                    return wrap_tool_output(text=f"❌ Erreur: Aucune donnée indexée pour le slug {slug}.", status={"status": "error"})
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                
                results = resp.json().get("result", [])

            if not results:
                return wrap_tool_output(text="Aucune information trouvée dans cette source.", status={"status": "success", "results": []})

            md = f"### 📖 Extraits trouvés dans `{slug}`\n\n"
            for r in results:
                if r["score"] < self.valves.SCORE_THRESHOLD:
                    continue
                p = r["payload"]
                md += f"**Extrait (Score: {r['score']:.2f})**\n> {p.get('text', '')}\n\n"

            await events.status("🧠 Recherche RAG terminée.", done=True)
            return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})
