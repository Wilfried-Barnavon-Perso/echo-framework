"""
title: ECHO Shared Utils (Core)
author: Wilfried BARNAVON
version: 6.6
description: 6.6: Rollback de la modification d'endpoint Code Assist (tentative infructueuse pour fix 404).
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
    GOOGLE_API_BASE_URL, ECHO_USER_AGENT, ECHO_USERS_ROOT,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    ECHO_RETRY_BASE_DELAY, ECHO_RETRY_MULTIPLIER,
    ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX,
    AUTH_METHOD_KEY_PRIMARY, AUTH_METHOD_OAUTH2, AUTH_METHOD_KEY_SECONDARY,
    DEFAULT_AUTH_PRIORITY, ECHO_OAUTH_CLIENT_ID, ECHO_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_TOKEN_URL, AUTH_DATA_PROJECT_ID, CODE_ASSIST_BASE_URL,
    GOOGLE_OAUTH_TOKEN_LIFETIME, AUTH_DATA_USER_TIER
)

# ==============================================================================
# SECTION 0 : CLIENT HTTP GLOBAL (HTTP/2)
# ==============================================================================

_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(
    timeout: int = 600,
    max_connections: int = 100,
    max_keepalive: int = 20,
    keepalive_expiry: int = 300
) -> httpx.AsyncClient:
    """Gestionnaire de client HTTP/2 STRICT (Mutualisé)."""
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
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
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=True)

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

# ==============================================================================
# SECTION 2 : RÉSOLUTION DE FICHIERS & VERSIONS
# ==============================================================================

def generate_echo_file_id(user_id: str, chat_id: str) -> str:
    ts = int(time.time() * 1000)
    return f"U_{user_id}_C_{chat_id}_T_{ts}"

def resolve_upload_file_path(user_id: str, file_id: str, uploads_dir: str = ECHO_UPLOADS_TRANSIT_DIR) -> Optional[str]:
    if not file_id: return None
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        user_vault = os.path.join(ECHO_USERS_ROOT, safe_uid, "files")
        pattern = os.path.join(user_vault, f"{file_id}_*")
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
# SECTION 4 : SERVICE D'AUTHENTIFICATION (Citadelle)
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

    async def get_ordered_auth_mesh(self, user_id: str) -> List[Dict]:
        """Résout le maillage d'authentification par priorité (Agnostique)."""
        uid = user_id or self.user_id
        mesh = []
        
        # 1. Vérification OAuth2 (Premium/Souverain)
        refresh_token = self.get_auth_data("google_oauth2_refresh_token", uid)
        if refresh_token:
            mesh.append({
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
                mesh.append({"type": method, "key": key_val})

        return mesh

    async def refresh_google_oauth_token(self, refresh_token: str, user_id: str = None) -> Optional[str]:
        """Rafraîchit silencieusement le jeton d'accès Google OAuth2."""
        client = await _get_global_client()
        payload = {
            "client_id": ECHO_OAUTH_CLIENT_ID,
            "client_secret": ECHO_OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
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
    async def _get_auth_headers(provider: Dict, is_code_assist: bool = False, is_generation: bool = False) -> Dict[str, str]:
        """Génère les en-têtes d'authentification selon le type de fournisseur (Agnostique)."""
        if is_code_assist:
            # Simulation parfaite de l'extension VS Code (Cloud Code) v0.41.0
            ua = "CloudCodeVSCode/0.41.0 (aidev_client; os_type=Windows; os_version=10.0.22631; arch=x64; host_path=VSCode/1.94.2; proxy_client=geminicli)"
        else:
            ua = ECHO_USER_AGENT

        headers = {
            "Content-Type": "application/json", 
            "User-Agent": ua,
            "x-goog-api-client": "aidev_client/geminicli"
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
                if not is_code_assist or not is_generation:
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
    async def _prepare_request_context(provider: Dict, target_model: str, payload: Dict, method: str = "generateContent", chat_id: str = None) -> Optional[Dict]:
        """
        Sélecteur de Protocole Symétrique : Prépare URL, Headers et Payload selon le backend.
        Retourne un dictionnaire de configuration ou None si le fournisseur est invalide.
        """
        p_type = provider.get("type")
        is_code_assist = (p_type == AUTH_METHOD_OAUTH2)
        is_generation = method in ["generateContent", "streamGenerateContent", "embedContent"]
        headers = await EchoGeminiClient._get_auth_headers(provider, is_code_assist=is_code_assist, is_generation=is_generation)

        # --- CAS 1 : PROTOCOLE CODE ASSIST (OAuth2) ---
        if is_code_assist:
            project_id = provider.get("project_id")
            tier_id = provider.get("tier_id")
            g1_credits = provider.get("g1_credits")

            if not project_id:
                return None

            api_url = f"{CODE_ASSIST_BASE_URL}:{method}"
            if method == "streamGenerateContent":
                api_url += "?alt=sse"

            prompt_id = "echo-session"
            try:
                # Extraction intelligente du texte pour l'ID de prompt
                if "contents" in payload:
                    first_msg = payload.get("contents", [{}])[0].get("parts", [{}])[0].get("text", "")
                elif "content" in payload:
                    first_msg = payload.get("content", {}).get("parts", [{}])[0].get("text", "")
                else: first_msg = ""

                if first_msg:
                    prompt_id = f"echo-{hashlib.sha256(first_msg.encode()).hexdigest()[:16]}"
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
                
                request_body = {
                    "contents": payload.get("contents", []),
                    "systemInstruction": payload.get("systemInstruction"),
                    "generationConfig": payload.get("generationConfig", {}),
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
            # LOGIQUE DES CRÉDITS AI
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
        title: str = None
    ) -> List[float]:
        """
        Génère un vecteur d'embedding via Gemini Embedding 2 (Stable).
        Gère automatiquement les préfixes de tâche et l'authentification Citadelle.
        """
        from echo_constants import MODEL_EMBEDDING
        user_id = __user__.get("id", "system")
        chat_id = __metadata__.get("chat_id")
        
        # 1. Formatage multimodal Gemini Embedding 2
        if task_type == "query":
            prompt = f"task: search result | query: {text}"
        else:
            prompt = f"title: {title or 'none'} | text: {text}"

        # 2. Construction du Payload technique
        payload = {"content": {"parts": [{"text": prompt}]}}
        
        # 3. Appel via le moteur central (Gère OAuth2 / Code Assist)
        try:
            res = await EchoGeminiClient.call_raw(MODEL_EMBEDDING, payload, user_id, method="embedContent", chat_id=chat_id)
            # 4. Extraction du vecteur
            return res.get("embedding", {}).get("values", [])
        except: return []

    @staticmethod
    async def call_distillation(
        prompt: str, 
        __user__: dict, 
        __metadata__: dict, 
        is_json: bool = True,
        parts: Optional[List[Dict]] = None,
        max_tokens: int = 65000,
        target_model: Optional[str] = None
    ) -> Union[Dict, str]:
        """
        Exécute une tâche de distillation (extraction sémantique) via Gemini 2.5 Flash (par défaut).
        Gère automatiquement le nettoyage des thoughts et le parsing JSON.
        """
        from echo_constants import MODEL_DISTILLATION, MODEL_ROUTING
        user_id = __user__.get("id", "system")
        chat_id = __metadata__.get("chat_id")
        
        # Résolution du modèle (Priorité: target_model -> MODEL_DISTILLATION)
        actual_model = MODEL_ROUTING.get(target_model, target_model) if target_model else MODEL_DISTILLATION

        # 1. Préparation du Payload
        contents = parts if parts else [{"role": "user", "parts": [{"text": prompt}]}]
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

        # 2. Appel au Cortex
        try:
            data = await EchoGeminiClient.call(actual_model, payload, user_id, chat_id=chat_id)

            # 3. Extraction et Nettoyage
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
    async def call_raw(target_model: str, payload: dict, user_id: str, method: str = "generateContent", chat_id: str = None) -> dict:
        """Méthode bas niveau pour les appels non-content (Embed, CountTokens)."""
        auth = EchoAuth(user_id=user_id)
        auth_mesh = await auth.get_ordered_auth_mesh(user_id=user_id)
        client = await _get_global_client()
        
        for provider in auth_mesh:
            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, method, chat_id=chat_id)
                if not req_ctx: continue

                resp = await client.post(req_ctx["url"], json=req_ctx["payload"], headers=req_ctx["headers"], timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("response", data)
                if resp.status_code in [403, 404]: continue # Failover
                resp.raise_for_status()
            except: continue
        raise Exception(f"Échec de l'appel raw ({method}) après épuisement de la Citadelle.")

    @staticmethod
    async def call(
        target_model: str,
        payload: dict,
        user_id: str,
        auth_mesh: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        timeout: int = 120,
        chat_id: str = None
    ) -> dict:
        # Résolution via le service d'authentification souverain
        if not auth_mesh:
            auth = EchoAuth(user_id=user_id)
            auth_mesh = await auth.get_ordered_auth_mesh(user_id=user_id)

        if not auth_mesh: raise ValueError(f"Aucune Citadelle d'accès configurée pour l'utilisateur {user_id}.")
        client = await _get_global_client()
        active_idx = 0
        consecutive_errors = 0
        current_delay = ECHO_RETRY_BASE_DELAY

        for attempt in range(max_retries + 1):
            provider = auth_mesh[active_idx]

            # Préparation symétrique de la requête
            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, "generateContent", chat_id=chat_id)
                if not req_ctx:
                    # Fail-over immédiat si configuration incomplète (ex: Project ID manquant)
                    if events: await events.status(f"⚠️ Config incomplète pour {provider['type']}. Bascule...", done=False)
                    if active_idx < len(auth_mesh) - 1:
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
                    if active_idx < len(auth_mesh) - 1:
                        if events: await events.status(f"⚠️ Modèle non autorisé ou indisponible sur {provider['type']}. Bascule immédiate...", done=False)      
                        active_idx += 1
                        consecutive_errors = 0
                        continue

                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    if consecutive_errors >= threshold and active_idx < len(auth_mesh) - 1:
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
                                wait_msg = f"⏳ Quota OAuth2 épuisé. Reset prévu à : {reset_time}."

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
    async def stream(
        target_model: str,
        payload: dict,
        user_id: str,
        auth_mesh: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_THRESHOLD,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        process_callback: Optional[Any] = None,
        timeout: int = 300,
        chat_id: str = None
    ) -> AsyncGenerator[Union[str, Dict], None]:
        # Résolution via le service souverain (si non fourni par le pipe)
        if not auth_mesh:
            auth = EchoAuth(user_id=user_id)
            auth_mesh = await auth.get_ordered_auth_mesh(user_id=user_id)

        if not auth_mesh:
            raise ValueError(f"🚫 Citadelle verrouillée : Aucune méthode d'authentification disponible pour l'utilisateur {user_id}.")

        client = await _get_global_client()
        active_idx = 0
        consecutive_errors = 0
        current_delay = ECHO_RETRY_BASE_DELAY

        for attempt in range(max_retries + 1):
            provider = auth_mesh[active_idx]

            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, "streamGenerateContent", chat_id=chat_id)
                if not req_ctx:
                    if events: await events.status(f"⚠️ Config incomplète pour {provider['type']}. Bascule...", done=False)
                    if active_idx < len(auth_mesh) - 1:
                        active_idx += 1; continue
                    else: yield f"🚫 Erreur : Configuration d'authentification {provider['type']} invalide (Project ID manquant)."; return

                async with client.stream("POST", req_ctx["url"], content=json.dumps(req_ctx["payload"]), headers=req_ctx["headers"], timeout=timeout) as r:     
                    # --- FAIL-FAST SUR ERREUR SYNTAXE ---
                    if r.status_code == 400:
                        body = await r.aread()
                        raise Exception(f"Erreur 400 (Bad Request) - Payload rejeté par l'API: {body.decode('utf-8')}")

                    # --- BASCULEMENT IMMÉDIAT (DROITS/DISPO) ---
                    if r.status_code in [403, 404]:
                        if active_idx < len(auth_mesh) - 1:
                            if events: await events.status(f"⚠️ Modèle non autorisé ou indisponible sur {provider['type']}. Bascule immédiate...", done=False)  
                            active_idx += 1
                            consecutive_errors = 0
                            continue

                    if r.status_code in [429, 500, 503]:
                        consecutive_errors += 1
                        if consecutive_errors >= threshold and active_idx < len(auth_mesh) - 1:
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
                                    wait_msg = f"⏳ Quota OAuth2 épuisé. Reset prévu à : {reset_time}."

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
        auth_mesh: Optional[List[Dict]] = None,
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
        os.makedirs(os.path.join(self.user_dir, "files"), exist_ok=True)
        os.makedirs(os.path.join(self.user_dir, "chats"), exist_ok=True)
        if chat_id:
            safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
            self.db_path = os.path.join(self.user_dir, "chats", f"{safe_cid}.db")
        else: self.db_path = os.path.join(self.user_dir, "identity.db")
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;"); return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS suture_index (cumulative_hash TEXT PRIMARY KEY, chat_id TEXT NOT NULL, invariant_hash TEXT NOT NULL, parent_hash TEXT, message_id TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS rich_payloads (invariant_hash TEXT PRIMARY KEY, rich_parts_json TEXT NOT NULL, message_id TEXT, created_at INTEGER)")   
                conn.execute("CREATE TABLE IF NOT EXISTS message_shadows (message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, full_parts_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_chat_id ON message_shadows (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON suture_index (chat_id)")
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_signatures (cumulative_hash TEXT PRIMARY KEY, thought_signature TEXT NOT NULL, message_id TEXT, model_id TEXT, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS tool_journal (cumulative_hash TEXT PRIMARY KEY, io_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS thought_archive (cumulative_hash TEXT PRIMARY KEY, raw_thought TEXT NOT NULL, updated_at INTEGER)")    
                conn.execute("CREATE TABLE IF NOT EXISTS processed_files (chat_id TEXT, file_id TEXT, filename TEXT, mime TEXT, mode TEXT, timestamp INTEGER, file_content TEXT, message_id TEXT, PRIMARY KEY (chat_id, file_id))")
                conn.execute("CREATE TABLE IF NOT EXISTS call_bridge (call_id TEXT PRIMARY KEY, signature TEXT NOT NULL, function_name TEXT NOT NULL, args_json TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS session_state (id INTEGER PRIMARY KEY, last_model_id TEXT, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_pkce_context (user_id TEXT PRIMARY KEY, verifier TEXT NOT NULL, state TEXT NOT NULL, timestamp INTEGER NOT NULL)")
                
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
                    (message_id, chat_id, role, std_json.dumps(parts).decode('utf-8'), ts)
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
                if thought: conn.execute("INSERT OR REPLACE INTO thought_archive (cumulative_hash, raw_thought, updated_at) VALUES (?, ?, ?)", (cumul, thought, int(time.time())))
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
        old_path = resolve_upload_file_path(self.user_id, file_id)
        if not old_path: return False
        new_path = os.path.join(self.user_dir, "files", os.path.basename(old_path))
        try:
            if not os.path.exists(new_path): shutil.move(old_path, new_path)
            return True
        except: return False

