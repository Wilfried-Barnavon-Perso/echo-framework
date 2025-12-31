"""
title: Gemini Pro Unified System (Platinum Agentic V122.75 - Documented Stateless Fix)
author: ECHO Architecture
version: 122.75
description: Version documentée et robuste. Corrige la duplication d'historique (Mode Stateless) et gère strictement les Signatures de Pensée pour Gemini 3 Pro, tout en assurant la rétrocompatibilité Gemini 2.5.
"""

# ==============================================================================
# SECTION 0 : IMPORTATIONS
# ==============================================================================
import os
import json
import sys
import secrets
import hashlib
import random
import re
import time
import uuid
import httpx
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# ==============================================================================
# SECTION 1 : DÉPENDANCES OPTIONNELLES
# ==============================================================================
# Gestion souple des dépendances pour éviter les crashs si les libs Google manquent.
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from google.auth.transport.requests import Request as GoogleAuthRequest
    HAS_GOOGLE_LIBS = True
except ImportError:
    HAS_GOOGLE_LIBS = False

try:
    from zoneinfo import ZoneInfo
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False

# ==============================================================================
# SECTION 2 : CONFIGURATION CLIENT OAUTH2
# ==============================================================================
# Configuration standard pour l'authentification "Device Flow" ou "Installed App" de Google.
# Ces identifiants permettent d'obtenir un token OAuth2 pour l'API CloudCode interne.
OFFICIAL_CLIENT_CONFIG = {
    "installed": {
        "client_id": "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com",
        "client_secret": "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",
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["https://codeassist.google.com/authcode"],
    }
}

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# ==============================================================================
# SECTION 3 : SERVICE D'AUTHENTIFICATION
# ==============================================================================
class AuthService:
    """
    Gère le cycle de vie complet de l'authentification OAuth2 Google.
    - Génération PKCE (Proof Key for Code Exchange) pour la sécurité.
    - Échange du code d'autorisation contre un Token.
    - Rafraîchissement automatique du Token expiré.
    - Récupération de l'ID Projet Google Cloud (obligatoire pour l'API).
    """
    def __init__(self, data_dir: str):
        self.token_path = f"{data_dir}/gemini_official_token.json"
        self.pkce_path = f"{data_dir}/gemini_pkce_verifier.txt"
        self.internal_project_cache = f"{data_dir}/gemini_internal_project.txt"
        self.base_url = "https://cloudcode-pa.googleapis.com/v1internal"

    def _generate_pkce(self):
        """Génère le challenge PKCE pour sécuriser l'échange de code."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        import base64
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        """Génère l'URL de connexion Google pour l'utilisateur."""
        if not HAS_GOOGLE_LIBS:
            return "❌ **Erreur** : Librairies `google-auth` manquantes."

        # Logique de réutilisation du verifier PKCE s'il est récent (< 300s)
        # pour éviter les incohérences en cas de rechargement de page.
        should_generate_new = True
        if os.path.exists(self.pkce_path):
            try:
                creation_time = os.path.getmtime(self.pkce_path)
                if time.time() - creation_time < 300:
                    with open(self.pkce_path, "r") as f:
                        verifier = f.read().strip()
                    if len(verifier) > 10:
                        should_generate_new = False
            except:
                pass

        if should_generate_new:
            verifier, challenge = self._generate_pkce()
            try:
                with open(self.pkce_path, "w") as f:
                    f.write(verifier)
            except Exception as e:
                return f"❌ Erreur IO: {str(e)}"
        else:
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            import base64
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

        flow = Flow.from_client_config(
            OFFICIAL_CLIENT_CONFIG, scopes=SCOPES, autogenerate_code_verifier=False
        )
        flow.redirect_uri = "https://codeassist.google.com/authcode"

        url, _ = flow.authorization_url(
            prompt="consent",
            access_type="offline",
            code_challenge=challenge,
            code_challenge_method="S256",
        )

        return (
            f"### 🔐 Authentification Requise\n\n"
            f"1. **[Cliquez ici]({url})**\n"
            f"2. Connectez-vous.\n"
            f"3. Copiez le code `4/...`.\n"
            f"4. **Collez-le ici**."
        )

    def exchange_code(self, code: str) -> Tuple[bool, str]:
        """Échange le code reçu de l'utilisateur contre un token d'accès durable."""
        if not HAS_GOOGLE_LIBS:
            return False, "Libs manquantes."

        if not os.path.exists(self.pkce_path):
            # Tentative de récupération via thread concurrent si fichier verrouillé
            for _ in range(3):
                if self.get_valid_credentials():
                    return True, "Succès (Récupéré via thread concurrent)."
                time.sleep(0.5)
            return False, "Session expirée (PKCE introuvable et Token non généré)."

        try:
            with open(self.pkce_path, "r") as f:
                verifier = f.read().strip()

            flow = Flow.from_client_config(
                OFFICIAL_CLIENT_CONFIG, scopes=SCOPES, autogenerate_code_verifier=False
            )
            flow.redirect_uri = "https://codeassist.google.com/authcode"
            flow.fetch_token(code=code.strip(), code_verifier=verifier)

            with open(self.token_path, "w") as f:
                f.write(flow.credentials.to_json())

            if os.path.exists(self.pkce_path):
                os.remove(self.pkce_path)
            return True, "Succès."
        except Exception as e:
            if self.get_valid_credentials():
                return True, "Succès (Récupéré post-erreur)."
            return False, str(e)

    def get_valid_credentials(self):
        """Récupère les credentials stockés et les rafraîchit si nécessaire."""
        creds = None
        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except:
                pass

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                with open(self.token_path, "w") as f:
                    f.write(creds.to_json())
            except:
                return None
        return creds if (creds and creds.valid) else None

    def get_project_id(self, creds, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        """
        Récupère l'ID du projet Google Cloud associé à l'utilisateur.
        C'est indispensable pour router les requêtes vers l'API Gemini interne.
        Utilise un cache pour éviter les appels API superflus.
        """
        logs = ""
        if os.path.exists(self.internal_project_cache) and not debug_mode:
            with open(self.internal_project_cache, "r") as f:
                pid = f.read().strip()
                if pid:
                    return pid, "Cache utilisé."

        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        }
        # Fallback sur les variables d'environnement si disponibles
        env_proj = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")

        payload = {
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        if env_proj:
            payload["cloudaicompanionProject"] = env_proj

        try:
            # Appel API spécial 'loadCodeAssist' pour obtenir le projet par défaut
            url = f"{self.base_url}:loadCodeAssist"
            resp = httpx.post(url, headers=headers, json=payload, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("cloudaicompanionProject")
                pid = None
                if isinstance(raw, dict):
                    pid = raw.get("id")
                elif isinstance(raw, str):
                    pid = raw

                if pid:
                    if pid.startswith("projects/"):
                        pid = pid.replace("projects/", "")
                    with open(self.internal_project_cache, "w") as f:
                        f.write(pid)
                    return pid, logs
                return None, logs + "JSON ok, ID manquant."
            return None, logs + f"Err API: {resp.status_code}"
        except Exception as e:
            return None, logs + f"Ex: {str(e)}"

        if env_proj:
            return env_proj, logs + "Fallback ENV."
        return None, logs + "Echec."

    def reset_storage(self):
        """Nettoie tous les fichiers de cache et tokens."""
        for p in [self.token_path, self.pkce_path, self.internal_project_cache]:
            if os.path.exists(p):
                os.remove(p)

# ==============================================================================
# SECTION 4 : ORCHESTRATEUR (CONTEXTE & SIGNATURES)
# ==============================================================================
class Orchestrator:
    """
    Responsable de la préparation des données avant l'envoi à l'API.
    - Détection de l'authentification.
    - Injection du contexte temporel et géographique.
    - Conversion des outils Open WebUI au format Gemini.
    - **Reconstruction critique de l'historique** (gestion des rôles, pensées, et signatures).
    """
    def __init__(self, valves):
        self.valves = valves
        self.location_cache_file = "/app/backend/data/gemini_geo_cache_v2.json"
        self.tool_map = {}

    def check_for_auth_code(self, messages: List[Dict]) -> Optional[str]:
        """Détecte si l'utilisateur a collé un code d'authentification Google (4/...)."""
        if not messages:
            return None
        last_msg = messages[-1].get("content", "").strip()
        match = re.search(r"(4/[a-zA-Z0-9_-]+)", last_msg)
        if match and len(match.group(1)) > 30:
            return match.group(1)
        return None

    def _get_geo_info(self) -> Tuple[str, str]:
        """Récupère la localisation approximative du serveur pour le contexte."""
        loc, tz = "Paris, France", "Europe/Paris"
        override = getattr(self.valves, "OVERRIDE_LOCATION", "").strip()
        if override:
            loc = override
        if getattr(self.valves, "ENABLE_AUTO_LOCATION", True):
            if os.path.exists(self.location_cache_file):
                try:
                    if time.time() - os.path.getmtime(self.location_cache_file) < 86400:
                        with open(self.location_cache_file, "r") as f:
                            c = json.load(f)
                            if not override:
                                loc = c.get("location", loc)
                            return loc, c.get("timezone", tz)
                except:
                    pass
            try:
                import requests
                r = requests.get("http://ip-api.com/json/", timeout=2)
                if r.status_code == 200:
                    d = r.json()
                    l_api = f"{d.get('city')}, {d.get('country')}"
                    t_api = d.get("timezone", tz)
                    with open(self.location_cache_file, "w") as f:
                        json.dump({"location": l_api, "timezone": t_api}, f)
                    if not override:
                        loc = l_api
                    return loc, t_api
            except:
                pass
        return loc, tz

    def _get_current_time(self, timezone_id: str) -> Tuple[str, str]:
        """Récupère l'heure locale formatée."""
        try:
            if HAS_ZONEINFO:
                now = datetime.now(ZoneInfo(timezone_id))
            else:
                now = datetime.now()
        except:
            now = datetime.now()
        return now.strftime("%A %d %B %Y"), now.strftime("%H:%M")

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """Convertit les définitions d'outils Open WebUI vers le format Gemini Function Declarations."""
        if not tools:
            return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                parameters = f.get("parameters")
                if parameters is None:
                    parameters = {"type": "object", "properties": {}}
                if "type" not in parameters:
                    parameters["type"] = "object"

                funcs.append(
                    {
                        "name": f.get("name"),
                        "description": f.get("description", "No description provided"),
                        "parameters": parameters,
                    }
                )
        return [{"functionDeclarations": funcs}] if funcs else None

    def get_system_instruction(self) -> Dict:
        """Construit le prompt système avec injection du contexte spatio-temporel."""
        sys_prompt_text = self.valves.SYSTEM_PROMPT
        if getattr(self.valves, "ENABLE_DATE_TIME", True):
            loc, tz = self._get_geo_info()
            d, t = self._get_current_time(tz)
            sys_prompt_text += f"\n\n[CONTEXT]\nDate: {d}\nTime: {t}\nLocation: {loc}\n"

        return {"parts": [{"text": sys_prompt_text}]}

    def prepare_context(self, messages: List[Dict]) -> List[Dict]:
        """
        Reconstruit l'historique des messages pour l'API Gemini.
        Cette fonction est CRITIQUE pour éviter les boucles et le bégaiement.
        
        Logique appliquée :
        1. **Nettoyage des Pensées** : On supprime les balises <think> du texte envoyé à Google. 
           Si le modèle relit ses propres pensées passées, il a tendance à recommencer le raisonnement (boucle).
        
        2. **Gestion des Signatures (Thought Signatures)** : Gemini 3 Pro est 'Stateful' dans son raisonnement.
           Il émet une signature cryptée après avoir pensé. 
           POURQUOI : Cette signature contient l'état de sa mémoire de travail.
           COMMENT : On la récupère depuis un lien caché Markdown (stocké par StreamProcessor) et on la 
           réinjecte dans le champ 'thoughtSignature' de l'API.
           REGLE D'OR : La signature doit être attachée au TOUT PREMIER élément (part) du message modèle.
        
        3. **Gestion des Outils** : On regroupe les appels d'outils et leurs réponses pour respecter la 
           structure stricte User -> Model(Calls) -> User(Responses).
        """
        contents = []
        
        # 1. Indexation des Tool IDs pour associer les réponses aux appels
        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if "id" in tc and "function" in tc:
                        self.tool_map[tc["id"]] = tc["function"].get("name")

        # 2. Construction du flux séquentiel
        i = 0
        while i < len(messages):
            m = messages[i]
            role = m["role"]
            content = m.get("content", "")

            # Ignorer les messages systèmes (gérés par system_instruction) et les codes d'auth
            if role == "system" or ("4/" in str(content) and len(str(content)) > 30):
                i += 1
                continue

            # --- CAS 1 : TOOL RESPONSE (Résultat d'outil provenant du système) ---
            if role == "tool":
                parts = []
                # On regroupe toutes les réponses d'outils consécutives dans un seul message User
                while i < len(messages) and messages[i]["role"] == "tool":
                    tm = messages[i]
                    tool_call_id = tm.get("tool_call_id")
                    tool_name = self.tool_map.get(tool_call_id, tm.get("name", "unknown_tool"))
                    
                    try:
                        parsed_content = json.loads(tm.get("content", "{}"))
                    except:
                        parsed_content = {"result": str(tm.get("content", ""))}

                    parts.append({
                        "functionResponse": {
                            "name": tool_name,
                            "response": parsed_content
                        }
                    })
                    i += 1
                
                # Ajout au flux : Les FunctionResponse doivent être envoyées par le rôle 'user'
                if contents and contents[-1]["role"] == "user":
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": "user", "parts": parts})
                continue

            # --- CAS 2 : MODEL (Réponse de l'Assistant) ---
            elif role in ["assistant", "model"]:
                parts = []
                text_content = str(content) if content else ""
                
                # A. NETTOYAGE DES PENSÉES (CRITIQUE ANTI-BOUCLE)
                # On retire tout ce qui est entre <think>...</think> pour que le modèle ne se "lise" pas penser.
                text_content = re.sub(r'<think>.*?</think>', '', text_content, flags=re.DOTALL).strip()
                
                # B. EXTRACTION THOUGHT SIGNATURE
                # On cherche le lien caché [ ](context://thought_signature/...) injecté par StreamProcessor
                thought_sig = None
                sig_match = re.search(r"\[\s*\]\(context://thought_signature/([a-zA-Z0-9_\-\.\=]+)\)", text_content)
                if sig_match:
                    thought_sig = sig_match.group(1)
                    # On retire le lien caché du texte visible pour l'API
                    text_content = text_content.replace(sig_match.group(0), "").strip()

                # C. CONSTRUCTION DES PARTS (Texte + Outils)
                
                # 1. Texte (toujours en premier si présent, convention standard)
                if text_content:
                    parts.append({"text": text_content})

                # 2. Outils (Function Calls)
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        parts.append({
                            "functionCall": {
                                "name": tc["function"]["name"],
                                "args": json.loads(tc["function"]["arguments"])
                            }
                        })
                
                # 3. ATTACHEMENT DE LA SIGNATURE (CRITIQUE)
                # La signature DOIT être attachée au TOUT PREMIER élément de la liste 'parts',
                # qu'il s'agisse de texte ou d'un appel de fonction.
                if thought_sig:
                    if parts:
                        parts[0]["thoughtSignature"] = thought_sig
                    else:
                        # Si aucun contenu visible mais une signature existe (cas rare pensée pure),
                        # on crée un bloc texte vide pour porter la signature.
                        parts.append({"text": "", "thoughtSignature": thought_sig})

                if parts:
                    if contents and contents[-1]["role"] == "model":
                        contents[-1]["parts"].extend(parts)
                    else:
                        contents.append({"role": "model", "parts": parts})

            # --- CAS 3 : USER (Message de l'Utilisateur) ---
            else:
                if content:
                    parts = [{"text": str(content)}]
                    if contents and contents[-1]["role"] == "user":
                        contents[-1]["parts"].extend(parts)
                    else:
                        contents.append({"role": "user", "parts": parts})
            
            i += 1

        # DEDUPLICATION & FUSION FINALE
        # Optimisation pour regrouper les messages consécutifs du même rôle
        final_contents = []
        for c in contents:
            if final_contents and final_contents[-1]["role"] == c["role"]:
                final_contents[-1]["parts"].extend(c["parts"])
            else:
                final_contents.append(c)

        return final_contents

