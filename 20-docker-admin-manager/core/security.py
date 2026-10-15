# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - CORE SECURITY & SSO
VERSION : 5.136
================================================================================
"""
import os
import sqlite3
import time
from flask import request, session  # pyright: ignore[reportMissingImports]
from core.config import (
    DOCKER_AVAILABLE,
    HOST_GATEWAY,
    WEBUI_DB_PATH,
    app,
    paramiko,
)
from core.settings_io import get_auth_settings

__all__ = [
    'generate_complex_temp_password',
    'check_sso_auth',
    'change_system_password',
]

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
    expected_secret = os.environ.get("ECHO_SSO_SECRET", "")
    if not expected_secret:
        return 

    # DEROGATION : Fallback SSH/Local Network (Vérification de l'absence du reverse proxy)
    # Si le header X-Real-IP (ou X-Forwarded-For) n'existe pas, la requête ne vient pas de BunkerWeb.
    # On laisse Flask poursuivre vers la mire de login SSH (HTML_LOGIN).
    if not request.headers.get("X-Real-IP") and not request.headers.get("X-Forwarded-For"):
        return

    webui_user = request.headers.get("X-Webui-User")
    sso_secret = request.headers.get("X-Echo-Sso-Secret", "").strip()
    expected_secret = expected_secret.strip()
    
    if sso_secret != expected_secret:
        return "<h1>Accès Refusé</h1><p>Absence de validation WAF ou discordance de la clé SSO.</p>", 401
    
    if not webui_user:
        return "<h1>Accès Refusé</h1><p>Identité utilisateur manquante.</p>", 401

    try:
        # Vérification stricte des droits Admin
        conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
        user_data = conn.execute("SELECT role FROM user WHERE email = ?", (webui_user,)).fetchone()
        conn.close()
        
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
