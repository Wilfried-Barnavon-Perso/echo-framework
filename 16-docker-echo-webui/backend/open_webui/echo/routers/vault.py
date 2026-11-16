"""
title: ECHO V6 Vault & Identity Router
author: Wilfried BARNAVON
version: 6.6.9.1
description: Routeur FastAPI pour coffre chiffré et identités LLM (/echo/v1/vault).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.9.1: Déportation asyncio.to_thread sur les accès SQLite et chiffrement du coffre (_get_vault_status_sync, _list_vault_secrets_sync, _save_vault_secret_sync, _delete_vault_secret_sync).
# 6.5.3.1: Ajout du catalogue GET /echo/v1/vault/schemas (connecteurs MCP/Vault) avec filtrage de user_required_instructions.
# 6.3.0.1: Création du routeur /echo/v1/vault (statut auth_data, quotas masqués, gestion CRUD chiffrée identity_vault).

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from open_webui.echo.identity_bridge import ensure_user_identity_db
from open_webui.echo.runtime import ensure_echo_sys_path
from open_webui.utils.auth import get_verified_user

ensure_echo_sys_path()
try:
    from echo_auth import EchoAuth  # type: ignore
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_paths import sanitize_echo_id  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_auth import EchoAuth  # type: ignore
    from echo_state_manager import EchoStateManager  # type: ignore
    from echo_paths import sanitize_echo_id  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


class VaultSecretPayload(BaseModel):
    service: str
    account_id: str
    secret: str


def _mask_key(key: str) -> str:
    """Masque une clé d'API pour l'affichage sécurisé dans l'UI."""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "***"
    return f"{k[:4]}...{k[-4:]}"


def _get_vault_status_sync(user_id: str, user_email: str, user_name: str) -> dict[str, Any]:
    ensure_user_identity_db(user_id, user_email, user_name)

    safe_uid = sanitize_echo_id(user_id)
    sm = EchoStateManager(user_id=safe_uid)
    auth = EchoAuth(user_id=safe_uid)

    raw_keys = auth.get_api_keys(safe_uid)
    masked_keys = [_mask_key(k) for k in raw_keys]

    auth_mode = sm.get_setting("auth_mode") or "api_key"
    tier = sm.get_setting("google_tier") or "free"
    project_id = sm.get_setting("google_project_id") or ""
    has_oauth = bool(sm.get_setting("google_tokens") or auth.get_auth_data("google_tokens"))
    quotas = auth.get_quota_summary()

    return {
        "user_id": safe_uid,
        "auth_mode": auth_mode,
        "api_keys_count": len(raw_keys),
        "api_keys_masked": masked_keys,
        "has_oauth": has_oauth,
        "tier": tier,
        "project_id": project_id,
        "quotas": quotas,
    }


def _list_vault_secrets_sync(user_id: str, user_email: str, user_name: str) -> dict[str, Any]:
    ensure_user_identity_db(user_id, user_email, user_name)

    safe_uid = sanitize_echo_id(user_id)
    sm = EchoStateManager(user_id=safe_uid)
    accounts = sm.list_vault_accounts()

    items = [
        {
            "service": r.get("service", ""),
            "account_id": r.get("account_id", ""),
            "has_secret": True,
        }
        for r in accounts
    ]
    return {"items": items, "count": len(items)}


def _save_vault_secret_sync(
    user_id: str,
    user_email: str,
    user_name: str,
    service: str,
    account_id: str,
    secret: str,
) -> None:
    ensure_user_identity_db(user_id, user_email, user_name)
    safe_uid = sanitize_echo_id(user_id)
    sm = EchoStateManager(user_id=safe_uid)
    sm.save_vault_credential(service, account_id, secret)


def _delete_vault_secret_sync(
    user_id: str,
    user_email: str,
    user_name: str,
    clean_service: str,
    clean_account: str,
) -> None:
    ensure_user_identity_db(user_id, user_email, user_name)
    safe_uid = sanitize_echo_id(user_id)
    sm = EchoStateManager(user_id=safe_uid)
    sm.delete_vault_credential(clean_service, clean_account)


@router.get("/status")
async def get_vault_status(
    user=Depends(get_verified_user),
):
    """Retourne l'état des identités et quotas sans jamais exposer de jeton ni de secret en clair."""
    user_email = getattr(user, "email", "")
    user_name = getattr(user, "name", "")
    content = await asyncio.to_thread(_get_vault_status_sync, user.id, user_email, user_name)
    return JSONResponse(content=content)


@router.get("/secrets")
async def list_vault_secrets(
    user=Depends(get_verified_user),
):
    """Liste les identifiants de compte stockés dans le coffre chiffré sans jamais retourner les secrets."""
    user_email = getattr(user, "email", "")
    user_name = getattr(user, "name", "")
    content = await asyncio.to_thread(_list_vault_secrets_sync, user.id, user_email, user_name)
    return JSONResponse(content=content)


@router.put("/secrets")
async def save_vault_secret(
    payload: VaultSecretPayload,
    user=Depends(get_verified_user),
):
    """Chiffre en AES-256-GCM et persiste un secret dans identity_vault."""
    service = payload.service.strip()
    account_id = payload.account_id.strip()
    secret = payload.secret

    if not service or not account_id:
        raise HTTPException(status_code=400, detail="Les champs 'service' et 'account_id' sont obligatoires.")

    user_email = getattr(user, "email", "")
    user_name = getattr(user, "name", "")

    try:
        await asyncio.to_thread(
            _save_vault_secret_sync,
            user.id,
            user_email,
            user_name,
            service,
            account_id,
            secret,
        )
    except RuntimeError as re_err:
        log.error("Clé de chiffrement coffre manquante ou invalide: %s", re_err)
        raise HTTPException(
            status_code=503,
            detail="Chiffrement indisponible (clé ECHO_VAULT_ENCRYPTION_KEY non configurée).",
        )
    except Exception as e:
        log.error("Erreur chiffrement coffre: %s", e)
        raise HTTPException(status_code=500, detail=f"Erreur lors de la persistance du secret : {e}")

    return JSONResponse(content={
        "success": True,
        "service": service,
        "account_id": account_id,
    })


@router.delete("/secrets/{service}/{account_id}")
async def delete_vault_secret(
    service: str,
    account_id: str,
    user=Depends(get_verified_user),
):
    """Supprime une entrée du coffre d'identités chiffré."""
    clean_service = service.strip()
    clean_account = account_id.strip()

    user_email = getattr(user, "email", "")
    user_name = getattr(user, "name", "")
    await asyncio.to_thread(
        _delete_vault_secret_sync,
        user.id,
        user_email,
        user_name,
        clean_service,
        clean_account,
    )

    return JSONResponse(content={
        "success": True,
        "service": clean_service,
        "account_id": clean_account,
    })


@router.get("/schemas")
async def get_vault_schemas(user=Depends(get_verified_user)):
    """Relaye les schémas d'authentification du Broker MCP vers l'UI."""
    broker_url = os.getenv(
        "ECHO_MCP_BROKER_URL", "http://echo-mcp-broker:8000"
    ).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{broker_url}/schemas")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return JSONResponse(
                        content={
                            "broker_available": True,
                            "schemas": data,
                        }
                    )
    except Exception as exc:
        log.debug("Broker MCP indisponible pour /schemas: %s", exc)
    return JSONResponse(content={"broker_available": False, "schemas": {}})
