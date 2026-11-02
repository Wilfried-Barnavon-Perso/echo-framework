# -*- coding: utf-8 -*-
"""
title: ECHO Echo Paths
author: Wilfried BARNAVON
version: 5.231.49.1
description: Résolution des chemins absolus du système.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.49.1: Factorisation de extract_clean_filename et resolve_extended_file_path (résolution unifiée par ID, Nom et Workspace).
# 5.231.29.1: Alignement de format_codex_storage_path sur la constante SSOT CODEX_DEFAULT_WORKSPACE.
# 5.231.28.1: Ajout de format_codex_storage_path(relative_path, workspace) garantissant le format canonique codex/<workspace>/<filename>.
# 5.231.20.1: Standardisation multi-user/concurrence : ajout de sanitize_echo_id, get_codex_lock, get_user_skill_lock et suffixe UUID anti-collision dans generate_echo_file_id.
# 5.230.0.1: Factorisation SSOT de normalize_codex_rel_path (avec unquote défensif) et check_codex_file_exists pour les outils, filtres et dépôts Codex.
import os
import re
import time
import glob
import uuid
import asyncio
from urllib.parse import unquote
from typing import Optional
from echo_constants import (
    CODEX_DEFAULT_WORKSPACE,
    CODEX_DIR_NAME,
    CODEX_WORKSPACE_MAIN,
    CODEX_WORKSPACE_SANDBOX,
    ECHO_CODEX_WORKSPACES,
    ECHO_GLOBAL_DOMAINS,
    ECHO_SESSION_DOMAINS,
    ECHO_USERS_ROOT,
    ECHO_UPLOADS_TRANSIT_DIR,
    ECHO_VERSION_PATH,
)

_VAULT_PREFIX_RE = re.compile(
    r"^(?:U_.+_C_.+_T_\d+_[0-9a-fA-F]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32,})_(.+)$"
)


def extract_clean_filename(path_or_name: str) -> str:
    """Extrait le nom de fichier original en retirant les préfixes d'identifiants techniques du Vault (U_..._C_..._T_... ou UUID)."""
    base = os.path.basename((path_or_name or "").replace("\\", "/").strip("/"))
    if not base:
        return "fichier"
    match = _VAULT_PREFIX_RE.match(base)
    return match.group(1) if match else base


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


def _resolve_upload_by_filename(
    user_id: str,
    filename: str,
    uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR,
    chat_id: Optional[str] = None,
) -> Optional[str]:
    """Recherche un fichier dans le Vault d'uploads par son nom original (ex: 'photo.png' -> '*_photo.png')."""
    clean_name = os.path.basename((filename or "").replace("\\", "/").strip("/"))
    if not clean_name or clean_name in (".", ".."):
        return None
    escaped = glob.escape(clean_name)
    search_dirs = []
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        if chat_id:
            search_dirs.append(get_echo_session_path(user_id, chat_id, "files"))
        search_dirs.append(get_echo_global_path(user_id, "files"))
    if uploads_dir:
        search_dirs.append(uploads_dir)

    target_lower = clean_name.lower()
    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        exact = os.path.join(d, clean_name)
        if os.path.isfile(exact):
            return exact
        matches = glob.glob(os.path.join(d, f"*_{escaped}"))
        if matches:
            return matches[0]
        try:
            for entry in os.listdir(d):
                if extract_clean_filename(entry).lower() == target_lower:
                    candidate = os.path.join(d, entry)
                    if os.path.isfile(candidate):
                        return candidate
        except Exception:
            pass
    return None


