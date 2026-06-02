"""
title: ECHO Shared Utils (Core)
author: Wilfried BARNAVON
version: 7.26
description: 7.6: Ajout EchoGeminiClient.index_text_in_ephemeral_rag. 7.7: Migration Antigravity 2.1 —
             Mise à jour User-Agent Code Assist, header x-goog-api-client, préfixe user_prompt_id.
             Mise à jour credentials refresh token. Migration douce table auth_pkce_context.
             7.8: Fix mismatch client OAuth2 dans refresh_google_oauth_token().
             7.9: CODE_ASSIST_MODEL_MAP dans _prepare_request_context (CAS 1).
             7.10: Strip thinkingConfig sur route Code Assist — thinking encodé dans le nom du
             modèle. L'API l'accepte (diag 200) mais le champ est redondant.
             7.11: Délégation → echo_protocol.py (get_ca_model_id, build_ca_generation_config).
             MAX_TOKENS_DEFAULT 65536→65535 (universel AI Studio+CA). call_distillation
             default max_tokens 65000→MAX_TOKENS_DEFAULT.
             7.12: EchoAuth.get_model_quota() — lecture quota par modèle CA depuis identity.db.
             Correction commentaire _prepare_request_context (thinkingConfig non strippé depuis v1.1).
             7.13: Propagation renommage AGY : ECHO_CODE_ASSIST_USER_AGENT→ECHO_AGY_USER_AGENT,
             CODE_ASSIST_BASE_URL→AGY_BASE_URL (echo_constants v5.0). Variables is_code_assist→is_agy.
             7.14: Routage LOCAL_GEMMA dans call_distillation — distillation locale par défaut
             (Gemma 4 E4B via echo-gemma-distiller). Fallback API transparent si service indisponible.
             Ajout méthode _call_local_distiller (pattern generate_embedding).
             7.15: Crédits OAuth2 opt-in via UserValve pipe (persistance identity.db).
             enabled_credit_types omis par défaut (alignement AGY-IDE).
              7.16: generate_embedding() retry (1 retry, 2s backoff) + timeouts relaxés
              pour absorber les environnements contraints (Hyper-V sous charge) :
              7.23: Suppression 'target_model != MODEL_DISTILLATION' dans call_distillation pour autoriser le routage.
              embedding 30→60s, distiller 60→120s, index_rag 120→180s.
               7.17: Centralisation politique modèle Pipe → outils. resolve_model_policy(),
               clamp_model(), call_cascade(). Crédits via enable_paid_credits paramètre
               (suppression lecture identity.db). call_distillation : suppression fallback
               API Gemini 2.5 Flash (Gemma local uniquement).
               7.18: wrap_cascade_output() — retour enrichi pour rendre le modèle effectif
               visible au LLM orchestrateur. Status clamping (🔒) et cascade (⚡) dans
               call_cascade().
               7.19: Fallback SQLite echo_settings pour propagation politique Pipe → outils
               (OWUI ne propage pas __metadata__ custom). Table echo_settings + get_setting/
               save_setting. Retrait thought_archive (write-only, jamais lu).
               7.20: Refactoring status tri-état (success/warning/error). wrap_cascade_output :
               suppression clamped:true, ajout param reason. call_cascade : retour tuple à 3
               (data, model_key, reason) avec reason contextuel (policy/API error/exhausted).
               7.21: Ajout table codex_docs et méthodes CRUD (save_codex_record, delete_codex_record,
               get_codex_docs, clear_codex_records) pour le Codex ECHO.
               7.22: Fix OSError dans move_to_vault (création du dossier session manquante).
               7.25: Retrait définitif de l'import obsolète ECHO_GEMMA_URL suite à la dépréciation
               du distiller local. Corrige les HTTP 400/401 lors de l'import des libs partagées.
"""

import copy
import os
import sqlite3
import orjson as json
import pybase64 as base64
import requests
import time
import asyncio
import glob
import hashlib
import re
import httpx
import random
import shutil
from typing import Optional, Tuple, List, Set, Any, Union, Dict, AsyncGenerator, Literal

# Alias pour json standard si besoin
import orjson as std_json

# Importation directe (Strict)
from echo_constants import (
    ECHO_UPLOADS_TRANSIT_DIR, ECHO_VERSION_PATH,
    GOOGLE_API_BASE_URL, ECHO_USER_AGENT, ECHO_AGY_USER_AGENT, ECHO_USERS_ROOT,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    ECHO_RETRY_BASE_DELAY, ECHO_RETRY_MULTIPLIER,
    ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX,
    AUTH_METHOD_KEY_PRIMARY, AUTH_METHOD_OAUTH2, AUTH_METHOD_KEY_SECONDARY,
    ANTIGRAVITY_OAUTH_CLIENT_ID, ANTIGRAVITY_OAUTH_CLIENT_SECRET,   # client LS (884354919052)
    ANTIGRAVITY_DESKTOP_CLIENT_ID, ANTIGRAVITY_DESKTOP_CLIENT_SECRET, # client Desktop (1071006060591)
    GOOGLE_OAUTH_TOKEN_URL, AUTH_DATA_PROJECT_ID, AGY_BASE_URL,
    GOOGLE_OAUTH_TOKEN_LIFETIME, AUTH_DATA_USER_TIER,
    MAX_TOKENS_DEFAULT,  # Utilisé comme valeur par défaut de call_distillation.max_tokens
    ECHO_HTTP_CLIENT_TIMEOUT, ECHO_HTTP_MAX_CONNECTIONS,
    ECHO_HTTP_MAX_KEEPALIVE, ECHO_HTTP_KEEPALIVE_EXPIRY,
)
from echo_protocol import get_ca_model_id, build_ca_generation_config

# ==============================================================================
# ROUTAGE D'ARCHITECTURE (Session & Global)
# ==============================================================================
from echo_constants import ECHO_SESSION_DOMAINS, ECHO_GLOBAL_DOMAINS

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

# ==============================================================================
# SECTION 0 : CLIENT HTTP GLOBAL (HTTP/2)
# ==============================================================================

_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(
    timeout: int = None,
    max_connections: int = None,
    max_keepalive: int = None,
    keepalive_expiry: int = None
) -> httpx.AsyncClient:
    """Gestionnaire de client HTTP/2 STRICT (Mutualisé)."""
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
    
    timeout = timeout or ECHO_HTTP_CLIENT_TIMEOUT
    max_connections = max_connections or ECHO_HTTP_MAX_CONNECTIONS
    max_keepalive = max_keepalive or ECHO_HTTP_MAX_KEEPALIVE
    keepalive_expiry = keepalive_expiry or ECHO_HTTP_KEEPALIVE_EXPIRY
    
    now = time.time()

    if _SHARED_ASYNC_CLIENT and (now - _LAST_CLIENT_ACCESS > timeout):
        old_client = _SHARED_ASYNC_CLIENT; _SHARED_ASYNC_CLIENT = None
        try: await old_client.aclose()
        except: pass

    if _SHARED_ASYNC_CLIENT is None or _SHARED_ASYNC_CLIENT.is_closed:
        limits = httpx.Limits(
            max_keepalive_connections=max_keepalive,
            max_connections=max_connections,
            keepalive_expiry=keepalive_expiry
        )
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=timeout, limits=limits, http2=True)

    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

