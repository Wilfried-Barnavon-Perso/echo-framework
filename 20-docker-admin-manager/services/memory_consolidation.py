# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - MEMORY CONSOLIDATION (LVL1 -> LVL2)
VERSION : 5.136
================================================================================
"""
from collections import Counter
import math
import time
import uuid
from core.config import (
    COLLECTION_META_ARTIFACTS,
    DEFAULT_MAINT_CONFIG,
    HAS_HTTPX,
    QDRANT_URL,
    httpx,
)

__all__ = [
    '_cosine_sim',
    '_centroid',
    'consolidate_memories_for_user',
]

def _cosine_sim(v1: list, v2: list) -> float:
    """Produit scalaire entre deux vecteurs normalisés L2 = similarité cosinus."""
    return sum(a * b for a, b in zip(v1, v2))

def _centroid(vectors: list) -> list:
    """
    Centroïde L2-normalisé d'une liste de vecteurs déjà normalisés.
    Représente le centre de gravité sémantique du cluster.
    Après re-normalisation, le vecteur est valide pour la recherche cosinus dans Qdrant.
    """
    dim = len(vectors[0])
    c = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
    norm = math.sqrt(sum(x**2 for x in c))
    return [x / norm for x in c] if norm > 0 else c

def consolidate_memories_for_user(user_id: str, config: dict) -> dict:
    """
    Consolide les souvenirs lvl1 d'un utilisateur en souvenirs lvl2 par clustering sémantique.

    Stratégie : clustering greedy sur les vecteurs récupérés via scroll (with_vector=True),
    puis fusion par centroïde L2-normalisé des vecteurs du cluster.
    - Zéro appel au worker Harrier-OSS (vecteurs déjà disponibles).
    - Zéro appel Gemini (pas de dépendance aux clés API utilisateur).
    - Zéro troncature : le summary concatène l'intégralité des textes originaux.
    - Idémpotente : cible uniquement memory_importance=1.

    Retourne : {clusters_found, points_merged, points_promoted, points_deleted}
    """
    if not HAS_HTTPX:
        return {"error": "httpx non disponible"}

    consol_cfg       = config.get("consolidation", DEFAULT_MAINT_CONFIG["consolidation"])
    trigger_threshold = int(consol_cfg.get("trigger_threshold", 10))
    min_cluster_size  = int(consol_cfg.get("min_cluster_size", 3))
    sim_threshold     = float(consol_cfg.get("similarity_threshold", 0.75))
    report = {"clusters_found": 0, "points_merged": 0, "points_promoted": 0, "points_deleted": 0}

    try:
        # 1. COUNT : guard — évite de charger les vecteurs si pas assez de lvl1
        count_resp = httpx.post(
            f"{QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/count",
            json={
                "filter": {"must": [
                    {"key": "user_id",          "match": {"value": user_id}},
                    {"key": "memory_importance", "match": {"value": 1}}
                ]},
                "exact": True
            }, timeout=10
        )
        if count_resp.status_code != 200:
            return report
        if count_resp.json().get("result", {}).get("count", 0) < trigger_threshold:
            return report  # Pas assez de lvl1 pour consolider

        # 2. SCROLL with_vector=True (API REST Qdrant : singulier obligatoire)
        scroll_resp = httpx.post(
            f"{QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/scroll",
            json={
                "filter": {"must": [
                    {"key": "user_id",          "match": {"value": user_id}},
                    {"key": "memory_importance", "match": {"value": 1}}
                ]},
                "limit": 200,
                "with_payload": True,
                "with_vector": True    # Clé REST Qdrant (singulier) : zéro appel embedding
            }, timeout=30
        )
        if scroll_resp.status_code != 200:
            return report
        points = scroll_resp.json().get("result", {}).get("points", [])
        if not points:
            return report

        # 3. CLUSTERING GREEDY
        # Chaque point non assigné devient la graine d'un nouveau cluster.
        # Tous ses voisins avec cos ≥ sim_threshold le rejoignent.
        assigned, clusters = set(), []
        for p in points:
            if p["id"] in assigned or not p.get("vector"):
                continue
            cluster = [p]
            assigned.add(p["id"])
            for q in points:
                if q["id"] in assigned or not q.get("vector"):
                    continue
                if _cosine_sim(p["vector"], q["vector"]) >= sim_threshold:
                    cluster.append(q)
                    assigned.add(q["id"])
            if len(cluster) >= min_cluster_size:
                clusters.append(cluster)

        report["clusters_found"] = len(clusters)
        if not clusters:
            return report

        # 4. FUSION PAR CENTROÏDE
        for cluster in clusters:
            # Vecteur fusionné = centroïde L2-normalisé des vecteurs du cluster
            fused_vector = _centroid([pt["vector"] for pt in cluster])

            # Summary = concaténation complète (zéro troncature)
            summaries    = [pt["payload"].get("summary", "") for pt in cluster if pt.get("payload")]
            fused_summary = " | ".join(s for s in summaries if s)
            tags          = list({t for pt in cluster for t in pt.get("payload", {}).get("tags", [])})[:5]
            
            # Propagation de l'artifact_name (le plus fréquent du cluster)
            a_names = [pt["payload"].get("artifact_name") for pt in cluster if pt.get("payload") and pt["payload"].get("artifact_name")]
            major_artifact = Counter(a_names).most_common(1)[0][0] if a_names else None
            
            new_memory_id = f"consolidated_{uuid.uuid4().hex[:8]}"
            new_id        = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{new_memory_id}"))

            payload_data = {
                "user_id":           user_id,
                "chat_id":           "consolidated",
                "timestamp":         int(time.time()),
                "memory_importance": 2,      # Promotion lvl1 -> lvl2
                "memory_id":         new_memory_id,
                "tags":              tags,
                "summary":           fused_summary
            }
            if major_artifact:
                payload_data["artifact_name"] = major_artifact

            # Upsert du nouveau point lvl2
            upsert_resp = httpx.put(
                f"{QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points",
                json={"points": [{
                    "id": new_id,
                    "vector": fused_vector,
                    "payload": payload_data
                }]}, timeout=30
            )
            if upsert_resp.status_code not in (200, 206):
                print(f"[ECHO-CONSOLIDATION] \u274c Upsert échoué : {upsert_resp.text[:200]}")
                continue

            # Suppression des anciens points par liste d'IDs
            # (API Qdrant : {"points": [id1, id2, ...]} et non pas {"filter": ...})
            del_resp = httpx.post(
                f"{QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/delete",
                json={"points": [pt["id"] for pt in cluster]},
                timeout=30
            )
            if del_resp.status_code == 200:
                report["points_deleted"]  += len(cluster)
                report["points_promoted"] += 1
                report["points_merged"]   += len(cluster)
                print(f"[ECHO-CONSOLIDATION] \u2705 Cluster {new_memory_id} : "
                      f"{len(cluster)} lvl1 → 1 lvl2 (user={user_id[:8]})")

    except Exception as e:
        print(f"[ECHO-CONSOLIDATION] \u274c Erreur user={user_id[:8]}: {e}")

    return report