def resolve_extended_file_path(
    user_id: str,
    chat_id: Optional[str],
    target: str,
    workspace: Optional[str] = None,
    uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR,
) -> Optional[str]:
    """Résout de façon unifiée le chemin physique d'un fichier local via son ID, son Nom ou son Workspace ('main', 'sandbox', 'files')."""
    if not target:
        return None
    raw = unquote(str(target).strip())
    for urn_prefix in ("codex:session:", "codex:global:", "codex:"):
        if raw.lower().startswith(urn_prefix):
            raw = raw[len(urn_prefix):]
            break
    safe_target = raw.replace("\\", "/").strip("/")
    if not safe_target or ".." in safe_target.split("/"):
        return None

    effective_ws = workspace.strip().lower() if workspace and workspace.strip() else None
    rel_target = safe_target
    if not effective_ws:
        for ws_candidate in ECHO_CODEX_WORKSPACES:
            if safe_target.startswith(f"{CODEX_DIR_NAME}/{ws_candidate}/"):
                effective_ws = ws_candidate
                rel_target = safe_target[len(f"{CODEX_DIR_NAME}/{ws_candidate}/"):]
                break
            if safe_target.startswith(f"{ws_candidate}/"):
                effective_ws = ws_candidate
                rel_target = safe_target[len(f"{ws_candidate}/"):]
                break

    state = None
    base_codex = get_echo_session_path(user_id, chat_id, CODEX_DIR_NAME) if chat_id else None
    if chat_id:
        try:
            from echo_state_manager import EchoStateManager
            state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        except Exception:
            state = None

    def _resolve_from_resource(res: dict) -> Optional[str]:
        if not res:
            return None
        sp = res.get("storage_path") or ""
        if sp and os.path.isabs(sp) and os.path.isfile(sp):
            return sp
        if res.get("resource_type") == "codex" and base_codex:
            for ws_c in ECHO_CODEX_WORKSPACES:
                if sp.startswith(f"{CODEX_DIR_NAME}/{ws_c}/"):
                    rel_sp = sp[len(f"{CODEX_DIR_NAME}/{ws_c}/"):]
                    cand = os.path.join(base_codex, ws_c, rel_sp)
                    if os.path.isfile(cand):
                        return cand
            cand_main = os.path.join(base_codex, CODEX_WORKSPACE_MAIN, res.get("name", ""))
            if os.path.isfile(cand_main):
                return cand_main
        up = resolve_upload_file_path(user_id, res.get("id", ""), uploads_dir, chat_id=chat_id)
        if up and os.path.isfile(up):
            return up
        if res.get("name"):
            return _resolve_upload_by_filename(user_id, res["name"], uploads_dir, chat_id=chat_id)
        return None

    # 1. Si un workspace Codex explicite est demandé ('main' ou 'sandbox')
    if effective_ws in ECHO_CODEX_WORKSPACES and base_codex:
        ws_dir = os.path.join(base_codex, effective_ws)
        clean_rel = normalize_codex_rel_path(rel_target, workspace=effective_ws, repo_path=ws_dir)
        if clean_rel:
            cand = os.path.join(ws_dir, clean_rel)
            if os.path.isfile(cand):
                return cand
        if state:
            res = state.get_resource(rel_target) or state.get_resource(safe_target)
            resolved = _resolve_from_resource(res)
            if resolved:
                return resolved

    # 2. Résolution par ID exact dans le Registre Unifié (Codex, Media, Binary)
    if state:
        res = state.get_resource(safe_target)
        resolved = _resolve_from_resource(res)
        if resolved:
            return resolved

    # 3. Résolution directe par ID d'upload dans le Vault (files/<id>_*)
    fpath = resolve_upload_file_path(user_id, safe_target, uploads_dir, chat_id=chat_id)
    if fpath and os.path.isfile(fpath):
        return fpath

    # 4. Résolution par Nom dans le Registre Unifié (echo_resources.name)
    if state:
        base_search = os.path.basename(rel_target)
        candidates = state.get_resources(search=base_search) or []
        target_l = rel_target.lower()
        base_l = base_search.lower()
        for c_res in candidates:
            c_name = (c_res.get("name") or "").replace("\\", "/").strip("/")
            if c_name.lower() == target_l or os.path.basename(c_name).lower() == base_l:
                resolved = _resolve_from_resource(c_res)
                if resolved:
                    return resolved

    # 5. Résolution physique directe par Nom/Chemin relatif (Codex 'main' -> Codex 'sandbox' -> Vault 'files')
    if base_codex:
        for ws_name in (CODEX_WORKSPACE_MAIN, CODEX_WORKSPACE_SANDBOX):
            ws_dir = os.path.join(base_codex, ws_name)
            clean_rel = normalize_codex_rel_path(rel_target, workspace=ws_name, repo_path=ws_dir)
            if clean_rel:
                cand = os.path.join(ws_dir, clean_rel)
                if os.path.isfile(cand):
                    return cand

    return _resolve_upload_by_filename(user_id, rel_target, uploads_dir, chat_id=chat_id)


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


def format_codex_storage_path(relative_path: str, workspace: str = CODEX_DEFAULT_WORKSPACE) -> str:
    """Formate de façon canonique le storage_path d'une ressource Codex ('codex/<workspace>/<chemin>')."""
    ws = workspace if workspace in ECHO_CODEX_WORKSPACES else CODEX_DEFAULT_WORKSPACE
    clean = normalize_codex_rel_path(relative_path, workspace=ws) or (relative_path or "").replace("\\", "/").strip("/")
    return f"{CODEX_DIR_NAME}/{ws}/{clean}"


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
