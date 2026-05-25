"""
title: ECHO Auth Service
author: Wilfried BARNAVON
version: 7.2
description: 5.x: PKCE flow avec serveur asyncio TCP sur port fixe 8765.
             6.0: Tentative FastAPI callback endpoint (annulée).
             7.0: Tunnel SSH éphémère via asyncssh (echo_ssh_tunnel.py).
             Ports dynamiques dans la plage ECHO_AUTH_PORT_RANGE_*.
             Callback TCP via echo_pkce_server.py (localhost uniquement).
             get_auth_prompt() : commande SSH complète avec IP/ports détectés.
             7.1: Fix fetchAvailableModels : parsing défensif (type-check avant .get("models"))
             pour éviter 'str' object has no attribute 'get' si format API inattendu.
             7.2: Fix fetchAvailableModels : l'API retourne models comme dict {id: data},
             non une liste. Capture quotaInfo par modèle → google_quota_by_model (JSON).
             fetch_user_quota : capture tous les types de crédits → google_credits_total.
"""

import time
import orjson as std_json
import re
import sys
import asyncio
import hashlib
import os
import uuid
import sqlite3
import random
from typing import Optional, Tuple, Any, List, Dict
from urllib.parse import urlencode, urlparse, parse_qs
import base64

import httpx

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, _get_global_client
# Note : echo_ssh_tunnel et echo_pkce_server sont importés en lazy loading
# dans initiate_pkce_flow() / await_pkce_callback() pour éviter l'échec au
# chargement si asyncssh n'est pas encore installé (premier démarrage du pipe).

from echo_constants import (
    GOOGLE_API_BASE_URL,
    ECHO_USER_AGENT,
    ECHO_CODE_ASSIST_USER_AGENT,
    GOOGLE_API_KEY_PATTERN,
    GOOGLE_AI_STUDIO_WEB_URL,
    GOOGLE_OAUTH_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    ANTIGRAVITY_DESKTOP_CLIENT_ID,
    ANTIGRAVITY_DESKTOP_CLIENT_SECRET,
    ECHO_OAUTH_SCOPES,
    GOOGLE_AUTH_URL,
    AUTH_METHOD_KEY_PRIMARY,
    AUTH_METHOD_KEY_SECONDARY,
    AUTH_METHOD_OAUTH2,
    CODE_ASSIST_BASE_URL,
    ECHO_CLIENT_METADATA,
    AUTH_DATA_PROJECT_ID,
    AUTH_DATA_USER_EMAIL,
    AUTH_DATA_USER_TIER,
    ECHO_SSH_TUNNEL_USER,
    ECHO_SSH_TUNNEL_TIMEOUT,
    ECHO_SSH_PORT_RANGE_START,
    ECHO_SSH_PORT_RANGE_END,
    ECHO_CALLBACK_PORT_RANGE_START,
    ECHO_CALLBACK_PORT_RANGE_END,
)

