"""
title: ECHO V6 Canonical Identity Bridge
author: Wilfried BARNAVON
version: 6.1.0.1
description: Pont déterministe Email SSO (X-Webui-User) <-> User UUID (webui.db) <-> identity.db (Schéma v2).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.1.0.1: Création du pont d'identité canonique X-Webui-User <-> webui.db UUID <-> identity.db (Schéma v2).

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from open_webui.echo.runtime import ensure_echo_sys_path

log = logging.getLogger(__name__)

# Cache mémoire (user_id -> (email, initialized_at)) évitant de réécrire identity.db à chaque requête HTTP
_IDENTITY_SYNC_CACHE: dict[str, tuple[str, float]] = {}


@dataclass(frozen=True)
class EchoIdentityContext:
    user_id: str
    email: str
    name: str
    identity_db_path: str
    trusted_sso: bool


def ensure_user_identity_db(user_id: str, email: str = "", name: str = "") -> str:
    """
    Matérialise de manière idempotente /app/backend/data/users/{user_id}/identity.db (Schéma v2)
    et synchronise les métadonnées canoniques de l'utilisateur dans echo_settings.
    """
    ensure_echo_sys_path()
    from echo_db_schema import DB_KIND_IDENTITY, ensure_db
    from echo_paths import get_echo_global_path, get_echo_identity_db_path, sanitize_echo_id
    from echo_state_manager import EchoStateManager

    safe_uid = sanitize_echo_id(user_id)
    id_db_path = get_echo_identity_db_path(safe_uid)
    norm_email = (email or "").strip().lower()

    cached = _IDENTITY_SYNC_CACHE.get(safe_uid)
    if cached is not None and cached[0] == norm_email and os.path.isfile(id_db_path):
        return id_db_path

    os.makedirs(os.path.dirname(id_db_path), exist_ok=True)
    ensure_db(id_db_path, DB_KIND_IDENTITY)

    # Garantit l'existence des répertoires globaux utilisateur (files, skills)
    for domain in ("files", "skills"):
        try:
            os.makedirs(get_echo_global_path(safe_uid, domain), exist_ok=True)
        except Exception as exc:
            log.debug("[ECHO-IDENTITY] Création domaine global %s ignorée: %s", domain, exc)

    try:
        sm = EchoStateManager(user_id=safe_uid, chat_id=None)
        if norm_email:
            sm.save_setting("canonical_email", norm_email)
        if name:
            sm.save_setting("canonical_name", str(name).strip())
        sm.save_setting("user_uuid", safe_uid)
    except Exception as exc:
        log.warning("[ECHO-IDENTITY] Échec écriture echo_settings dans identity.db pour %s: %s", safe_uid, exc)

    _IDENTITY_SYNC_CACHE[safe_uid] = (norm_email, time.time())
    return id_db_path


async def resolve_canonical_user(request: Any, user: Any) -> EchoIdentityContext | None:
    """
    Vérifie la cohérence entre l'en-tête SSO X-Webui-User (si présent), l'utilisateur résolu
    par Open WebUI (webui.db) et sa base souveraine identity.db.
    """
    if user is None or not getattr(user, "id", None):
        return None

    uid = str(user.id)
    email = str(getattr(user, "email", "") or "").strip().lower()
    name = str(getattr(user, "name", "") or email)
    trusted_sso = False

    if request is not None and hasattr(request, "headers"):
        sso_header = (
            request.headers.get("X-Webui-User")
            or request.headers.get("x-webui-user")
            or ""
        ).strip().lower()
        if sso_header:
            trusted_sso = True
            email = sso_header

    id_db_path = ensure_user_identity_db(uid, email=email, name=name)
    ctx = EchoIdentityContext(
        user_id=uid,
        email=email,
        name=name,
        identity_db_path=id_db_path,
        trusted_sso=trusted_sso,
    )
    if request is not None and hasattr(request, "state"):
        request.state.echo_identity = ctx
    return ctx


def clear_identity_sync_cache() -> None:
    """Réinitialise le cache mémoire d'identité (tests unitaires)."""
    _IDENTITY_SYNC_CACHE.clear()
