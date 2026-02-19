"""
title: ECHO Shared Utils
author: ECHO Framework
version: 1.4
description: v1.4 : Ajout de EchoStateManager pour la gestion stateful des fichiers (Sync/Prune).
"""

import os
import sqlite3
import json
import requests
import time
from typing import Optional, Tuple, List, Set

# --- CONSTANTES ---
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

class EchoAuth:
    def __init__(self, data_dir: str = "/app/backend/data"):
        self.data_dir = data_dir
        self.user_db_dir = os.path.join(data_dir, "user_dbs")

    def _get_db_path(self, user_id: str) -> str:
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        return os.path.join(self.user_db_dir, f"user-{safe_uid}.db")

    def _save_token_safe(self, db_path: str, token_json: str):
        try:
            conn = sqlite3.connect(db_path, timeout=2.0)
            conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)",
                         ('google_token', token_json, int(time.time())))
            conn.commit()
            conn.close()
        except: pass

    def get_credentials(self, user_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Retourne (AccessToken, ProjectID). Gère le refresh automatique."""
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return None, None

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_token'")
            row_token = cursor.fetchone()
            cursor.execute("SELECT value FROM auth_data WHERE key = 'google_project_id'")
            row_pid = cursor.fetchone()
            conn.close()

            if not row_token: return None, None

            token_data = json.loads(row_token[0])
            access_token = token_data.get("token")
            refresh_token = token_data.get("refresh_token")
            project_id = row_pid[0] if row_pid else None
            
            if refresh_token:
                new_token_data = self._refresh_access_token(refresh_token)
                if new_token_data:
                    token_data["token"] = new_token_data["access_token"]
                    if "refresh_token" in new_token_data:
                        token_data["refresh_token"] = new_token_data["refresh_token"]
                    self._save_token_safe(db_path, json.dumps(token_data))
                    return token_data["token"], project_id
            
            return access_token, project_id

        except Exception as e:
            print(f"[EchoAuth] Error: {str(e)}")
            return None, None

    def _refresh_access_token(self, refresh_token: str) -> Optional[dict]:
        payload = {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        try:
            resp = requests.post(GOOGLE_TOKEN_URI, data=payload, timeout=5)
            if resp.status_code == 200: return resp.json()
        except: pass
        return None

class EchoStateManager:
    """Gère l'état de traitement des fichiers par Chat ID."""
    
    def __init__(self, data_dir: str = "/app/backend/data", user_id: str = "system"):
        self.db_dir = os.path.join(data_dir, "user_dbs")
        os.makedirs(self.db_dir, exist_ok=True)
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        self.db_path = os.path.join(self.db_dir, f"user-{safe_uid}.db")
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_files (
                    chat_id TEXT,
                    file_id TEXT,
                    mode TEXT,
                    timestamp INTEGER,
                    PRIMARY KEY (chat_id, file_id)
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[EchoStateManager] Init Error: {e}")

    def sync_state(self, chat_id: str, current_file_ids: List[str]) -> Set[str]:
        """
        Synchronise l'état de la base avec la liste actuelle des fichiers.
        1. Supprime de la DB les fichiers qui ne sont plus dans current_file_ids (Prune).
        2. Retourne l'ensemble des IDs qui sont déjà dans la DB (Known Files).
        """
        if not chat_id: return set()
        
        known_files = set()
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()
            
            # 1. Récupérer tous les fichiers connus pour ce chat
            cursor.execute("SELECT file_id FROM processed_files WHERE chat_id = ?", (chat_id,))
            db_files = {row[0] for row in cursor.fetchall()}
            
            # 2. Identifier les disparus (DB - Current)
            current_set = set(current_file_ids)
            to_delete = list(db_files - current_set)
            
            # 3. Supprimer les disparus
            if to_delete:
                print(f"[EchoStateManager] Pruning {len(to_delete)} files from chat {chat_id}")
                cursor.executemany(
                    "DELETE FROM processed_files WHERE chat_id = ? AND file_id = ?",
                    [(chat_id, fid) for fid in to_delete]
                )
            
            # 4. Identifier les connus (Intersection)
            known_files = db_files.intersection(current_set)
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[EchoStateManager] Sync Error: {e}")
            
        return known_files

    def mark_processed(self, chat_id: str, file_id: str, mode: str):
        """Marque un fichier comme traité."""
        if not chat_id or not file_id: return
        try:
            conn = sqlite3.connect(self.db_path, timeout=2.0)
            conn.execute(
                "INSERT OR REPLACE INTO processed_files (chat_id, file_id, mode, timestamp) VALUES (?, ?, ?, ?)",
                (chat_id, file_id, mode, int(time.time()))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[EchoStateManager] Mark Error: {e}")