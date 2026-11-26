# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO V6 LLM Authentication Router
author: Wilfried BARNAVON
version: 6.21.8.1
description: Routeur FastAPI pour l'authentification LLM (OAuth2 Google PKCE + SSH éphémère, clés AI Studio, quotas et réinitialisation) (/echo/v1/llm-auth).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.21.8.1: Validation des clés API AI Studio via /models?pageSize=1000 et en-têtes x-goog-api-key, User-Agent et x-goog-api-client (ECHO_AISTUDIO_API_CLIENT).
# 6.18.23.1: Création du routeur souverain /echo/v1/llm-auth remplaçant le flux inline du pipe et reset_auth_action.py.

import asyncio
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.echo.http_utils import get_echo_verified_user
from open_webui.echo.identity_bridge import ensure_user_identity_db
from open_webui.echo.runtime import ensure_echo_sys_path

ensure_echo_sys_path()
try:
    from echo_auth import AuthService, EchoAuth  # type: ignore
    from echo_constants import (  # type: ignore
        AUTH_METHOD_KEY_PRIMARY,
        AUTH_METHOD_KEY_SECONDARY,
        ECHO_AISTUDIO_API_CLIENT,
        ECHO_SSH_TUNNEL_TIMEOUT,
        ECHO_SSH_TUNNEL_USER,
        GOOGLE_API_BASE_URL,
        GOOGLE_API_KEY_PATTERN,
    )
    from echo_paths import sanitize_echo_id  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_auth import AuthService, EchoAuth  # type: ignore
    from echo_constants import (  # type: ignore
        AUTH_METHOD_KEY_PRIMARY,
        AUTH_METHOD_KEY_SECONDARY,
        ECHO_AISTUDIO_API_CLIENT,
        ECHO_SSH_TUNNEL_TIMEOUT,
        ECHO_SSH_TUNNEL_USER,
        GOOGLE_API_BASE_URL,
        GOOGLE_API_KEY_PATTERN,
    )
    from echo_paths import sanitize_echo_id  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()

# Registre mémoire des flows PKCE/SSH actifs indexé par safe_uid
_ACTIVE_PKCE_FLOWS: dict[str, dict[str, Any]] = {}
_FLOWS_LOCK = asyncio.Lock()


class ApiKeyPayload(BaseModel):
    api_key: str
    slot: Optional[Literal["primary", "secondary"]] = None


def _mask_api_key(key: str) -> str:
    """Masque une clé AI Studio sous la forme AIza••••XXXX."""
    clean = (key or "").strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        return "••••"
    return f"{clean[:4]}••••{clean[-4:]}"


def _build_ssh_command(server_ip: str, ssh_port: int, cb_port: int) -> str:
    return (
        f"ssh "
        f"-o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null "
        f"-L {cb_port}:localhost:{cb_port} "
        f"{ECHO_SSH_TUNNEL_USER}@{server_ip} -p {ssh_port}"
    )


