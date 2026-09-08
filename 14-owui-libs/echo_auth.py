# -*- coding: utf-8 -*-
"""
title: ECHO Echo Auth
author: Wilfried BARNAVON
version: 1.0
description: Gestion de l'authentification et OAuth2.
"""
import os
import time
import sqlite3
from typing import Dict, List, Optional
from echo_http import _get_global_client
from echo_constants import ANTIGRAVITY_DESKTOP_CLIENT_ID, ANTIGRAVITY_DESKTOP_CLIENT_SECRET, AUTH_DATA_PROJECT_ID, AUTH_DATA_USER_TIER, AUTH_METHOD_KEY_PRIMARY, AUTH_METHOD_KEY_SECONDARY, AUTH_METHOD_OAUTH2, ECHO_USERS_ROOT, GOOGLE_OAUTH_TOKEN_URL

class EchoAuth:
    def __init__(self, user_id: str = "system"):
        """
        NOTE ARCHITECTURALE : EchoAuth réside dans echo_utils pour permettre au transporteur EchoGeminiClient
        d'accéder au maillage d'authentification sans créer de dépendance circulaire avec echo_auth.py.
        """
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        safe_uid = "".join(x for x in str(self.user_id) if x.isalnum() or x in "-_")
        self.user_dir = os.path.join(ECHO_USERS_ROOT, safe_uid)
        os.makedirs(self.user_dir, exist_ok=True)

    def _get_db_path(self, user_id: str = None) -> str:
        uid = user_id or self.user_id
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")
        return os.path.join(ECHO_USERS_ROOT, safe_uid, "identity.db")

    def get_api_keys(self, user_id: str = None) -> List[str]:
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return []
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                rows = conn.execute("SELECT key FROM auth_data").fetchall()
                return [r[0] for r in rows]
        except: return []

    def get_auth_data(self, key_name: str, user_id: str = None) -> Optional[str]:
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return None
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                row = conn.execute("SELECT value FROM auth_data WHERE key = ?", (key_name,)).fetchone()
                return row[0] if row else None
        except: return None

    def get_model_quota(self, ca_model_id: str) -> dict:
        """
        Retourne {remainingFraction, resetTime} pour un modèle CA donné.
        Source : google_quota_by_model (JSON) persisté par AuthService.fetch_available_models.
        """
        import json as _j
        raw = self.get_auth_data("google_quota_by_model")
        if not raw:
            return {}
        return _j.loads(raw).get(ca_model_id, {})

    async def get_ordered_auth_providers(self, user_id: str) -> List[Dict]:
        """Résout le registre des fournisseurs d'accès aux modèles par priorité (OAuth2 > Clé Primaire > Clé Secondaire)."""
        uid = user_id or self.user_id
        providers = []
        
        # 1. Vérification OAuth2 (Prioritaire)
        refresh_token = self.get_auth_data("google_oauth2_refresh_token", uid)
        if refresh_token:
            providers.append({
                "type": AUTH_METHOD_OAUTH2,
                "refresh_token": refresh_token,
                "user_id": uid,
                "project_id": self.get_auth_data(AUTH_DATA_PROJECT_ID, uid),
                "tier_id": self.get_auth_data(AUTH_DATA_USER_TIER, uid),
                "g1_credits": self.get_auth_data("google_g1_credits", uid)
            })

        # 2. Vérification des Clés API (Standard/Fallback)
        for method in [AUTH_METHOD_KEY_PRIMARY, AUTH_METHOD_KEY_SECONDARY]:
            key_val = self.get_auth_data(method, uid)
            if key_val:
                providers.append({"type": method, "key": key_val})

        return providers

    async def refresh_google_oauth_token(self, refresh_token: str, user_id: str = None) -> Optional[str]:
        """Rafraîchit silencieusement le jeton d'accès Google OAuth2."""
        client = await _get_global_client()
        payload = {
            "client_id":     ANTIGRAVITY_DESKTOP_CLIENT_ID,   # PKCE emis par le client Desktop
            "client_secret": ANTIGRAVITY_DESKTOP_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token"
        }
        try:
            resp = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                new_access_token = data.get("access_token")
                if new_access_token:
                    self.save_api_key("google_oauth2_access_token", new_access_token, user_id)
                    self.save_api_key("google_oauth2_last_refresh", str(time.time()), user_id)
                    return new_access_token
        except Exception as e:
            print(f"[EchoAuth] Erreur Refresh OAuth2: {e}")
        return None

    def save_api_key(self, key_name: str, value: str, user_id: str = None):
        db_path = self._get_db_path(user_id)
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key_name, value, int(time.time())))
                conn.commit()
        except Exception as e:
            print(f"[EchoAuth] Erreur sauvegarde clé {key_name}: {e}")

    def delete_api_key(self, key_name: str, user_id: str = None):
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("DELETE FROM auth_data WHERE key = ?", (key_name,))
                conn.commit()
        except Exception as e:
            print(f"[EchoAuth] Erreur suppression clé {key_name}: {e}")

