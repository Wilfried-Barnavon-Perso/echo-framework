# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - CORE SETTINGS I/O
VERSION : 6.22.1.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.22.1.1: Chiffrement AEAD de sftp_pass dans settings.json (chmod 0600) avec préservation si champ soumis vide.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import configparser
import copy
import datetime
import json
import os
import subprocess
import time
from core.config import (
    AUTH_SETTINGS_PATH,
    DEFAULT_AUTH_SETTINGS,
    DEFAULT_BACKUP_CONFIG,
    DEFAULT_MAINT_CONFIG,
    HAS_ECHO_LIBS,
    MAINT_HISTORY_FILE,
    OWUI_DATA_ROOT,
    RCLONE_CONF_PATH,
    SETTINGS_FILE,
    read_host_env_dict,
)

if HAS_ECHO_LIBS:
    from echo_crypto import (
        decrypt_admin_secret,
        encrypt_admin_secret,
        is_encrypted_payload,
    )

__all__ = [
    'get_auth_settings',
    'save_auth_settings',
    'load_maint_config',
    'save_maint_config',
    'load_maint_history',
    'save_maint_report',
    'load_settings',
    'save_settings',
    'get_rclone_remotes',
]

def get_auth_settings():
    if not os.path.exists(AUTH_SETTINGS_PATH): return DEFAULT_AUTH_SETTINGS
    try:
        with open(AUTH_SETTINGS_PATH, 'r') as f:
            s = json.load(f)
            merged = copy.deepcopy(DEFAULT_AUTH_SETTINGS)
            merged.update(s)
            return merged
    except: return DEFAULT_AUTH_SETTINGS

def save_auth_settings(data):
    try:
        os.makedirs(os.path.dirname(AUTH_SETTINGS_PATH), exist_ok=True)
        with open(AUTH_SETTINGS_PATH, 'w') as f:
            json.dump(data, f)
        return True
    except: return False

def load_maint_config() -> dict:
    """
    Charge la config de maintenance depuis le fichier JSON.
    Utilise deepcopy pour éviter toute mutation silencieuse du dict DEFAULT_MAINT_CONFIG
    via des références partagées lors d'écritures ultérieures sur les sous-dicts (retention, etc.).
    """
    c = copy.deepcopy(DEFAULT_MAINT_CONFIG)
    m_file = os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")
    if os.path.exists(m_file):
        try:
            with open(m_file, 'r') as f:
                saved = json.load(f)
                # Fusion profonde des sous-dicts pour ne pas écraser les clés absentes du fichier
                for key, val in saved.items():
                    if isinstance(val, dict) and isinstance(c.get(key), dict):
                        c[key].update(val)
                    else:
                        c[key] = val
        except Exception as e:
            print(f"[ECHO-MAINT] ⚠️ Impossible de lire la config maintenance : {e}")
    return c

def save_maint_config(c: dict) -> bool:
    """Sauvegarde la config de maintenance. Retourne True si OK, False si erreur."""
    try:
        target = os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w') as f:
            json.dump(c, f, indent=4)
        return True
    except Exception as e:
        print(f"[ECHO-MAINT] ❌ Impossible de sauvegarder la config maintenance : {e}")
        return False

def load_maint_history():
    if os.path.exists(MAINT_HISTORY_FILE):
        try:
            with open(MAINT_HISTORY_FILE, 'r') as f: return json.load(f)
        except Exception: pass
    return []

def save_maint_report(report_str):
    history = load_maint_history()
    new_entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "report": report_str
    }
    history.insert(0, new_entry)
    
    # Auto-purge de l'historique (Rétention 1 an / 365 entrées max)
    cutoff = time.time() - (365 * 86400)
    history = [h for h in history if time.mktime(time.strptime(h["timestamp"], "%Y-%m-%d %H:%M:%S")) > cutoff]
    
    try:
        with open(MAINT_HISTORY_FILE, 'w') as f: json.dump(history[:500], f, indent=4)
    except Exception: pass

def _get_settings_crypto_key() -> str:
    """Résout la clé de chiffrement locale des paramètres Admin sans dépendance circulaire."""
    env_map = read_host_env_dict()
    return (
        env_map.get("ECHO_BACKUP_ENCRYPTION_KEY")
        or env_map.get("ECHO_SSO_SECRET")
        or os.environ.get("ECHO_SSO_SECRET", "").strip()
    )


def load_settings():
    c = DEFAULT_BACKUP_CONFIG.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                c.update(json.loads(f.read()))
        except Exception:
            pass
    if HAS_ECHO_LIBS and is_encrypted_payload(c.get("sftp_pass", "")):
        crypto_key = _get_settings_crypto_key()
        if crypto_key:
            try:
                c["sftp_pass"] = decrypt_admin_secret(c["sftp_pass"], crypto_key, "sftp_pass")
            except Exception:
                c["sftp_pass"] = ""
        else:
            c["sftp_pass"] = ""
    # Auto-Heal : le formulaire local ne comporte pas de switch auto_cleanup séparé,
    # la rétention ("Garder X" / "Max X jours") doit toujours rester active.
    c["auto_cleanup"] = True
    return c


def save_settings(new_s):
    c = load_settings()
    prev_pass = c.get("sftp_pass", "")
    c.update(new_s)
    if "sftp_pass" in new_s and not (new_s.get("sftp_pass") or "").strip():
        c["sftp_pass"] = prev_pass

    to_write = dict(c)
    if HAS_ECHO_LIBS and to_write.get("sftp_pass") and not is_encrypted_payload(to_write["sftp_pass"]):
        crypto_key = _get_settings_crypto_key()
        if not crypto_key:
            raise RuntimeError("Clé de chiffrement absente pour sécuriser sftp_pass.")
        to_write["sftp_pass"] = encrypt_admin_secret(to_write["sftp_pass"], crypto_key, "sftp_pass")

    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(to_write, f, indent=4)
    try:
        os.chmod(SETTINGS_FILE, 0o600)
    except OSError:
        pass
    from services.maintenance_backup import update_system_schedules
    update_system_schedules()

def get_rclone_remotes():
    """Retrieve list of available remotes directly from rclone config file or command."""
    remotes = []
    if os.path.exists(RCLONE_CONF_PATH):
        try:
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read(RCLONE_CONF_PATH, encoding='utf-8')
            remotes = [s.strip() for s in cfg.sections() if s.strip()]
            if remotes:
                return remotes
            res = subprocess.run(['rclone', '--config', RCLONE_CONF_PATH, 'listremotes'], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                remotes = [r.strip().replace(':', '') for r in res.stdout.splitlines() if r.strip()]
        except Exception: pass
    return remotes
