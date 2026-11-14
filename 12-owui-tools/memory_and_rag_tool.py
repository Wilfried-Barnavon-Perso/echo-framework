"""
title: ECHO Memory & RAG Tool
author: Wilfried BARNAVON
version: 6.5.2.1
description: ECHO Toolbox pour gestion RAG (Sessions & Codex).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.5.2.1: Abaissement de la limite par défaut max_fragments de 20 à 10 extraits dans search_sessions_context.
# 5.239.14.2: Refonte de search_sessions_context en 3 modes explicites (inventory, chronological, semantic), seuil COSINE/cosine_threshold strict, retours PAS impersonnels et horodatage ISO 8601 UTC.
# 5.235.5.1: Filtrage automatique des recherches locales de session sur les messages de la branche active via build_branch_rag_filter.
# 5.233.0.1: Contrainte de sortie JSON via response_schema=RagDistillOutput dans update_meta_artifact, décoration @echo_tool et simplification des retours.
# 5.231.20.1: Isolation inter-sous-agents par sub_sid dans search_sessions_context et préservation du score memory_importance maximal dans update_meta_artifact.

from typing import Optional, Any, Literal
from datetime import datetime, timezone
import difflib
import sys
import logging
import time
import uuid
import httpx
from pydantic import BaseModel, Field

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_core import echo_tool, wrap_tool_output, EchoCompactionEngine  # noqa: E402
from echo_gemini_client import EchoGeminiClient
from echo_session_gc import build_branch_rag_filter
from echo_constants import (
    COLLECTION_META_ARTIFACTS, EMBEDDING_DIM,
    MEMORY_IMPORTANCE_WEIGHTS, MEMORY_IMPORTANCE_LABELS,
    ECHO_QDRANT_URL
)
from echo_prompts import SYS_RAG_DISTILL
from echo_protocol import RagDistillOutput

try:
    from open_webui.models.chats import Chats
except ImportError:
    Chats = None

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
                logger.info(f"[ECHO-MEMORY] 🏗️ Création collection {COLLECTION_META_ARTIFACTS} ({EMBEDDING_DIM}d)...")
                create_payload = {"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}}
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

    def _parse_iso_date(self, date_str: Optional[str], is_end_of_day: bool = False) -> Optional[int]:
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

    @staticmethod
    def _format_rag_ts(ts: Any) -> str:
        if not ts:
            return "Date inconnue"
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _deduplicate_snippets(items: list, max_items: int) -> list:
        """Déduplique une liste de dicts contenant payload.text (ratio > 0.85) jusqu'à max_items."""
        selected = []
        seen_texts = []
        for item in items:
            if len(selected) >= max_items:
                break
            txt = (item.get("payload") or {}).get("text", "")
            if any(difflib.SequenceMatcher(None, txt, s).ratio() > 0.85 for s in seen_texts):
                continue
            seen_texts.append(txt)
            selected.append(item)
        return selected

    @staticmethod
    async def _scroll_session_headers(
        client: httpx.AsyncClient,
        collection: str,
        must_filters: list,
        payload_keys: list,
        max_pages: Optional[int] = None,
    ) -> tuple:
        """Parcourt les métadonnées légères (sans texte ni vecteur) de Qdrant par pages de 256."""
        all_points = []
        next_offset = None
        pages = 0
        truncated = False
        url = f"{ECHO_QDRANT_URL}/collections/{collection}/points/scroll"
        while True:
            if max_pages is not None and pages >= max_pages:
                truncated = True
                break
            body: dict = {
                "filter": {"must": must_filters},
                "limit": 256,
                "with_payload": payload_keys,
                "with_vector": False,
            }
            if next_offset is not None:
                body["offset"] = next_offset
            resp = await client.post(url, json=body)
            if resp.status_code == 404:
                return [], False, None
            if resp.status_code != 200:
                return None, False, f"❌ Erreur Qdrant : {resp.text}"
            res_obj = resp.json().get("result") or {}
            pts = res_obj.get("points") or []
            all_points.extend(pts)
            pages += 1
            next_offset = res_obj.get("next_page_offset")
            if next_offset is None or not pts:
                break
        return all_points, truncated, None

    @staticmethod
    async def _fetch_session_points(
        client: httpx.AsyncClient,
        collection: str,
        point_ids: list,
        payload_keys: list,
    ) -> tuple:
        """Hydrate uniquement les payloads des IDs ciblés en une requête POST /points."""
        if not point_ids:
            return {}, None
        resp = await client.post(
            f"{ECHO_QDRANT_URL}/collections/{collection}/points",
            json={"ids": point_ids, "with_payload": payload_keys, "with_vector": False},
        )
        if resp.status_code != 200:
            return None, f"❌ Erreur Qdrant : {resp.text}"
        items = resp.json().get("result") or []
        return {it["id"]: (it.get("payload") or {}) for it in items if "id" in it}, None

    # ==========================================================================
    # ÉCRITURE : Mémoriser un fait explicitement
    # ==========================================================================

    @echo_tool
    async def update_meta_artifact(
        self,
        artifact_name: Literal["Profil d'Alignement", "Hypothèses d'Apprentissage"],
        fact: str,
        importance: Literal["1", "2", "3", "4", "5"],
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Consigne de manière persistante un fait (`fact`) dans un Méta-Artéfact (`Profil d'Alignement` ou `Hypothèses d'Apprentissage`) avec niveau d'importance obligatoire de '1' à '5', et retourne son `memory_id`."""
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return "❌ Contexte manquant."

        try:
            imp_val = int(str(importance).strip())
            if imp_val not in (1, 2, 3, 4, 5):
                raise ValueError
        except (ValueError, TypeError):
            return "❌ Paramètre `importance` invalide. Valeurs autorisées : '1', '2', '3', '4', '5'."

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        await events.status(f"🧠 Mémorisation dans le Méta-Artéfact ({artifact_name})...")
        try:
            # Extraction memory_id + tags via LLM
            distilled = await EchoGeminiClient.call_distillation(
                prompt=f"Fait à distiller : {fact}",
                __user__=__user__,
                __metadata__=__metadata__,
                system_instruction=SYS_RAG_DISTILL.format(fact=fact),
                response_schema=RagDistillOutput,
            )
            memory_id = distilled.get("memory_id", distilled.get("slug", f"note_{uuid.uuid4().hex[:8]}")) if distilled else f"note_{uuid.uuid4().hex[:8]}"
            tags = distilled.get("tags", ["user_pref"]) if distilled else ["user_pref"]

            # Vectorisation
            vector = await EchoGeminiClient.generate_embedding(fact, "document", __user__, __metadata__, title=memory_id)
            if not vector:
                return "❌ Échec vectorisation."

            # UUIDv5 déterministe (anti-doublons)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{memory_id}"))

            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                await self._ensure_collection(client)
                # Préservation du score memory_importance maximal en cas de mise à jour concurrente/itérative
                try:
                    existing_resp = await client.get(
                        f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/{point_id}"
                    )
                    if existing_resp.status_code == 200:
                        existing_payload = existing_resp.json().get("result", {}).get("payload", {})
                        existing_imp = int(existing_payload.get("memory_importance", 1))
                        imp_val = max(imp_val, existing_imp)
                except Exception:
                    pass
                upsert_payload = {"points": [{
                    "id": point_id, "vector": vector,
                    "payload": {
                        "user_id": user_id, "chat_id": chat_id, "memory_id": memory_id, "summary": fact,
                        "artifact_name": artifact_name,
                        "memory_importance": imp_val, "tags": tags, "timestamp": int(time.time())
                    }
                }]}
                resp = await client.put(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points",
                    json=upsert_payload
                )
                if resp.status_code != 200:
                    return f"❌ Erreur Qdrant : {resp.text}"
                imp_label = MEMORY_IMPORTANCE_LABELS.get(imp_val, str(imp_val))
                if self.valves.DEBUG_MODE:
                    print(f"[ECHO-MEMORY] ✅ save_memory : {memory_id} inséré avec succès. (Tags: {tags}, Imp: {imp_val} - {imp_label})", flush=True)
                return f"✅ Souvenir `{memory_id}` enregistré dans la base vectorielle (Niveau {imp_val} - {imp_label})."

        except Exception as e:
            return f"❌ Erreur : {str(e)}"

    # ==========================================================================
    # LECTURE : Recherche sémantique
    # ==========================================================================

    @echo_tool
    async def search_meta_artifacts(
        self,
        query: Optional[str] = None,
        artifact_name: Optional[Literal["Profil d'Alignement", "Hypothèses d'Apprentissage"]] = None,
        limit: int = 20,
        start_date: str = "",
        end_date: str = "",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """Recherche par similarité sémantique (`query` défini) ou lit chronologiquement (`query` omis) les faits stockés dans les Méta-Artéfacts long-terme."""

        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return "❌ Contexte manquant."

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
                    if ts_start:
                        rng["gte"] = ts_start
                    if ts_end:
                        rng["lte"] = ts_end
                    qdrant_filter["must"].append({"key": "timestamp", "range": rng})

                if query:
                    # RECHERCHE SÉMANTIQUE
                    await events.status("🧠 Recherche sémantique ciblée...")
                    vector = await EchoGeminiClient.generate_embedding(query, "query", __user__, __metadata__)
                    if not vector:
                        return "❌ Échec vectorisation."

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
                        return f"❌ Erreur Qdrant : {resp.text}"
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
                            status={"status": "success", "results": []})

                    if self.valves.DEBUG_MODE:
                        print(f"[ECHO-MEMORY] search_memory : {len(reranked)} résultats retournés pour '{query}' après reranking.", flush=True)

                    md = "🧠 **Souvenirs retrouvés (Recherche)**\n\n"
                    for r in reranked:
                        p = r["payload"]
                        imp = int(p.get("memory_importance", p.get("importance", 3)))
                        label = MEMORY_IMPORTANCE_LABELS.get(imp, "?")

                        ts = p.get("timestamp")
                        date_str = f" | {self._format_rag_ts(ts)}" if ts else ""

                        md += (
                            f"- **{p.get('memory_id', p.get('slug', 'Note'))}** "
                            f"[{label} / score: {r['_weighted']:.2f}{date_str}]\n"
                            f"  > {p.get('summary', '')}\n\n"
                        )

                    await events.status("🧠 Recherche terminée.", done=True)
                    return md

                else:
                    # LECTURE GLOBALE CHRONOLOGIQUE
                    await events.status("🧠 Lecture globale de la mémoire...")
                    scroll_payload = {
                        "filter": qdrant_filter,
                        "limit": limit,
                        "with_payload": True
                    }
                    resp = await client.post(
                        f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/scroll",
                        json=scroll_payload
                    )
                    if resp.status_code != 200:
                        return f"❌ Erreur Qdrant : {resp.text}"

                    points = resp.json().get("result", {}).get("points", [])
                    if not points:
                        return "La base vectorielle des souvenirs est actuellement vide pour ce contexte."

                    if self.valves.DEBUG_MODE:
                        print(f"[ECHO-MEMORY] read_memory_global : {len(points)} souvenirs trouvés pour {user_id}.", flush=True)

                    md = "### 🧠 Contenu de la Base Vectorielle (Lecture Globale)\n\n"
                    for p in points:
                        pay = p.get("payload", {})
                        ts = pay.get("timestamp")
                        date_str = f" | {self._format_rag_ts(ts)}" if ts else ""
                        pay_tags = pay.get('tags') or []
                        imp = int(pay.get("memory_importance", pay.get("importance", 1)))
                        label = MEMORY_IMPORTANCE_LABELS.get(imp, "?")

                        md += (
                            f"- **{pay.get('memory_id', pay.get('slug', 'Note'))}** "
                            f"({pay.get('artifact_name', 'Global')}) | [{label}{date_str}] | `{', '.join(pay_tags)}`\n"
                            f"  > {pay.get('summary', '')}\n\n"
                        )

                    await events.status("🧠 Lecture globale terminée.", done=True)
                    return md

        except Exception as e:
            return f"❌ Erreur : {str(e)}"

    # ==========================================================================
    # SUPPRESSION : Oublier un souvenir
    # ==========================================================================

    @echo_tool
    async def delete_meta_artifact_item(
        self,
        memory_id: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        """Supprime définitivement un fait d'un Méta-Artéfact long-terme à partir de son `memory_id`."""
        events = EchoEvents(__event_emitter__)
        if not __user__ or not __user__.get("id"):
            return "❌ Erreur : Utilisateur non identifié."

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
                    return f"❌ Erreur Qdrant : {resp.text}"
                await events.status("🧠 Suppression terminée.", done=True)
                if self.valves.DEBUG_MODE:
                    print(f"[ECHO-MEMORY] ✅ forget_memory : {memory_id} supprimé avec succès.", flush=True)
                return f"✅ Souvenir `{memory_id}` supprimé avec succès."

        except Exception as e:
            return f"❌ Erreur : {str(e)}"

    # ==========================================================================
    # MÉMOIRE VECTORISÉE DE SESSION : Écriture + Lecture documentaire sur la session
    # ==========================================================================

    @echo_tool
    async def save_session_context(
        self,
        text: str,
        source_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """Vectorise et sauvegarde un texte (`text`) sous l'identifiant `source_id` dans la Mémoire Vectorielle de la session."""
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return "❌ Contexte manquant."

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")
        
        # PURGE PRÉVENTIVE DES VECTEURS FANTÔMES (Data Leak)
        await events.status(f"🧠 Nettoyage préalable de la source {source_id}...")
        await self.delete_session_context_source(source_id, __user__, __metadata__, __event_emitter__, __event_call__)

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
                return f"❌ Échec indexation Mémoire Vectorisée de Session ({source_id}) : {err}"
            await events.status("🧠 Indexation terminée.", done=True)
            return wrap_tool_output(
                text=f"✅ `{source_id}` indexé dans la Mémoire Vectorisée de Session ({nb_points} fragments).",
                status={"status": "success", "source_id": source_id, "vectors": nb_points})
        except Exception as e:
            return f"❌ Erreur : {str(e)}"

    @echo_tool
    async def delete_session_context_source(
        self,
        source_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """Supprime tous les vecteurs associés à `source_id` dans la Mémoire Vectorielle de la session active."""
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return "❌ Contexte manquant."

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
                
                # Isolation RAG : Si le caller est un sous-agent, il ne peut manipuler que ses propres créations.
                from echo_constants import ECHO_SUBAGENT_CONTEXT
                sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
                if sub_ctx.get("is_subagent") and sub_ctx.get("sub_sid"):
                    must_filters.append({"key": "sub_sid", "match": {"value": sub_ctx["sub_sid"]}})
                count_payload = {
                    "filter": {"must": must_filters}
                }
                count_resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/count",
                    json=count_payload
                )
                if count_resp.status_code != 200:
                    return f"Erreur de vérification Qdrant : {count_resp.text}"

                count = count_resp.json().get("result", {}).get("count", 0)
                if count == 0:
                    return "Échec : Source introuvable ou isolée dans une autre session. Suppression inter-session bloquée par sécurité."

                delete_payload = {
                    "filter": {"must": must_filters}
                }
                del_resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/delete",
                    json=delete_payload
                )
                if del_resp.status_code != 200:
                    return f"Erreur Qdrant : {del_resp.text}"

                await events.status(f"🧠 Source {source_id} supprimée.", done=True)
                return f"Source purgée avec succès ({count} vecteurs supprimés)."

        except Exception as e:
            return f"Erreur lors de la suppression : {str(e)}"

    @echo_tool
    async def search_sessions_context(
        self,
        mode: Literal["inventory", "chronological", "semantic"],
        query: Optional[str] = None,
        certainty_level: Literal["BROAD", "STANDARD", "STRICT", "EXACT", "COSINE"] = "STANDARD",
        cosine_threshold: Optional[float] = None,
        sort_order: Optional[Literal["cosine_desc", "newest_first", "oldest_first"]] = None,
        source_id: Optional[str] = None,
        global_search: bool = False,
        tag: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_fragments: int = 10,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None
    ) -> dict:
        """Interroge la mémoire vectorielle de session (`global_search=False`) ou inter-sessions (`True`) selon `mode` : `inventory` (liste les `source_id`, leur nombre de fragments et leurs dates début/fin), `chronological` (lit les extraits par date `newest_first`/`oldest_first`), ou `semantic` (recherche par sens avec `query`, selon un palier `certainty_level` ou un seuil `cosine_threshold` si `certainty_level="COSINE"`)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or not __user__.get("id") or not __metadata__:
            return "❌ Contexte manquant."

        # --- Validation stricte du contrat d'arguments (zéro repli défensif) ---
        if mode not in ("inventory", "chronological", "semantic"):
            return f"❌ Paramètre 'mode' invalide ('{mode}'). Valeurs autorisées : inventory, chronological, semantic."
        if max_fragments < 1:
            return "❌ Paramètre 'max_fragments' invalide : doit être >= 1."
        if sort_order is not None and sort_order not in ("cosine_desc", "newest_first", "oldest_first"):
            return f"❌ Paramètre 'sort_order' invalide ('{sort_order}'). Valeurs autorisées : cosine_desc, newest_first, oldest_first."

        certainty_map = {"BROAD": 0.34, "STANDARD": 0.52, "STRICT": 0.63, "EXACT": 0.81}
        c_min = 0.52
        threshold_label = ""

        if mode == "inventory":
            if query and query.strip():
                return "❌ Paramètre 'query' ABSOLUMENT INTERDIT avec mode='inventory' (mode='semantic' REQUIS pour une requête textuelle)."
            if sort_order is not None:
                return "❌ Paramètre 'sort_order' ABSOLUMENT INTERDIT avec mode='inventory'."
            if cosine_threshold is not None or certainty_level != "STANDARD":
                return "❌ Paramètres 'certainty_level' et 'cosine_threshold' ABSOLUMENT INTERDITS hors mode='semantic'."
        elif mode == "chronological":
            if query and query.strip():
                return "❌ Paramètre 'query' ABSOLUMENT INTERDIT avec mode='chronological' (mode='semantic' REQUIS pour une requête textuelle)."
            if sort_order == "cosine_desc":
                return "❌ sort_order='cosine_desc' ABSOLUMENT INTERDIT avec mode='chronological' (valeurs autorisées : 'newest_first', 'oldest_first')."
            if cosine_threshold is not None or certainty_level != "STANDARD":
                return "❌ Paramètres 'certainty_level' et 'cosine_threshold' ABSOLUMENT INTERDITS hors mode='semantic'."
        else:  # mode == "semantic"
            if not query or not query.strip():
                return "❌ Paramètre 'query' REQUIS en mode='semantic' (modes sans 'query' : 'inventory', 'chronological')."
            if certainty_level == "COSINE":
                if cosine_threshold is None or not (0.0 <= float(cosine_threshold) <= 1.0):
                    return "❌ Paramètre 'cosine_threshold' (dans [0.0, 1.0]) REQUIS lorsque certainty_level='COSINE'."
                c_min = float(cosine_threshold)
                threshold_label = f"COSINE ({c_min:.2f})"
            elif certainty_level in certainty_map:
                if cosine_threshold is not None:
                    return "❌ Incohérence : certainty_level='COSINE' REQUIS lorsque 'cosine_threshold' est défini (sinon 'cosine_threshold' DOIT être omis)."
                c_min = certainty_map[certainty_level]
                threshold_label = f"{certainty_level} ({c_min:.2f})"
            else:
                return f"❌ Paramètre 'certainty_level' invalide ('{certainty_level}'). Valeurs autorisées : BROAD, STANDARD, STRICT, EXACT, COSINE."

        ts_start = self._parse_iso_date(start_date, False)
        if start_date and start_date.strip() and ts_start is None:
            return f"❌ Format 'start_date' invalide ('{start_date}'). Format ISO attendu : YYYY-MM-DD."
        ts_end = self._parse_iso_date(end_date, True)
        if end_date and end_date.strip() and ts_end is None:
            return f"❌ Format 'end_date' invalide ('{end_date}'). Format ISO attendu : YYYY-MM-DD."
        if ts_start is not None and ts_end is not None and ts_start > ts_end:
            return "❌ Plage temporelle invalide : 'start_date' est postérieure à 'end_date'."

        user_id = __user__.get("id")
        chat_id = __metadata__.get("chat_id")

        matching_chat_ids = None
        if tag and tag.strip():
            clean_tag = tag.strip()
            try:
                matching_chats = await Chats.get_chat_list_by_user_id_and_tag_name(
                    user_id=user_id, tag_name=clean_tag, skip=0, limit=100
                ) if Chats and hasattr(Chats, "get_chat_list_by_user_id_and_tag_name") else []
                matching_chat_ids = [c.id for c in matching_chats]
                if not matching_chat_ids:
                    return wrap_tool_output(
                        text=f"Aucune conversation associée au tag '{clean_tag}' n'a été trouvée.",
                        status={"status": "success", "results": []})
            except Exception as e:
                return f"❌ Erreur résolution du tag '{clean_tag}': {str(e)}"

        scope = "globale" if global_search else "locale"

        try:
            from echo_constants import COLLECTION_SESSION_RAG, ECHO_SUBAGENT_CONTEXT
            sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
            async with httpx.AsyncClient(timeout=self.valves.RECALL_TIMEOUT) as client:
                must_filters = [{"key": "user_id", "match": {"value": user_id}}]
                if matching_chat_ids is not None:
                    must_filters.append({"key": "chat_id", "match": {"any": matching_chat_ids}})
                    scope = f"filtrée sur tag '{tag.strip()}' ({len(matching_chat_ids)} chats)"
                elif not global_search:
                    must_filters.append({"key": "chat_id", "match": {"value": chat_id}})
                    active_ids = await EchoCompactionEngine.load_active_branch_ids(chat_id)
                    if active_ids:
                        must_filters.append(build_branch_rag_filter(active_ids))
                if source_id and source_id.strip():
                    must_filters.append({"key": "source_id", "match": {"value": source_id.strip()}})
                if sub_ctx and sub_ctx.get("is_subagent") and sub_ctx.get("sub_sid"):
                    must_filters.append({
                        "should": [
                            {"is_empty": {"key": "sub_sid"}},
                            {"key": "sub_sid", "match": {"value": sub_ctx["sub_sid"]}},
                        ]
                    })

                if ts_start is not None or ts_end is not None:
                    rng = {}
                    if ts_start is not None:
                        rng["gte"] = ts_start
                    if ts_end is not None:
                        rng["lte"] = ts_end
                    must_filters.append({"key": "timestamp", "range": rng})

                # --- MODE 1 : INVENTAIRE DES SOURCES ---
                if mode == "inventory":
                    await events.status(f"🧠 Inventaire du RAG Session ({scope})...")
                    max_pages = 16 if (global_search or matching_chat_ids is not None) else None
                    pts, truncated, err = await self._scroll_session_headers(
                        client, COLLECTION_SESSION_RAG, must_filters,
                        ["source_id", "timestamp", "chunk_index", "tags"],
                        max_pages=max_pages,
                    )
                    if err:
                        return err
                    if not pts:
                        await events.status("🧠 Inventaire terminé.", done=True)
                        return wrap_tool_output(text="Aucune ressource indexée trouvée.", status={"status": "success", "results": []})

                    sources: dict = {}
                    tags_set = set()
                    for p in pts:
                        pid = p.get("id")
                        pay = p.get("payload") or {}
                        for t in (pay.get("tags") or []):
                            tags_set.add(t)
                        sid = pay.get("source_id")
                        if not sid:
                            continue
                        ts = pay.get("timestamp") or 0
                        cidx = pay.get("chunk_index") or 0
                        ord_key = (ts, cidx)
                        if sid not in sources:
                            sources[sid] = {
                                "count": 1,
                                "min_key": ord_key, "min_ts": ts, "oldest_id": pid,
                                "max_key": ord_key, "max_ts": ts, "newest_id": pid,
                            }
                        else:
                            entry = sources[sid]
                            entry["count"] += 1
                            if ord_key < entry["min_key"]:
                                entry["min_key"], entry["min_ts"], entry["oldest_id"] = ord_key, ts, pid
                            if ord_key >= entry["max_key"]:
                                entry["max_key"], entry["max_ts"], entry["newest_id"] = ord_key, ts, pid

                    boundary_ids = list({
                        bid for s in sources.values()
                        for bid in (s["oldest_id"], s["newest_id"]) if bid is not None
                    })
                    hydrated, err = await self._fetch_session_points(
                        client, COLLECTION_SESSION_RAG, boundary_ids, ["text"]
                    )
                    if err:
                        return err

                    def _preview(pt_id: Any) -> str:
                        raw = (hydrated.get(pt_id) or {}).get("text", "")
                        clean = raw[:120].replace("\n", " ").strip()
                        return f"{clean}..." if len(raw) > 120 else clean

                    trunc_note = " *(plafonné aux 4096 premiers fragments)*" if truncated else ""
                    md = f"### 🧠 Inventaire RAG Session ({scope}){trunc_note}\n\n"
                    md += "**Sources indexées (`source_id`) :**\n"
                    for sid, data in sorted(sources.items(), key=lambda x: x[1]["max_ts"], reverse=True):
                        cnt = data["count"]
                        min_str = self._format_rag_ts(data["min_ts"])
                        max_str = self._format_rag_ts(data["max_ts"])
                        if cnt == 1 or data["oldest_id"] == data["newest_id"]:
                            md += f"- **`{sid}`** — **{cnt} fragment** | [{max_str}] : _{_preview(data['newest_id'])}_\n"
                        else:
                            md += (
                                f"- **`{sid}`** — **{cnt} fragments** | du **{min_str}** au **{max_str}**\n"
                                f"  - Plus ancien [{min_str}] : _{_preview(data['oldest_id'])}_\n"
                                f"  - Plus récent [{max_str}] : _{_preview(data['newest_id'])}_\n"
                            )
                    md += f"\n**Tags détectés :** `{', '.join(sorted(tags_set))}`"
                    await events.status("🧠 Inventaire terminé.", done=True)
                    return md

                # --- MODE 2 : LECTURE CHRONOLOGIQUE ---
                if mode == "chronological":
                    effective_order = sort_order or "newest_first"
                    await events.status(f"🧠 Lecture chronologique RAG ({scope} | {effective_order})...")
                    max_pages = 16 if (global_search or matching_chat_ids is not None) else None
                    pts, truncated, err = await self._scroll_session_headers(
                        client, COLLECTION_SESSION_RAG, must_filters,
                        ["source_id", "timestamp", "chunk_index"],
                        max_pages=max_pages,
                    )
                    if err:
                        return err
                    if not pts:
                        await events.status("🧠 Lecture chronologique terminée.", done=True)
                        return wrap_tool_output(text="Aucune information trouvée.", status={"status": "success", "results": []})

                    if effective_order == "oldest_first":
                        pts.sort(key=lambda p: ((p.get("payload") or {}).get("timestamp") or 0, (p.get("payload") or {}).get("chunk_index") or 0))
                    else:
                        pts.sort(key=lambda p: (-((p.get("payload") or {}).get("timestamp") or 0), (p.get("payload") or {}).get("chunk_index") or 0))

                    candidate_pts = pts[: max_fragments * 2]
                    hydrated, err = await self._fetch_session_points(
                        client, COLLECTION_SESSION_RAG,
                        [p["id"] for p in candidate_pts if "id" in p],
                        ["source_id", "timestamp", "text"],
                    )
                    if err:
                        return err

                    ordered_candidates = [
                        {"id": p["id"], "payload": hydrated.get(p["id"], p.get("payload") or {})}
                        for p in candidate_pts if p.get("id") in hydrated
                    ]
                    selected = self._deduplicate_snippets(ordered_candidates, max_fragments)
                    if not selected:
                        return wrap_tool_output(text="Aucune information trouvée.", status={"status": "success", "results": []})

                    total_lbl = f"{len(pts)}+" if truncated else str(len(pts))
                    md = f"### 📖 Lecture chronologique RAG ({scope} | tri: {effective_order} | {len(selected)} sur {total_lbl} fragments)\n\n"
                    for item in selected:
                        pay = item["payload"]
                        src = pay.get("source_id", "Inconnu")
                        ts_str = self._format_rag_ts(pay.get("timestamp"))
                        md += f"**Source `{src}` [{ts_str}]**\n> {pay.get('text', '')}\n\n"
                    await events.status("🧠 Lecture chronologique terminée.", done=True)
                    return md

                # --- MODE 3 : RECHERCHE SÉMANTIQUE ---
                effective_order = sort_order or "cosine_desc"
                await events.status(f"🧠 Recherche dans la Mémoire Vectorisée de Session ({scope})...")
                vector = await EchoGeminiClient.generate_embedding(query, "query", __user__, __metadata__)
                if not vector:
                    return "❌ Échec vectorisation."

                search_payload = {
                    "vector": vector,
                    "limit": max_fragments * 2,
                    "with_payload": True,
                    "score_threshold": c_min,
                    "filter": {"must": must_filters},
                }
                resp = await client.post(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/search",
                    json=search_payload,
                )
                if resp.status_code == 404:
                    return wrap_tool_output(text="Aucune information trouvée.", status={"status": "success", "results": []})
                if resp.status_code != 200:
                    return f"❌ Erreur Qdrant : {resp.text}"

                results = [r for r in (resp.json().get("result") or []) if r.get("score", 0.0) >= c_min]
                if not results:
                    return wrap_tool_output(text="Aucune information trouvée.", status={"status": "success", "results": []})

                if effective_order == "newest_first":
                    results.sort(key=lambda x: (-((x.get("payload") or {}).get("timestamp") or 0), (x.get("payload") or {}).get("chunk_index") or 0))
                elif effective_order == "oldest_first":
                    results.sort(key=lambda x: ((x.get("payload") or {}).get("timestamp") or 0, (x.get("payload") or {}).get("chunk_index") or 0))
                else:
                    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)

                selected = self._deduplicate_snippets(results, max_fragments)
                md = f"### 📖 Extraits RAG trouvés ({scope} | seuil: {threshold_label} | tri: {effective_order})\n\n"
                for r in selected:
                    pay = r["payload"]
                    src = pay.get("source_id", "Inconnu")
                    ts_str = self._format_rag_ts(pay.get("timestamp"))
                    md += f"**Source `{src}` (Score: {r.get('score', 0.0):.2f} | {ts_str})**\n> {pay.get('text', '')}\n\n"

                await events.status("🧠 Recherche RAG terminée.", done=True)
                return md

        except Exception as e:
            return f"❌ Erreur : {str(e)}"