class AuthService:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.base_url = GOOGLE_API_BASE_URL
        self.echo_auth = EchoAuth(user_id=user_id)

    # ==========================================================================
    # PKCE + AUTHORIZATION CODE FLOW (RFC 7636 + RFC 6749)
    # Client Desktop Antigravity.
    # Tunnel SSH éphémère via asyncssh (echo_ssh_tunnel.py).
    # Callback TCP via echo_pkce_server.py sur localhost uniquement.
    # Ports alloués dynamiquement — multi-user natif.
    # ==========================================================================

    def _pkce_generate(self) -> Tuple[str, str]:
        """
        Génère un couple (code_verifier, code_challenge) conforme RFC 7636.
        code_verifier : 64 octets random en base64url (sans padding)
        code_challenge : SHA-256(code_verifier) en base64url (sans padding)
        """
        raw          = os.urandom(64)
        verifier     = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        digest       = hashlib.sha256(verifier.encode()).digest()
        challenge    = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return verifier, challenge

    def build_auth_url(self, code_challenge: str, state: str, redirect_uri: str) -> str:
        """
        Construit l'URL d'autorisation Google avec PKCE (RFC 7636 + RFC 8252).
        redirect_uri = localhost:{callback_port}/callback — port alloué dynamiquement.
        Google accepte n'importe quel port loopback (RFC 8252 §7.3).
        """
        params = {
            "client_id":             ANTIGRAVITY_DESKTOP_CLIENT_ID,
            "redirect_uri":          redirect_uri,
            "response_type":         "code",
            "scope":                 " ".join(ECHO_OAUTH_SCOPES),
            "code_challenge":        code_challenge,
            "code_challenge_method": "S256",
            "access_type":           "offline",
            "prompt":                "consent",
            "state":                 state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def initiate_pkce_flow(self, request=None) -> Tuple[bool, str, str, int, int, str]:
        """
        Lance le flow PKCE :
        1. Démarre le serveur SSH éphémère (echo_ssh_tunnel.py) — ports dynamiques
        2. Génère code_verifier + code_challenge (RFC 7636 S256)
        3. Construit l'URL OAuth2 avec redirect_uri dynamique
        4. Persiste verifier + state en SQLite
        Retourne (True, auth_url, server_ip, ssh_port, callback_port, temp_password).
        """
        import secrets as _secrets
        from echo_ssh_tunnel import EchoSSHTunnelServer  # type: ignore

        self._ssh_server = EchoSSHTunnelServer(
            ssh_range_start      = ECHO_SSH_PORT_RANGE_START,
            ssh_range_end        = ECHO_SSH_PORT_RANGE_END,
            callback_range_start = ECHO_CALLBACK_PORT_RANGE_START,
            callback_range_end   = ECHO_CALLBACK_PORT_RANGE_END,
            tunnel_user          = ECHO_SSH_TUNNEL_USER,
            timeout              = ECHO_SSH_TUNNEL_TIMEOUT,
        )
        server_ip, ssh_port, cb_port, temp_pwd = await self._ssh_server.start(request=request)
        self._cb_port = cb_port
        redirect_uri  = f"http://localhost:{cb_port}/callback"

        code_verifier, code_challenge = self._pkce_generate()
        state    = _secrets.token_urlsafe(16)
        auth_url = self.build_auth_url(code_challenge, state, redirect_uri=redirect_uri)

        # Persistance SQLite (survit à la durée du flow)
        self.echo_auth.save_api_key("pkce_code_verifier", code_verifier)
        self.echo_auth.save_api_key("pkce_state",         state)
        self.echo_auth.save_api_key("pkce_status",        "pending")
        self.echo_auth.save_api_key("pkce_redirect_uri",  redirect_uri)

        return True, auth_url, server_ip, ssh_port, cb_port, temp_pwd

    async def await_pkce_callback(self) -> Tuple[bool, str]:
        """
        Démarre le serveur de callback TCP (echo_pkce_server.py) sur localhost:{cb_port}.
        Attend le code OAuth2 via asyncio.Queue (timeout ECHO_SSH_TUNNEL_TIMEOUT).
        À la fin : arrête le serveur SSH et le serveur callback.
        Retourne (succès: bool, message: str).
        """
        from echo_pkce_server import EchoPKCECallbackServer  # type: ignore

        code_verifier  = self.echo_auth.get_auth_data("pkce_code_verifier")
        expected_state = self.echo_auth.get_auth_data("pkce_state")
        redirect_uri   = self.echo_auth.get_auth_data("pkce_redirect_uri") or ""
        cb_port        = getattr(self, "_cb_port", 0)

        if not code_verifier or not expected_state or not cb_port:
            return False, "Session PKCE expirée ou absente. Relancez l'authentification."

        pkce_srv = EchoPKCECallbackServer()
        code_queue = await pkce_srv.start(
            callback_port  = cb_port,
            expected_state = expected_state,
        )

        auth_code: Optional[str] = None
        error_msg: Optional[str] = None
        try:
            result = await asyncio.wait_for(
                code_queue.get(),
                timeout=ECHO_SSH_TUNNEL_TIMEOUT,
            )
            if result.startswith("ERROR:"):
                error_msg = result[6:]  # strip "ERROR:"
            else:
                auth_code = result
        except asyncio.TimeoutError:
            error_msg = f"Timeout — aucune réponse en {ECHO_SSH_TUNNEL_TIMEOUT}s."
        finally:
            await pkce_srv.stop()
            if hasattr(self, "_ssh_server"):
                await self._ssh_server.stop()
            # Nettoyage SQLite
            self.echo_auth.save_api_key("pkce_code_verifier", "")
            self.echo_auth.save_api_key("pkce_state",         "")
            self.echo_auth.save_api_key("pkce_status",        "" if error_msg else "done")
            self.echo_auth.save_api_key("pkce_redirect_uri",  "")

        if error_msg:
            return False, f"Erreur d'authentification : {error_msg} Relancez en envoyant un message."

        return await self._exchange_pkce_code(auth_code, code_verifier, redirect_uri)

    async def _exchange_pkce_code(
        self,
        auth_code:     str,
        code_verifier: str,
        redirect_uri:  str,
    ) -> Tuple[bool, str]:
        """Échange le code OAuth2 contre access_token + refresh_token, puis provisioning."""
        client = await _get_global_client()
        try:
            resp = await client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "client_id":     ANTIGRAVITY_DESKTOP_CLIENT_ID,
                    "client_secret": ANTIGRAVITY_DESKTOP_CLIENT_SECRET,
                    "code":          auth_code,
                    "code_verifier": code_verifier,
                    "redirect_uri":  redirect_uri,
                    "grant_type":    "authorization_code",
                },
                timeout=20
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return False, f"Erreur lors de l'échange du code : {e}"

        access_token  = data.get("access_token")
        refresh_token = data.get("refresh_token")

        if not access_token:
            return False, f"Tokens absents dans la réponse Google : {data}"

        # Persistance
        if refresh_token:
            self.echo_auth.save_api_key("google_oauth2_refresh_token", refresh_token)
        self.echo_auth.save_api_key("google_oauth2_access_token",  access_token)
        self.echo_auth.save_api_key("google_oauth2_last_refresh",   str(time.time()))

        # Identité & Provisioning
        email      = await self._fetch_google_user_info(access_token)
        project_id, tier_id = await self._provision_google_account(access_token)

        if email:      self.echo_auth.save_api_key(AUTH_DATA_USER_EMAIL, email)
        if tier_id:    self.echo_auth.save_api_key(AUTH_DATA_USER_TIER,  tier_id)
        if project_id:
            self.echo_auth.save_api_key(AUTH_DATA_PROJECT_ID, project_id)
        else:
            return False, "Provisioning échoué : aucun Project ID obtenu depuis loadCodeAssist."

        self.echo_auth.save_api_key("google_auth_priority", AUTH_METHOD_OAUTH2)

        # Quota initial + modèles disponibles
        await self.fetch_user_quota(access_token, project_id)
        models = await self.fetch_available_models(access_token, project_id)
        if models:
            self.echo_auth.save_api_key("google_available_models", std_json.dumps(models).decode())

        msg = f"✅ Authentification PKCE réussie pour {email or 'Compte inconnu'}."
        if tier_id:    msg += f" Tier : {tier_id}."
        if project_id: msg += f" Projet : {project_id}."
        return True, msg

    def get_auth_prompt(
        self,
        auth_url:  str = "",
        server_ip: str = "",
        ssh_port:  int = 0,
        cb_port:   int = 0,
        temp_pwd:  str = "",
    ) -> str:
        """
        Message d'instruction unifié affiché dans le chat ECHO.
        Quand auth_url est fourni : affiche commande SSH + mot de passe + lien Google.
        Sinon : invite à envoyer un message pour générer le lien.
        """
        if auth_url and server_ip and ssh_port and cb_port and temp_pwd:
            # -o StrictHostKeyChecking=no : obligatoire - la cle hote SSH est ephemere
            # (regeneree a chaque session). Sans cette option le client SSH bloque sur
            # "Are you sure you want to continue connecting (yes/no/[fingerprint])?"
            # -o UserKnownHostsFile=/dev/null : evite les conflits dans ~/.ssh/known_hosts
            ssh_cmd = (
                f"ssh -N "
                f"-o StrictHostKeyChecking=no "
                f"-o UserKnownHostsFile=/dev/null "
                f"-L {cb_port}:localhost:{cb_port} "
                f"{ECHO_SSH_TUNNEL_USER}@{server_ip} -p {ssh_port}"
            )
            pkce_section = (
                f"1. **Authentification Google (OAuth2 \u2014 Recommandé)**\n\n"
                f"> 🖥️ **Étape 1** \u2014 Ouvrez un terminal et exécutez :\n"
                f"> ```\n"
                f"> {ssh_cmd}\n"
                f"> ```\n"
                f"> Mot de passe : `{temp_pwd}` \u00a0 ⏱️ *Valide {ECHO_SSH_TUNNEL_TIMEOUT}\u00a0secondes*\n\n"
                f"> 🔗 **Étape 2** \u2014 [Autoriser ECHO avec Google]({auth_url})\n\n"
                f"> *L'adresse IP et les ports sont détectés automatiquement depuis votre connexion.*\n"
            )
        else:
            pkce_section = (
                "1. **Authentification Google (OAuth2 \u2014 Recommandé)** \u2014 "
                "Envoyez n'importe quel message pour générer le lien d'autorisation.\n"
            )

        return (
            "\U0001f510 **Configuration de l'Authentification ECHO**\n\n"
            "⚠️ **Priorisation des accès :** L'authentification Google (OAuth2) est toujours prioritaire. "
            "Si vous fournissez une clé API, elle ne sera enregistrée que si la vérification "
            "du bon fonctionnement d'OAuth échoue.\n\n"
            f"{pkce_section}\n"
            f"2. **Clés d'API Google AI Studio** : [Créez vos clés ici]({GOOGLE_AI_STUDIO_WEB_URL}). "
            "*(L'ordre de saisie définit la priorité Principale/Secondaire)*\n\n"
            "**Action :** Collez directement une clé `AIza\u2026` dans ce chat \u2014 "
            "ECHO la détecte, la valide et l'enregistre automatiquement.\n\n"
            "*(ECHO_SESSION_AUTH_PENDING)*"
        )



    # ==========================================================================
    # IDENTITÉ & PROVISIONING
    # ==========================================================================

    async def _fetch_google_user_info(self, access_token: str) -> Optional[str]:
        """Récupère l'adresse email du compte Google lié."""
        client = await _get_global_client()
        try:
            resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10
            )
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
            "Content-Type":  "application/json",
            "User-Agent":    ECHO_CODE_ASSIST_USER_AGENT
        }

        # 1. RAFRAÎCHISSEMENT DES CRÉDITS (Mode HEALTH_CHECK)
        health_payload = {
            "cloudaicompanionProject": project_id,
            "metadata": {**ECHO_CLIENT_METADATA, "duetProject": project_id},
            "mode": "HEALTH_CHECK"
        }
        try:
            h_resp = await client.post(
                f"{CODE_ASSIST_BASE_URL}:loadCodeAssist",
                json=health_payload, headers=headers, timeout=15
            )
            if h_resp.status_code == 200:
                data  = h_resp.json()
                t_obj = data.get("paidTier") or data.get("currentTier")
                if isinstance(t_obj, dict):
                    avail = t_obj.get("availableCredits") or []
                    # Capture de tous les crédits disponibles
                    total = sum(int(c.get("creditAmount", 0)) for c in avail if c.get("creditAmount"))
                    if total > 0:
                        self.echo_auth.save_api_key("google_credits_total", str(total))
                    # Backward compat : clé spécifique GOOGLE_ONE_AI
                    for c in avail:
                        if c.get("creditType") == "GOOGLE_ONE_AI":
                            self.echo_auth.save_api_key("google_g1_credits", str(c.get("creditAmount", "0")))
                            break
        except: pass

        # 2. RAFRAÎCHISSEMENT DES QUOTAS (retrieveUserQuota)
        try:
            q_resp = await client.post(
                f"{CODE_ASSIST_BASE_URL}:retrieveUserQuota",
                json={"project": project_id}, headers=headers, timeout=15
            )
            if q_resp.status_code == 200:
                data    = q_resp.json()
                buckets = data.get("buckets", [])
                if buckets:
                    b = buckets[0]
                    self.echo_auth.save_api_key("google_quota_amount",   str(b.get("remainingAmount", "N/A")))
                    self.echo_auth.save_api_key("google_quota_fraction",  str(b.get("remainingFraction", "1.0")))
                    self.echo_auth.save_api_key("google_quota_reset",     str(b.get("resetTime", "N/A")))
                    self.echo_auth.save_api_key("google_quota_type",      str(b.get("tokenType", "UNKNOWN")))
                    self.echo_auth.save_api_key("google_quota_last_fetch", str(time.time()))
        except Exception as e:
            print(f"[AuthService] Erreur récupération quota: {e}")

    async def fetch_available_models(self, access_token: str, project_id: str) -> list:
        """
        Appel post-provisioning : récupère la liste dynamique des modèles disponibles
        et capture le quotaInfo par modèle CA.
        L'API retourne models comme dict {ca_model_id: {quotaInfo, displayName, ...}}.
        """
        import json as _j
        client = await _get_global_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
            "User-Agent":    ECHO_CODE_ASSIST_USER_AGENT,
        }
        try:
            resp = await client.post(
                f"{CODE_ASSIST_BASE_URL}:fetchAvailableModels",
                json={"project": project_id},
                headers=headers, timeout=15
            )
            if resp.status_code == 200:
                raw = resp.json()
                print(f"[AuthService] fetchAvailableModels raw response: {str(raw)[:300]}")

                # L'API retourne {"models": {ca_model_id: {...}}} (dict, pas liste)
                models_raw = raw.get("models") or raw.get("availableModels") or {}

                if isinstance(models_raw, dict):
                    model_ids = list(models_raw.keys())
                    # Capture quotaInfo par modèle CA
                    quota_map = {
                        mid: {
                            "remainingFraction": float(mdata.get("quotaInfo", {}).get("remainingFraction", 1.0)),
                            "resetTime": str(mdata.get("quotaInfo", {}).get("resetTime", "N/A"))
                        }
                        for mid, mdata in models_raw.items()
                        if isinstance(mdata, dict) and mdata.get("quotaInfo")
                    }
                elif isinstance(models_raw, list):
                    # Format liste (compaté future)
                    model_ids = [
                        m.get("id") or m.get("name") or m.get("model")
                        for m in models_raw
                        if isinstance(m, dict) and (m.get("id") or m.get("name") or m.get("model"))
                    ]
                    quota_map = {}
                else:
                    model_ids = []
                    quota_map = {}

                # Persistance du quota par modèle
                if quota_map:
                    self.echo_auth.save_api_key("google_quota_by_model", _j.dumps(quota_map))
                    # Legacy : premier modèle avec quotaInfo comme référence globale
                    first_qi = next(iter(quota_map.values()))
                    self.echo_auth.save_api_key("google_quota_fraction",   str(first_qi["remainingFraction"]))
                    self.echo_auth.save_api_key("google_quota_reset",      first_qi["resetTime"])
                    self.echo_auth.save_api_key("google_quota_type",       "CODE_ASSIST")
                    self.echo_auth.save_api_key("google_quota_last_fetch", str(time.time()))

                return [mid for mid in model_ids if mid]
            else:
                print(f"[AuthService] fetchAvailableModels HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[AuthService] fetchAvailableModels erreur: {e}")
        return []

    async def refresh_quota_if_needed(self):
        """Rafraîchit les métriques si OAuth2 est actif (Threshold: 10 min)."""
        priority = self.echo_auth.get_auth_data("google_auth_priority")
        if priority != AUTH_METHOD_OAUTH2: return

        try:
            last_fetch = float(self.echo_auth.get_auth_data("google_quota_last_fetch") or 0)
        except: last_fetch = 0

        if time.time() - last_fetch < 600: return

        access_token = self.echo_auth.get_auth_data("google_oauth2_access_token")
        project_id   = self.echo_auth.get_auth_data(AUTH_DATA_PROJECT_ID)

        if access_token and project_id:
            await self.fetch_user_quota(access_token, project_id)
            await self.fetch_available_models(access_token, project_id)

    async def _provision_google_account(self, access_token: str) -> Tuple[Optional[str], Optional[str]]:
        """Séquence de Provisioning Protocolée : Découverte du Tier et Capture de l'ID Projet."""
        client = await _get_global_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
            "User-Agent":    ECHO_CODE_ASSIST_USER_AGENT
        }

        project_id = None
        tier_id    = None

        def _extract_project_id(data: Dict) -> Optional[str]:
            """Gère le polymorphisme Google (String vs Dictionnaire {'id': '...'}) pour le projectId."""
            raw = data.get("cloudaicompanionProject")
            if isinstance(raw, dict): return raw.get("id")
            return raw  # Cas String ou None

        async def _extract_tier_info(data: Dict) -> Tuple[Optional[str], int]:
            """Extrait l'ID du Tier et le solde des crédits AI."""
            t_obj = data.get("paidTier") or data.get("paid_tier") or data.get("currentTier") or data.get("current_tier")
            tier_id = None
            credits = 0
            if isinstance(t_obj, dict):
                tier_id = t_obj.get("id")
                avail   = t_obj.get("availableCredits") or t_obj.get("available_credits") or []
                for c in avail:
                    if c.get("creditType") == "GOOGLE_ONE_AI":
                        try: credits += int(c.get("creditAmount", 0))
                        except: pass
            return tier_id, credits

        try:
            # 1. DÉCOUVERTE (loadCodeAssist)
            load_payload = {"metadata": {**ECHO_CLIENT_METADATA, "duetProject": None}}
            resp = await client.post(
                f"{CODE_ASSIST_BASE_URL}:loadCodeAssist",
                json=load_payload, headers=headers, timeout=20
            )

            if resp.status_code == 200:
                data       = resp.json()
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
                    "tierId":   tier_id,
                    "metadata": {**ECHO_CLIENT_METADATA, "duetProject": project_id}
                }
                if tier_id != "free-tier":
                    onboard_payload["cloudaicompanionProject"] = None

                onboard_resp = await client.post(
                    f"{CODE_ASSIST_BASE_URL}:onboardUser",
                    json=onboard_payload, headers=headers, timeout=20
                )

                if onboard_resp.status_code == 200:
                    lro = onboard_resp.json()
                    if lro.get("done") and lro.get("response"):
                        project_id = _extract_project_id(lro["response"])
                        return project_id, tier_id

                    # 4. RÉSOLUTION DE L'OPÉRATION (Long Running Operation)
                    op_name = lro.get("name")
                    if op_name:
                        for _ in range(12):
                            await asyncio.sleep(5)
                            op_resp = await client.get(
                                f"{CODE_ASSIST_BASE_URL}/{op_name}",
                                headers=headers, timeout=10
                            )
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

    # ==========================================================================
    # VALIDATION & SAUVEGARDE (Clés API AI Studio — Fallback)
    # ==========================================================================

    async def validate_and_save_api_key(self, raw_input: str) -> Tuple[bool, str]:
        """
        Traitement des clés API AI Studio (fallback OAuth2).
        Le Device Flow est déclenché séparément via la commande /auth start dans le pipe.
        """
        tokens     = raw_input.split()
        found_keys = []

        for t in tokens:
            if re.match(GOOGLE_API_KEY_PATTERN, t):
                if len(found_keys) < 2:
                    found_keys.append(t)

        if not found_keys:
            return False, "Aucune clé API valide détectée (format attendu : `AIza…`). Pour l'authentification OAuth2, tapez `/auth start`."

        # Validation des clés API
        success_msgs = []
        error_msgs   = []
        valid_keys   = []

        async with httpx.AsyncClient() as client:
            for i, k in enumerate(found_keys):
                test_url = f"{GOOGLE_API_BASE_URL}/models?key={k}"
                try:
                    resp = await client.get(
                        test_url,
                        headers={"User-Agent": ECHO_USER_AGENT},
                        timeout=10
                    )
                    if resp.status_code == 200:
                        valid_keys.append(k)
                        label = "Primaire" if i == 0 else "Secondaire"
                        success_msgs.append(f"Clé d'API {label} validée.")
                    else:
                        error_msgs.append(f"Clé {i+1} rejetée (HTTP {resp.status_code}).")
                except Exception as e:
                    error_msgs.append(f"Erreur réseau Clé {i+1} ({str(e)}).")

        if valid_keys:
            self.echo_auth.save_api_key(AUTH_METHOD_KEY_PRIMARY, valid_keys[0])
            priority = [AUTH_METHOD_KEY_PRIMARY]
            if len(valid_keys) > 1:
                self.echo_auth.save_api_key(AUTH_METHOD_KEY_SECONDARY, valid_keys[1])
                priority.append(AUTH_METHOD_KEY_SECONDARY)
            self.echo_auth.save_api_key("google_auth_priority", ",".join(priority))

            return True, "✅ Clé(s) d'API activée(s). | " + " | ".join(success_msgs)

        return False, "Échec de la validation. " + " | ".join(error_msgs)
