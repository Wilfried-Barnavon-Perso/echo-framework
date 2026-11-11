"""
title: ECHO V6 Native Meta-Artifacts Router
author: Wilfried BARNAVON
version: 6.2.0.1
description: Routeur souverain FastAPI pour la gestion CRUD des Méta-Artéfacts Qdrant (/echo/v1/meta-artifacts).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.2.0.1: Routeur souverain /echo/v1/meta-artifacts (CRUD, promotion Axiome et vectorisation Qdrant PACP/PRAC).

from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.utils.auth import get_verified_user

ensure_echo_sys_path()
try:
    from echo_constants import (  # type: ignore  # noqa: F401
        COLLECTION_META_ARTIFACTS,
        ECHO_QDRANT_URL,
        EMBEDDING_DIM,
        MEMORY_IMPORTANCE_LABELS,
        MEMORY_IMPORTANCE_WEIGHTS,
    )
    from echo_gemini_client import EchoGeminiClient  # type: ignore
except ImportError:
    # Fallback d'import pour l'exécution hors conteneur (ex: tests locaux Windows)
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_constants import (  # type: ignore  # noqa: F401
        COLLECTION_META_ARTIFACTS,
        ECHO_QDRANT_URL,
        EMBEDDING_DIM,
        MEMORY_IMPORTANCE_LABELS,
        MEMORY_IMPORTANCE_WEIGHTS,
    )
    from echo_gemini_client import EchoGeminiClient  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()

_MEMORY_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_VALID_ARTIFACT_NAMES = ("Profil d'Alignement", "Hypothèses d'Apprentissage")


class CreateMetaArtifactPayload(BaseModel):
    memory_id: Optional[str] = None
    summary: str
    artifact_name: str = "Profil d'Alignement"
    memory_importance: int = Field(default=3, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    preserve_max_importance: bool = True


class UpdateMetaArtifactPayload(BaseModel):
    summary: Optional[str] = None
    artifact_name: Optional[str] = None
    memory_importance: Optional[int] = Field(default=None, ge=1, le=5)
    tags: Optional[list[str]] = None


def _compute_point_id(user_id: str, memory_id: str) -> str:
    """Génère un UUIDv5 déterministe par utilisateur et memory_id."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{memory_id}"))


async def _ensure_meta_collection(client: httpx.AsyncClient) -> None:
    """Vérifie l'existence de la collection Qdrant des méta-artéfacts et la crée si absente."""
    try:
        resp = await client.get(f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}")
        if resp.status_code == 404:
            create_payload = {
                "vectors": {
                    "size": EMBEDDING_DIM,
                    "distance": "Cosine",
                }
            }
            await client.put(f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}", json=create_payload)
    except httpx.RequestError as e:
        log.warning("Impossible de joindre Qdrant pour vérifier la collection: %s", e)
        raise HTTPException(status_code=503, detail="Service Qdrant indisponible")


async def _generate_vector(user_id: str, summary: str, memory_id: str) -> list[float]:
    """Vectorise le résumé du méta-artéfact via EchoGeminiClient."""
    try:
        vec = await EchoGeminiClient.generate_embedding(
            summary, "document", {"id": user_id}, {}, title=memory_id
        )
        if vec and isinstance(vec, list):
            return vec
    except Exception as e:
        log.error("Erreur lors de la vectorisation du méta-artéfact: %s", e)
    raise HTTPException(status_code=503, detail="Service de vectorisation indisponible")


