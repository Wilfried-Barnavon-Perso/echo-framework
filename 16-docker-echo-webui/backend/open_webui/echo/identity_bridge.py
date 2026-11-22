# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO V6 Canonical Identity Bridge
author: Wilfried BARNAVON
version: 6.18.9.1
description: Pont déterministe Email SSO (X-Webui-User) <-> User UUID (webui.db) <-> identity.db (Schéma v2).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.9.1: SSOT webui.db (user.gender) sans duplication identity.db, migration douce unique et resolve_profile_gender_and_avatar.
# 6.7.16.1: Ajout du cache mémoire _CANONICAL_GENDER_CACHE et déport asyncio.to_thread sur cache miss dans resolve_canonical_user.
# 6.6.3.1: Résolution X-Webui-Gender, persistance canonical_gender et bascule dynamique d'avatar M/F/X sans écraser les avatars personnalisés.
# 6.6.2.1: Stabilisation de la résolution identitaire SSO et synchronisation identity.db.
# 6.1.0.1: Création du pont d'identité canonique X-Webui-User <-> webui.db UUID <-> identity.db (Schéma v2).

from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from open_webui.echo.runtime import ensure_echo_sys_path, get_echo_plugin_root

log = logging.getLogger(__name__)

# Cache mémoire (user_id -> (email, name)) évitant de réécrire identity.db à chaque requête HTTP
_IDENTITY_SYNC_CACHE: dict[str, tuple[str, str]] = {}
_DEFAULT_USER_AVATAR_CACHE: dict[str, str] = {}
_AVATAR_CHECKED_USERS: dict[str, str] = {}

_GENDER_AVATAR_FILES: dict[str, str] = {
    "M": "user-echo-homme.png",
    "F": "user-echo-femme.png",
    "X": "user-echo-neutre.png",
}


def normalize_gender(value: Any) -> str:
    """Normalise une valeur de genre vers 'M', 'F' ou 'X' ('X' par défaut)."""
    norm = str(value or "").strip().upper()
    return norm if norm in ("M", "F", "X") else "X"


def get_default_user_avatar_data_uri(gender: str = "X") -> str | None:
    """Charge et met en cache le Data-URI base64 de l'avatar utilisateur ECHO par défaut selon le genre."""
    norm_gender = normalize_gender(gender)
    if norm_gender in _DEFAULT_USER_AVATAR_CACHE:
        return _DEFAULT_USER_AVATAR_CACHE[norm_gender]

    filename = _GENDER_AVATAR_FILES[norm_gender]
    root = get_echo_plugin_root()
    candidates = [
        root / "echo-images" / filename,
        root / "_assets" / "images" / filename,
        Path("/app/backend/open_webui/static/user.png"),
        Path("/app/build/static/user.png"),
    ]
    try:
        from open_webui.config import STATIC_DIR
        candidates.append(Path(STATIC_DIR) / "user.png")
    except Exception:
        pass

    for img_path in candidates:
        try:
            if img_path.is_file():
                b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
                uri = f"data:image/png;base64,{b64}"
                _DEFAULT_USER_AVATAR_CACHE[norm_gender] = uri
                return uri
        except Exception as exc:
            log.debug("[ECHO-IDENTITY] Lecture avatar par défaut ignorée (%s): %s", img_path, exc)
    return None


def get_all_default_user_avatar_data_uris() -> set[str]:
    """Retourne l'ensemble des Data-URIs des avatars ECHO par défaut (M, F, X)."""
    uris: set[str] = set()
    for g in _GENDER_AVATAR_FILES:
        uri = get_default_user_avatar_data_uri(g)
        if uri:
            uris.add(uri)
    return uris


def is_default_echo_avatar_url(url: str) -> bool:
    """Indique si l'URL correspond à un avatar générique ou à un avatar ECHO par défaut (M/F/X)."""
    clean = str(url or "").strip()
    if not clean or clean.startswith("/api/v1/users/"):
        return True
    base_path = clean.split("?", 1)[0].split("#", 1)[0]
    if base_path == "/user.png" or base_path.endswith("/user.png"):
        return True
    for fname in _GENDER_AVATAR_FILES.values():
        if base_path == f"/static/{fname}" or base_path.endswith(f"/static/{fname}"):
            return True
    return clean in get_all_default_user_avatar_data_uris()


def resolve_profile_gender_and_avatar(
    gender: Any,
    profile_image_url: str | None = None,
    *,
    fallback_gender: Any = "X",
    user_id: str | None = None,
) -> tuple[str, str | None]:
    """Résout le genre normalisé et l'avatar par défaut associé si l'URL courante est générique."""
    if gender is not None and str(gender).strip() != "":
        norm_gender = normalize_gender(gender)
    else:
        norm_gender = normalize_gender(fallback_gender)
    resolved_avatar = profile_image_url
    if profile_image_url is not None and is_default_echo_avatar_url(profile_image_url):
        default_uri = get_default_user_avatar_data_uri(norm_gender)
        if default_uri:
            resolved_avatar = default_uri
    if user_id:
        _AVATAR_CHECKED_USERS[str(user_id)] = norm_gender
    return (norm_gender, resolved_avatar)