# ==============================================================================
# SECTION 5 : ADAPTATEUR API
# ==============================================================================
class GeminiAdapter:
    """
    Construit la requête JSON finale pour l'API Google.
    Gère la compatibilité Hybride (Gemini 2.5 vs Gemini 3) et le mode Stateless.
    """
    def __init__(self, base_url):
        self.base_url = base_url

    def build(self, project_id, contents, system_instr, temp, max_tok, think_level, model_id, session_id_context, tools=None):
        # 1. Configuration de base (Compatible Gemini 2.5 / 3)
        gen_config = {
            "temperature": temp,
            "maxOutputTokens": max_tok,
        }

        # 2. Configuration Spécifique Gemini 3 (Thinking Mode)
        # Uniquement si l'ID du modèle contient "gemini-3", on active le bloc thinkingConfig.
        # Gemini 2.5 rejetterait ce paramètre (Erreur 400).
        if "gemini-3" in model_id:
            t_level = think_level.lower()
            if t_level == "dynamic":
                t_level = "high" # Default pour Pro
            
            # Injection conditionnelle du bloc thinkingConfig
            gen_config["thinkingConfig"] = {
                "includeThoughts": True, # Nécessaire pour recevoir les pensées et la signature
                "thinkingLevel": t_level
            }

        # --- FIX CRITIQUE "STATELESS" (Anti-Bégaiement) ---
        # Problème : L'API 'v1internal' conserve un historique côté serveur lié au session_id.
        # Open WebUI gère déjà son propre historique complet et le renvoie à chaque requête.
        # Si on réutilise le même session_id, Google AJOUTE l'historique reçu à celui déjà stocké.
        # Résultat : Le contexte double, triple, quadruple... et le modèle boucle.
        # 
        # Solution : On génère un UUID aléatoire (uuid4) à CHAQUE requête.
        # Cela force Google à traiter la requête comme une nouvelle session propre,
        # en se basant uniquement sur l'historique fourni dans 'contents'.
        final_session_id = str(uuid.uuid4())

        payload = {
            "model": model_id,
            "project": project_id,
            "user_prompt_id": hex(random.getrandbits(64))[2:],
            "request": {
                "systemInstruction": system_instr,
                "contents": contents,
                "generationConfig": gen_config,
                "session_id": final_session_id, # Session UNIQUE par appel
            },
        }

        if tools:
            payload["request"]["tools"] = tools
            payload["request"]["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        return {
            "url": f"{self.base_url}:streamGenerateContent?alt=sse",
            "headers": {
                "Content-Type": "application/json",
                "User-Agent": "GeminiCLI/0.20.0",
                "x-goog-api-client": "gl-python/3.10",
            },
            "json": payload,
        }

