# -*- coding: utf-8 -*-
"""
title: ECHO Echo Paths
author: Wilfried BARNAVON
version: 5.231.20.1
description: Résolution des chemins absolus du système.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.20.1: Standardisation multi-user/concurrence : ajout de sanitize_echo_id, get_codex_lock, get_user_skill_lock et suffixe UUID anti-collision dans generate_echo_file_id.
# 5.230.0.1: Factorisation SSOT de normalize_codex_rel_path (avec unquote défensif) et check_codex_file_exists pour les outils, filtres et dépôts Codex.
# 5.226.18.1: Ajout du registre de verrous asyncio (_CHAT_CLONE_LOCKS, get_chat_clone_lock, wait_for_chat_clone).
# 5.226.18.0: Alignement sur le versioning global ECHO.
import os
import time
import glob
import uuid
import asyncio
from urllib.parse import unquote
from typing import Optional
from echo_constants import (
    CODEX_DIR_NAME,
    ECHO_CODEX_WORKSPACES,
    ECHO_GLOBAL_DOMAINS,
    ECHO_SESSION_DOMAINS,
    ECHO_USERS_ROOT,
    ECHO_UPLOADS_TRANSIT_DIR,
    ECHO_VERSION_PATH,
)

# Registre en mémoire des verrous asyncio de clonage de session en cours
_CHAT_CLONE_LOCKS: dict[str, asyncio.Lock] = {}
_CODEX_LOCKS: dict[str, asyncio.Lock] = {}
_SKILL_LOCKS: dict[str, asyncio.Lock] = {}


def sanitize_echo_id(raw_id: str, fallback: str = "default") -> str:
    """Nettoie un identifiant (user_id, chat_id, skill_id, etc.) pour prévenir toute traversée de chemin."""
    cleaned = "".join(x for x in str(raw_id or "") if x.isalnum() or x in "-_")
    return cleaned if cleaned else fallback


def get_codex_lock(user_id: str, chat_id: str) -> asyncio.Lock:
    """Retourne ou crée le verrou asyncio exclusif protégeant les dépôts Git Codex d'une session (user_id, chat_id)."""
    key = f"{sanitize_echo_id(user_id)}:{sanitize_echo_id(chat_id)}"
    if key not in _CODEX_LOCKS:
        _CODEX_LOCKS[key] = asyncio.Lock()
    return _CODEX_LOCKS[key]


def get_user_skill_lock(user_id: str) -> asyncio.Lock:
    """Retourne ou crée le verrou asyncio protégeant les opérations disque sur les skills d'un utilisateur."""
    key = sanitize_echo_id(user_id)
    if key not in _SKILL_LOCKS:
        _SKILL_LOCKS[key] = asyncio.Lock()
    return _SKILL_LOCKS[key]


def get_chat_clone_lock(chat_id: str) -> asyncio.Lock:
    """Retourne ou crée un verrou asyncio pour synchroniser la création d'un chat cloné."""
    if chat_id not in _CHAT_CLONE_LOCKS:
        _CHAT_CLONE_LOCKS[chat_id] = asyncio.Lock()
    return _CHAT_CLONE_LOCKS[chat_id]


async def wait_for_chat_clone(chat_id: str, timeout: float = 10.0) -> None:
    """Attend la fin d'un clonage éventuel sur ce chat_id avant l'exécution des filtres Inlet."""
    lock = _CHAT_CLONE_LOCKS.get(chat_id)
    if lock and lock.locked():
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            lock.release()
        except Exception:
            pass


def get_echo_global_path(user_id: str, domain: str) -> str:
    """Retourne le chemin standardisé pour un domaine global (ex: skills)."""
    if domain not in ECHO_GLOBAL_DOMAINS:
        raise ValueError(f"[ECHO] Domaine global invalide : {domain}")
    safe_uid = sanitize_echo_id(user_id)
    return os.path.join(ECHO_USERS_ROOT, safe_uid, domain)