def _serialize_pkce_flow(entry: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not entry:
        return {
            "active": False,
            "ssh_command": "",
            "temp_password": "",
            "auth_url": "",
            "server_ip": "",
            "ssh_port": 0,
            "cb_port": 0,
            "expires_in_sec": 0,
            "ssh_tunnel_connected": False,
            "last_result": None,
        }

    is_active = bool(entry.get("active"))
    expires_in_sec = 0
    ssh_tunnel_connected = False
    if is_active:
        elapsed = max(0.0, time.time() - float(entry.get("started_at") or 0.0))
        ttl = int(entry.get("ttl_sec") or ECHO_SSH_TUNNEL_TIMEOUT)
        expires_in_sec = max(0, int(ttl - elapsed))
        ssh_srv = getattr(entry.get("auth_service"), "_ssh_server", None)
        if ssh_srv is not None:
            ssh_tunnel_connected = bool(
                getattr(ssh_srv, "_active_connections", None)
                or getattr(ssh_srv, "_active_sessions", None)
            )

    return {
        "active": is_active,
        "ssh_command": entry.get("ssh_command", "") if is_active else "",
        "temp_password": entry.get("temp_password", "") if is_active else "",
        "auth_url": entry.get("auth_url", "") if is_active else "",
        "server_ip": entry.get("server_ip", "") if is_active else "",
        "ssh_port": int(entry.get("ssh_port") or 0) if is_active else 0,
        "cb_port": int(entry.get("cb_port") or 0) if is_active else 0,
        "expires_in_sec": expires_in_sec,
        "ssh_tunnel_connected": ssh_tunnel_connected,
        "last_result": entry.get("last_result"),
    }


async def _stop_existing_flow(safe_uid: str, stop_message: str) -> Optional[dict[str, Any]]:
    """Stoppe proprement un flow PKCE/SSH actif pour safe_uid (à appeler sous _FLOWS_LOCK ou en cleanup)."""
    entry = _ACTIVE_PKCE_FLOWS.get(safe_uid)
    if not entry:
        return None

    auth_service = entry.get("auth_service")
    ssh_srv = getattr(auth_service, "_ssh_server", None)
    if ssh_srv is not None and getattr(ssh_srv, "is_active", False):
        try:
            await ssh_srv.stop(message=stop_message)
        except Exception as exc:
            log.debug(f"[ECHO-LLM-AUTH] Arrêt SSH ({safe_uid}): {exc}")

    task: Optional[asyncio.Task] = entry.get("task")
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    entry["active"] = False
    return entry


async def _run_pkce_await_task(safe_uid: str, auth_service: AuthService, entry: dict[str, Any]) -> None:
    """Exécute await_pkce_callback() en tâche de fond et consigne le résultat final."""
    success = False
    message = "Échec de l'authentification OAuth2."
    try:
        success, message = await auth_service.await_pkce_callback()
    except asyncio.CancelledError:
        success = False
        message = "Connexion OAuth2 annulée."
        raise
    except Exception as exc:
        log.error(f"[ECHO-LLM-AUTH] Erreur tâche PKCE ({safe_uid}): {exc}", exc_info=True)
        success = False
        message = f"Erreur OAuth2 : {exc}"
    finally:
        async with _FLOWS_LOCK:
            if _ACTIVE_PKCE_FLOWS.get(safe_uid) is entry:
                entry["active"] = False
                entry["last_result"] = {
                    "success": bool(success),
                    "message": str(message),
                    "finished_at": time.time(),
                }


def _read_status_sync(user_id: str, user_email: str, user_name: str) -> dict[str, Any]:
    ensure_user_identity_db(user_id, user_email, user_name)
    safe_uid = sanitize_echo_id(user_id)
    auth = EchoAuth(user_id=safe_uid)
    providers = auth.get_active_auth_providers(safe_uid)
    snapshot = auth.get_ui_quota_snapshot(auth_providers=providers)

    primary_raw = auth.get_auth_data(AUTH_METHOD_KEY_PRIMARY, safe_uid) or ""
    secondary_raw = auth.get_auth_data(AUTH_METHOD_KEY_SECONDARY, safe_uid) or ""
    oauth_connected = bool(auth.get_auth_data("google_oauth2_refresh_token", safe_uid))

    return {
        "user_id": safe_uid,
        "oauth_connected": oauth_connected,
        "snapshot": snapshot,
        "api_keys": {
            "primary_configured": bool(primary_raw),
            "primary_masked": _mask_api_key(primary_raw),
            "secondary_configured": bool(secondary_raw),
            "secondary_masked": _mask_api_key(secondary_raw),
        },
    }


def _rebuild_priority(auth: EchoAuth, safe_uid: str) -> None:
    priority = []
    if auth.get_auth_data("google_oauth2_refresh_token", safe_uid):
        priority.append("google_oauth2")
    if auth.get_auth_data(AUTH_METHOD_KEY_PRIMARY, safe_uid):
        priority.append(AUTH_METHOD_KEY_PRIMARY)
    if auth.get_auth_data(AUTH_METHOD_KEY_SECONDARY, safe_uid):
        priority.append(AUTH_METHOD_KEY_SECONDARY)
    auth.save_api_key("google_auth_priority", ",".join(priority), safe_uid)


async def _validate_google_api_key(api_key: str) -> None:
    test_url = f"{GOOGLE_API_BASE_URL}/models?pageSize=1000"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                test_url,
                headers={"x-goog-api-key": api_key, "User-Agent": ECHO_AISTUDIO_API_CLIENT, "x-goog-api-client": ECHO_AISTUDIO_API_CLIENT},
                timeout=10.0,
            )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Impossible de joindre Google AI Studio pour valider la clé : {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Clé API rejetée par Google AI Studio (HTTP {resp.status_code}).",
        )


