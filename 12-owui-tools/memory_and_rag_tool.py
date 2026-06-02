"""
title: ECHO Memory & RAG Tool
author: Wilfried BARNAVON
version: 2.5
description: 1.2: Ajout forget_memory. 1.3: RAG éphémère. 1.4: Mise à jour version. 1.5: Reranking par importance (MEMORY_IMPORTANCE_WEIGHTS) dans recall_memories.
             1.6: Docstrings proactifs memorize_that + recall_memories. Fix double-docstring (bug Python L82-83).
             1.7: Docstring proactif query_distilled_data + distinction claire RAG organique vs éphémère.
             1.8: Renommage sémantique : memorize_that→save_memory, recall_memories→search_memory,
             query_distilled_data→search_session_context.
             1.9: Ajout save_session_context (outil d'écriture RAG éphémère, symétrique de search_session_context).
             2.0: Réécriture complète des 6 docstrings — format orienté-modèle (résumé/Quand/Paramètres).
             2.1: Mention Vallée de la Mort dans les 4 docstrings pertinents.
             2.2: Directives de reformulation search_memory et list_memory_topics (contenu
             invisible pour l'utilisateur dans l'UI OWUI).
             2.3: Clean Slate architecture: remplacement de slug par memory_id (long terme) et source_id (éphémère).
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
    MEMORY_IMPORTANCE_WEIGHTS, MEMORY_IMPORTANCE_LABELS,
    ECHO_QDRANT_URL
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-MEMORY-TOOL")


class Tools:
    class Valves(BaseModel):
        SCORE_THRESHOLD: float = Field(default=0.45, description="Seuil de confiance minimal pour la recherche sémantique (0.0 à 1.0).")
        RECALL_TIMEOUT: int = Field(default=60, description="Délai d'attente maximum (secondes) pour les requêtes Qdrant.")
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
            resp = await client.get(f"{ECHO_QDRANT_URL}/collections/{COLLECTION_MEMORY}")
            if resp.status_code == 404:
                logger.info(f"[ECHO-MEMORY] 🏗️ Création collection {COLLECTION_MEMORY} ({EMBEDDING_DIM_V2}d)...")
                create_payload = {"vectors": {"size": EMBEDDING_DIM_V2, "distance": "Cosine"}}
                cr = await client.put(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_MEMORY}",
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

    async def save_memory(
        self,
        fact: str,
        importance: int = 1,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Sauvegarde définitivement un fait en mémoire long terme — accessible dans toutes les sessions futures.

        **Quand l'utiliser :**
        - Préférences utilisateur ("préfère Python 3.12", "utilise dark mode")
        - Décisions prises ("on a choisi PostgreSQL pour ce projet")
        - Contraintes techniques découvertes (OS, versions, architecture, limites)
        - Identifiants critiques (noms de projets, IDs, URLs importantes)
        - Règles ou conventions établies par l'utilisateur

        **Ne pas utiliser** si l'info n'a de sens que pour cette session → utiliser save_session_context.

        **Vallée de la Mort :** Sauvegarder les faits importants proactivement dès qu'ils sont
        identifiés, avant que la saturation contextuelle ne les rende difficiles à retrouver.

        **Paramètre `importance`** (1→5) :
        - 1 Trivial     : Préférences légères, anecdotes
        - 2 Ordinaire   : Infos utiles mais non critiques
        - 3 Significatif : Décisions, faits importants [défaut]
        - 4 Clé         : Contraintes majeures, ressources critiques
        - 5 Axiome      : Règles absolues, vérités fondamentales
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status("🧠 Distillation contextuelle et enregistrement dans la base vectorielle...")
        try:
            # Extraction memory_id + tags via LLM
            distill_prompt = f"Extrais un 'memory_id' technique court et 2-3 'tags' pour ce fait :\n{fact}"
            distilled = await EchoGeminiClient.call_distillation(distill_prompt, __user__, __metadata__)
            memory_id = distilled.get("memory_id", distilled.get("slug", f"note_{uuid.uuid4().hex[:8]}")) if distilled else f"note_{uuid.uuid4().hex[:8]}"
            tags = distilled.get("tags", ["user_pref"]) if distilled else ["user_pref"]

            # Vectorisation
            vector = await EchoGeminiClient.generate_embedding(fact, "document", __user__, __metadata__, title=memory_id)
            if not vector:
                return wrap_tool_output(text="❌ Échec vectorisation.", status={"status": "error"})

            # UUIDv5 déterministe (anti-doublons)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{memory_id}"))

            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                upsert_payload = {"points": [{
                    "id": point_id, "vector": vector,
                    "payload": {
                        "user_id": user_id, "chat_id": chat_id, "memory_id": memory_id, "summary": fact,
                        "memory_importance": int(importance), "tags": tags, "timestamp": int(time.time())
                    }
                }]}
                resp = await client.put(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_MEMORY}/points",
                    json=upsert_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                if self.valves.DEBUG_MODE:
                    print(f"[ECHO-MEMORY] ✅ save_memory : {memory_id} inséré avec succès. (Tags: {tags}, Imp: {importance})", flush=True)
                return wrap_tool_output(text=f"✅ Souvenir `{memory_id}` enregistré dans la base vectorielle.", status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # LECTURE : Recherche sémantique
    # ==========================================================================

    async def search_memory(
        self,
        query: str,
        limit: int = 5,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """
        Recherche dans la mémoire long terme — retrouve des faits mémorisés lors de sessions précédentes.

        **Quand l'utiliser :**
        - Avant de répondre à une question sur des préférences, habitudes ou décisions passées
        - Quand l'utilisateur évoque quelque chose qui a pu être mentionné avant
        - Pour vérifier si un fait a déjà été mémorisé avant de le sauvegarder à nouveau
        - Toute question impliquant un historique au-delà de la session courante

        **Vallée de la Mort :** À forte charge contextuelle, les informations des sessions
        précédentes sont totalement absentes du contexte — ce RAG est le seul moyen de les récupérer.

        Les souvenirs d'importance élevée (Clé, Axiome) remontent même avec une faible similarité.
        Préférer des requêtes courtes et précises ("préférences Python", "décision architecture").

        IMPORTANT : Les résultats de cette recherche sont encapsulés dans un bloc
        technique invisible pour l'utilisateur. Reformule les souvenirs retrouvés
        dans ta réponse de manière naturelle et synthétique.
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
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/search",
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
                if self.valves.DEBUG_MODE:
                    print(f"[ECHO-MEMORY] search_memory : Aucun souvenir pour '{query}' après reranking (seuil {self.valves.SCORE_THRESHOLD}).", flush=True)
                return wrap_tool_output(
                    text="Aucun souvenir pertinent après reranking.",
                    status={"status": "success", "results": []}
                )

            if self.valves.DEBUG_MODE:
                print(f"[ECHO-MEMORY] search_memory : {len(reranked)} résultats retournés pour '{query}' après reranking.", flush=True)

            md = "🧠 **Souvenirs retrouvés**\n\n"
            for r in reranked:
                p = r["payload"]
                imp = int(p.get("memory_importance", p.get("importance", 3)))
                label = MEMORY_IMPORTANCE_LABELS.get(imp, "?")
                md += (
                    f"- **{p.get('memory_id', p.get('slug', 'Note'))}** "
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
        """
        Liste tous les sujets mémorisés en mémoire long terme (memory_id, tags, niveau d'importance).

        **Quand l'utiliser :**
        - Avant un forget_memory, pour trouver le memory_id exact à supprimer
        - Pour répondre à "qu'est-ce que tu sais sur moi ?" ou "qu'as-tu mémorisé ?"
        - Pour vérifier si un sujet a déjà été indexé avant d'utiliser search_memory

        IMPORTANT : Le contenu retourné est invisible pour l'utilisateur. Présente
        la liste des topics dans ta réponse en langage naturel.
        """
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
                    "with_payload": ["memory_id", "slug", "tags", "memory_importance", "timestamp"]
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/scroll",
                    json=scroll_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})

                points = resp.json().get("result", {}).get("points", [])
                if not points:
                    return wrap_tool_output(text="Votre base vectorielle des souvenirs est actuellement vide.", status={"status": "success"})

                if self.valves.DEBUG_MODE:
                    print(f"[ECHO-MEMORY] list_memory_topics : {len(points)} sujets trouvés pour {user_id}.", flush=True)

                md = "### 📚 Index de votre Base Vectorielle des Souvenirs\n\n"
                for p in points:
                    pay = p.get("payload", {})
                    md += f"- **{pay.get('memory_id', pay.get('slug', 'Note'))}** | Lvl {pay.get('memory_importance', pay.get('importance', 1))} | `{', '.join(pay.get('tags', []))}`\n"
                return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # SUPPRESSION : Oublier un souvenir
    # ==========================================================================

    async def forget_memory(
        self,
        memory_id: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Supprime définitivement un souvenir de la mémoire long terme.

        **ATTENTION :** Irréversible. Ne supprime que la mémoire long terme (pas le RAG éphémère).
        **Règle :** Le memory_id exact est requis.
        Si inconnu → utiliser d'abord list_memory_topics ou search_memory pour le retrouver.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return wrap_tool_output(text="❌ Erreur : Utilisateur non identifié.", status={"status": "error"})

        user_id = __user__.get("id")
        await events.status(f"🧠 Suppression du souvenir '{memory_id}'...")
        try:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{memory_id}"))

            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                delete_payload = {
                    "points": [point_id]
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete",
                    json=delete_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                await events.status("🧠 Suppression terminée.", done=True)
                if self.valves.DEBUG_MODE:
                    print(f"[ECHO-MEMORY] ✅ forget_memory : {memory_id} supprimé avec succès.", flush=True)
                return wrap_tool_output(text=f"✅ Souvenir `{memory_id}` supprimé avec succès.", status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # RAG ÉPHÉMÈRE : Écriture + Lecture documentaire sur la session
    # ==========================================================================

    async def save_session_context(
        self,
        text: str,
        source_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Indexe du texte dans le RAG éphémère — mémoire de travail valable uniquement pour cette session.

        **Quand l'utiliser :**
        - Conclusion d'une analyse longue à retrouver plus tard dans la session
        - Résultats intermédiaires d'un calcul ou d'une recherche
        - Contenu extrait d'un document utilisé plusieurs fois dans la session
        - Toute information utile maintenant mais sans intérêt après la session

        **Différence clé :**
        - save_memory          → permanent, accessible dans toutes les sessions futures
        - save_session_context → temporaire, session courante seulement

        **Vallée de la Mort :** Dès que le contexte dépasse ~30% de saturation, indexer
        proactivement les résultats intermédiaires importants pour ne pas les perdre.

        Après indexation, retrouver via search_session_context(source_id=..., query=...).
        Paramètre `source_id` : identifiant court de la source (ex: "analyse-pr42", "résultat-tva").
        Paramètre `text` : texte à indexer (découpé automatiquement en chunks sémantiques).
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status(f"🧠 Indexation dans le RAG éphémère ({source_id})...", done=False)

        try:
            nb_points, err = await EchoGeminiClient.index_text_in_ephemeral_rag(
                distillate=text,
                source_id=source_id,
                uid=user_id,
                chat_id=chat_id,
                __user__=__user__,
                __metadata__=__metadata__
            )
            if nb_points == 0:
                return wrap_tool_output(
                    text=f"❌ Échec indexation RAG éphémère ({source_id}) : {err}",
                    status={"status": "error"}
                )
            await events.status("🧠 Indexation terminée.", done=True)
            return wrap_tool_output(
                text=f"✅ `{source_id}` indexé dans le RAG éphémère ({nb_points} vecteurs). "
                     f"Utilisez search_session_context(source_id=\"{source_id}\", ...) pour l'interroger.",
                status={"status": "success", "source_id": source_id, "vectors": nb_points}
            )
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    async def search_session_context(
        self,
        source_id: str,
        query: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Recherche dans le RAG éphémère — retrouve du contenu indexé plus tôt dans la session courante.

        **Quand l'utiliser :**
        - Après navigation web : le contenu de la page est indexé (source_id = domaine ou nom court)
        - Après analyse de fichier : le contenu est indexé (source_id = file_id)
        - Après save_session_context : retrouver ce qui a été mis en mémoire de travail
        - Quand le contenu source est sorti de la fenêtre de contexte visible

        **Vallée de la Mort :** À forte charge contextuelle (>30%), préférer ce RAG plutôt
        que de tenter de relire loin dans l'historique — la précision sémantique est bien supérieure.

        Le contenu disparaît à la fin de la session (contrairement à search_memory).
        Paramètre `source_id` : identifiant de la source (affiché lors de l'indexation ou dans le registre_fichiers).
        Paramètre `query` : question sémantique posée sur ce contenu.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status(f"🧠 Recherche dans le RAG éphémère ({source_id})...")

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
                            {"key": "source_id", "match": {"value": source_id}}
                        ]
                    }
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_EPHEMERAL}/points/search",
                    json=search_payload
                )
                
                if resp.status_code == 404:
                    return wrap_tool_output(text=f"❌ Erreur: Aucune donnée indexée pour la source {source_id}.", status={"status": "error"})
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                
                results = resp.json().get("result", [])

            if not results:
                return wrap_tool_output(text="Aucune information trouvée dans cette source.", status={"status": "success", "results": []})

            md = f"### 📖 Extraits trouvés dans `{source_id}`\n\n"
            for r in results:
                if r["score"] < self.valves.SCORE_THRESHOLD:
                    continue
                p = r["payload"]
                md += f"**Extrait (Score: {r['score']:.2f})**\n> {p.get('text', '')}\n\n"

            await events.status("🧠 Recherche RAG terminée.", done=True)
            return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})