@router.get("/")
async def list_meta_artifacts(
    artifact_name: Optional[str] = Query(default=None),
    importance: Optional[int] = Query(default=None, ge=1, le=5),
    tag: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    user: Any = Depends(get_verified_user),
) -> dict[str, Any]:
    """Liste et filtre les méta-artéfacts (PACP / PRAC) stockés dans Qdrant pour l'utilisateur."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _ensure_meta_collection(client)

        scroll_filter: dict[str, Any] = {
            "must": [{"key": "user_id", "match": {"value": user.id}}]
        }
        if artifact_name and artifact_name in _VALID_ARTIFACT_NAMES:
            scroll_filter["must"].append({"key": "artifact_name", "match": {"value": artifact_name}})

        try:
            resp = await client.post(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/scroll",
                json={
                    "filter": scroll_filter,
                    "limit": 1000,
                    "with_payload": True,
                    "with_vector": False,
                },
            )
        except httpx.RequestError as e:
            log.error("Échec de connexion à Qdrant lors du scroll: %s", e)
            raise HTTPException(status_code=503, detail="Service Qdrant indisponible")

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Erreur Qdrant: {resp.text}")

        data = resp.json().get("result", {})
        raw_points = data.get("points", [])

    items: list[dict[str, Any]] = []
    pacp_count = 0
    prac_count = 0
    axioms_count = 0

    q_lower = q.strip().lower() if q else None
    tag_lower = tag.strip().lower() if tag else None

    for pt in raw_points:
        payload = pt.get("payload") or {}
        if payload.get("user_id") != user.id:
            continue

        item_art_name = payload.get("artifact_name", "")
        item_imp = int(payload.get("memory_importance", 1))

        if item_art_name == "Profil d'Alignement":
            pacp_count += 1
        elif item_art_name == "Hypothèses d'Apprentissage":
            prac_count += 1

        if item_imp == 5:
            axioms_count += 1

        # Application des filtres mémoires optionnels
        if importance is not None and item_imp != importance:
            continue

        item_tags = [str(t) for t in payload.get("tags", [])]
        if tag_lower and not any(tag_lower == t.lower() for t in item_tags):
            continue

        if q_lower:
            m_id = str(payload.get("memory_id", "")).lower()
            summ = str(payload.get("summary", "")).lower()
            tag_match = any(q_lower in t.lower() for t in item_tags)
            if q_lower not in m_id and q_lower not in summ and not tag_match:
                continue

        items.append({
            "id": pt.get("id"),
            "memory_id": payload.get("memory_id"),
            "summary": payload.get("summary", ""),
            "artifact_name": item_art_name,
            "memory_importance": item_imp,
            "importance_label": MEMORY_IMPORTANCE_LABELS.get(item_imp, str(item_imp)),
            "tags": item_tags,
            "timestamp": payload.get("timestamp", 0),
            "user_id": user.id,
        })

    # Tri décroissant : importance d'abord (5 -> 1), puis timestamp le plus récent
    items.sort(key=lambda x: (x["memory_importance"], x["timestamp"]), reverse=True)

    return {
        "items": items,
        "total": len(items),
        "counts": {
            "pacp": pacp_count,
            "prac": prac_count,
            "axioms": axioms_count,
        },
    }


@router.post("/")
async def create_meta_artifact(
    payload: CreateMetaArtifactPayload,
    user: Any = Depends(get_verified_user),
) -> dict[str, Any]:
    """Crée ou met à jour un méta-artéfact avec vectorisation automatique."""
    if payload.artifact_name not in _VALID_ARTIFACT_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"artifact_name invalide. Valeurs permises: {_VALID_ARTIFACT_NAMES}",
        )

    summary_clean = payload.summary.strip()
    if not summary_clean:
        raise HTTPException(status_code=400, detail="Le résumé ne peut pas être vide")

    raw_mem_id = (payload.memory_id or "").strip()
    if not raw_mem_id:
        slug_seed = re.sub(r"[^a-zA-Z0-9_-]+", "_", summary_clean[:32]).strip("_").lower()
        raw_mem_id = slug_seed or f"note_{uuid.uuid4().hex[:8]}"

    if not _MEMORY_ID_RE.match(raw_mem_id):
        raise HTTPException(status_code=400, detail="Identifiant memory_id invalide")

    point_id = _compute_point_id(user.id, raw_mem_id)
    vector = await _generate_vector(user.id, summary_clean, raw_mem_id)

    async with httpx.AsyncClient(timeout=10.0) as client:
        await _ensure_meta_collection(client)

        final_imp = payload.memory_importance
        if payload.preserve_max_importance:
            try:
                exist_resp = await client.get(
                    f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/{point_id}"
                )
                if exist_resp.status_code == 200:
                    exist_data = exist_resp.json().get("result") or {}
                    exist_payload = exist_data.get("payload") or {}
                    if exist_payload.get("user_id") == user.id:
                        existing_imp = int(exist_payload.get("memory_importance", 1))
                        final_imp = max(final_imp, existing_imp)
            except Exception:
                pass

        point_record = {
            "user_id": user.id,
            "memory_id": raw_mem_id,
            "summary": summary_clean,
            "artifact_name": payload.artifact_name,
            "memory_importance": final_imp,
            "tags": payload.tags,
            "timestamp": int(time.time()),
        }

        try:
            upsert_resp = await client.put(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points",
                json={"points": [{"id": point_id, "vector": vector, "payload": point_record}]},
            )
        except httpx.RequestError as e:
            log.error("Erreur de connexion à Qdrant lors de l'upsert: %s", e)
            raise HTTPException(status_code=503, detail="Service Qdrant indisponible")

        if upsert_resp.status_code != 200:
            raise HTTPException(status_code=upsert_resp.status_code, detail=f"Erreur Qdrant: {upsert_resp.text}")

    return {
        "id": point_id,
        "memory_id": raw_mem_id,
        "summary": summary_clean,
        "artifact_name": payload.artifact_name,
        "memory_importance": final_imp,
        "importance_label": MEMORY_IMPORTANCE_LABELS.get(final_imp, str(final_imp)),
        "tags": payload.tags,
        "timestamp": point_record["timestamp"],
        "user_id": user.id,
    }


@router.patch("/{memory_id}")
async def update_meta_artifact(
    memory_id: str,
    payload: UpdateMetaArtifactPayload,
    user: Any = Depends(get_verified_user),
) -> dict[str, Any]:
    """Modifie un méta-artéfact existant (recalcule le vecteur si le résumé change)."""
    if not _MEMORY_ID_RE.match(memory_id):
        raise HTTPException(status_code=400, detail="Identifiant memory_id invalide")

    point_id = _compute_point_id(user.id, memory_id)

    async with httpx.AsyncClient(timeout=10.0) as client:
        await _ensure_meta_collection(client)

        try:
            exist_resp = await client.get(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/{point_id}"
            )
        except httpx.RequestError as e:
            log.error("Échec de connexion à Qdrant: %s", e)
            raise HTTPException(status_code=503, detail="Service Qdrant indisponible")

        if exist_resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Méta-artéfact introuvable")

        point_data = exist_resp.json().get("result") or {}
        existing_payload = point_data.get("payload") or {}

        if existing_payload.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="Accès refusé à ce méta-artéfact")

        current_summary = existing_payload.get("summary", "")
        new_summary = payload.summary.strip() if payload.summary is not None else current_summary
        if not new_summary:
            raise HTTPException(status_code=400, detail="Le résumé ne peut pas être vide")

        new_art_name = payload.artifact_name or existing_payload.get("artifact_name", "Profil d'Alignement")
        if new_art_name not in _VALID_ARTIFACT_NAMES:
            raise HTTPException(status_code=400, detail=f"artifact_name invalide: {new_art_name}")

        new_imp = payload.memory_importance if payload.memory_importance is not None else int(
            existing_payload.get("memory_importance", 1)
        )
        new_tags = payload.tags if payload.tags is not None else existing_payload.get("tags", [])

        updated_payload = {
            "user_id": user.id,
            "memory_id": memory_id,
            "summary": new_summary,
            "artifact_name": new_art_name,
            "memory_importance": new_imp,
            "tags": new_tags,
            "timestamp": int(time.time()),
        }

        # Si le résumé a été modifié, on régénère le vecteur
        if new_summary != current_summary:
            vector = await _generate_vector(user.id, new_summary, memory_id)
            upsert_body = {"points": [{"id": point_id, "vector": vector, "payload": updated_payload}]}
            save_resp = await client.put(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points",
                json=upsert_body,
            )
        else:
            save_resp = await client.post(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/payload",
                json={"payload": updated_payload, "points": [point_id]},
            )

        if save_resp.status_code != 200:
            raise HTTPException(status_code=save_resp.status_code, detail=f"Erreur Qdrant: {save_resp.text}")

    return {
        "id": point_id,
        "memory_id": memory_id,
        "summary": new_summary,
        "artifact_name": new_art_name,
        "memory_importance": new_imp,
        "importance_label": MEMORY_IMPORTANCE_LABELS.get(new_imp, str(new_imp)),
        "tags": new_tags,
        "timestamp": updated_payload["timestamp"],
        "user_id": user.id,
    }


@router.post("/{memory_id}/promote")
async def promote_meta_artifact(
    memory_id: str,
    user: Any = Depends(get_verified_user),
) -> dict[str, Any]:
    """Promeut immédiatement un méta-artéfact au rang maximal (5 - Axiome)."""
    if not _MEMORY_ID_RE.match(memory_id):
        raise HTTPException(status_code=400, detail="Identifiant memory_id invalide")

    point_id = _compute_point_id(user.id, memory_id)

    async with httpx.AsyncClient(timeout=10.0) as client:
        await _ensure_meta_collection(client)

        try:
            exist_resp = await client.get(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/{point_id}"
            )
        except httpx.RequestError as e:
            log.error("Échec de connexion à Qdrant: %s", e)
            raise HTTPException(status_code=503, detail="Service Qdrant indisponible")

        if exist_resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Méta-artéfact introuvable")

        point_data = exist_resp.json().get("result") or {}
        existing_payload = point_data.get("payload") or {}

        if existing_payload.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="Accès refusé à ce méta-artéfact")

        now_ts = int(time.time())
        promote_resp = await client.post(
            f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/payload",
            json={
                "payload": {
                    "memory_importance": 5,
                    "timestamp": now_ts,
                },
                "points": [point_id],
            },
        )

        if promote_resp.status_code != 200:
            raise HTTPException(status_code=promote_resp.status_code, detail=f"Erreur Qdrant: {promote_resp.text}")

    return {
        "id": point_id,
        "memory_id": memory_id,
        "memory_importance": 5,
        "importance_label": "Axiome",
        "timestamp": now_ts,
        "user_id": user.id,
    }


@router.delete("/{memory_id}")
async def delete_meta_artifact(
    memory_id: str,
    user: Any = Depends(get_verified_user),
) -> dict[str, Any]:
    """Supprime un méta-artéfact de la collection Qdrant."""
    if not _MEMORY_ID_RE.match(memory_id):
        raise HTTPException(status_code=400, detail="Identifiant memory_id invalide")

    point_id = _compute_point_id(user.id, memory_id)

    async with httpx.AsyncClient(timeout=10.0) as client:
        await _ensure_meta_collection(client)

        try:
            exist_resp = await client.get(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/{point_id}"
            )
        except httpx.RequestError as e:
            log.error("Échec de connexion à Qdrant: %s", e)
            raise HTTPException(status_code=503, detail="Service Qdrant indisponible")

        if exist_resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Méta-artéfact introuvable")

        point_data = exist_resp.json().get("result") or {}
        existing_payload = point_data.get("payload") or {}

        if existing_payload.get("user_id") != user.id:
            raise HTTPException(status_code=403, detail="Accès refusé à ce méta-artéfact")

        del_resp = await client.post(
            f"{ECHO_QDRANT_URL}/collections/{COLLECTION_META_ARTIFACTS}/points/delete",
            json={"points": [point_id]},
        )

        if del_resp.status_code != 200:
            raise HTTPException(status_code=del_resp.status_code, detail=f"Erreur Qdrant: {del_resp.text}")

    return {"deleted": True, "memory_id": memory_id, "id": point_id}
