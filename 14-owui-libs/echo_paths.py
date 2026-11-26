# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Echo Paths
author: Wilfried BARNAVON
version: 6.23.10.1
description: Résolution des chemins absolus du système.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.23.10.1: Paramètre allow_existing dans scope_subagent_sandbox_path (cohérence read/edit/delete racine sandbox vs isolation stricte create).
# 6.21.9.1: Migration de _CHAT_CLONE_LOCKS, _CODEX_LOCKS et _SKILL_LOCKS vers weakref.WeakValueDictionary protégés par _PATHS_LOCK.
# 6.19.0.1: Ajout de scope_subagent_sandbox_path pour l'isolation paresseuse des chemins sandbox/<sub_sid>/ des sous-agents.
# 6.14.2.1: Ajout de EMBED_URI_PREFIX, ASSET_URI_PREFIX, get_echo_embeds_dir, save_embed_binary_asset et save_embed_html_document.
# 6.11.0.1: Ajout de is_safe_user_exportable_path, resolve_user_exportable_file_path et support domaine 'files' sécurisé.
import os
import re
import json
import time
import glob
import uuid
import base64
import asyncio
import threading
import weakref
from urllib.parse import unquote
from typing import Optional
from echo_constants import (
    BROWSER_VIEWS_DIR_NAME,
    CODEX_DEFAULT_WORKSPACE,
    CODEX_DIR_NAME,
    CODEX_FORBIDDEN_PATH_SEGMENTS,
    CODEX_MAX_PATH_DEPTH,
    CODEX_SANDBOX_INTERNAL_NAMES,
    CODEX_SANDBOX_INTERNAL_PREFIXES,
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
_PATHS_LOCK = threading.Lock()
_CHAT_CLONE_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_CODEX_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_SKILL_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def sanitize_echo_id(raw_id: str, fallback: str = "default", default: Optional[str] = None) -> str:
    """Nettoie un identifiant (user_id, chat_id, skill_id, etc.) pour prévenir toute traversée de chemin."""
    effective_fallback = default if default is not None else fallback
    cleaned = "".join(x for x in str(raw_id or "") if x.isalnum() or x in "-_")
    return cleaned if cleaned else effective_fallback


def get_codex_lock(user_id: str, chat_id: str) -> asyncio.Lock:
    """Retourne ou crée le verrou asyncio exclusif protégeant les dépôts Git Codex d'une session (user_id, chat_id)."""
    key = f"{sanitize_echo_id(user_id)}:{sanitize_echo_id(chat_id)}"
    with _PATHS_LOCK:
        lock = _CODEX_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _CODEX_LOCKS[key] = lock
        return lock


def get_user_skill_lock(user_id: str) -> asyncio.Lock:
    """Retourne ou crée le verrou asyncio protégeant les opérations disque sur les skills d'un utilisateur."""
    key = sanitize_echo_id(user_id)
    with _PATHS_LOCK:
        lock = _SKILL_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SKILL_LOCKS[key] = lock
        return lock


def get_chat_clone_lock(chat_id: str) -> asyncio.Lock:
    """Retourne ou crée un verrou asyncio pour synchroniser la création d'un chat cloné."""
    with _PATHS_LOCK:
        lock = _CHAT_CLONE_LOCKS.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            _CHAT_CLONE_LOCKS[chat_id] = lock
        return lock


async def wait_for_chat_clone(chat_id: str, timeout: float = 10.0) -> None:
    """Attend la fin d'un clonage éventuel sur ce chat_id avant l'exécution des filtres Inlet."""
    with _PATHS_LOCK:
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


def get_echo_identity_db_path(user_id: str) -> str:
    """Retourne le chemin standardisé de la base utilisateur identity.db."""
    return os.path.join(ECHO_USERS_ROOT, sanitize_echo_id(user_id), "identity.db")


def get_echo_db_path(user_id: str, chat_id: str) -> str:
    """Retourne le chemin standardisé de la base de session session.db. Alias direct de get_echo_session_path(..., 'db')."""
    return get_echo_session_path(user_id, chat_id, "db")


get_echo_session_db_path = get_echo_db_path


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

        # Repli sur le dossier global des fichiers utilisateur
        user_global_files = get_echo_global_path(user_id, "files")
        pattern_global = os.path.join(user_global_files, f"{file_id}_*")
        matches_global = glob.glob(pattern_global)
        if matches_global:
            return matches_global[0]

        # Repli sur les autres sessions du même utilisateur
        user_chats = get_echo_global_path(user_id, "chats")
        pattern = os.path.join(user_chats, "*", "files", f"{file_id}_*")
        matches = glob.glob(pattern)
        if matches:
            return matches[0]

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
    for urn_prefix in ("codex:current:", "codex:session:", "codex:global:", "codex:"):
        if raw.lower().startswith(urn_prefix):
            raw = raw[len(urn_prefix):]
            break
    safe_target = raw.replace("\\", "/").strip("/")
    if not safe_target or ".." in safe_target.split("/"):
        return None

    effective_ws = workspace.strip().lower() if workspace and workspace.strip() else None
    rel_target = safe_target
    if not effective_ws:
        if safe_target.startswith(f"{BROWSER_VIEWS_DIR_NAME}/"):
            effective_ws = BROWSER_VIEWS_DIR_NAME
            rel_target = safe_target[len(f"{BROWSER_VIEWS_DIR_NAME}/"):]
        elif safe_target.startswith("files/"):
            effective_ws = "files"
            rel_target = safe_target[len("files/"):]
        else:
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
    base_bv = get_echo_session_path(user_id, chat_id, BROWSER_VIEWS_DIR_NAME) if chat_id else None
    if chat_id:
        try:
            from echo_state_manager import EchoStateManager
            state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        except Exception:
            state = None

    def _resolve_codex_file(ws_name: str, rel_path: str) -> Optional[str]:
        if not base_codex or ws_name not in ECHO_CODEX_WORKSPACES:
            return None
        ws_dir = os.path.join(base_codex, ws_name)
        try:
            _, cand = resolve_confined_codex_path(
                ws_dir,
                rel_path,
                workspace=ws_name,
                allow_empty=False,
            )
        except ValueError:
            return None
        if not os.path.islink(cand) and os.path.isfile(cand):
            return cand
        return None

    def _resolve_from_resource(res: dict) -> Optional[str]:
        if not res:
            return None
        sp = res.get("storage_path") or ""
        if sp and os.path.isabs(sp) and not os.path.islink(sp) and os.path.isfile(sp):
            if is_safe_user_exportable_path(user_id, sp, uploads_dir=uploads_dir, trusted_resource=True):
                return os.path.realpath(sp)
            return None
        if base_bv and (
            res.get("resource_type") == "browserview"
            or sp.startswith(f"{BROWSER_VIEWS_DIR_NAME}/")
        ):
            base_bv_real = os.path.realpath(base_bv)
            for cand_name in (os.path.basename(sp), res.get("name", "")):
                if not cand_name:
                    continue
                cand_real = os.path.realpath(os.path.join(base_bv, os.path.basename(cand_name)))
                if cand_real.startswith(base_bv_real + os.sep) and os.path.isfile(cand_real):
                    return cand_real
        if base_codex and (
            res.get("resource_type") in ("codex", "plan", "generated")
            or sp.startswith(f"{CODEX_DIR_NAME}/")
        ):
            for ws_c in ECHO_CODEX_WORKSPACES:
                if sp.startswith(f"{CODEX_DIR_NAME}/{ws_c}/"):
                    rel_sp = sp[len(f"{CODEX_DIR_NAME}/{ws_c}/"):]
                    cand = _resolve_codex_file(ws_c, rel_sp)
                    if cand:
                        return cand
            cand_main = _resolve_codex_file(
                CODEX_WORKSPACE_MAIN,
                res.get("name") or "",
            )
            if cand_main:
                return cand_main
        up = resolve_upload_file_path(user_id, res.get("id", ""), uploads_dir, chat_id=chat_id)
        if up and os.path.isfile(up):
            return up
        if res.get("name"):
            return _resolve_upload_by_filename(user_id, res["name"], uploads_dir, chat_id=chat_id)
        return None

    # 0. Si le domaine browserviews est explicitement ciblé
    if effective_ws == BROWSER_VIEWS_DIR_NAME and base_bv:
        base_bv_real = os.path.realpath(base_bv)
        for cand_name in (rel_target, os.path.basename(rel_target)):
            if not cand_name:
                continue
            cand_real = os.path.realpath(os.path.join(base_bv, cand_name))
            if cand_real.startswith(base_bv_real + os.sep) and os.path.isfile(cand_real):
                return cand_real
        if state:
            res = state.get_resource(rel_target) or state.get_resource(safe_target)
            resolved = _resolve_from_resource(res)
            if resolved:
                return resolved

    # 0b. Si le domaine 'files' est explicitement ciblé
    if effective_ws == "files" and user_id and user_id != "anonymous":
        direct_up = resolve_upload_file_path(user_id, rel_target, uploads_dir="", chat_id=chat_id)
        if direct_up and not os.path.islink(direct_up) and os.path.isfile(direct_up):
            return direct_up
        by_name = _resolve_upload_by_filename(user_id, rel_target, uploads_dir="", chat_id=chat_id)
        if by_name and not os.path.islink(by_name) and os.path.isfile(by_name):
            return by_name

    # 1. Si un workspace Codex explicite est demandé ('main' ou 'sandbox')
    if effective_ws in ECHO_CODEX_WORKSPACES and base_codex:
        cand = _resolve_codex_file(effective_ws, rel_target)
        if cand:
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

    # 5. Résolution physique directe par Nom/Chemin relatif (tous les workspaces Codex -> Vault 'files')
    if base_codex:
        for ws_name in ECHO_CODEX_WORKSPACES:
            cand = _resolve_codex_file(ws_name, rel_target)
            if cand:
                return cand

    if base_bv:
        base_bv_real = os.path.realpath(base_bv)
        for cand_name in (f"{sanitize_echo_id(safe_target)}_frame.jpg", os.path.basename(rel_target)):
            if not cand_name:
                continue
            cand_real = os.path.realpath(os.path.join(base_bv, cand_name))
            if cand_real.startswith(base_bv_real + os.sep) and os.path.isfile(cand_real):
                return cand_real

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


def is_sandbox_internal_segment(segment: str) -> bool:
    """Retourne True si segment est un élément technique interne sandbox."""
    if not segment:
        return False
    return (
        segment in CODEX_SANDBOX_INTERNAL_NAMES
        or any(
            segment.startswith(prefix)
            for prefix in CODEX_SANDBOX_INTERNAL_PREFIXES
        )
    )


def normalize_codex_rel_path(
    relative_path: str,
    workspace: str = "",
    repo_path: str = "",
) -> Optional[str]:
    """Normalise et sécurise un chemin relatif Codex (anti-traversal/IPC)."""
    if not relative_path or "\x00" in relative_path:
        return None
    clean_path = relative_path.replace("\\", "/").strip("/")
    for _ in range(2):
        if "%" in clean_path and (
            not repo_path
            or not os.path.exists(os.path.join(repo_path, clean_path))
        ):
            decoded = unquote(clean_path)
            if "\x00" in decoded:
                return None
            clean_path = decoded.replace("\\", "/").strip("/")
        else:
            break
    if not clean_path:
        return None
    if workspace:
        prefix_full = f"{CODEX_DIR_NAME}/{workspace}/"
        prefix_ws = f"{workspace}/"
        if clean_path.startswith(prefix_full):
            clean_path = clean_path[len(prefix_full):]
        elif clean_path.startswith(prefix_ws):
            clean_path = clean_path[len(prefix_ws):]
        if clean_path in ("", workspace, f"{CODEX_DIR_NAME}/{workspace}"):
            return None
    segments = clean_path.split("/")
    if not segments or len(segments) > CODEX_MAX_PATH_DEPTH:
        return None
    for seg in segments:
        if (
            not seg
            or seg in CODEX_FORBIDDEN_PATH_SEGMENTS
            or seg.lower() == ".git"
            or (
                workspace == CODEX_WORKSPACE_SANDBOX
                and is_sandbox_internal_segment(seg)
            )
        ):
            return None
    return "/".join(segments)


def resolve_confined_codex_path(
    base_dir: str,
    relative_path: str,
    *,
    workspace: str = "",
    allow_empty: bool = False,
    allowed_symlink_roots: Optional[tuple[str, ...]] = None,
) -> tuple[str, str]:
    """Résout (clean_rel, target_abs) sous base_dir avec confinement strict."""
    if not base_dir:
        raise ValueError("Répertoire de base manquant.")
    base_norm = os.path.normpath(base_dir)
    if os.path.islink(base_norm) and not allowed_symlink_roots:
        raise ValueError("Répertoire racine invalide (lien symbolique).")
    real_base = os.path.realpath(base_dir)
    valid_roots = [
        os.path.realpath(r) for r in (allowed_symlink_roots or ()) if r
    ]
    if allowed_symlink_roots and real_base not in valid_roots:
        valid_roots.append(real_base)

    def _is_under(real_p: str, root_real: str) -> bool:
        try:
            return os.path.commonpath([root_real, real_p]) == root_real
        except ValueError:
            return False

    if "\x00" in (relative_path or ""):
        raise ValueError("Caractère nul interdit dans le chemin.")
    raw = (relative_path or "").replace("\\", "/").strip("/")
    is_root_empty = raw == "" or (
        bool(workspace)
        and raw in (workspace, f"{CODEX_DIR_NAME}/{workspace}")
    )
    if is_root_empty:
        if allow_empty:
            return ("", real_base)
        raise ValueError("Chemin cible vide ou racine non autorisé.")

    clean_rel = normalize_codex_rel_path(
        relative_path,
        workspace=workspace,
        repo_path=base_dir,
    )
    if clean_rel is None:
        raise ValueError(
            "Chemin relatif invalide, protégé (.git / IPC) "
            "ou path traversal non autorisé."
        )

    cur = base_dir
    for seg in clean_rel.split("/"):
        cur = os.path.join(cur, seg)
        if os.path.lexists(cur) and os.path.islink(cur):
            if not allowed_symlink_roots:
                raise ValueError(
                    "Lien symbolique non autorisé dans cet espace."
                )
            real_cur = os.path.realpath(cur)
            if not any(_is_under(real_cur, r) for r in valid_roots):
                raise ValueError(
                    "Lien symbolique sortant du périmètre autorisé."
                )

    target_abs = os.path.abspath(
        os.path.join(base_dir, *clean_rel.split("/"))
    )
    real_target = os.path.realpath(target_abs)
    if not allowed_symlink_roots:
        if not (
            _is_under(real_target, real_base) and real_target != real_base
        ):
            raise ValueError("Sortie de confinement détectée.")
    elif not any(_is_under(real_target, r) for r in valid_roots):
        raise ValueError("Sortie de confinement détectée.")
    return (clean_rel, target_abs)


def scope_subagent_sandbox_path(
    relative_path: str,
    sub_sid: str = "",
    sandbox_root: str = "",
    for_write: bool = False,
    allow_existing: bool = False,
) -> str:
    """Résout un chemin relatif sandbox scopé paresseusement sous `<sub_sid>/` pour un sous-agent."""
    clean_sid = sanitize_echo_id(sub_sid, fallback="") if sub_sid else ""
    if not clean_sid or not relative_path:
        return relative_path
    clean_rel = normalize_codex_rel_path(
        relative_path,
        workspace=CODEX_WORKSPACE_SANDBOX,
        repo_path=sandbox_root,
    )
    if not clean_rel:
        return relative_path
    if clean_rel == clean_sid or clean_rel.startswith(f"{clean_sid}/"):
        return clean_rel
    if for_write:
        if sandbox_root and os.path.exists(
            os.path.join(sandbox_root, clean_sid, *clean_rel.split("/"))
        ):
            return f"{clean_sid}/{clean_rel}"
        if (
            allow_existing
            and sandbox_root
            and os.path.exists(os.path.join(sandbox_root, *clean_rel.split("/")))
        ):
            return clean_rel
        return f"{clean_sid}/{clean_rel}"
    if sandbox_root and os.path.exists(
        os.path.join(sandbox_root, clean_sid, *clean_rel.split("/"))
    ):
        return f"{clean_sid}/{clean_rel}"
    return clean_rel


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
    """Vérifie l'existence physique d'un fichier Codex sur disque."""
    try:
        if (
            not user_id
            or not chat_id
            or workspace not in ECHO_CODEX_WORKSPACES
        ):
            return False
        base_codex = get_echo_session_path(user_id, chat_id, CODEX_DIR_NAME)
        ws_dir = os.path.join(base_codex, workspace)
        try:
            _, cand_abs = resolve_confined_codex_path(
                ws_dir,
                relative_path,
                workspace=workspace,
                allow_empty=False,
            )
        except ValueError:
            return False
        return not os.path.islink(cand_abs) and os.path.isfile(cand_abs)
    except Exception:
        return False


_FORBIDDEN_EXPORT_FILENAMES = frozenset({"identity.db", "session.db", "webui.db"})
_FORBIDDEN_EXPORT_SUFFIXES = (".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".wal", ".shm")


def is_safe_user_exportable_path(
    user_id: str,
    candidate_path: str,
    uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR,
    trusted_resource: bool = False,
) -> bool:
    """Vérifie qu'un chemin physique est confinable au périmètre utilisateur et non sensible pour l'export."""
    if not user_id or user_id == "anonymous" or not candidate_path:
        return False
    if os.path.islink(candidate_path) or not os.path.isfile(candidate_path):
        return False
    safe_uid = sanitize_echo_id(user_id, fallback="")
    if not safe_uid:
        return False

    real_cand = os.path.realpath(candidate_path)
    base_name_l = os.path.basename(real_cand).lower()
    if base_name_l in _FORBIDDEN_EXPORT_FILENAMES or base_name_l.endswith(_FORBIDDEN_EXPORT_SUFFIXES):
        return False

    def _has_forbidden_segments(parts: list[str]) -> bool:
        for p in parts:
            pl = p.lower()
            if pl == ".env" or pl.startswith(".env.") or is_sandbox_internal_segment(p):
                return True
        return False

    user_root_real = os.path.realpath(os.path.join(ECHO_USERS_ROOT, safe_uid))
    try:
        in_user_root = os.path.commonpath([user_root_real, real_cand]) == user_root_real and real_cand != user_root_real
    except ValueError:
        in_user_root = False

    if in_user_root:
        rel_parts = os.path.relpath(real_cand, user_root_real).replace("\\", "/").split("/")
        if "db" in rel_parts or _has_forbidden_segments(rel_parts):
            return False
        return True

    if uploads_dir:
        uploads_real = os.path.realpath(uploads_dir)
        try:
            in_uploads = os.path.commonpath([uploads_real, real_cand]) == uploads_real and real_cand != uploads_real
        except ValueError:
            in_uploads = False
        if in_uploads:
            rel_parts = os.path.relpath(real_cand, uploads_real).replace("\\", "/").split("/")
            if _has_forbidden_segments(rel_parts):
                return False
            return bool(trusted_resource or os.path.basename(real_cand).startswith(f"U_{safe_uid}_"))

    return False


def resolve_user_exportable_file_path(
    user_id: str,
    chat_id: Optional[str],
    target: str,
    workspace: Optional[str] = None,
    uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR,
) -> Optional[str]:
    """Résout et confine un fichier local utilisateur exportable (hors bases SQLite, symlinks et fichiers tiers)."""
    if not user_id or user_id == "anonymous" or not target:
        return None

    cand = resolve_extended_file_path(
        user_id=user_id,
        chat_id=chat_id,
        target=target,
        workspace=workspace,
        uploads_dir="",
    )
    if cand and is_safe_user_exportable_path(user_id, cand, uploads_dir=uploads_dir, trusted_resource=False):
        return os.path.realpath(cand)

    if uploads_dir:
        cand_transit = resolve_extended_file_path(
            user_id=user_id,
            chat_id=chat_id,
            target=target,
            workspace=workspace,
            uploads_dir=uploads_dir,
        )
        if cand_transit:
            raw_clean = unquote(str(target).strip()).replace("\\", "/").strip("/")
            if raw_clean.startswith("files/"):
                raw_clean = raw_clean[len("files/"):]
            is_registered = False
            if chat_id and raw_clean:
                try:
                    from echo_state_manager import EchoStateManager
                    state = EchoStateManager(user_id=user_id, chat_id=chat_id)
                    if state.get_resource(raw_clean):
                        is_registered = True
                    else:
                        base_search = os.path.basename(raw_clean)
                        for r in (state.get_resources(search=base_search) or []):
                            r_name = (r.get("name") or "").replace("\\", "/").strip("/")
                            if r.get("id") == raw_clean or os.path.basename(r_name).lower() == base_search.lower():
                                is_registered = True
                                break
                except Exception:
                    is_registered = False
            if is_safe_user_exportable_path(user_id, cand_transit, uploads_dir=uploads_dir, trusted_resource=is_registered):
                return os.path.realpath(cand_transit)

    return None


EMBED_URI_PREFIX = "echo-embed://"
ASSET_URI_PREFIX = "echo-asset://"
_EMBED_ID_RE = re.compile(r"^[a-f0-9]{16,64}$")
_EMBED_ASSET_RE = re.compile(r"^[a-f0-9]{16,64}\.[a-z0-9]{2,5}$")
_EMBED_DATA_IMG_RE = re.compile(
    r"data:image/(png|jpeg|jpg|webp|gif|svg\+xml);base64,([A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)
_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}


def get_echo_embeds_dir(user_id: str, chat_id: str) -> str:
    """Retourne le répertoire embeds d'une session dans tools_output après création."""
    embeds_dir = os.path.join(get_echo_session_path(user_id, chat_id, "tools_output"), "embeds")
    os.makedirs(embeds_dir, exist_ok=True)
    return embeds_dir


def _atomic_write_bytes(target_path: str, data: bytes) -> None:
    """Écrit atomiquement un flux d'octets via un fichier temporaire puis os.replace."""
    tmp_path = f"{target_path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.replace(tmp_path, target_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def save_embed_binary_asset(
    user_id: str,
    chat_id: str,
    raw_bytes: bytes,
    mime: str,
    filename: str = "",
) -> str:
    """Persiste un asset binaire dans tools_output/embeds avec sa métadonnée JSON et retourne son URI echo-asset://."""
    mime_clean = (mime or "application/octet-stream").split(";")[0].strip().lower()
    ext = _MIME_TO_EXT.get(mime_clean, "bin")
    asset_name = f"{uuid.uuid4().hex}.{ext}"
    embeds_dir = get_echo_embeds_dir(user_id, chat_id)
    _atomic_write_bytes(os.path.join(embeds_dir, asset_name), raw_bytes)
    meta_bytes = json.dumps(
        {
            "mime": mime_clean,
            "filename": os.path.basename(filename or "")[:120],
            "created_at": int(time.time()),
        }
    ).encode("utf-8")
    _atomic_write_bytes(os.path.join(embeds_dir, f"{asset_name}.meta.json"), meta_bytes)
    return f"{ASSET_URI_PREFIX}{asset_name}"


def save_embed_html_document(user_id: str, chat_id: str, html: str) -> str:
    """Externalise les images data:image/...;base64 d'un document HTML vers echo-asset:// puis persiste le HTML et retourne echo-embed://."""
    embeds_dir = get_echo_embeds_dir(user_id, chat_id)

    def _replace_data_img(match: re.Match) -> str:
        subtype = match.group(1)
        b64_str = match.group(2)
        try:
            raw_bytes = base64.b64decode(re.sub(r"\s+", "", b64_str))
            return save_embed_binary_asset(user_id, chat_id, raw_bytes, f"image/{subtype.lower()}")
        except Exception:
            return match.group(0)

    processed_html = _EMBED_DATA_IMG_RE.sub(_replace_data_img, html or "")
    embed_id = uuid.uuid4().hex
    _atomic_write_bytes(
        os.path.join(embeds_dir, f"{embed_id}.html"),
        processed_html.encode("utf-8"),
    )
    return f"{EMBED_URI_PREFIX}{embed_id}?chat_id={sanitize_echo_id(chat_id)}"