def get_echo_session_path(user_id: str, chat_id: str, domain: str) -> str:
    """Retourne le chemin standardisé pour un domaine du conteneur de session."""
    if domain not in ECHO_SESSION_DOMAINS:
        raise ValueError(f"[ECHO] Domaine de session invalide : {domain}")

    safe_uid = sanitize_echo_id(user_id)
    safe_cid = sanitize_echo_id(chat_id)
    base_dir = os.path.join(ECHO_USERS_ROOT, safe_uid, "chats", safe_cid)

    # La base SQLite se nomme session.db à la racine du conteneur
    if domain == "db":
        return os.path.join(base_dir, "session.db")

    return os.path.join(base_dir, domain)


def resolve_upload_file_path(
    user_id: str,
    file_id: str,
    uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR,
    chat_id: Optional[str] = None,
) -> Optional[str]:
    if not file_id:
        return None
    file_id = file_id.strip()
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        if chat_id:
            user_vault = get_echo_session_path(user_id, chat_id, "files")
            pattern = os.path.join(user_vault, f"{file_id}_*")
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
        else:
            # Fallback de sécurité si appelé hors contexte chat_id
            user_chats = get_echo_global_path(user_id, "chats")
            pattern = os.path.join(user_chats, "*", "files", f"{file_id}_*")
            matches = glob.glob(pattern)
            if matches:
                return matches[0]

            # Fallback Ultime : Dossier global des fichiers
            user_global_files = get_echo_global_path(user_id, "files")
            pattern_global = os.path.join(user_global_files, f"{file_id}_*")
            matches_global = glob.glob(pattern_global)
            if matches_global:
                return matches_global[0]

    pattern = os.path.join(uploads_dir, f"{file_id}_*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def generate_echo_file_id(user_id: str, chat_id: str) -> str:
    ts = int(time.time() * 1000)
    uid_s = sanitize_echo_id(user_id)
    cid_s = sanitize_echo_id(chat_id)
    return f"U_{uid_s}_C_{cid_s}_T_{ts}_{uuid.uuid4().hex[:6]}"


def get_echo_version() -> str:
    try:
        if os.path.exists(ECHO_VERSION_PATH):
            with open(ECHO_VERSION_PATH, "r") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def normalize_codex_rel_path(
    relative_path: str,
    workspace: str = "",
    repo_path: str = "",
) -> Optional[str]:
    """Normalise et sécurise un chemin relatif Codex (décodage URL défensif, retrait des préfixes redondants et anti-traversal)."""
    clean_path = (relative_path or "").replace("\\", "/").strip("/")
    if "%" in clean_path and (not repo_path or not os.path.exists(os.path.join(repo_path, clean_path))):
        clean_path = unquote(clean_path).replace("\\", "/").strip("/")
    if not clean_path or ".." in clean_path.split("/") or clean_path.startswith("/"):
        return None
    if workspace:
        prefix_full = f"{CODEX_DIR_NAME}/{workspace}/"
        prefix_ws = f"{workspace}/"
        if clean_path.startswith(prefix_full):
            clean_path = clean_path[len(prefix_full):]
        elif clean_path.startswith(prefix_ws):
            clean_path = clean_path[len(prefix_ws):]
    return clean_path or None


def check_codex_file_exists(
    user_id: str,
    chat_id: str,
    workspace: str,
    relative_path: str,
) -> bool:
    """Vérifie l'existence physique d'un fichier Codex sur disque (agnostique à Git, compatible sandbox)."""
    try:
        if not user_id or not chat_id or workspace not in ECHO_CODEX_WORKSPACES:
            return False
        base_codex = get_echo_session_path(user_id, chat_id, CODEX_DIR_NAME)
        ws_dir = os.path.join(base_codex, workspace)
        clean_path = normalize_codex_rel_path(relative_path, workspace=workspace, repo_path=ws_dir)
        if not clean_path:
            return False
        return os.path.isfile(os.path.join(ws_dir, clean_path))
    except Exception:
        return False