async def ensure_user_default_avatar(user: Any, gender: str = "X") -> str | None:
    """Injecte le Data-URI de l'avatar ECHO genré si l'utilisateur utilise encore un avatar par défaut."""
    if user is None or not getattr(user, "id", None):
        return None
    uid = str(user.id)
    norm_gender = normalize_gender(gender)
    if _AVATAR_CHECKED_USERS.get(uid) == norm_gender:
        return getattr(user, "profile_image_url", None)

    current_url = str(getattr(user, "profile_image_url", None) or "").strip()
    if not is_default_echo_avatar_url(current_url):
        _AVATAR_CHECKED_USERS[uid] = norm_gender
        return current_url

    avatar_uri = get_default_user_avatar_data_uri(norm_gender)
    if not avatar_uri:
        return current_url or None
    if avatar_uri == current_url:
        _AVATAR_CHECKED_USERS[uid] = norm_gender
        return current_url

    try:
        from open_webui.models.users import Users
        await Users.update_user_profile_image_url_by_id(uid, avatar_uri)
        try:
            setattr(user, "profile_image_url", avatar_uri)
        except Exception:
            pass
        _AVATAR_CHECKED_USERS[uid] = norm_gender
        return avatar_uri
    except Exception as exc:
        log.warning("[ECHO-IDENTITY] Échec mise à jour avatar pour %s: %s", uid, exc)
        return current_url or None


@dataclass(frozen=True)
class EchoIdentityContext:
    user_id: str
    email: str
    name: str
    identity_db_path: str
    trusted_sso: bool
    gender: str = "X"


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
    norm_name = str(name or "").strip()

    cached = _IDENTITY_SYNC_CACHE.get(safe_uid)
    if cached == (norm_email, norm_name) and os.path.isfile(id_db_path):
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
        if norm_name:
            sm.save_setting("canonical_name", norm_name)
        sm.save_setting("user_uuid", safe_uid)
    except Exception as exc:
        log.warning("[ECHO-IDENTITY] Échec écriture echo_settings dans identity.db pour %s: %s", safe_uid, exc)

    _IDENTITY_SYNC_CACHE[safe_uid] = (norm_email, norm_name)
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
    raw_header_gender = ""

    if request is not None and hasattr(request, "headers"):
        sso_header = (
            request.headers.get("X-Webui-User")
            or request.headers.get("x-webui-user")
            or ""
        ).strip().lower()
        if sso_header:
            trusted_sso = True
            email = sso_header
        raw_header_gender = (
            request.headers.get("X-Webui-Gender")
            or request.headers.get("x-webui-gender")
            or ""
        ).strip()

    ensure_echo_sys_path()
    from echo_paths import get_echo_identity_db_path, sanitize_echo_id

    safe_uid = sanitize_echo_id(uid)

    existing_gender = str(getattr(user, "gender", None) or "").strip().upper()
    if existing_gender in ("M", "F", "X"):
        norm_gender = existing_gender
    else:
        if raw_header_gender:
            norm_gender = normalize_gender(raw_header_gender)
        else:
            raw_fallback = "X"
            try:
                from echo_state_manager import EchoStateManager

                def _read_canonical_gender() -> str:
                    return (
                        EchoStateManager(user_id=safe_uid, chat_id=None).get_setting("canonical_gender")
                        or "X"
                    )

                raw_fallback = await asyncio.to_thread(_read_canonical_gender)
            except Exception as exc:
                log.debug("[ECHO-IDENTITY] Lecture canonical_gender ignorée pour %s: %s", uid, exc)
                raw_fallback = "X"
            norm_gender = normalize_gender(raw_fallback)

        try:
            from open_webui.models.users import Users

            await Users.update_user_by_id(uid, {"gender": norm_gender})
        except Exception as exc:
            log.warning("[ECHO-IDENTITY] Échec persistance gender dans webui.db pour %s: %s", uid, exc)
        try:
            setattr(user, "gender", norm_gender)
        except Exception:
            pass

    norm_name = name.strip()
    id_db_path = get_echo_identity_db_path(safe_uid)
    if _IDENTITY_SYNC_CACHE.get(safe_uid) != (email, norm_name) or not os.path.isfile(id_db_path):
        id_db_path = await asyncio.to_thread(
            ensure_user_identity_db, uid, email=email, name=name
        )
    await ensure_user_default_avatar(user, gender=norm_gender)
    ctx = EchoIdentityContext(
        user_id=uid,
        email=email,
        name=name,
        identity_db_path=id_db_path,
        trusted_sso=trusted_sso,
        gender=norm_gender,
    )
    if request is not None and hasattr(request, "state"):
        request.state.echo_identity = ctx
    return ctx


def clear_identity_sync_cache() -> None:
    """Réinitialise le cache mémoire d'identité (tests unitaires)."""
    _IDENTITY_SYNC_CACHE.clear()
    _AVATAR_CHECKED_USERS.clear()
