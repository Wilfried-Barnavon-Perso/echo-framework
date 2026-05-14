"""
title: ECHO Auth Service
author: Wilfried BARNAVON
version: 3.7
description: 3.7: Provisioning OAuth2 déterministe et priorité souveraine.
"""

import time
import orjson as std_json
import pybase64 as base64
import hashlib
import secrets
import re
import sys
import asyncio
import sqlite3
from typing import Optional, Tuple, Any, List, Dict
from urllib.parse import urlencode

import httpx

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, _get_global_client

from echo_constants import (
    GOOGLE_API_BASE_URL,
    ECHO_USER_AGENT,
    GOOGLE_API_KEY_PATTERN,
    GOOGLE_AI_STUDIO_WEB_URL,
    GOOGLE_OAUTH_CODE_REGEX,
    GOOGLE_OAUTH_AUTH_URL,
    GOOGLE_OAUTH_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    ECHO_OAUTH_CLIENT_ID,
    ECHO_OAUTH_CLIENT_SECRET,
    ECHO_OAUTH_SCOPES,
    PKCE_REUSE_WINDOW,
    AUTH_METHOD_KEY_PRIMARY,
    AUTH_METHOD_KEY_SECONDARY,
    AUTH_METHOD_OAUTH2,
    CODE_ASSIST_BASE_URL,
    ECHO_CLIENT_METADATA,
    AUTH_DATA_PROJECT_ID,
    AUTH_DATA_USER_EMAIL,
    AUTH_DATA_USER_TIER
)

