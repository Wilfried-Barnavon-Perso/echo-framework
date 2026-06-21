"""
title: ECHO Memory & RAG Tool
author: Wilfried BARNAVON
version: 2.17
description: 2.17: Ajout start_date/end_date dans consult_session_context. Clarification SNR purge.
             2.16: Fix Gemini API 400 Bad Request sur l'enum vide de l'artifact_name.
             2.10: Migration complète vers la nomenclature Méta-Artéfacts et ajout du drapeau global_search.
             1.2: Ajout forget_memory. 1.3: Mémoire Vectorisée de Session. 1.4: Mise à jour version. 1.5: Reranking par importance (MEMORY_IMPORTANCE_WEIGHTS) dans recall_memories.
             1.6: Docstrings proactifs memorize_that + recall_memories. Fix double-docstring (bug Python L82-83).
             1.7: Docstring proactif query_distilled_data + distinction claire RAG organique vs éphémère.
             1.8: Renommage sémantique : memorize_that→save_memory, recall_memories→search_memory,
             query_distilled_data→search_session_context.
             1.9: Ajout save_session_context (outil d'écriture Mémoire Vectorisée de Session, symétrique de search_session_context).
             2.0: Réécriture complète des 6 docstrings — format orienté-modèle (résumé/Quand/Paramètres).
             2.1: Mention Vallée de la Mort dans les 4 docstrings pertinents.
             2.2: Directives de reformulation search_memory et list_memory_topics (contenu
             invisible pour l'utilisateur dans l'UI OWUI).
             2.3: Clean Slate architecture: remplacement de slug par memory_id (long terme) et source_id (éphémère).
             2.11: Ajout de l'horodatage UTC dans les retours de search_meta_artifacts et consult_meta_artifacts.
             2.12: Rétrocompatibilité tags null et correction indentation source_id dans global_search.
             2.13: Filtres temporels (start_date/end_date) et consult_session_context.
             2.14: Bypass vectoriel (API Scroll chronologique) pour les requêtes vides et extraction preview RAG.
"""

