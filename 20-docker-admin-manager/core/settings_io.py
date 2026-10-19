# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - CORE SETTINGS I/O
VERSION : 5.226.18.0
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
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
    MAINT_HISTORY_FILE,
    OWUI_DATA_ROOT,
    RCLONE_CONF_PATH,
    SETTINGS_FILE,
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

def load_settings():
    c = DEFAULT_BACKUP_CONFIG.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                c.update(json.loads(f.read()))
        except Exception:
            pass
    # Auto-Heal : le formulaire local ne comporte pas de switch auto_cleanup séparé,
    # la rétention ("Garder X" / "Max X jours") doit toujours rester active.
    c["auto_cleanup"] = True
    return c

def save_settings(new_s):
    c = load_settings(); c.update(new_s)
    with open(SETTINGS_FILE, 'w') as f: json.dump(c, f, indent=4)
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
