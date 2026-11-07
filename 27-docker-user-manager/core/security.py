# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER - CORE SECURITY & SSO
VERSION : 5.236.5.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.236.5.1: Barrière Zero-Trust SSO (HMAC X-Echo-Sso-Secret, X-Webui-User) et validation Argon2id/TOTP.

from contextlib import closing
import hmac
import os
import re
import sqlite3
from flask import g, request  # pyright: ignore[reportMissingImports]
from passlib.hash import argon2  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]
from core.config import AUTH_DB_PATH, WEBUI_DB_PATH, app
from core.utils import get_auth_settings

__all__ = [
    "check_user_sso_auth",
    "verify_user_password",
    "hash_user_password",
    "verify_user_totp",
    "validate_password_policy",
]


@app.before_request
def check_user_sso_auth():
    """Middleware de sécurité Zero-Trust validant l'identité SSO injectée par BunkerWeb."""
    if request.path == "/health":
        return None

    expected_secret = os.environ.get("ECHO_SSO_SECRET", "").strip()
    if not expected_secret:
        return (
            "<h1>Mode Public Requis</h1>"
            "<p>Le portail Utilisateur ECHO nécessite l'activation du mode public sécurisé (BunkerWeb / SSO).</p>",
            403,
        )

    sso_secret = (request.headers.get("X-Echo-Sso-Secret") or "").strip()
    if not sso_secret or not hmac.compare_digest(sso_secret, expected_secret):
        return (
            "<h1>Accès Refusé</h1>"
            "<p>Validation WAF/SSO absente ou invalide.</p>",
            401,
        )

    webui_user = (request.headers.get("X-Webui-User") or "").strip()
    if not webui_user:
        return (
            "<h1>Accès Refusé</h1>"
            "<p>Identité utilisateur manquante.</p>",
            401,
        )

    if not os.path.exists(AUTH_DB_PATH):
        return (
            "<h1>Erreur Interne</h1>"
            "<p>Base d'authentification indisponible.</p>",
            503,
        )

    try:
        with closing(sqlite3.connect(f"file:{AUTH_DB_PATH}?mode=ro", uri=True, timeout=5.0)) as conn:
            conn.row_factory = sqlite3.Row
            auth_user = conn.execute(
                "SELECT email, name, totp_secret, must_enroll, last_enrollment FROM users WHERE email = ?",
                (webui_user,),
            ).fetchone()
            if not auth_user:
                return (
                    "<h1>Session Invalide</h1>"
                    "<p>Ce compte n'existe plus dans l'Auth Manager.</p>",
                    401,
                )
            g.user_email = str(auth_user["email"])
            g.auth_name = str(auth_user["name"] or "")
            g.has_totp = bool(auth_user["totp_secret"])
            g.last_enrollment = int(auth_user["last_enrollment"] or 0)
            g.current_session_id = (request.cookies.get("echo_auth_session") or "").strip()
    except Exception:
        return (
            "<h1>Erreur Interne</h1>"
            "<p>Impossible de vérifier l'identité SSO.</p>",
            500,
        )

    g.user_id = None
    g.webui_name = ""
    g.user_role = "user"
    if os.path.exists(WEBUI_DB_PATH):
        try:
            with closing(sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True, timeout=5.0)) as w_conn:
                row = w_conn.execute(
                    "SELECT id, name, role FROM user WHERE email = ?",
                    (g.user_email,),
                ).fetchone()
                if row:
                    g.user_id = str(row[0])
                    g.webui_name = str(row[1] or "")
                    g.user_role = str(row[2] or "user")
        except Exception:
            pass

    g.display_name = g.webui_name or g.auth_name or g.user_email.split("@")[0]
    return None


def verify_user_password(email: str, plain_password: str) -> bool:
    """Vérifie le mot de passe Argon2id de l'utilisateur dans auth.db."""
    if not email or not plain_password or not os.path.exists(AUTH_DB_PATH):
        return False
    try:
        with closing(sqlite3.connect(f"file:{AUTH_DB_PATH}?mode=ro", uri=True, timeout=5.0)) as conn:
            row = conn.execute(
                "SELECT pass_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            if not row or not row[0]:
                return False
            return bool(argon2.verify(plain_password, row[0]))
    except Exception:
        return False


def hash_user_password(plain_password: str) -> str:
    """Génère le condensat Argon2id d'un mot de passe."""
    return argon2.hash(plain_password)


def verify_user_totp(email: str, totp_code: str) -> bool:
    """Vérifie le code MFA TOTP à 6 chiffres selon la fenêtre de tolérance configurée."""
    clean_code = (totp_code or "").strip().replace(" ", "")
    if not email or not clean_code or not os.path.exists(AUTH_DB_PATH):
        return False
    try:
        settings = get_auth_settings()
        valid_window = int(settings.get("totp_tolerance", 1))
        with closing(sqlite3.connect(f"file:{AUTH_DB_PATH}?mode=ro", uri=True, timeout=5.0)) as conn:
            row = conn.execute(
                "SELECT totp_secret FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            if not row or not row[0]:
                return False
            totp = pyotp.TOTP(row[0])
            return bool(totp.verify(clean_code, valid_window=valid_window))
    except Exception:
        return False


def validate_password_policy(new_pwd: str, settings: dict | None = None) -> tuple[bool, str]:
    """Valide un nouveau mot de passe selon les règles définies dans auth-settings.json."""
    pwd = new_pwd or ""
    cfg = settings if isinstance(settings, dict) else get_auth_settings()
    min_len = int(cfg.get("pwd_min_length", 12))
    if len(pwd) < min_len:
        return False, f"Le mot de passe doit contenir au moins {min_len} caractères."
    if cfg.get("pwd_require_upper", True) and not re.search(r"[A-Z]", pwd):
        return False, "Le mot de passe doit contenir au moins une lettre majuscule."
    if cfg.get("pwd_require_lower", True) and not re.search(r"[a-z]", pwd):
        return False, "Le mot de passe doit contenir au moins une lettre minuscule."
    if cfg.get("pwd_require_digit", True) and not re.search(r"\d", pwd):
        return False, "Le mot de passe doit contenir au moins un chiffre."
    if cfg.get("pwd_require_special", True) and not re.search(r"[^A-Za-z0-9]", pwd):
        return False, "Le mot de passe doit contenir au moins un caractère spécial."
    return True, "OK"
