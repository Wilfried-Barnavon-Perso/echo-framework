"""
title: ECHO Auth Service
author: Wilfried BARNAVON
version: 1.2
description: 1.2: Migrated to Google AI Studio API Key authentication.
"""

import time
import orjson as std_json
import pybase64 as base64
import hashlib
import secrets
from typing import Optional, Tuple, Any

import httpx

from echo_constants import (
    GOOGLE_API_BASE_URL,
    ECHO_USER_AGENT,
    GOOGLE_API_KEY_REGEX
)

class AuthService:
    def __init__(self, user_data_manager: Any):
        self.user_data_manager = user_data_manager
        self.base_url = GOOGLE_API_BASE_URL

    def get_auth_prompt(self) -> str:
        """Retourne le message d'instruction pour la configuration de la clé API."""
        url = "https://aistudio.google.com/app/apikey"
        return (
            f"🔐 **Configuration ECHO Requise**\n\n"
            f"ECHO utilise désormais les clés d'API **Google AI Studio** pour plus de performance.\n\n"
            f"1. [Créez votre clé API gratuite ici]({url})\n"
            f"2. Copiez la clé commençant par `AIza...`.\n"
            f"3. Collez simplement la clé dans ce chat.\n\n"
            f"*(ECHO_SESSION_AUTH_PENDING)*"
        )

    async def validate_and_save_api_key(self, api_key: str) -> Tuple[bool, str]:
        """Vérifie la validité de la clé via un appel léger et l'enregistre."""
        api_key = api_key.strip()
        
        # Validation formatale (Regex)
        import re
        if not re.match(GOOGLE_API_KEY_REGEX, api_key):
            return False, "Le format de la clé API semble invalide."

        # Handshake technique avec Google AI Studio
        test_url = f"{GOOGLE_API_BASE_URL}/models?key={api_key}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(test_url, headers={"User-Agent": ECHO_USER_AGENT}, timeout=10)
                if resp.status_code == 200:
                    # Sauvegarde en base de données
                    self.user_data_manager.save_auth_data('google_api_key', api_key)
                    # Nettoyage des anciens tokens OAuth pour éviter les conflits
                    self.user_data_manager.delete_auth_data('google_token')
                    self.user_data_manager.delete_auth_data('google_project_id')
                    return True, "Succès."
                else:
                    return False, f"Clé rejetée par Google (HTTP {resp.status_code})."
        except Exception as e:
            return False, f"Erreur de connexion : {str(e)}"

    def get_valid_credentials(self):
        """Compatibilité API : Retourne la clé API si elle existe."""
        return self.user_data_manager.get_auth_data('google_api_key')

    def get_project_id(self, creds=None, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        """AI Studio n'utilise pas de Project ID obligatoire dans le payload."""
        return None, "N/A (AI Studio)"

    async def fetch_user_quota_async(self, creds=None, pid: str = None, model_id: str = None):
        """Les quotas AI Studio ne sont pas exposés via cette API Cloud."""
        pass
