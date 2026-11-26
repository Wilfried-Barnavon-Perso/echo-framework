# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - CORE SECURITY & SSO
VERSION : 6.22.1.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.22.1.1: Lecture dynamique .secrets/.env (get_instance_secret) et vérification temps constant hmac.compare_digest sur X-Echo-Sso-Secret.
# 6.18.23.1: Ajout du helper centralisé open_sqlite_db (PRAGMA journal_mode=WAL, synchronous=NORMAL, busy_timeout et fermeture garantie) et bascule de check_sso_auth.
# 5.226.18.0: Alignement sur le versioning global ECHO.
from contextlib import contextmanager
import hmac
import os
import sqlite3
import time
from flask import request, session  # pyright: ignore[reportMissingImports]
from core.config import (
    DOCKER_AVAILABLE,
    HOST_GATEWAY,
    SECRETS_DIR,
    WEBUI_DB_PATH,
    app,
    paramiko,
    parse_env_content,
    read_host_env_dict,
)
from core.settings_io import get_auth_settings

__all__ = [
    'open_sqlite_db',
    'parse_env_content',
    'read_host_env_dict',
    'get_instance_secret',
    'get_admin_settings_crypto_key',
    'generate_complex_temp_password',
    'check_sso_auth',
    'change_system_password',
]


def get_instance_secret(key_name: str) -> str:
    """Lit dynamiquement un secret d'instance depuis /app/secrets/.env (repli env ou legacy)."""
    env_map = read_host_env_dict()
    val = (env_map.get(key_name) or os.environ.get(key_name, "")).strip()
    if not val and key_name == "ECHO_ADMIN_PASSWORD":
        legacy_file = os.path.join(SECRETS_DIR, ".owui-admin-secret")
        if os.path.isfile(legacy_file):
            try:
                with open(legacy_file, "r", encoding="utf-8", errors="ignore") as f_sec:
                    val = f_sec.read().strip()
            except OSError:
                pass
    return val


def get_admin_settings_crypto_key() -> str:
    """Retourne la clé maître utilisée pour chiffrer les secrets locaux de settings.json."""
    return (
        get_instance_secret("ECHO_BACKUP_ENCRYPTION_KEY")
        or get_instance_secret("ECHO_SSO_SECRET")
    )


@contextmanager
def open_sqlite_db(
    db_path: str,
    *,
    readonly: bool = False,
    timeout: float = 10.0,
    row_factory=None,
    auto_commit: bool = True,
):
    """Ouvre une connexion SQLite durcie (WAL/NORMAL/busy_timeout) avec fermeture garantie."""
    if readonly:
        uri_path = os.path.abspath(db_path).replace("\\", "/")
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
        if not readonly:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        if row_factory is not None:
            conn.row_factory = row_factory
        yield conn
        if not readonly and auto_commit:
            conn.commit()
    except Exception:
        if not readonly and auto_commit:
            conn.rollback()
        raise
    finally:
        conn.close()

def generate_complex_temp_password():
    settings = get_auth_settings()
    import secrets, string
    min_len = int(settings.get("pwd_min_length", 12))
    pwd = []
    if settings.get("pwd_require_upper", True): pwd.append(secrets.choice(string.ascii_uppercase))
    if settings.get("pwd_require_lower", True): pwd.append(secrets.choice(string.ascii_lowercase))
    if settings.get("pwd_require_digit", True): pwd.append(secrets.choice(string.digits))
    if settings.get("pwd_require_special", True): pwd.append(secrets.choice("!@#$%^&*"))
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while len(pwd) < min_len:
        pwd.append(secrets.choice(alphabet))
    secrets.SystemRandom().shuffle(pwd)
    return ''.join(pwd)

@app.before_request
def check_sso_auth():
    if session.get('logged_in'):
        return

    # Si le WAF n'est pas configuré, on laisse Flask poursuivre vers la page de login locale
    expected_secret = get_instance_secret("ECHO_SSO_SECRET")
    if not expected_secret:
        return 

    # DEROGATION : Fallback SSH/Local Network (Vérification de l'absence du reverse proxy)
    # Si le header X-Real-IP (ou X-Forwarded-For) n'existe pas, la requête ne vient pas de BunkerWeb.
    # On laisse Flask poursuivre vers la mire de login SSH (HTML_LOGIN).
    if not request.headers.get("X-Real-IP") and not request.headers.get("X-Forwarded-For"):
        return

    webui_user = request.headers.get("X-Webui-User")
    sso_secret = request.headers.get("X-Echo-Sso-Secret", "").strip()

    if not sso_secret or not hmac.compare_digest(sso_secret, expected_secret):
        return "<h1>Accès Refusé</h1><p>Absence de validation WAF ou discordance de la clé SSO.</p>", 401
    
    if not webui_user:
        return "<h1>Accès Refusé</h1><p>Identité utilisateur manquante.</p>", 401

    try:
        # Vérification stricte des droits Admin
        with open_sqlite_db(WEBUI_DB_PATH, readonly=True) as conn:
            user_data = conn.execute("SELECT role FROM user WHERE email = ?", (webui_user,)).fetchone()
        
        if user_data and user_data[0] == 'admin':
            session['logged_in'] = True
            session['username'] = webui_user
        else:
            return "<h1>Accès Interdit</h1><p>Privilèges administrateur requis pour cette console.</p>", 403
    except Exception as e:
        print(f"[ECHO-AUTH] Erreur lecture DB SSO : {e}")
        return "<h1>Erreur Interne</h1>", 500

def change_system_password(username, current_pwd, new_pwd):
    if not DOCKER_AVAILABLE: return False, "Module SSH absent"
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=username, password=current_pwd, timeout=10)
        chan = ssh.invoke_shell(); time.sleep(1)
        chan.send('passwd\n'); time.sleep(1)
        chan.send(f'{current_pwd}\n'); time.sleep(0.5)
        chan.send(f'{new_pwd}\n'); time.sleep(0.5)
        chan.send(f'{new_pwd}\n'); time.sleep(1)
        out = chan.recv(4096).decode('utf-8', errors='ignore'); ssh.close()
        if "updated successfully" in out or "mis à jour avec succès" in out: return True, "Succès"
        return False, "Refus système"
    except Exception as e: return False, str(e)