def get_stealth_headers(url: Optional[str] = None) -> Dict[str, str]:
    """Génère des en-têtes HTTP de haute fidélité pour simuler un navigateur réel (Stealth)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",    
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="123", "Not:A-Brand";v="8", "Google Chrome";v="123"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "image",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "cross-site",
        "sec-fetch-user": "?1",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1"
    }
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        headers["Host"] = parsed.netloc
        if any(x in parsed.netloc for x in ["wikimedia", "wikipedia"]):
             headers["sec-fetch-dest"] = "document"
             headers["sec-fetch-mode"] = "navigate"
             headers["sec-fetch-site"] = "none"
             headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    return headers

# ==============================================================================
# SECTION 1 : STANDARDS DE COMMUNICATION (MULTI-PARTS)
# ==============================================================================

def split_thought_process(text: str) -> Tuple[str, Optional[str]]:
    if not isinstance(text, str): return text, None
    for tag in ["think", "thought"]:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            thoughts = match.group(1).strip()
            clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
            return clean_text, thoughts
    return text, None

def wrap_tool_output(text: str, status: dict = None, echo_tool_multiparts: List[dict] = None, nouveaux_fichiers: List[dict] = None) -> dict:
    if nouveaux_fichiers:
        json_str = json.dumps(nouveaux_fichiers, option=json.OPT_INDENT_2).decode('utf-8')
        text += f"\n\n```json:nouveaux_artefacts\n{json_str}\n```"
    return {"text": text, "status": status or {"status": "success"}, "echo_tool_multiparts": echo_tool_multiparts or []}


def wrap_cascade_output(text: str, model_requested: str, model_used: str, status: dict = None, echo_tool_multiparts: List[dict] = None, reason: str = None) -> dict:
    """
    Enrichit wrap_tool_output avec les métadonnées de cascade.
    Le LLM orchestrateur voit model_used dans le status dict ET dans un préfixe texte si le modèle a changé.
    reason : cause du changement de modèle (ex: "policy", "503/429").
    """
    s = status or {"status": "success"}
    s["model_requested"] = model_requested
    s["model_used"] = model_used
    if model_requested != model_used:
        s["status"] = "warning"
        s["warning"] = reason or f"{model_requested} unavailable"
        text = f"[Modèle effectif : {model_used} (demandé : {model_requested})]\n\n{text}"
    return {"text": text, "status": s, "echo_tool_multiparts": echo_tool_multiparts or []}

# ==============================================================================
# SECTION 2 : RÉSOLUTION DE FICHIERS & VERSIONS
# ==============================================================================

def generate_echo_file_id(user_id: str, chat_id: str) -> str:
    ts = int(time.time() * 1000)
    return f"U_{user_id}_C_{chat_id}_T_{ts}"

def resolve_upload_file_path(user_id: str, file_id: str, uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR, chat_id: Optional[str] = None) -> Optional[str]:
    if not file_id: return None
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        if chat_id:
            user_vault = get_echo_session_path(user_id, chat_id, "files")
            pattern = os.path.join(user_vault, f"{file_id}_*")
            matches = glob.glob(pattern)
            if matches: return matches[0]
        else:
            # Fallback de sécurité si appelé hors contexte chat_id
            user_chats = os.path.join(ECHO_USERS_ROOT, safe_uid, "chats")
            pattern = os.path.join(user_chats, "*", "files", f"{file_id}_*")
            matches = glob.glob(pattern)
            if matches: return matches[0]
            
    pattern = os.path.join(uploads_dir, f"{file_id}_*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None

def get_echo_version() -> str:
    try:
        if os.path.exists(ECHO_VERSION_PATH):
            with open(ECHO_VERSION_PATH, "r") as f: return f.read().strip()
    except: pass
    return ""

# ==============================================================================
# SECTION 2b : POLITIQUE MODÈLE CENTRALISÉE (Pipe → outils via __metadata__)
# ==============================================================================

def resolve_model_policy(metadata: dict, user_id: str = None) -> tuple:
    """
    Résout la politique modèle depuis __metadata__ (injecté par le Pipe).
    Fallback : lecture identity.db si absent de metadata (OWUI ne propage pas __metadata__ du pipe aux outils).
    Retourne (mode, plafond_key).
    - mode "fixed" + plafond = modèle forcé
    - mode "auto"/"auto_pro" + plafond = choix libre jusqu'au plafond
    """
    from echo_constants import MODEL_HIERARCHY
    selection = (metadata or {}).get("_echo_model_policy")

    # Fallback SQLite (echo_settings) si absent de metadata
    if not selection and user_id:
        try:
            state = EchoStateManager(user_id=user_id)
            selection = state.get_setting("model_policy")
        except Exception:
            pass

    if not selection:
        selection = "AUTO"

    if selection == "AUTO":
        return ("auto", "MODEL_FLASH")
    elif selection == "AUTO_PRO":
        return ("auto_pro", "MODEL_PRO")
    elif selection in MODEL_HIERARCHY:
        return ("fixed", selection)
    return ("auto", "MODEL_FLASH")


def clamp_model(requested: str, metadata: dict, user_id: str = None) -> str:
    """
    Applique la politique du Pipe sur un modèle demandé par un outil.
    Mode fixé → retourne le modèle fixé (ignore la demande).
    Mode auto → min(demandé, plafond).
    Fallback SQLite si la politique est absente de metadata.
    """
    from echo_constants import MODEL_HIERARCHY
    # user_id depuis metadata si non fourni
    uid = user_id or (metadata or {}).get("user_id")
    mode, ceiling = resolve_model_policy(metadata, user_id=uid)
    if mode == "fixed":
        return ceiling
    req_level = MODEL_HIERARCHY.get(requested, 1)
    ceil_level = MODEL_HIERARCHY.get(ceiling, 1)
    return ceiling if req_level > ceil_level else requested

# ==============================================================================
# SECTION 3 : GESTION DES ÉVÉNEMENTS (OWUI COMPAT)
# ==============================================================================

class EchoEvents:
    def __init__(self, emitter: Any = None, caller: Any = None):
        self.emitter = emitter; self.caller = caller
    async def emit(self, event_type: str, data: dict):
        if self.emitter:
            try: await self.emitter({"type": event_type, "data": data})
            except Exception as e: print(f"[EchoEvents] Emit Error: {e}")
    async def status(self, description: str, done: bool = False, hidden: bool = False):
        await self.emit("status", {"description": description, "done": done, "hidden": hidden})
    async def toast(self, content: str, level: str = "info"):
        await self.emit("notification", {"type": level, "content": content})
    async def call(self, event_type: str, data: dict) -> Any:
        if self.caller:
            try: return await self.caller({"type": event_type, "data": data})
            except Exception as e: print(f"[EchoEvents] Call Error: {e}")
        return None
    async def input(self, title: str, message: str, placeholder: str = "", type: str = "text") -> Optional[str]:
        return await self.call("input", {"title": title, "message": message, "placeholder": placeholder, "type": type})
    async def confirm(self, title: str, message: str) -> bool:
        res = await self.call("confirmation", {"title": title, "message": message})
        return bool(res)

# ==============================================================================
# SECTION 4 : IDENTITÉS D'ACCÈS AUX MODÈLES (Authentification)
# ==============================================================================

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

class EchoGeminiClient:
    """Moteur factorisé pour les appels API Gemini avec Architecture Symétrique (AI Studio & Code Assist)."""

    @staticmethod
    async def _get_auth_headers(provider: Dict, is_agy: bool = False, is_generation: bool = False) -> Dict[str, str]:
        """Génère les en-têtes d'authentification selon le type de fournisseur (Agnostique)."""
        if is_agy:
            # User-Agent Antigravity 2.1 (Language Server)
            ua = ECHO_AGY_USER_AGENT
        else:
            ua = ECHO_USER_AGENT

        headers = {
            "Content-Type":       "application/json",
            "User-Agent":         ua,
            "x-goog-api-client": "antigravity/2.1.0"
        }
        p_type = provider.get("type")

        if p_type == AUTH_METHOD_OAUTH2:
            token = provider.get("token") # Priorité au token direct fourni
            if not token:
                uid = provider.get("user_id")
                auth = EchoAuth(user_id=uid)
                token = auth.get_auth_data("google_oauth2_access_token", uid)
                last_refresh = float(auth.get_auth_data("google_oauth2_last_refresh", uid) or 0)

                # Rafraîchissement proactif si le jeton est expiré et qu'un refresh_token est présent
                refresh_token = provider.get("refresh_token")
                if refresh_token and (not token or (time.time() - last_refresh) > GOOGLE_OAUTH_TOKEN_LIFETIME):
                    token = await auth.refresh_google_oauth_token(refresh_token, uid)

            if token:
                headers["Authorization"] = f"Bearer {token}"
                # Pour Code Assist Generation, le project est dans le payload JSON.
                # L'ajout du header x-goog-user-project peut provoquer une erreur 403 Forbidden.
                if not is_agy or not is_generation:
                    project_id = provider.get("project_id")
                    if not project_id:
                         project_id = (EchoAuth(user_id=provider.get("user_id")).get_auth_data(AUTH_DATA_PROJECT_ID) if provider.get("user_id") else None)
                    
                    if project_id:
                        headers["x-goog-user-project"] = project_id
            else:
                raise Exception("Échec de récupération du jeton d'accès (OAuth2).")
        else:
            headers["x-goog-api-key"] = provider.get("key")

        return headers

    @staticmethod
    async def _prepare_request_context(provider: Dict, target_model: str, payload: Dict, method: str = "generateContent", chat_id: str = None, enable_paid_credits: bool = False) -> Optional[Dict]:
        """
        Sélecteur de Protocole Symétrique : Prépare URL, Headers et Payload selon le backend.
        Retourne un dictionnaire de configuration ou None si le fournisseur est invalide.
        """
        p_type = provider.get("type")
        is_agy = (p_type == AUTH_METHOD_OAUTH2)
        is_generation = method in ["generateContent", "streamGenerateContent", "embedContent"]
        headers = await EchoGeminiClient._get_auth_headers(provider, is_agy=is_agy, is_generation=is_generation)

        # --- CAS 1 : PROTOCOLE API ANTIGRAVITY (OAuth2) ---
        if is_agy:
            # Traduction nom AI Studio → ID interne Code Assist (les namespaces sont distincts)
            target_model = get_ca_model_id(target_model)

            project_id = provider.get("project_id")
            tier_id = provider.get("tier_id")
            g1_credits = provider.get("g1_credits")

            if not project_id:
                return None

            api_url = f"{AGY_BASE_URL}:{method}"
            if method == "streamGenerateContent":
                api_url += "?alt=sse"

            prompt_id = "agy-echo"
            try:
                # Extraction intelligente du texte pour l'ID de prompt
                if "contents" in payload:
                    first_msg = payload.get("contents", [{}])[0].get("parts", [{}])[0].get("text", "")
                elif "content" in payload:
                    first_msg = payload.get("content", {}).get("parts", [{}])[0].get("text", "")
                else: first_msg = ""

                if first_msg:
                    prompt_id = f"agy-{hashlib.sha256(first_msg.encode()).hexdigest()[:16]}"
            except: pass

            # ENCAPSULATION DU PAYLOAD (Code Assist Strict)
            if method == "embedContent":
                request_body = {
                    "content": payload.get("content", {}),
                    "session_id": chat_id
                }
            else:
                # Harmonisation tool_config (owui) -> toolConfig (API)
                t_conf = payload.get("toolConfig") or payload.get("tool_config")

                # Adaptation generationConfig → protocole Code Assist (echo_protocol.py).
                # Depuis echo_protocol v1.1 : thinkingConfig passe à travers (CA l'accepte).
                # Cap maxOutputTokens à MAX_TOKENS_DEFAULT (65535 — corrige le 400 prod Gemini 3.1).
                gen_conf = build_ca_generation_config(payload.get("generationConfig", {}))

                request_body = {
                    "contents": payload.get("contents", []),
                    "systemInstruction": payload.get("systemInstruction"),
                    "generationConfig": gen_conf,
                    "tools": payload.get("tools"),
                    "toolConfig": t_conf,
                    "session_id": chat_id
                }
                # Suppression des valeurs None (L'API Code Assist rejette les nulls explicites)
                request_body = {k: v for k, v in request_body.items() if v is not None}

            wrapped_payload = {
                "model": target_model,
                "project": project_id,
                "user_prompt_id": prompt_id, # Correction : snake_case requis ici
                "request": request_body
            }
            # CRÉDITS AI — opt-in via UserValve pipe (propagé __metadata__ → enable_paid_credits)
            if enable_paid_credits:
                enabled_credits = None
                if tier_id and ("g1-" in tier_id.lower() or "standard" in tier_id.lower()):
                    enabled_credits = ["GOOGLE_ONE_AI"]
                elif g1_credits and int(g1_credits) > 50:
                    enabled_credits = ["GOOGLE_ONE_AI"]
                if enabled_credits:
                    wrapped_payload["enabled_credit_types"] = enabled_credits

            return {"url": api_url, "headers": headers, "payload": wrapped_payload}

        # --- CAS 2 : PROTOCOLE AI STUDIO (API Key) ---
        else:
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:{method}"
            if method == "streamGenerateContent":
                api_url += "?alt=sse"

            return {"url": api_url, "headers": headers, "payload": payload}

    @staticmethod
    async def generate_embedding(
        text: str, 
        task_type: Literal["query", "document"], 
        __user__: dict, 
        __metadata__: dict, 
        title: str = None,
        max_retries: int = 1
    ) -> List[float]:
        """
        Génère un vecteur d'embedding via BAAI/bge-m3 (Local).
        Zéro donnée sortante (infrastructure auto-hébergée).
        Retry intégré (1 retry, 2s backoff) pour absorber les indisponibilités transitoires.
        """
        from echo_constants import MODEL_EMBEDDING, ECHO_EMBEDDING_URL
        
        payload = {
            "model": MODEL_EMBEDDING,
            "input": text
        }
        
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                client = await _get_global_client()
                resp = await client.post(f"{ECHO_EMBEDDING_URL}/embeddings", json=payload, timeout=60)
                
                if resp.status_code == 200:
                    data = resp.json()
                    embeddings = data.get("data", [])
                    if embeddings:
                        return embeddings[0].get("embedding", [])
                    # Réponse 200 mais pas d'embeddings → pas de retry (réponse valide)
                    return []
                else:
                    last_error = f"HTTP {resp.status_code} - {resp.text[:100]}"
                    print(f"[EchoGemini] ⚠️ Échec Embedding Local : {last_error}")
            except Exception as e:
                last_error = str(e)
                print(f"[EchoGemini] ⚠️ Embedding Local tentative {attempt + 1}/{max_retries + 1} : {last_error}")
            
            # Retry avec backoff (sauf dernière tentative)
            if attempt < max_retries:
                await asyncio.sleep(2 * (attempt + 1))
        
        print(f"[EchoGemini] ❌ Embedding Local échoué après {max_retries + 1} tentatives : {last_error}")
        return []

    @staticmethod
    async def call_distillation(
        prompt: str, 
        __user__: dict, 
        __metadata__: dict, 
        is_json: bool = True,
        parts: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS_DEFAULT,  # 65535. Surchargeable : ex. 8192 (RAG), 2048 (brief).
        target_model: Optional[str] = None
    ) -> Union[Dict, str]:
        """
        Exécute une tâche de distillation (extraction sémantique).
        Route exclusivement vers l'API Gemini (MODEL_DISTILLATION) suite à la rationalisation (suppression Gemma).
        """
        from echo_constants import MODEL_DISTILLATION, MODEL_ROUTING, TEMP_DISTILLATION, TOP_P_DISTILLATION
        user_id = __user__.get("id", "system")
        chat_id = __metadata__.get("chat_id")
        
        # Résolution du modèle
        actual_model = MODEL_ROUTING.get(target_model, MODEL_DISTILLATION) if target_model else MODEL_DISTILLATION

        # 1. Préparation du contenu
        contents = parts if parts else [{"role": "user", "parts": [{"text": prompt}]}]

        # --- ROUTAGE API (Gemini 2.5 Flash ou modèle explicite) ---
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": TEMP_DISTILLATION,
                "topP": TOP_P_DISTILLATION,
                "maxOutputTokens": max_tokens
            }
        }
        if is_json:
            payload["generationConfig"]["response_mime_type"] = "application/json"

        try:
            data = await EchoGeminiClient.call(actual_model, payload, user_id, chat_id=chat_id)

            # Extraction et Nettoyage
            target = data.get("response", {}) if "response" in data else data
            candidates = target.get("candidates", [])
            if not candidates: return {} if is_json else ""

            full_text = "".join([p.get("text", "") for p in candidates[0]["content"].get("parts", [])])
            clean_text, _ = split_thought_process(full_text)

            if is_json:
                return std_json.loads(clean_text)
            return clean_text
        except:
            return {} if is_json else "Analyse indisponible."

    @staticmethod
    async def index_text_in_ephemeral_rag(
        distillate: str,
        source_id: str,
        uid: str,
        chat_id: str,
        __user__: dict,
        __metadata__: dict,
        max_chunk: int = 1600,
        timeout: int = 180
    ) -> tuple:
        """
        Factorise le pipeline chunk → embed → upsert Qdrant pour le RAG éphémère.

        Découpe `distillate` (markdown structuré) en chunks sémantiques (~400 tokens bge-m3)
        basés sur les séparateurs de paragraphes \\n\\n, avec recouvrement entre chunks contigus
        pour éviter la perte de contexte aux jointures.

        Retourne (nb_points_indexés: int, message_erreur: str).
        0 points indique un échec total (embedding worker indisponible ou Qdrant KO).
        """
        import uuid as _uuid
        from echo_constants import COLLECTION_EPHEMERAL, EMBEDDING_DIM_V2, ECHO_QDRANT_URL
        qdrant_base = ECHO_QDRANT_URL

        # --- 1. CHUNKING PAR PARAGRAPHES SÉMANTIQUES ---
        raw_paragraphs = [p.strip() for p in distillate.split("\n\n") if p.strip()]

        merged = []
        current = ""
        for para in raw_paragraphs:
            if len(current) + len(para) + 2 <= max_chunk:
                current = (current + "\n\n" + para).strip() if current else para
            else:
                if current:
                    merged.append(current)
                # Paragraphe supra-limite : découpe sur frontière de phrase
                if len(para) > max_chunk:
                    for sentence in para.replace(". ", ".\n").replace("! ", "!\n").replace("? ", "?\n").split("\n"):
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                        if len(current) + len(sentence) + 1 <= max_chunk:
                            current = (current + " " + sentence).strip() if current else sentence
                        else:
                            if current:
                                merged.append(current)
                            current = sentence
                else:
                    current = para
        if current:
            merged.append(current)

        # --- 2. RECOUVREMENT (évite la perte de contexte aux jointures) ---
        if len(merged) > 1:
            overlapped = [merged[0]]
            for i in range(1, len(merged)):
                prev_last = merged[i - 1].split("\n\n")[-1]
                overlapped.append(prev_last + "\n\n" + merged[i])
            chunks = overlapped
        else:
            chunks = merged if merged else [distillate[:max_chunk]]

        # --- 3. CRÉATION CONDITIONNELLE DE LA COLLECTION ---
        points = []
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                coll_check = await client.get(f"{qdrant_base}/collections/{COLLECTION_EPHEMERAL}")
                if coll_check.status_code == 404:
                    cr = await client.put(
                        f"{qdrant_base}/collections/{COLLECTION_EPHEMERAL}",
                        json={"vectors": {"size": EMBEDDING_DIM_V2, "distance": "Cosine"}}
                    )
                    if cr.status_code not in (200, 201):
                        return 0, f"Échec création collection Qdrant : HTTP {cr.status_code}"

                # --- 4. GÉNÉRATION DES EMBEDDINGS ET CONSTRUCTION DES POINTS ---
                for i, chunk in enumerate(chunks):
                    vector = await EchoGeminiClient.generate_embedding(
                        chunk, "document", __user__, __metadata__, title=source_id
                    )
                    if vector:
                        point_id = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"{uid}_{chat_id}_{source_id}_{i}"))
                        points.append({
                            "id": point_id,
                            "vector": vector,
                            "payload": {
                                "user_id": uid,
                                "chat_id": chat_id,
                                "source_id": source_id,
                                "text": chunk,
                                "timestamp": int(time.time())
                            }
                        })

                if not points:
                    return 0, "Aucun embedding généré (worker d'embedding indisponible ?)"

                # --- 5. UPSERT ADDITIF (non destructif) ---
                upsert_resp = await client.put(
                    f"{qdrant_base}/collections/{COLLECTION_EPHEMERAL}/points",
                    json={"points": points}
                )
                if upsert_resp.status_code not in (200, 206):
                    return 0, f"Erreur Qdrant upsert : HTTP {upsert_resp.status_code} — {upsert_resp.text[:100]}"

        except Exception as e:
            return 0, str(e)

        return len(points), ""

    @staticmethod
    async def call_raw(target_model: str, payload: dict, user_id: str, method: str = "generateContent", chat_id: str = None) -> dict:
        """Méthode bas niveau pour les appels non-content (Embed, CountTokens)."""
        auth = EchoAuth(user_id=user_id)
        auth_providers = await auth.get_ordered_auth_providers(user_id=user_id)
        client = await _get_global_client()
        
        for provider in auth_providers:
            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, method, chat_id=chat_id)
                if not req_ctx: continue

                resp = await client.post(req_ctx["url"], json=req_ctx["payload"], headers=req_ctx["headers"], timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("response", data)
                
                print(f"[EchoGemini] ⚠️ Échec provider {provider.get('type')} : HTTP {resp.status_code} - {resp.text[:200]}")
                if resp.status_code in [403, 404]: continue # Failover
                resp.raise_for_status()
            except Exception as e: 
                print(f"[EchoGemini] ⚠️ Exception sur provider {provider.get('type')} : {str(e)}")
                continue
        raise Exception(f"Aucune identité d'accès fonctionnelle après {max_retries} tentatives ({method}).")

    @staticmethod
    async def call(
        target_model: str,
        payload: dict,
        user_id: str,
        auth_providers: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        timeout: int = 120,
        chat_id: str = None,
        enable_paid_credits: bool = False,
    ) -> dict:
        # Résolution des identités d'accès aux modèles pour cet utilisateur
        if not auth_providers:
            auth = EchoAuth(user_id=user_id)
            auth_providers = await auth.get_ordered_auth_providers(user_id=user_id)

        if not auth_providers: raise ValueError(f"Aucune identité d'accès aux modèles configurée pour l'utilisateur {user_id}.")
        client = await _get_global_client()
        active_idx = 0
        consecutive_errors = 0
        current_delay = ECHO_RETRY_BASE_DELAY

        for attempt in range(max_retries + 1):
            provider = auth_providers[active_idx]

            # Préparation symétrique de la requête
            # MESSAGE_SHADOWS: Injection de contexte persistant (RAG-lite)
            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, "generateContent", chat_id=chat_id, enable_paid_credits=enable_paid_credits)
                if not req_ctx:
                    # Fail-over immédiat si configuration incomplète (ex: Project ID manquant)
                    if events: await events.status(f"⚠️ Config incomplète pour {provider['type']}. Bascule...", done=False)
                    if active_idx < len(auth_providers) - 1:
                        active_idx += 1; continue
                    else: raise Exception(f"Configuration d'authentification {provider['type']} invalide (Project ID manquant).")

                resp = await client.post(req_ctx["url"], json=req_ctx["payload"], headers=req_ctx["headers"], timeout=timeout)

                if resp.status_code == 200:
                    json_data = resp.json()
                    # NORMALISATION : Déballage automatique de l'enveloppe Code Assist (OAuth2)
                    return json_data.get("response", json_data)

                # --- FAIL-FAST SUR ERREUR SYNTAXE ---
                if resp.status_code == 400:
                    raise Exception(f"Erreur 400 (Bad Request) - Payload rejeté par l'API: {resp.text}")

                # --- BASCULEMENT IMMÉDIAT (DROITS/DISPO) ---
                if resp.status_code in [403, 404]:
                    if active_idx < len(auth_providers) - 1:
                        if events: await events.status(f"⚠️ Modèle non autorisé ou indisponible sur {provider['type']}. Bascule immédiate...", done=False)      
                        active_idx += 1
                        consecutive_errors = 0
                        continue

                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    if consecutive_errors >= threshold and active_idx < len(auth_providers) - 1:
                        active_idx += 1
                        consecutive_errors = 0
                        if events: await events.status(f"🔄 Surcharge source {provider['type']}. Bascule sur la suivante...", done=False)
                        continue
                    
                    if attempt < max_retries:
                        # [NOUVEAU] Amélioration du feedback Quota
                        wait_msg = f"⚠️ Surcharge API Google ({resp.status_code})."
                        if resp.status_code == 429 and provider.get("type") == AUTH_METHOD_OAUTH2:
                            auth = EchoAuth(user_id=user_id)
                            reset_time = auth.get_auth_data("google_quota_reset", user_id)
                            if reset_time and reset_time != "N/A":
                                wait_msg = f"⏳ API Google : limite de débit atteinte. Reprise à : {reset_time}."

                        wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                        if events: await events.status(f"{wait_msg} Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s....", done=False)
                        await asyncio.sleep(wait_time)
                        current_delay *= ECHO_RETRY_MULTIPLIER
                        continue
                resp.raise_for_status()
            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                    if events: await events.status(f"⚠️ Erreur réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= ECHO_RETRY_MULTIPLIER
                    continue
                raise e
        raise Exception(f"Échec après {max_retries} tentatives.")

    @staticmethod
    async def call_cascade(
        target_model_key: str,
        payload: dict,
        user_id: str,
        metadata: dict = None,
        events: Optional[EchoEvents] = None,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        timeout: int = 120,
        chat_id: str = None,
        include_thoughts: bool = False,
        enable_paid_credits: bool = False,
    ) -> tuple:
        """
        Appel LLM avec cascade descendante centralisée.
        Gère : clamping (politique Pipe), thinkingConfig auto, cascade sur 429/503, toast, crédits.
        Retourne (response_data, model_key_used, reason).
        reason est None si nominal, sinon chaîne expliquant la divergence modèle.
        """
        from echo_constants import (
            MODEL_ROUTING,
            THINKING_LEVEL_PRO, THINKING_LEVEL_FLASH, THINKING_LEVEL_LITE,
        )
        # 1. Clamping via politique Pipe (lecture __metadata__ → fallback identity.db)
        clamped_key = clamp_model(target_model_key, metadata, user_id=user_id)
        reason = None  # Tracker de raison pour le warning

        # Notification si le modèle a été ajusté par la politique
        if clamped_key != target_model_key:
            reason = f"{target_model_key} unavailable (policy)"
            if events:
                await events.status(
                    f"🔒 Politique Pipe : {target_model_key} → {clamped_key}", done=False
                )

        # 2. Construction cascade descendante depuis le modèle clampé
        cascade_order = ["MODEL_PRO", "MODEL_FLASH", "MODEL_LITE"]
        start_idx = cascade_order.index(clamped_key) if clamped_key in cascade_order else 1
        cascade = cascade_order[start_idx:]

        thinking_map = {
            "MODEL_PRO": THINKING_LEVEL_PRO,
            "MODEL_FLASH": THINKING_LEVEL_FLASH,
            "MODEL_LITE": THINKING_LEVEL_LITE,
        }

        # 3. Tentatives en cascade
        for model_key in cascade:
            actual_model = MODEL_ROUTING[model_key]
            thinking_level = thinking_map.get(model_key, THINKING_LEVEL_FLASH)

            # Injection automatique thinkingConfig
            gen_config = payload.get("generationConfig", {})
            gen_config["thinkingConfig"] = {
                "includeThoughts": include_thoughts,
                "thinkingLevel": thinking_level.lower(),
            }
            payload["generationConfig"] = gen_config

            try:
                data = await EchoGeminiClient.call(
                    target_model=actual_model,
                    payload=payload,
                    user_id=user_id,
                    threshold=threshold,
                    max_retries=max_retries,
                    events=events,
                    timeout=timeout,
                    chat_id=chat_id,
                    enable_paid_credits=enable_paid_credits,
                )
                # Confirmation du modèle effectif (visible dans le status)
                if model_key != clamped_key:
                    # Cascade descendante technique (429/503)
                    reason = f"{clamped_key} unavailable (API error)"
                    if events:
                        await events.status(
                            f"⚡ Cascade : {clamped_key} → {model_key} (fallback)", done=False
                        )
                return data, model_key, reason

            except Exception as e:
                # Toast warning sur erreur technique (tous modes)
                if events:
                    await events.toast(
                        f"⚠️ {model_key} indisponible — repli automatique", "warning"
                    )
                    await events.status(
                        f"⚠️ {model_key} ({str(e)[:60]}). Repli...", done=False
                    )
                continue

        # Tous les modèles ont échoué — signal final
        if events:
            await events.toast(
                "❌ Cascade épuisée — aucun modèle disponible", "error"
            )
            await events.status(
                f"❌ Cascade épuisée : {clamped_key} → aucun modèle disponible.", done=True
            )
        return None, None, f"{clamped_key} unavailable (cascade exhausted)"

    @staticmethod
    async def stream(
        target_model: str,
        payload: dict,
        user_id: str,
        auth_providers: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        process_callback: Optional[Any] = None,
        timeout: int = 300,
        chat_id: str = None,
        enable_paid_credits: bool = False,
    ) -> AsyncGenerator[Union[str, Dict], None]:
        # Résolution des identités d'accès aux modèles (si non fourni par le pipe)
        if not auth_providers:
            auth = EchoAuth(user_id=user_id)
            auth_providers = await auth.get_ordered_auth_providers(user_id=user_id)

        if not auth_providers:
            raise ValueError(f"🚫 Aucune identité d'accès aux modèles disponible pour l'utilisateur {user_id}.")

        client = await _get_global_client()
        active_idx = 0
        consecutive_errors = 0
        current_delay = ECHO_RETRY_BASE_DELAY

        for attempt in range(max_retries + 1):
            provider = auth_providers[active_idx]

            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, "streamGenerateContent", chat_id=chat_id, enable_paid_credits=enable_paid_credits)
                if not req_ctx:
                    if events: await events.status(f"⚠️ Config incomplète pour {provider['type']}. Bascule...", done=False)
                    if active_idx < len(auth_providers) - 1:
                        active_idx += 1; continue
                    else: yield f"🚫 Erreur : Configuration d'authentification {provider['type']} invalide (Project ID manquant)."; return

                async with client.stream("POST", req_ctx["url"], content=json.dumps(req_ctx["payload"]), headers=req_ctx["headers"], timeout=timeout) as r:     
                    # --- FAIL-FAST SUR ERREUR SYNTAXE ---
                    if r.status_code == 400:
                        body = await r.aread()
                        raise Exception(f"Erreur 400 (Bad Request) - Payload rejeté par l'API: {body.decode('utf-8')}")

                    # --- BASCULEMENT IMMÉDIAT (DROITS/DISPO) ---
                    if r.status_code in [403, 404]:
                        if active_idx < len(auth_providers) - 1:
                            if events: await events.status(f"⚠️ Modèle non autorisé ou indisponible sur {provider['type']}. Bascule immédiate...", done=False)  
                            active_idx += 1
                            consecutive_errors = 0
                            continue

                    if r.status_code in [429, 500, 503]:
                        consecutive_errors += 1
                        if consecutive_errors >= threshold and active_idx < len(auth_providers) - 1:
                            active_idx += 1
                            consecutive_errors = 0
                            if events: await events.status(f"🔄 Surcharge source {provider['type']}. Bascule sur la suivante...", done=False)
                            continue
                        if attempt < max_retries:
                            # [NOUVEAU] Amélioration du feedback Quota
                            wait_msg = f"⚠️ Surcharge API Google ({r.status_code})."
                            if r.status_code == 429 and provider.get("type") == AUTH_METHOD_OAUTH2:
                                auth = EchoAuth(user_id=user_id)
                                reset_time = auth.get_auth_data("google_quota_reset", user_id)
                                if reset_time and reset_time != "N/A":
                                    wait_msg = f"⏳ API Google : limite de débit atteinte. Reprise à : {reset_time}."

                            wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                            if events: await events.status(f"{wait_msg} Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s....", done=False)
                            await asyncio.sleep(wait_time)
                            current_delay *= ECHO_RETRY_MULTIPLIER
                            continue
                    r.raise_for_status()
                    if process_callback:
                        async for chunk in process_callback(r): yield chunk
                    else:
                        # Processeur par défaut pour les outils (Extraction Texte et Objets JSON)
                        buffer = ""
                        import codecs
                        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
                        buffered_lines = []

                        async for chunk in r.aiter_bytes():
                            buffer += decoder.decode(chunk, final=False)
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.strip()
                                
                                if line.startswith("data: "):
                                    buffered_lines.append(line[6:].strip())
                                elif line == "" and buffered_lines:
                                    # Fin d'un bloc SSE : accumulation et parsing du JSON complet
                                    full_json_str = "\n".join(buffered_lines)
                                    try:
                                        data = json.loads(full_json_str)
                                        yield data 
                                    except: pass
                                    buffered_lines = []
                    break
            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                    if events: await events.status(f"⚠️ Erreur réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= ECHO_RETRY_MULTIPLIER
                    continue
                else: yield f"🚫 Erreur système : {str(e)}"; return

    @staticmethod
    async def embed(
        model: str,
        content: dict,
        user_id: str,
        auth_providers: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        timeout: int = 30,
        chat_id: str = None
    ) -> dict:
        """Legacy : Reste ici pour compatibilité, mais redirige vers call_raw."""
        return await EchoGeminiClient.call_raw(model, {"content": content}, user_id, method="embedContent", chat_id=chat_id)

# ==============================================================================
# SECTION 5 : GESTIONNAIRE D'ÉTAT (SQLite)
# ==============================================================================

class EchoStateManager:
    def __init__(self, user_id: str = "system", chat_id: Optional[str] = None):
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        self.chat_id = chat_id
        safe_uid = "".join(x for x in str(self.user_id) if x.isalnum() or x in "-_")
        self.user_dir = os.path.join(ECHO_USERS_ROOT, safe_uid)
        
        if chat_id:
            for domain in ECHO_SESSION_DOMAINS:
                if domain != "db":
                    os.makedirs(get_echo_session_path(self.user_id, self.chat_id, domain), exist_ok=True)
            self.db_path = get_echo_session_path(self.user_id, self.chat_id, "db")
        else: 
            self.db_path = os.path.join(self.user_dir, "identity.db")
            
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;"); return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS suture_index (cumulative_hash TEXT PRIMARY KEY, chat_id TEXT NOT NULL, invariant_hash TEXT NOT NULL, parent_hash TEXT, message_id TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS rich_payloads (invariant_hash TEXT PRIMARY KEY, rich_parts_json TEXT NOT NULL, message_id TEXT, created_at INTEGER)")   
                # Table 'message_shadows' : Métadonnées Spécifiques à Gemini
                # Nom conservé pour compatibilité avec les bases de données en production.
                # Stocke les métadonnées propres à l'API Gemini (thoughtSignature, usageMetadata,
                # candidateIndex) nécessaires à la Suture Bit-Perfect des Métadonnées Gemini.
                conn.execute("CREATE TABLE IF NOT EXISTS message_shadows (message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, full_parts_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_chat_id ON message_shadows (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON suture_index (chat_id)")
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_signatures (cumulative_hash TEXT PRIMARY KEY, thought_signature TEXT NOT NULL, message_id TEXT, model_id TEXT, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS tool_journal (cumulative_hash TEXT PRIMARY KEY, io_json TEXT NOT NULL, updated_at INTEGER)")

                conn.execute("CREATE TABLE IF NOT EXISTS processed_files (chat_id TEXT, file_id TEXT, filename TEXT, mime TEXT, mode TEXT, timestamp INTEGER, file_content TEXT, message_id TEXT, PRIMARY KEY (chat_id, file_id))")
                conn.execute("CREATE TABLE IF NOT EXISTS call_bridge (call_id TEXT PRIMARY KEY, signature TEXT NOT NULL, function_name TEXT NOT NULL, args_json TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS session_state (id INTEGER PRIMARY KEY, last_model_id TEXT, updated_at INTEGER NOT NULL)")
                # Préférences Pipe propagées aux outils (politique modèle, crédits)
                conn.execute("CREATE TABLE IF NOT EXISTS echo_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")

                # Echos Skills & Cognitive Council (V9)
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_threads (sub_sid TEXT, chat_id TEXT NOT NULL, role_id TEXT NOT NULL, step_index INTEGER, role TEXT NOT NULL, content_json TEXT NOT NULL, thought_signature TEXT, updated_at INTEGER, PRIMARY KEY (sub_sid, step_index))")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_thread_chat ON cognitive_threads (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_thread_sid ON cognitive_threads (sub_sid)")

                # Strategic Planner (v5.159)
                conn.execute("""CREATE TABLE IF NOT EXISTS plans (
                    plan_id      TEXT PRIMARY KEY,
                    filename     TEXT NOT NULL,
                    goal         TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'draft',
                    author_model TEXT,
                    created_at   INTEGER NOT NULL,
                    updated_at   INTEGER NOT NULL
                )""")

                # ECHO Codex (v5.165)
                conn.execute("""CREATE TABLE IF NOT EXISTS codex_docs (
                    filename     TEXT PRIMARY KEY,
                    language     TEXT NOT NULL DEFAULT 'plaintext',
                    lines        INTEGER NOT NULL DEFAULT 0,
                    last_commit  TEXT,
                    commit_msg   TEXT,
                    created_at   INTEGER NOT NULL,
                    updated_at   INTEGER NOT NULL
                )""")

                # MIGRATION : Ajout des colonnes manquantes si nécessaire
                try: conn.execute("ALTER TABLE rich_payloads ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE cognitive_signatures ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE cognitive_signatures ADD COLUMN model_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE suture_index ADD COLUMN message_id TEXT")
                except: pass

                conn.commit()
        except Exception as e: print(f"[EchoStateManager] Init DB Error: {e}")

    def save_message_shadow(self, message_id: str, chat_id: str, role: str, parts: List[dict], updated_at: Optional[int] = None):
        if not message_id: return
        ts = updated_at if updated_at is not None else int(time.time())
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (message_id, chat_id, role, json.dumps(parts).decode('utf-8'), ts)
                )
                conn.commit()
        except: pass

    def get_message_shadow(self, message_id: str, updated_at: int) -> Optional[List[dict]]:
        if not message_id: return None
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT full_parts_json FROM message_shadows WHERE message_id = ? AND updated_at = ?", (message_id, int(updated_at))).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def calculate_invariant_hash(self, role: str, content: Any, tool_io: dict = None) -> str:
        norm_c = content.strip() if isinstance(content, str) else json.dumps(content, option=json.OPT_SORT_KEYS).decode('utf-8')
        norm_t = json.dumps(tool_io, option=json.OPT_SORT_KEYS).decode('utf-8') if tool_io else ""
        return hashlib.sha256(f"{role.lower()}|{norm_c}|{norm_t}".encode("utf-8")).hexdigest()

    def calculate_cumulative_hash(self, inv: str, parent: str = None) -> str:
        return hashlib.sha256(f"{inv}|{parent or ''}".encode("utf-8")).hexdigest()

    def get_session_registry(self, chat_id: str, active_message_ids: Optional[List[str]] = None) -> dict:
        reg = {}
        try:
            with self._get_connection() as conn:
                if active_message_ids:
                    placeholders = ','.join('?' for _ in active_message_ids)
                    query = f"SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ? AND message_id IN ({placeholders})"
                    rows = conn.execute(query, [chat_id] + active_message_ids).fetchall()
                else:
                    rows = conn.execute("SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ?", (chat_id,)).fetchall()
                for row in rows: reg[row[0]] = {"id": row[1], "mime": row[2] or "application/octet-stream", "statut": row[3] or "unknown"}
        except: pass
        return reg

    def mark_processed(self, chat_id: str, file_id: str, filename: str, mime: str, mode: str, content: Optional[str] = None, message_id: Optional[str] = None): 
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp, file_content, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chat_id, file_id, filename, mime, mode, int(time.time()), content, message_id))
                conn.commit()
        except: pass

    def save_call_bridge(self, call_id: str, signature: str, function_name: str, args: dict = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO call_bridge (call_id, signature, function_name, args_json, timestamp) VALUES (?, ?, ?, ?, ?)", (call_id, signature, function_name, json.dumps(args).decode('utf-8'), int(time.time())))
                conn.commit()
        except: pass

    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT signature, function_name, args_json FROM call_bridge WHERE call_id = ?", (call_id,)).fetchone()
                if row: return {"signature": row[0], "name": row[1], "args": std_json.loads(row[2]) if row[2] else {}}
        except: pass
        return None

    def get_rich_payload(self, inv: str) -> Optional[List[dict]]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT rich_parts_json FROM rich_payloads WHERE invariant_hash = ?", (inv,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_rich_payload(self, inv: str, rich: List[dict], message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO rich_payloads (invariant_hash, rich_parts_json, message_id, created_at) VALUES (?, ?, ?, ?)", (inv, json.dumps(rich).decode('utf-8'), message_id, int(time.time())))
                conn.commit()
        except: pass

    def index_suture(self, cumul: str, chat_id: str, inv: str, parent: str = None, message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO suture_index (cumulative_hash, chat_id, invariant_hash, parent_hash, message_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (cumul, chat_id, inv, parent, message_id, int(time.time())))
                conn.commit()
        except: pass

    def save_cognitive_data(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None, model_id: str = None):        
        try:
            with self._get_connection() as conn:
                if sig:
                    conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, model_id, updated_at) VALUES (?, ?, ?, ?, ?)", (cumul, sig, message_id, model_id, int(time.time())))
                # thought_archive retiré (write-only, jamais lu — table purgée au prochain rebuild-echo)
                if tool_io: conn.execute("INSERT OR REPLACE INTO tool_journal (cumulative_hash, io_json, updated_at) VALUES (?, ?, ?)", (cumul, json.dumps(tool_io).decode('utf-8'), int(time.time())))
                if model_id:
                    conn.execute("INSERT OR REPLACE INTO session_state (id, last_model_id, updated_at) VALUES (1, ?, ?)", (model_id, int(time.time())))
                conn.commit()
        except: pass

    def get_signature_by_id(self, message_id: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT thought_signature FROM cognitive_signatures WHERE message_id = ?", (message_id,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def save_signature_by_id(self, message_id: str, signature: str):
        try:
            with self._get_connection() as conn:
                # Cette méthode est un fallback si le cumulative hash n'est pas encore connu
                # On utilise un hash factice ou on met à jour par ID si la ligne existe
                conn.execute("UPDATE cognitive_signatures SET thought_signature = ? WHERE message_id = ?", (signature, message_id))
                conn.commit()
        except: pass

    def get_last_active_model(self) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT last_model_id FROM session_state WHERE id = 1").fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_thought_signature(self, cumul: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT thought_signature FROM cognitive_signatures WHERE cumulative_hash = ?", (cumul,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_tool_io(self, cumul: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT io_json FROM tool_journal WHERE cumulative_hash = ?", (cumul,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_auth_data(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
                conn.commit()
        except: pass

    def save_setting(self, key: str, value: str):
        """Persiste une préférence Pipe dans echo_settings (identity.db)."""
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS echo_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("INSERT OR REPLACE INTO echo_settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
                conn.commit()
        except: pass

    def get_setting(self, key: str) -> Optional[str]:
        """Lit une préférence Pipe depuis echo_settings (identity.db)."""
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS echo_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                row = conn.execute("SELECT value FROM echo_settings WHERE key = ?", (key,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def save_context_stats(self, stats: dict):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO context_stats (id, data, updated_at) VALUES (1, ?, ?)", (std_json.dumps(stats).decode('utf-8'), int(time.time())))
                conn.commit()
        except: pass

    def get_last_context_stats(self) -> dict:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT data FROM context_stats WHERE id = 1").fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return {}

    def move_to_vault(self, file_id: str, filename: str) -> bool:
        old_path = resolve_upload_file_path(self.user_id, file_id, chat_id=self.chat_id)
        if not old_path: return False
        new_path = os.path.join(get_echo_session_path(self.user_id, self.chat_id, "files"), os.path.basename(old_path))
        try:
            if not os.path.exists(new_path):
                if ECHO_UPLOADS_TRANSIT_DIR in old_path:
                    shutil.move(old_path, new_path)
                else:
                    shutil.copy2(old_path, new_path)
            return True
        except: return False

    def save_thread_step(self, sub_sid: str, chat_id: str, role_id: str, step_index: int, role: str, content: List[dict], signature: Optional[str] = None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cognitive_threads (sub_sid, chat_id, role_id, step_index, role, content_json, thought_signature, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (sub_sid, chat_id, role_id, step_index, role, json.dumps(content).decode('utf-8'), signature, int(time.time()))
                )
                conn.commit()
        except: pass

    def get_thread_history(self, sub_sid: str) -> List[dict]:
        history = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT role, content_json, thought_signature FROM cognitive_threads WHERE sub_sid = ? ORDER BY step_index ASC",
                    (sub_sid,)
                ).fetchall()
                for row in rows:
                    parts = json.loads(row[1])
                    item = {"role": row[0], "parts": parts}
                    if row[2]: # Si on a une thoughtSignature, on l'injecte dans la première part
                        if parts and isinstance(parts, list):
                            if "functionCall" in parts[0] or "text" in parts[0]:
                                parts[0]["thoughtSignature"] = row[2]
                    history.append(item)
        except: pass
        return history

    def get_thread_steps_enriched(self, sub_sid: str) -> List[dict]:
        """Retourne les steps enrichis d'un thread (role, role_id, parts, timestamp).
        Utilisé par le Sub-Agent Monitor pour reconstruire l'arbre d'appels."""
        steps = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT step_index, role, role_id, content_json, updated_at "
                    "FROM cognitive_threads WHERE sub_sid = ? ORDER BY step_index ASC",
                    (sub_sid,)
                ).fetchall()
                for row in rows:
                    parts = json.loads(row[3])
                    steps.append({
                        "index": row[0],
                        "role": row[1],
                        "role_id": row[2],
                        "parts": parts,
                        "timestamp": row[4]
                    })
        except: pass
        return steps


    def list_threads(self, chat_id: str) -> List[dict]:
        threads = []
        try:
            with self._get_connection() as conn:
                # On récupère le dernier message de chaque thread pour avoir un résumé
                rows = conn.execute(
                    "SELECT sub_sid, role_id, MAX(step_index), content_json, updated_at FROM cognitive_threads WHERE chat_id = ? GROUP BY sub_sid ORDER BY updated_at DESC",
                    (chat_id,)
                ).fetchall()
                for row in rows:
                    content = json.loads(row[3])
                    summary = content[0].get("text", "")[:100] if content and "text" in content[0] else "Appel de fonction..."
                    threads.append({
                        "sub_sid": row[0],
                        "role_id": row[1],
                        "last_step": row[2],
                        "summary": summary,
                        "updated_at": row[4]
                    })
        except: pass
        return threads

    def delete_thread(self, sub_sid: str):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM cognitive_threads WHERE sub_sid = ?", (sub_sid,))
                conn.commit()
        except: pass

    # ==========================================================================
    # STRATEGIC PLANNER — Registre des Plans
    # ==========================================================================

    def save_plan_record(self, plan_id: str, filename: str, goal: str,
                         status: str, author_model: str = None):
        """Enregistre un nouveau plan dans le registre de contrôle du chat."""
        ts = int(time.time())
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO plans "
                    "(plan_id, filename, goal, status, author_model, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (plan_id, filename, goal, status, author_model, ts, ts)
                )
                conn.commit()
        except: pass

    def update_plan_record_status(self, plan_id: str, status: str):
        """Met à jour uniquement le statut d'un plan (préserve created_at)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
                    (status, int(time.time()), plan_id)
                )
                conn.commit()
        except: pass

    def delete_plan_record(self, plan_id: str):
        """Supprime un plan du registre de contrôle."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
                conn.commit()
        except: pass

    def get_plans(self) -> List[dict]:
        """Retourne tous les plans du chat (pour injection dans registre_plan)."""
        plans = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT plan_id, filename, goal, status, author_model, created_at "
                    "FROM plans ORDER BY created_at DESC"
                ).fetchall()
                for row in rows:
                    plans.append({
                        "plan_id": row[0], "filename": row[1], "goal": row[2],
                        "status": row[3], "author_model": row[4], "created_at": row[5]
                    })
        except: pass
        return plans

    # --- ECHO CODEX ---

    def save_codex_record(self, filename: str, language: str, lines: int,
                          last_commit: str, commit_msg: str):
        """Enregistre ou met à jour un document Codex dans le registre."""
        ts = int(time.time())
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO codex_docs (filename, language, lines, last_commit, commit_msg, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(filename) DO UPDATE SET language=?, lines=?, last_commit=?, commit_msg=?, updated_at=?",
                    (filename, language, lines, last_commit, commit_msg, ts, ts,
                     language, lines, last_commit, commit_msg, ts)
                )
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] save_codex_record error: {e}")

    def delete_codex_record(self, filename: str):
        """Supprime un document du registre Codex."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM codex_docs WHERE filename = ?", (filename,))
                conn.commit()
        except: pass

    def get_codex_docs(self) -> List[dict]:
        """Retourne tous les documents Codex du chat (pour injection dans registre_codex)."""
        docs = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT filename, language, lines, last_commit, commit_msg "
                    "FROM codex_docs ORDER BY updated_at DESC"
                ).fetchall()
                for row in rows:
                    docs.append({
                        "filename": row[0], "language": row[1], "lines": row[2],
                        "last_commit": row[3], "commit_msg": row[4]
                    })
        except: pass
        return docs

    def clear_codex_records(self):
        """Purge tous les documents Codex du chat (appelé par reset_all)."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM codex_docs")
                conn.commit()
        except: pass

    def get_active_branch_shadows(self, chat_id: str, limit: int = 20) -> List[dict]:
        """Remonte la généalogie de la branche active via suture_index pour une distillation bit-perfect."""
        shadows = []
        try:
            with self._get_connection() as conn:
                # 1. On identifie le dernier message scellé dans la suture pour ce chat
                row = conn.execute(
                    "SELECT cumulative_hash, parent_hash, message_id FROM suture_index WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 1",
                    (chat_id,)
                ).fetchone()
                
                if not row: return []
                
                chain = []
                curr_cumul, curr_parent, curr_mid = row
                
                while curr_mid and len(chain) < limit:
                    chain.append(curr_mid)
                    if not curr_parent: break
                    
                    # Remontée vers le parent via son Cumulative Hash
                    parent_row = conn.execute(
                        "SELECT cumulative_hash, parent_hash, message_id FROM suture_index WHERE cumulative_hash = ?",
                        (curr_parent,)
                    ).fetchone()
                    
                    if not parent_row: break
                    curr_cumul, curr_parent, curr_mid = parent_row
                
                # 2. Récupération des ombres (on inverse pour l'ordre chronologique)
                for mid in reversed(chain):
                    s_row = conn.execute("SELECT role, full_parts_json FROM message_shadows WHERE message_id = ?", (mid,)).fetchone()
                    if s_row:
                        shadows.append({"role": s_row[0], "parts": json.loads(s_row[1])})
        except Exception as e:
            print(f"[EchoStateManager] Error in genealogy: {e}")
        return shadows