@router.get("/status")
async def get_llm_auth_status(
    refresh: bool = False,
    user=Depends(get_echo_verified_user),
) -> dict[str, Any]:
    safe_uid = sanitize_echo_id(user.id)
    user_email = getattr(user, "email", "") or ""
    user_name = getattr(user, "name", "") or ""

    await asyncio.to_thread(ensure_user_identity_db, user.id, user_email, user_name)

    if refresh:
        try:
            service = AuthService(user_id=safe_uid)
            await service.refresh_quota_if_needed(force=True)
        except Exception as exc:
            log.warning(f"[ECHO-LLM-AUTH] Rafraîchissement quota ignoré ({safe_uid}): {exc}")

    status_data = await asyncio.to_thread(_read_status_sync, user.id, user_email, user_name)
    async with _FLOWS_LOCK:
        flow_entry = _ACTIVE_PKCE_FLOWS.get(safe_uid)
        pkce_flow = _serialize_pkce_flow(flow_entry)

    status_data["pkce_flow"] = pkce_flow
    return status_data


@router.post("/oauth/start")
async def start_llm_oauth(
    request: Request,
    user=Depends(get_echo_verified_user),
) -> dict[str, Any]:
    safe_uid = sanitize_echo_id(user.id)
    user_email = getattr(user, "email", "") or ""
    user_name = getattr(user, "name", "") or ""
    await asyncio.to_thread(ensure_user_identity_db, user.id, user_email, user_name)

    async with _FLOWS_LOCK:
        await _stop_existing_flow(safe_uid, "Remplacé par une nouvelle tentative d'authentification")

        auth_service = AuthService(user_id=safe_uid)
        try:
            ok, auth_url, server_ip, ssh_port, cb_port, temp_pwd = await auth_service.initiate_pkce_flow(
                request=request
            )
        except Exception as exc:
            log.error(f"[ECHO-LLM-AUTH] Échec démarrage PKCE/SSH ({safe_uid}): {exc}", exc_info=True)
            raise HTTPException(status_code=503, detail=f"Impossible de démarrer le tunnel SSH/PKCE : {exc}") from exc

        if not ok:
            raise HTTPException(status_code=500, detail="Initialisation du flux PKCE refusée.")

        ssh_command = _build_ssh_command(server_ip, ssh_port, cb_port)
        entry: dict[str, Any] = {
            "auth_service": auth_service,
            "task": None,
            "auth_url": auth_url,
            "ssh_command": ssh_command,
            "server_ip": server_ip,
            "ssh_port": ssh_port,
            "cb_port": cb_port,
            "temp_password": temp_pwd,
            "started_at": time.time(),
            "ttl_sec": ECHO_SSH_TUNNEL_TIMEOUT,
            "active": True,
            "last_result": None,
        }
        entry["task"] = asyncio.create_task(_run_pkce_await_task(safe_uid, auth_service, entry))
        _ACTIVE_PKCE_FLOWS[safe_uid] = entry

        return {
            "status": "started",
            "pkce_flow": _serialize_pkce_flow(entry),
        }