from typing import Optional, List, Any, Dict, Literal
from datetime import datetime, timezone
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
    COLLECTION_META_ARTIFACTS, EMBEDDING_DIM_V2,
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
            resp = await client.get(f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}")
            if resp.status_code == 404:
                logger.info(f"[ECHO-MEMORY] 🏗️ Création collection {COLLECTION_META_ARTIFACTS} ({EMBEDDING_DIM_V2}d)...")
                create_payload = {"vectors": {"size": EMBEDDING_DIM_V2, "distance": "Cosine"}}
                cr = await client.put(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}",
                    json=create_payload
                )
                if cr.status_code not in (200, 201):
                    logger.error(f"[ECHO-MEMORY] ❌ Échec création collection ({cr.status_code}): {cr.text}")
                    return  # Ne pas valider si la création a échoué
                logger.info(f"[ECHO-MEMORY] ✅ Collection {COLLECTION_META_ARTIFACTS} créée.")
            self._collection_verified = True
        except Exception as e:
            logger.error(f"[ECHO-MEMORY] ❌ _ensure_collection : {e}")

    def _parse_iso_date(self, date_str: str, is_end_of_day: bool = False) -> Optional[int]:
        if not date_str:
            return None
        try:
            if "T" not in date_str and len(date_str) <= 10:
                date_str += "T23:59:59" if is_end_of_day else "T00:00:00"
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            return None

    # ==========================================================================
    # ÉCRITURE : Mémoriser un fait explicitement
    # ==========================================================================

    async def update_meta_artifact(
        self,
        artifact_name: Literal["Profil d'Alignement", "Hypothèses d'Apprentissage"],
        fact: str,
        importance: int = 3,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Consigne de manière persistante une information dans un Méta-Artéfact (PACP ou PRAC). Retourne le memory_id généré. Ce retour est indispensable pour permettre au Modèle de supprimer ou cibler ce fait ultérieurement via delete_meta_artifact_item.
        :param artifact_name: Le nom du Méta-Artéfact cible.
        :param fact: Le fait ou l'hypothèse à enregistrer.
        :param importance: Niveau d'importance de 1 à 5.
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
                        "artifact_name": artifact_name,
                        "memory_importance": int(importance), "tags": tags, "timestamp": int(time.time())
                    }
                }]}
                resp = await client.put(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points",
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

    async def search_meta_artifacts(
        self,
        query: str,
        artifact_name: Optional[Literal["Profil d'Alignement", "Hypothèses d'Apprentissage"]] = None,
        limit: int = 20,
        start_date: str = "",
        end_date: str = "",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """
        Recherche sémantique vectorielle dans les Méta-Artéfacts (PACP / PRAC).
        Utilise le concept pour extraire des faits pertinents. Pour une lecture globale ou temporelle, utiliser l'outil consult_* correspondant.

        :param query: Obligatoire. Le concept, l'idée ou le mot-clé à retrouver.
        :param artifact_name: Optionnel. Restreint la recherche vectorielle à un Méta-Artéfact spécifique.
        :param limit: Optionnel. Nombre maximum de résultats. Défaut: 20.
        :param start_date: Optionnel. Borne chronologique inférieure (ISO 8601).
        :param end_date: Optionnel. Borne chronologique supérieure (ISO 8601).
        """

        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        try:
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                qdrant_filter = {"must": [{"key": "user_id", "match": {"value": user_id}}]}
                if artifact_name:
                    qdrant_filter["must"].append({"key": "artifact_name", "match": {"value": artifact_name}})
                
                ts_start = self._parse_iso_date(start_date, False)
                ts_end = self._parse_iso_date(end_date, True)
                if ts_start or ts_end:
                    rng = {}
                    if ts_start: rng["gte"] = ts_start
                    if ts_end: rng["lte"] = ts_end
                    qdrant_filter["must"].append({"key": "timestamp", "range": rng})

                # RECHERCHE SÉMANTIQUE
                await events.status("🧠 Recherche sémantique...")
                vector = await EchoGeminiClient.generate_embedding(query, "query", __user__, __metadata__)
                if not vector:
                    return wrap_tool_output(text="❌ Échec vectorisation.", status={"status": "error"})

                search_payload = {
                    "vector": vector,
                    "limit": limit * 3,
                    "with_payload": True,
                    "score_threshold": 0.35,
                    "filter": qdrant_filter
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/search",
                    json=search_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})
                candidates = resp.json().get("result", [])

            if not candidates:
                return wrap_tool_output(text="Aucun souvenir trouvé.", status={"status": "success", "results": []})

            # --- RERANKING PAR IMPORTANCE ---
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
                
                ts = p.get("timestamp")
                date_str = f" | {datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}" if ts else ""
                
                md += (
                    f"- **{p.get('memory_id', p.get('slug', 'Note'))}** "
                    f"[{label} / score: {r['_weighted']:.2f}{date_str}]\n"
                    f"  > {p.get('summary', '')}\n\n"
                )

            await events.status("🧠 Recherche terminée.", done=True)
            return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # LECTURE : Index des sujets mémorisés
    # ==========================================================================

    async def consult_meta_artifacts(
        self,
        artifact_name: Optional[Literal["Profil d'Alignement", "Hypothèses d'Apprentissage"]] = None,
        limit: int = 20,
        start_date: str = "",
        end_date: str = "",
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Aspiration chronologique des Méta-Artéfacts.
        Sert à balayer aveuglément le contexte sans biais thématique (lecture temporelle).
        Renvoie la liste des derniers faits mémorisés triés par date décroissante.

        :param artifact_name: Optionnel. Filtre pour ne remonter que les faits d'un Méta-Artéfact spécifique.
        :param limit: Optionnel. Nombre maximum de souvenirs à lister. Défaut: 20.
        :param start_date: Optionnel. Borne chronologique inférieure (ISO 8601).
        :param end_date: Optionnel. Borne chronologique supérieure (ISO 8601).
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return wrap_tool_output(text="❌ Erreur : Utilisateur non identifié.", status={"status": "error"})

        user_id = __user__.get("id")
        await events.status("🧠 Consultation de l'index de la mémoire...")
        try:
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                qdrant_filter = {"must": [{"key": "user_id", "match": {"value": user_id}}]}
                if artifact_name:
                    qdrant_filter["must"].append({"key": "artifact_name", "match": {"value": artifact_name}})
                
                ts_start = self._parse_iso_date(start_date, False)
                ts_end = self._parse_iso_date(end_date, True)
                if ts_start or ts_end:
                    rng = {}
                    if ts_start: rng["gte"] = ts_start
                    if ts_end: rng["lte"] = ts_end
                    qdrant_filter["must"].append({"key": "timestamp", "range": rng})

                scroll_payload = {
                    "filter": qdrant_filter,
                    "limit": limit,
                    "with_payload": ["memory_id", "slug", "tags", "memory_importance", "timestamp", "artifact_name"]
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/scroll",
                    json=scroll_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})

                points = resp.json().get("result", {}).get("points", [])
                if not points:
                    return wrap_tool_output(text="La base vectorielle des souvenirs est actuellement vide.", status={"status": "success"})

                if self.valves.DEBUG_MODE:
                    print(f"[ECHO-MEMORY] list_memory_topics : {len(points)} sujets trouvés pour {user_id}.", flush=True)

                md = "### 🧠 Index de la Base Vectorielle des Souvenirs\n\n"
                for p in points:
                    pay = p.get("payload", {})
                    ts = pay.get("timestamp")
                    date_str = f" | {datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}" if ts else ""
                    pay_tags = pay.get('tags') or []
                    md += f"- **{pay.get('memory_id', pay.get('slug', 'Note'))}** ({pay.get('artifact_name', 'Global')}) | Lvl {pay.get('memory_importance', pay.get('importance', 1))}{date_str} | `{', '.join(pay_tags)}`\n"
                return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    # ==========================================================================
    # SUPPRESSION : Oublier un souvenir
    # ==========================================================================

    async def delete_meta_artifact_item(
        self,
        memory_id: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Supprime une information obsolète ou erronée d'un Méta-Artéfact par son memory_id."""
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
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/delete",
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
    # MÉMOIRE VECTORISÉE DE SESSION : Écriture + Lecture documentaire sur la session
    # ==========================================================================

    async def save_session_context(
        self,
        text: str,
        source_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Sauvegarde éphémère dans le RAG Temporaire (Contexte de la session en cours)."""
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status(f"🧠 Indexation dans la Mémoire Vectorisée de Session ({source_id})...", done=False)

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
                    text=f"❌ Échec indexation Mémoire Vectorisée de Session ({source_id}) : {err}",
                    status={"status": "error"}
                )
            await events.status("🧠 Indexation terminée.", done=True)
            return wrap_tool_output(
                text=f"✅ `{source_id}` indexé dans la Mémoire Vectorisée de Session ({nb_points} vecteurs). "
                     f"Utilisez search_session_context(source_id=\"{source_id}\", ...) pour l'interroger.",
                status={"status": "success", "source_id": source_id, "vectors": nb_points}
            )
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    async def consult_session_context(
        self,
        limit: int = 20,
        start_date: str = "",
        end_date: str = "",
        global_search: bool = False,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Aspiration exhaustive de la Mémoire Vectorisée de Session.
        Sert à balayer aveuglément le contexte sans biais thématique (lecture temporelle).
        Renvoie la liste détaillée de toutes les sources (fichiers, URLs, notes) actuellement actives.

        :param limit: Optionnel. Nombre maximal de sources à lister. Défaut: 20.
        :param start_date: Optionnel. Borne chronologique inférieure (ISO 8601).
        :param end_date: Optionnel. Borne chronologique supérieure (ISO 8601).
        :param global_search: Optionnel. Si True, liste les sources de TOUTES les sessions de l'utilisateur.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        scope = "globale" if global_search else "locale"
        await events.status(f"🧠 Cartographie du RAG Session ({scope})...")

        try:
            from echo_constants import COLLECTION_SESSION_RAG
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                must_filters = [{"key": "user_id", "match": {"value": user_id}}]
                if not global_search:
                    must_filters.append({"key": "chat_id", "match": {"value": chat_id}})
                
                ts_start = self._parse_iso_date(start_date, False)
                ts_end = self._parse_iso_date(end_date, True)
                if ts_start or ts_end:
                    rng = {}
                    if ts_start: rng["gte"] = ts_start
                    if ts_end: rng["lte"] = ts_end
                    must_filters.append({"key": "timestamp", "range": rng})

                scroll_payload = {
                    "filter": {"must": must_filters},
                    "limit": limit,
                    "with_payload": ["source_id", "tags", "timestamp", "text"]
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/scroll",
                    json=scroll_payload
                )
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {resp.text}", status={"status": "error"})

                points = resp.json().get("result", {}).get("points", [])
                if not points:
                    return wrap_tool_output(text="Aucune ressource indexée trouvée.", status={"status": "success"})

                sources = {}
                tags = set()
                for p in points:
                    pay = p.get("payload", {})
                    if "source_id" in pay:
                        sid = pay["source_id"]
                        ts = pay.get("timestamp", 0)
                        if sid not in sources or ts > sources[sid]["timestamp"]:
                            sources[sid] = {
                                "timestamp": ts,
                                "preview": pay.get("text", "")[:60].replace("\n", " ") + "..."
                            }
                    pay_tags = pay.get("tags") or []
                    for t in pay_tags:
                        tags.add(t)

                md = f"### 🧠 Cartographie du RAG Session ({scope})\n\n"
                md += f"**Sources disponibles (avec aperçu) :**\n"
                # Tri par timestamp décroissant
                sorted_sources = sorted(sources.items(), key=lambda x: x[1]["timestamp"], reverse=True)
                for sid, data in sorted_sources:
                    ts_str = datetime.fromtimestamp(data["timestamp"], timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if data["timestamp"] else "Inconnu"
                    md += f"- **`{sid}`** [{ts_str}] : _{data['preview']}_\n"
                md += f"\n**Tags détectés :** `{', '.join(sorted(tags))}`"
                return wrap_tool_output(text=md, status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    async def delete_session_context_source(
        self,
        source_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Suppression intégrale et irréversible d'une source du RAG Éphémère.
        Permet d'éliminer le contexte d'un fichier, d'une page web ou d'une session obsolète de la mémoire de travail.
        Attention : Purge strictement locale à la source nommée. Une suppression globale de session est impossible via cet outil.

        :param source_id: Obligatoire. L'identifiant strict de la source (ex: nom de fichier, UUID de session, slug libre) à purger de la mémoire.
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status(f"🧠 Suppression de la source {source_id}...")

        try:
            from echo_constants import COLLECTION_SESSION_RAG
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                must_filters = [
                    {"key": "user_id", "match": {"value": user_id}},
                    {"key": "chat_id", "match": {"value": chat_id}},
                    {"key": "source_id", "match": {"value": source_id}}
                ]
                count_payload = {
                    "filter": {"must": must_filters}
                }
                count_resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/count",
                    json=count_payload
                )
                if count_resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur de vérification Qdrant : {count_resp.text}", status={"status": "error"})
                
                count = count_resp.json().get("result", {}).get("count", 0)
                if count == 0:
                    return wrap_tool_output(
                        text="❌ Échec : Source introuvable ou isolée dans une autre session. Suppression inter-session bloquée par sécurité.",
                        status={"status": "error"}
                    )
                
                delete_payload = {
                    "filter": {"must": must_filters}
                }
                del_resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/delete",
                    json=delete_payload
                )
                if del_resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ Erreur Qdrant : {del_resp.text}", status={"status": "error"})

                await events.status(f"🧠 Source {source_id} supprimée.", done=True)
                return wrap_tool_output(text=f"✅ Source purgée avec succès ({count} vecteurs supprimés).", status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur lors de la suppression : {str(e)}", status={"status": "error"})

    async def search_session_context(
        self,
        query: str,
        source_id: str = "",
        global_search: bool = False,
        limit: int = 20,
        start_date: str = "",
        end_date: str = "",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """
        Recherche sémantique vectorielle dans la mémoire de travail de la session courante (RAG Éphémère).
        Renvoie les extraits textuels complets correspondant au concept recherché.

        :param query: Obligatoire. Concept textuel ou mots-clés de la recherche sémantique.
        :param source_id: Optionnel. UUID de la source pour restreindre la recherche à un document ou une session spécifique.
        :param global_search: Optionnel. Si True, étend la recherche à toutes les sessions actives. Défaut: False.
        :param limit: Optionnel. Nombre maximum de résultats. Défaut: 20.
        :param start_date: Optionnel. Borne chronologique inférieure (ISO 8601).
        :param end_date: Optionnel. Borne chronologique supérieure (ISO 8601).
        """
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return wrap_tool_output(text="❌ Contexte manquant.", status={"status": "error"})

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        try:
            from echo_constants import COLLECTION_SESSION_RAG
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                must_filters = [{"key": "user_id", "match": {"value": user_id}}]
                if not global_search:
                    must_filters.append({"key": "chat_id", "match": {"value": chat_id}})
                if source_id:
                    must_filters.append({"key": "source_id", "match": {"value": source_id}})
                
                ts_start = self._parse_iso_date(start_date, False)
                ts_end = self._parse_iso_date(end_date, True)
                if ts_start or ts_end:
                    rng = {}
                    if ts_start: rng["gte"] = ts_start
                    if ts_end: rng["lte"] = ts_end
                    must_filters.append({"key": "timestamp", "range": rng})

                # RECHERCHE SÉMANTIQUE
                await events.status(f"🧠 Recherche dans la Mémoire Vectorisée de Session ({source_id})...")
                vector = await EchoGeminiClient.generate_embedding(query, "query", __user__, __metadata__)
                if not vector:
                    return wrap_tool_output(text="❌ Échec vectorisation.", status={"status": "error"})

                search_payload = {
                    "vector": vector, "limit": limit, "with_payload": True,
                    "filter": {
                        "must": must_filters
                    }
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/search",
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