# ==============================================================================
# SECTION 6 : PROCESSEUR DE FLUX
# ==============================================================================
class StreamProcessor:
    """
    Traite le flux SSE (Server-Sent Events) renvoyé par Google.
    - Extrait les chunks de texte.
    - Gère l'affichage des balises <think> (pensées).
    - Capture et persiste la Thought Signature.
    - Formate les appels d'outils pour Open WebUI.
    """
    def __init__(self, debug=False):
        self.debug = debug

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        buffer = ""
        in_think = False
        last_sig = None
        tool_index = 0

        async for chunk in response.aiter_bytes():
            buffer += chunk.decode("utf-8", errors="ignore")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                try:
                    json_str = line[6:]
                    data = json.loads(json_str)
                    if self.debug:
                        yield f"\n`[SSE] {json.dumps(data, ensure_ascii=False)}`\n"

                    cand = data.get("response", {}).get("candidates", [])
                    if cand and "content" in cand[0]:
                        parts = cand[0]["content"].get("parts", [])
                        for part in parts:
                            txt = part.get("text", "")
                            is_think = part.get("thought", False)
                            func_call = part.get("functionCall")

                            # Capture de la signature (CRITIQUE pour le maintien du raisonnement)
                            if "thoughtSignature" in part:
                                last_sig = part["thoughtSignature"]

                            # Cas 1 : Appel d'outil
                            if func_call:
                                if in_think:
                                    yield "\n</think>\n"
                                    in_think = False

                                chunk_dict = {
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {
                                                "tool_calls": [
                                                    {
                                                        "index": tool_index,
                                                        "id": f"call_{secrets.token_hex(8)}",
                                                        "type": "function",
                                                        "function": {
                                                            "name": func_call["name"],
                                                            "arguments": json.dumps(func_call["args"]),
                                                        },
                                                    }
                                                ]
                                            },
                                            "finish_reason": "tool_calls",
                                        }
                                    ]
                                }
                                tool_index += 1
                                yield chunk_dict

                            # Cas 2 : Pensée (Thinking)
                            elif is_think:
                                if not in_think:
                                    yield "<think>\n"
                                    in_think = True
                                yield txt

                            # Cas 3 : Texte standard
                            else:
                                if in_think:
                                    yield "\n</think>\n"
                                    in_think = False
                                if txt:
                                    yield txt
                except:
                    pass

        if in_think:
            yield "\n</think>\n"
        
        # PERSISTENCE DE L'ÉTAT AGENTIQUE
        # On injecte la signature capturée dans l'historique d'Open WebUI via un lien caché.
        # L'Orchestrator la relira au prochain tour pour restaurer l'état mental du modèle.
        if last_sig:
            yield f"\n[ ](context://thought_signature/{last_sig})"

