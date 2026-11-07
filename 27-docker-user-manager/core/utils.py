# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER - CORE UTILITIES
VERSION : 5.236.5.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.236.5.1: Création des utilitaires de calcul de taille et de lecture de auth-settings.json.

import json
import os
from core.config import AUTH_SETTINGS_PATH, DEFAULT_AUTH_SETTINGS

__all__ = [
    "_format_bytes",
    "_get_tree_size",
    "get_echo_version",
    "get_auth_settings",
]


def _format_bytes(size: int) -> str:
    """Formate un nombre d'octets en unité lisible (B, KB, MB, GB)."""
    val = float(max(0, int(size or 0)))
    for unit in ["B", "KB", "MB", "GB"]:
        if val < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} TB"


def _get_tree_size(path: str) -> int:
    """Calcule récursivement la taille en octets d'un dossier (sans suivre les liens symboliques)."""
    total = 0
    if not path or not os.path.exists(path):
        return 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                if not os.path.islink(fp):
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
    except OSError:
        pass
    return total


def get_echo_version() -> str:
    """Lit la version globale du framework ECHO."""
    try:
        if os.path.exists("/app/ECHO_VERSION"):
            with open("/app/ECHO_VERSION", "r", encoding="utf-8") as f:
                return f"v{f.read().strip()}"
    except OSError:
        pass
    return "v5.236+"


def get_auth_settings() -> dict:
    """Charge la politique de sécurité SSO depuis auth-settings.json avec repli sur DEFAULT_AUTH_SETTINGS."""
    cfg = DEFAULT_AUTH_SETTINGS.copy()
    if os.path.exists(AUTH_SETTINGS_PATH):
        try:
            with open(AUTH_SETTINGS_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except Exception:
            pass
    return cfg
