"""
title: ECHO Auth Service
author: Wilfried BARNAVON
version: 1.0
description: Service d'authentification OAuth2 pour Google Cloud AI.
"""

import time
import json as std_json
import base64
import hashlib
import secrets
from typing import Optional, Tuple, Any

import httpx

from echo_constants import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_AUTH_URI,
    GOOGLE_TOKEN_URI,
    GOOGLE_REDIRECT_URI,
    GOOGLE_API_BASE_URL,
    GOOGLE_SCOPES,
    ECHO_USER_AGENT
)

class AuthService:
    def __init__(self, user_data_manager: Any):
        self.user_data_manager = user_data_manager
        self.base_url = GOOGLE_API_BASE_URL

    def _generate_pkce(self):
        """Génération PKCE robuste conforme v150.3."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        """Génère l'URL d'authentification avec challenge PKCE (S256) via httpx.QueryParams. Réutilise le verifier s'il est actif."""
        existing_data = self.user_data_manager.get_auth_data('pkce_verifier')
        if existing_data and (int(time.time()) - existing_data[1] < 290):
            verifier = existing_data[0]
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        else:
            verifier, challenge = self._generate_pkce()
            self.user_data_manager.save_auth_data('pkce_verifier', verifier)
        
        params = httpx.QueryParams({
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent"
        })
        url = f"{GOOGLE_AUTH_URI}?{params}"
        return (
            f"🔐 **Authentification ECHO Requise**\n\n"
            f"Votre session Google Cloud a expiré ou a été réinitialisée.\n\n"
            f"1. [Cliquez ici pour autoriser ECHO]({url})\n"
            f"2. Copiez le code affiché (ex: `4/0Af...`)\n"
            f"3. Collez-le simplement dans ce chat.\n\n"
            f"*(ECHO_SESSION_AUTH_PENDING)*"
        )

    def exchange_code(self, code: str) -> Tuple[bool, str]:
        pkce_data = self.user_data_manager.get_auth_data('pkce_verifier')
        if not pkce_data: return False, "Session expirée."
        
        # Contrôle strict de 300 secondes (5 minutes)
        if int(time.time()) - pkce_data[1] > 300:
            self.user_data_manager.delete_auth_data('pkce_verifier')
            return False, "Le délai de 5 minutes est dépassé. Veuillez générer un nouveau lien."

        try:
            from google_auth_oauthlib.flow import Flow
            OFFICIAL_CLIENT_CONFIG = {"installed": {"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "auth_uri": GOOGLE_AUTH_URI, "token_uri": GOOGLE_TOKEN_URI, "redirect_uris": [GOOGLE_REDIRECT_URI]}}
            flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False)
            flow.redirect_uri = GOOGLE_REDIRECT_URI
            flow.fetch_token(code=code.strip(), code_verifier=pkce_data[0])
            self.user_data_manager.save_auth_data('google_token', flow.credentials.to_json())
            
            # Suppression uniquement en cas de succès
            self.user_data_manager.delete_auth_data('pkce_verifier')
            return True, "Succès."
        except Exception as e: return False, str(e)

    def get_valid_credentials(self):
        token_data = self.user_data_manager.get_auth_data('google_token')
        if not token_data: return None
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GoogleAuthRequest
            creds = Credentials.from_authorized_user_info(std_json.loads(token_data[0]), GOOGLE_SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(GoogleAuthRequest()); self.user_data_manager.save_auth_data('google_token', creds.to_json())
            return creds if (creds and creds.valid) else None
        except: return None

    def get_project_id(self, creds, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        cached = self.user_data_manager.get_auth_data('google_project_id')
        plan_cached = self.user_data_manager.get_auth_data('google_plan_name')
        if cached and plan_cached and not debug_mode: return cached[0], "Cache."
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
        try:
            resp = httpx.post(f"{GOOGLE_API_BASE_URL}:loadCodeAssist", headers=headers, json={"metadata": {"ideType": "IDE_UNSPECIFIED", "pluginType": "GEMINI"}}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                
                # --- [Nouveau] Récupération du Plan et des Crédits ---
                paid_tier = data.get("paidTier") or data.get("currentTier") or {}
                plan_name = paid_tier.get("name", paid_tier.get("id", "Plan Inconnu"))
                self.user_data_manager.save_auth_data('google_plan_name', plan_name)
                
                available_credits = paid_tier.get("availableCredits", [])
                for credit in available_credits:
                    if credit.get("creditType") == "GOOGLE_ONE_AI":
                        self.user_data_manager.save_auth_data('google_credits', str(credit.get("creditAmount", "0")))
                        break
                # -----------------------------------------------------

                pid = data.get("cloudaicompanionProject", {}).get("id") if isinstance(data.get("cloudaicompanionProject"), dict) else data.get("cloudaicompanionProject")
                if pid:
                    pid = pid.replace("projects/", ""); self.user_data_manager.save_auth_data('google_project_id', pid)
                    return pid, "API OK."
            return None, "Handshake Fail."
        except Exception as e: return None, str(e)

    async def fetch_user_quota_async(self, creds, pid: str, model_id: str):
        """Récupère le quota principal (Pooled) de manière asynchrone."""
        if not creds or not pid: return
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
        payload = {"project": pid, "userAgent": ECHO_USER_AGENT}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{GOOGLE_API_BASE_URL}:retrieveUserQuota", headers=headers, json=payload, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    buckets = data.get("buckets", [])
                    
                    total_rem = 0
                    total_lim = 0
                    furthest_reset = ""
                    
                    for b in buckets:
                        rem_str = b.get("remainingAmount")
                        frac = b.get("remainingFraction")
                        r_time = b.get("resetTime", "")
                        
                        if rem_str and frac is not None and float(frac) > 0:
                            rem = int(rem_str)
                            lim = round(rem / float(frac))
                            total_rem += rem
                            total_lim += lim
                            if r_time > furthest_reset: furthest_reset = r_time
                    
                    if total_lim > 0:
                        self.user_data_manager.save_auth_data("google_quota_remaining", str(total_rem))
                        self.user_data_manager.save_auth_data("google_quota_limit", str(total_lim))
                        self.user_data_manager.save_auth_data("google_quota_reset_time", furthest_reset)
                        self.user_data_manager.save_auth_data("google_quota_last_sync", str(int(time.time())))
        except Exception: pass
