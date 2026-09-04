# -*- coding: utf-8 -*-
"""
title: ECHO Echo Paths
author: Wilfried BARNAVON
version: 1.0
description: Résolution des chemins absolus du système.
"""
import os
import time
import glob
from typing import Optional
from echo_constants import ECHO_GLOBAL_DOMAINS, ECHO_SESSION_DOMAINS, ECHO_USERS_ROOT, ECHO_UPLOADS_TRANSIT_DIR, ECHO_VERSION_PATH

def get_echo_global_path(user_id: str, domain: str) -> str:
    """Retourne le chemin standardisé pour un domaine global (ex: skills)."""
    if domain not in ECHO_GLOBAL_DOMAINS:
        raise ValueError(f"[ECHO] Domaine global invalide : {domain}")
    safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
    return os.path.join(ECHO_USERS_ROOT, safe_uid, domain)

def get_echo_session_path(user_id: str, chat_id: str, domain: str) -> str:
    """Retourne le chemin standardisé pour un domaine du conteneur de session."""
    if domain not in ECHO_SESSION_DOMAINS:
        raise ValueError(f"[ECHO] Domaine de session invalide : {domain}")
    
    safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
    safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
    base_dir = os.path.join(ECHO_USERS_ROOT, safe_uid, "chats", safe_cid)
    
    # La base SQLite se nomme session.db à la racine du conteneur
    if domain == "db":
        return os.path.join(base_dir, "session.db")
    
    return os.path.join(base_dir, domain)

def resolve_upload_file_path(user_id: str, file_id: str, uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR, chat_id: Optional[str] = None) -> Optional[str]:
    if not file_id: return None
    file_id = file_id.strip()
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        if chat_id:
            user_vault = get_echo_session_path(user_id, chat_id, "files")
            pattern = os.path.join(user_vault, f"{file_id}_*")
            matches = glob.glob(pattern)
            if matches: return matches[0]
        else:
            # Fallback de sécurité si appelé hors contexte chat_id
            user_chats = get_echo_global_path(user_id, "chats")
            pattern = os.path.join(user_chats, "*", "files", f"{file_id}_*")
            matches = glob.glob(pattern)
            if matches: return matches[0]
            
            # Fallback Ultime : Dossier global des fichiers
            user_global_files = get_echo_global_path(user_id, "files")
            pattern_global = os.path.join(user_global_files, f"{file_id}_*")
            matches_global = glob.glob(pattern_global)
            if matches_global: return matches_global[0]
            
    pattern = os.path.join(uploads_dir, f"{file_id}_*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None

def generate_echo_file_id(user_id: str, chat_id: str) -> str:
    ts = int(time.time() * 1000)
    return f"U_{user_id}_C_{chat_id}_T_{ts}"

def get_echo_version() -> str:
    try:
        if os.path.exists(ECHO_VERSION_PATH):
            with open(ECHO_VERSION_PATH, "r") as f: return f.read().strip()
    except: pass
    return ""