class AuthService:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.base_url = GOOGLE_API_BASE_URL
        self.echo_auth = EchoAuth(user_id=user_id)

    def _get_pkce_context(self) -> Optional[Dict]:
        """Récupère le contexte PKCE actuel s'il est encore valide (5 min)."""
        db_path = self.echo_auth._get_db_path()
        try:
            with sqlite3.connect(f"file://{db_path}?mode=ro", uri=True, timeout=5.0) as conn:
                row = conn.execute("SELECT verifier, state, timestamp FROM auth_pkce_context WHERE user_id = ?", (self.user_id,)).fetchone()
                if row:
                    verifier, state, ts = row
                    if int(time.time()) - ts < PKCE_REUSE_WINDOW:
                        return {"verifier": verifier, "state": state}
        except: pass
        return None

    def _save_pkce_context(self, verifier: str, state: str):
        db_path = self.echo_auth._get_db_path()
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("INSERT OR REPLACE INTO auth_pkce_context (user_id, verifier, state, timestamp) VALUES (?, ?, ?, ?)",
                            (self.user_id, verifier, state, int(time.time())))
                conn.commit()
        except Exception as e:
            print(f"[AuthService] Erreur sauvegarde PKCE: {e}")

    def _generate_oauth_url(self) -> str:
        """Génère l'URL d'Authentification Google avec PKCE (Mode /authcode)."""
        ctx = self._get_pkce_context()
        if ctx:
            verifier = ctx["verifier"]
            state = ctx["state"]
        else:
            verifier = secrets.token_urlsafe(64)
            state = secrets.token_urlsafe(16)
            self._save_pkce_context(verifier, state)

        # Challenge S256
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().replace("=", "")
        
        params = {
            "client_id": ECHO_OAUTH_CLIENT_ID,
            "redirect_uri": "https://codeassist.google.com/authcode",
            "response_type": "code",
            "scope": " ".join(ECHO_OAUTH_SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent"
        }
        return f"{GOOGLE_OAUTH_AUTH_URL}?{urlencode(params)}"

    def get_auth_prompt(self) -> str:
        """Retourne le message d'instruction pour la configuration unifiée avec avertissement de priorité."""
        oauth_url = self._generate_oauth_url()
        return (
            f"🔐 **Configuration de l'Authentification ECHO**\n\n"
            f"⚠️ **Note sur l'Authentification API Gemini :** L'utilisation d'un compte Google (OAuth2) est prioritaire. "
            f"Si vous fournissez un code d'accès, vos clés API ne seront pas enregistrées, sauf si votre compte Google est inopérant, "
            f"afin de garantir la continuité du fonctionnement de Gemini.\n\n"
            f"1. **Authentification Google** : [Cliquez ici pour obtenir votre code d'accès]({oauth_url}). *(Idéal pour vos crédits personnels)*\n"
            f"2. **Clés d'API Google AI Studio** : [Créez vos clés ici]({GOOGLE_AI_STUDIO_WEB_URL}). *(L'ordre de saisie définit la priorité Primaire/Secondaire)*\n\n"
            f"**Action :** Collez vos **Identifiants** ci-dessous, séparés par un espace.\n\n"
            f"*(ECHO_SESSION_AUTH_PENDING)*"
        )

    async def _fetch_google_user_info(self, access_token: str) -> Optional[str]:
        """Récupère l'adresse email du compte Google lié."""
        client = await _get_global_client()
        try:
            resp = await client.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("email")
        except Exception as e:
            print(f"[AuthService] Erreur UserInfo: {e}")
        return None

    async def fetch_user_quota(self, access_token: str, project_id: str):
        """Récupère et persiste les quotas et crédits Code Assist (Truth Source)."""
        client = await _get_global_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": ECHO_USER_AGENT
        }

        # 1. RAFRAÎCHISSEMENT DES CRÉDITS (Mode HEALTH_CHECK)
        health_url = f"{CODE_ASSIST_BASE_URL}:loadCodeAssist"
        health_payload = {
            "cloudaicompanionProject": project_id,
            "metadata": {**ECHO_CLIENT_METADATA, "duetProject": project_id},
            "mode": "HEALTH_CHECK"
        }
        
        try:
            h_resp = await client.post(health_url, json=health_payload, headers=headers, timeout=15)
            if h_resp.status_code == 200:
                data = h_resp.json()
                t_obj = data.get("paidTier") or data.get("currentTier")
                if isinstance(t_obj, dict):
                    avail = t_obj.get("availableCredits") or []
                    for c in avail:
                        if c.get("creditType") == "GOOGLE_ONE_AI":
                            self.echo_auth.save_api_key("google_g1_credits", str(c.get("creditAmount", "0")))
                            break
        except: pass

        # 2. RAFRAÎCHISSEMENT DES QUOTAS (retrieveUserQuota)
        try:
            q_resp = await client.post(f"{CODE_ASSIST_BASE_URL}:retrieveUserQuota", json={"project": project_id}, headers=headers, timeout=15)
            if q_resp.status_code == 200:
                data = q_resp.json()
                buckets = data.get("buckets", [])
                if buckets:
                    b = buckets[0] 
                    self.echo_auth.save_api_key("google_quota_amount", str(b.get("remainingAmount", "N/A")))
                    self.echo_auth.save_api_key("google_quota_fraction", str(b.get("remainingFraction", "1.0")))
                    self.echo_auth.save_api_key("google_quota_reset", str(b.get("resetTime", "N/A")))
                    self.echo_auth.save_api_key("google_quota_type", str(b.get("tokenType", "UNKNOWN")))
                    self.echo_auth.save_api_key("google_quota_last_fetch", str(time.time()))
        except Exception as e:
            print(f"[AuthService] Erreur récupération quota: {e}")

    async def refresh_quota_if_needed(self):
        """Rafraîchit les métriques si OAuth2 est actif (Treshold: 10 min)."""
        priority = self.echo_auth.get_auth_data("google_auth_priority")
        if priority != AUTH_METHOD_OAUTH2: return

        try:
            last_fetch = float(self.echo_auth.get_auth_data("google_quota_last_fetch") or 0)
        except: last_fetch = 0

        if time.time() - last_fetch < 600: return 

        access_token = self.echo_auth.get_auth_data("google_oauth2_access_token")
        project_id = self.echo_auth.get_auth_data(AUTH_DATA_PROJECT_ID)

        if access_token and project_id:
            await self.fetch_user_quota(access_token, project_id)

    async def _provision_google_account(self, access_token: str) -> Tuple[Optional[str], Optional[str]]:
        """Séquence de Provisioning Protocolée : Découverte du Tier et Capture de l'ID Projet."""
        client = await _get_global_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": ECHO_USER_AGENT
        }
        
        project_id = None
        tier_id = None

        def _extract_project_id(data: Dict) -> Optional[str]:
            """Gère le polymorphisme Google (String vs Dictionnaire {'id': '...'}) pour le projectId."""
            raw = data.get("cloudaicompanionProject")
            if isinstance(raw, dict): return raw.get("id")
            return raw # Cas String ou None

        async def _extract_tier_info(data: Dict) -> Tuple[Optional[str], int]:
            """Extrait l'ID du Tier et le solde des crédits AI."""
            t_obj = data.get("paidTier") or data.get("paid_tier") or data.get("currentTier") or data.get("current_tier")
            tier_id = None
            credits = 0
            if isinstance(t_obj, dict):
                tier_id = t_obj.get("id")
                avail = t_obj.get("availableCredits") or t_obj.get("available_credits") or []
                for c in avail:
                    if c.get("creditType") == "GOOGLE_ONE_AI":
                        try: credits += int(c.get("creditAmount", 0))
                        except: pass
            return tier_id, credits

        try:
            # 1. DÉCOUVERTE (loadCodeAssist)
            load_payload = {"metadata": {**ECHO_CLIENT_METADATA, "duetProject": None}}
            resp = await client.post(f"{CODE_ASSIST_BASE_URL}:loadCodeAssist", json=load_payload, headers=headers, timeout=20)
            
            if resp.status_code == 200:
                data = resp.json()
                project_id = _extract_project_id(data)
                tier_id, g1_credits = await _extract_tier_info(data)
                
                if g1_credits > 0:
                    self.echo_auth.save_api_key("google_g1_credits", str(g1_credits))

                if project_id:
                    return project_id, tier_id or "standard-tier"

                # 2. SÉLECTION DU TIER PAR DÉFAUT
                allowed_tiers = data.get("allowedTiers") or data.get("allowed_tiers") or []
                selected_tier = None
                for t in allowed_tiers:
                    if t.get("isDefault") or t.get("is_default"):
                        selected_tier = t.get("id")
                        break
                
                tier_id = selected_tier or "free-tier"

                # 3. ONBOARDING CHIRURGICAL
                onboard_payload = {
                    "tierId": tier_id,
                    "metadata": {**ECHO_CLIENT_METADATA, "duetProject": project_id}
                }
                if tier_id != "free-tier":
                    onboard_payload["cloudaicompanionProject"] = None

                onboard_resp = await client.post(f"{CODE_ASSIST_BASE_URL}:onboardUser", json=onboard_payload, headers=headers, timeout=20)
                
                if onboard_resp.status_code == 200:
                    lro = onboard_resp.json()
                    if lro.get("done") and lro.get("response"):
                        project_id = _extract_project_id(lro["response"])
                        return project_id, tier_id

                    # 4. RÉSOLUTION DE L'OPÉRATION
                    op_name = lro.get("name")
                    if op_name:
                        for _ in range(12): 
                            await asyncio.sleep(5)
                            op_resp = await client.get(f"{CODE_ASSIST_BASE_URL}/{op_name}", headers=headers, timeout=10)
                            if op_resp.status_code == 200:
                                op_data = op_resp.json()
                                if op_data.get("done"):
                                    res_obj = op_data.get("response", {})
                                    project_id = _extract_project_id(res_obj)
                                    final_tier, final_credits = await _extract_tier_info(res_obj)
                                    if final_credits > 0:
                                        self.echo_auth.save_api_key("google_g1_credits", str(final_credits))
                                    return project_id, final_tier or tier_id


        except Exception as e:
            print(f"[AuthService] Erreur Provisioning Critique: {e}")
        
        return project_id, tier_id

    async def _exchange_oauth_code(self, code: str) -> Tuple[bool, str]:
        """Échange le code, récupère l'identité et provisionne le compte."""
        ctx = self._get_pkce_context()
        if not ctx:
            return False, "Contexte PKCE expiré ou inexistant. Veuillez générer un nouveau lien."
        
        client = await _get_global_client()
        payload = {
            "client_id": ECHO_OAUTH_CLIENT_ID,
            "client_secret": ECHO_OAUTH_CLIENT_SECRET,
            "code": code,
            "code_verifier": ctx["verifier"],
            "grant_type": "authorization_code",
            "redirect_uri": "https://codeassist.google.com/authcode"
        }
        
        try:
            resp = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                refresh_token = data.get("refresh_token")
                access_token = data.get("access_token")
                if refresh_token:
                    self.echo_auth.save_api_key("google_oauth2_refresh_token", refresh_token)
                    self.echo_auth.save_api_key("google_oauth2_last_refresh", str(time.time()))
                    if access_token:
                        self.echo_auth.save_api_key("google_oauth2_access_token", access_token)
                        
                        # --- IDENTITÉ & PROVISIONING ---
                        email = await self._fetch_google_user_info(access_token)
                        project_id, tier_id = await self._provision_google_account(access_token)
                        
                        if email: self.echo_auth.save_api_key(AUTH_DATA_USER_EMAIL, email)
                        if project_id: 
                            self.echo_auth.save_api_key(AUTH_DATA_PROJECT_ID, project_id)
                        else:
                            return False, "Échec du provisioning : Aucun Project ID Google Cloud n'a pu être récupéré."
                            
                        if tier_id: self.echo_auth.save_api_key(AUTH_DATA_USER_TIER, tier_id)
                        
                        # --- QUOTA ---
                        if access_token and project_id:
                            await self.fetch_user_quota(access_token, project_id)
                        
                        msg = f"✅ Authentification Google réussie pour {email or 'Compte Inconnu'}."
                        if tier_id: msg += f" Tier : {tier_id}."
                        msg += f" Projet : {project_id}."
                        return True, msg
                return False, "Le serveur n'a pas renvoyé de jetons valides."
            else:
                return False, f"Échec Google OAuth (HTTP {resp.status_code}): {resp.text}"
        except Exception as e:
            return False, f"Erreur réseau lors de l'échange OAuth: {str(e)}"

    async def validate_and_save_api_key(self, raw_input: str) -> Tuple[bool, str]:
        """Traitement intelligent avec Stratégie d'Authentification Exclusive (Priorité OAuth2)."""
        
        tokens = raw_input.split()
        found_keys = []
        found_oauth_code = None
        
        for t in tokens:
            if re.match(GOOGLE_API_KEY_PATTERN, t):
                if len(found_keys) < 2:
                    found_keys.append(t)
            elif re.match(GOOGLE_OAUTH_CODE_REGEX, t):
                if not found_oauth_code:
                    found_oauth_code = t

        if not found_keys and not found_oauth_code:
            return False, "Aucun identifiant valide détecté (Clé AIza ou Code 4/)."

        # --- PRIORITÉ 1 : OAuth2 ---
        if found_oauth_code:
            success, msg = await self._exchange_oauth_code(found_oauth_code)
            if success:
                # Enregistrement exclusif OAuth2
                self.echo_auth.save_api_key("google_auth_priority", AUTH_METHOD_OAUTH2)
                
                # Suppression du contexte PKCE
                db_path = self.echo_auth._get_db_path()
                try:
                    with sqlite3.connect(db_path) as conn:
                        conn.execute("DELETE FROM auth_pkce_context WHERE user_id = ?", (self.user_id,))
                except: pass
                
                return True, f"✅ OAuth2 activé. Vos clés API ont été ignorées pour garantir la stabilité de vos sessions. | {msg}"

        # --- PRIORITÉ 2 : Clés d'API (AI Studio) ---
        # Exécutée si pas de code OAuth2 ou si son échange a échoué
        if found_keys:
            success_msgs = []
            error_msgs = []
            valid_keys = []

            async with httpx.AsyncClient() as client:
                for i, k in enumerate(found_keys):
                    test_url = f"{GOOGLE_API_BASE_URL}/models?key={k}"
                    try:
                        resp = await client.get(test_url, headers={"User-Agent": ECHO_USER_AGENT}, timeout=10)
                        if resp.status_code == 200:
                            valid_keys.append(k)
                            label = "Primaire" if i == 0 else "Secondaire"
                            success_msgs.append(f"Clé d'API {label} validée.")
                        else:
                            error_msgs.append(f"Clé {i+1} rejetée (HTTP {resp.status_code}).")
                    except Exception as e:
                        error_msgs.append(f"Erreur réseau Clé {i+1} ({str(e)}).")

            if valid_keys:
                # Sauvegarde des clés en préservant l'ordre de saisie
                self.echo_auth.save_api_key(AUTH_METHOD_KEY_PRIMARY, valid_keys[0])
                priority = [AUTH_METHOD_KEY_PRIMARY]
                if len(valid_keys) > 1:
                    self.echo_auth.save_api_key(AUTH_METHOD_KEY_SECONDARY, valid_keys[1])
                    priority.append(AUTH_METHOD_KEY_SECONDARY)
                
                # Mise à jour de la priorité (monotype clés)
                self.echo_auth.save_api_key("google_auth_priority", ",".join(priority))
                
                return True, f"✅ Clés d'API activées. (Note : Compte Google inopérant ou non fourni). | " + " | ".join(success_msgs)
        
        return False, "Échec de la validation."