# ==============================================================================
# SECTION 7 : LE PIPE (POINT D'ENTRÉE)
# ==============================================================================
class Pipe:
    class Valves(BaseModel):
        RUN_DIAGNOSTICS: bool = Field(default=False, description="🚑 DIAGNOSTICS")
        FORCE_RESET_AUTH: bool = Field(default=False, description="🔴 RESET AUTH")
        DEBUG_MODE: bool = Field(default=False, description="🐞 DEBUG MODE")
        MODEL_SELECTION: Literal["gemini-3-pro-preview", "gemini-2.5-pro"] = Field(
            default="gemini-3-pro-preview", description="Modèle"
        )
        TEMPERATURE: float = Field(default=1.0, description="Température")
        MAX_TOKENS: int = Field(default=65536, description="Max Tokens")
        THINKING_LEVEL: Literal["DYNAMIC", "LOW", "HIGH"] = Field(
            default="DYNAMIC", description="Niveau de réflexion (Gemini 3)"
        )
        SYSTEM_PROMPT: str = Field(
            default="Tu es un assistant expert.", description="Prompt Système"
        )
        ENABLE_DATE_TIME: bool = Field(default=True, description="🕒 Injecter Temps")
        ENABLE_AUTO_LOCATION: bool = Field(default=True, description="📍 Injecter Lieu")
        OVERRIDE_LOCATION: str = Field(default="", description="✏️ Forcer Lieu")

    def __init__(self):
        self.valves = self.Valves()
        self.data_dir = "/app/backend/data"
        os.makedirs(self.data_dir, exist_ok=True)
        self.auth = AuthService(self.data_dir)
        self.base_url = "https://cloudcode-pa.googleapis.com/v1internal"
        if os.path.exists(self.auth.internal_project_cache):
            try:
                os.remove(self.auth.internal_project_cache)
            except:
                pass

    async def pipe(self, body: dict, __user__: dict = None, __request__: Optional[any] = None) -> AsyncGenerator[Union[str, Dict], None]:
        orch = Orchestrator(self.valves)
        proc = StreamProcessor(self.valves.DEBUG_MODE)

        if self.valves.DEBUG_MODE:
            last_msg_content = "Aucun"
            if body.get("messages"):
                last_msg_content = body["messages"][-1].get("content", "")[:200]
            yield f"🐞 **DEBUG: INPUT**\n`{last_msg_content}...`\n"

        ac = orch.check_for_auth_code(body.get("messages", []))
        if ac:
            success, msg = self.auth.exchange_code(ac)
            yield f"✅ **{msg}**" if success else f"❌ **Échec** : `{msg}`"
            return

        if self.valves.FORCE_RESET_AUTH:
            self.auth.reset_storage()
            yield "🔄 **Reset.**"
            return

        creds = self.auth.get_valid_credentials()
        if not creds:
            yield self.auth.get_auth_url()
            return

        pid, debug_log = self.auth.get_project_id(creds, self.valves.DEBUG_MODE)
        if not pid:
            yield f"❌ **Erreur Projet**\n{debug_log}"
            return

        tools = orch.convert_owui_tools(body.get("tools"))
        adapter = GeminiAdapter(self.base_url)
        context = orch.prepare_context(body.get("messages", []))
        sys_instr = orch.get_system_instruction()
        chat_id = body.get("chat_id")

        req = adapter.build(
            pid,
            context,
            sys_instr,
            self.valves.TEMPERATURE,
            self.valves.MAX_TOKENS,
            self.valves.THINKING_LEVEL,
            self.valves.MODEL_SELECTION,
            chat_id,
            tools,
        )
        req["headers"]["Authorization"] = f"Bearer {creds.token}"

        if self.valves.DEBUG_MODE:
            yield f"🐞 **API REQ**\nBody snippet: `{json.dumps(req['json'])[:500]}...`\n"

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST", req["url"], json=req["json"], headers=req["headers"]
                ) as r:
                    if r.status_code != 200:
                        err = await r.aread()
                        yield f"⚠️ **API ERROR {r.status_code}**\n`{err.decode(errors='ignore')}`"
                        return

                    async for token in proc.process(r):
                        yield token
        except Exception as e:
            yield f"🔥 **CRASH** : `{str(e)}`"