@router.post("/oauth/cancel")
async def cancel_llm_oauth(user=Depends(get_echo_verified_user)) -> dict[str, Any]:
    safe_uid = sanitize_echo_id(user.id)

    async with _FLOWS_LOCK:
        entry = await _stop_existing_flow(safe_uid, "Annulé par l'utilisateur")
        if entry is not None:
            entry["last_result"] = {
                "success": False,
                "message": "Connexion OAuth2 annulée par l'utilisateur.",
                "finished_at": time.time(),
            }

    def _clear_pkce_keys() -> None:
        EchoAuth(user_id=safe_uid).delete_auth_data_by_prefixes(("pkce_",))

    await asyncio.to_thread(_clear_pkce_keys)
    return {"status": "cancelled"}


@router.put("/api-key")
async def upsert_llm_api_key(
    payload: ApiKeyPayload,
    user=Depends(get_echo_verified_user),
) -> dict[str, Any]:
    raw_key = (payload.api_key or "").strip()
    if not raw_key or not re.match(GOOGLE_API_KEY_PATTERN, raw_key):
        raise HTTPException(
            status_code=400,
            detail="Format de clé API invalide (attendu : AIza… ou AQ.…).",
        )

    safe_uid = sanitize_echo_id(user.id)
    user_email = getattr(user, "email", "") or ""
    user_name = getattr(user, "name", "") or ""
    await asyncio.to_thread(ensure_user_identity_db, user.id, user_email, user_name)

    await _validate_google_api_key(raw_key)

    def _save_slot_sync() -> str:
        auth = EchoAuth(user_id=safe_uid)
        target_slot = payload.slot
        if target_slot is None:
            has_primary = bool(auth.get_auth_data(AUTH_METHOD_KEY_PRIMARY, safe_uid))
            target_slot = "secondary" if has_primary else "primary"

        method = AUTH_METHOD_KEY_PRIMARY if target_slot == "primary" else AUTH_METHOD_KEY_SECONDARY
        auth.save_api_key(method, raw_key, safe_uid)
        _rebuild_priority(auth, safe_uid)
        return target_slot

    saved_slot = await asyncio.to_thread(_save_slot_sync)
    return {
        "status": "ok",
        "slot": saved_slot,
        "masked_key": _mask_api_key(raw_key),
    }


@router.delete("/api-key/{slot}")
async def delete_llm_api_key(
    slot: Literal["primary", "secondary"],
    user=Depends(get_echo_verified_user),
) -> dict[str, Any]:
    safe_uid = sanitize_echo_id(user.id)
    user_email = getattr(user, "email", "") or ""
    user_name = getattr(user, "name", "") or ""
    await asyncio.to_thread(ensure_user_identity_db, user.id, user_email, user_name)

    def _delete_slot_sync() -> None:
        auth = EchoAuth(user_id=safe_uid)
        method = AUTH_METHOD_KEY_PRIMARY if slot == "primary" else AUTH_METHOD_KEY_SECONDARY
        auth.delete_api_key(method, safe_uid)
        _rebuild_priority(auth, safe_uid)

    await asyncio.to_thread(_delete_slot_sync)
    return {"status": "deleted", "slot": slot}


@router.delete("/reset")
async def reset_llm_auth(user=Depends(get_echo_verified_user)) -> dict[str, Any]:
    safe_uid = sanitize_echo_id(user.id)
    user_email = getattr(user, "email", "") or ""
    user_name = getattr(user, "name", "") or ""
    await asyncio.to_thread(ensure_user_identity_db, user.id, user_email, user_name)

    async with _FLOWS_LOCK:
        await _stop_existing_flow(safe_uid, "Réinitialisation de l'authentification LLM")
        _ACTIVE_PKCE_FLOWS.pop(safe_uid, None)

    def _reset_sync() -> int:
        return EchoAuth(user_id=safe_uid).delete_auth_data_by_prefixes(("google_", "pkce_"), safe_uid)

    deleted = await asyncio.to_thread(_reset_sync)
    return {"status": "reset", "deleted": deleted}
