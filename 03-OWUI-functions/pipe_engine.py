"""
title: Gemini Pro Unified System (Platinum Agentic V123.05 - Master Documented)
author: ECHO Architecture
version: 123.05
description: Version de référence. Architecture "Zero Disk" (In-Memory). Intègre la persistance des Signatures via Injection JSON (résistance aux outils) et Markdown (résistance au texte). Mode Stateless pour éviter les boucles. Commentaires pédagogiques complets.
"""

# ==============================================================================
# SECTION 0 : IMPORTATIONS & UTILITAIRES
# ==============================================================================
# Ces modules sont standards dans Python et ne nécessitent pas d'installation via pip
# dans le conteneur Open WebUI, garantissant une portabilité maximale.
import os
import json
import sys
import secrets  # Génération crypto-sécurisée (PKCE, IDs)
import hashlib  # Hachage SHA256 pour le protocole PKCE
import random   # Génération d'IDs non-critiques
import re       # Expressions régulières (Vital pour parser les signatures et codes)
import time
import uuid     # Génération d'UUIDs pour le mode Stateless
import httpx    # Client HTTP asynchrone moderne (remplace requests pour l'async)
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, AsyncGenerator, Literal, Tuple, Any, Union

# ==============================================================================
# SECTION 1 : DÉPENDANCES OPTIONNELLES (GRACEFUL DEGRADATION)
# ==============================================================================
# Pour éviter que le Pipe ne fasse crasher tout Open WebUI si une librairie manque,
# on utilise des blocs try/except.
# - google-auth : Nécessaire uniquement pour le login initial.
# - zoneinfo : Pour une gestion précise des fuseaux horaires (Python 3.9+).

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
# SECTION 2 : CONFIGURATION OAUTH2 (IDENTITY PROVIDER)
# ==============================================================================
# Configuration "Hardcodée" du client OAuth2 Google.
# POURQUOI : Ces IDs sont publics et correspondent à l'application "Google Cloud SDK"
# ou similaires, permettant d'utiliser le flow "Device/Installed App".
# Cela permet d'obtenir un token avec les scopes nécessaires pour l'API interne.
OFFICIAL_CLIENT_CONFIG = {
    "installed": {
        "client_id": "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com",
        "client_secret": "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",
        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["https://codeassist.google.com/authcode"],
    }
}

# SCOPES : Les permissions demandées à l'utilisateur.
# "cloud-platform" est le scope "Dieu" qui permet d'interagir avec Vertex AI / Gemini.
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# ==============================================================================
# SECTION 3 : SERVICE D'AUTHENTIFICATION (PKCE FLOW)
# ==============================================================================
class AuthService:
    """
    Gère l'obtention et le rafraîchissement des tokens d'accès Google.
    Utilise le protocole PKCE (Proof Key for Code Exchange) pour sécuriser l'échange
    de code, car nous ne pouvons pas garantir la confidentialité du client secret
    dans un script distribué.
    """
    def __init__(self, data_dir: str):
        # Chemins de persistance : Seuls les tokens sont stockés sur disque.
        # C'est nécessaire pour ne pas avoir à se relogguer à chaque requête.
        self.token_path = f"{data_dir}/gemini_official_token.json"
        self.pkce_path = f"{data_dir}/gemini_pkce_verifier.txt"
        self.internal_project_cache = f"{data_dir}/gemini_internal_project.txt"
        
        # API Endpoint Interne : C'est le secret de la performance.
        # Contrairement à l'API publique Vertex, celle-ci est optimisée pour les IDEs.
        self.base_url = "https://cloudcode-pa.googleapis.com/v1internal"

    def _generate_pkce(self):
        """
        PKCE (RFC 7636) :
        1. On génère un secret aléatoire (verifier).
        2. On le hache (challenge).
        3. On envoie le challenge à Google lors de la demande d'URL.
        4. On envoie le verifier lors de l'échange du code.
        Preuve que c'est bien nous qui avons initié la demande.
        """
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        import base64
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        """
        Construit l'URL de login pour l'utilisateur.
        Implémente une logique de cache du PKCE (300s) pour éviter les erreurs
        si l'utilisateur rafraîchit la page contenant le lien.
        """
        if not HAS_GOOGLE_LIBS:
            return "❌ **Erreur** : Librairies `google-auth` manquantes."

        should_generate_new = True
        if os.path.exists(self.pkce_path):
            try:
                creation_time = os.path.getmtime(self.pkce_path)
                if time.time() - creation_time < 300: # 5 minutes de validité
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
            # On recalcule le challenge à partir du verifier existant
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            import base64
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

        flow = Flow.from_client_config(
            OFFICIAL_CLIENT_CONFIG, scopes=SCOPES, autogenerate_code_verifier=False
        )
        flow.redirect_uri = "https://codeassist.google.com/authcode"

        url, _ = flow.authorization_url(
            prompt="consent",
            access_type="offline", # CRUCIAL : Pour obtenir un Refresh Token
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
        """
        Finalise le flux OAuth2.
        Prend le code collé par l'utilisateur et le 'verifier' PKCE stocké localement
        pour obtenir les tokens définitifs.
        """
        if not HAS_GOOGLE_LIBS:
            return False, "Libs manquantes."

        if not os.path.exists(self.pkce_path):
            # Fallback : Si le fichier PKCE a disparu (ex: redémarrage container),
            # on vérifie si on a déjà un token valide.
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

            # Sauvegarde atomique des credentials
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
        """
        Retourne des credentials valides.
        Si le token d'accès est expiré, utilise le refresh token pour le renouveler
        automatiquement sans intervention utilisateur.
        """
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
        L'API Gemini nécessite un ID de projet Google Cloud.
        Cette fonction interroge l'API `loadCodeAssist` pour savoir quel projet
        est lié par défaut au compte Google de l'utilisateur.
        """
        logs = ""
        # 1. Vérification Cache
        if os.path.exists(self.internal_project_cache) and not debug_mode:
            with open(self.internal_project_cache, "r") as f:
                pid = f.read().strip()
                if pid:
                    return pid, "Cache utilisé."

        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        }
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
            # 2. Appel API de découverte
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
                    # Mise en cache
                    with open(self.internal_project_cache, "w") as f:
                        f.write(pid)
                    return pid, logs
                return None, logs + "JSON ok, ID manquant."
            return None, logs + f"Err API: {resp.status_code}"
        except Exception as e:
            return None, logs + f"Ex: {str(e)}"

        # 3. Fallback ENV
        if env_proj:
            return env_proj, logs + "Fallback ENV."
        return None, logs + "Echec."

    def reset_storage(self):
        """Force la déconnexion en supprimant tous les fichiers."""
        for p in [self.token_path, self.pkce_path, self.internal_project_cache]:
            if os.path.exists(p):
                os.remove(p)

# ==============================================================================
# SECTION 4 : ORCHESTRATEUR (LOGIQUE CÉRÉBRALE DU PIPE)
# ==============================================================================
class Orchestrator:
    """
    Cette classe prépare les messages avant de les envoyer à Google.
    C'est ici que réside toute la complexité de la gestion de l'état (State Management).
    """
    def __init__(self, valves):
        self.valves = valves
        self.location_cache_file = "/app/backend/data/gemini_geo_cache_v2.json"
        self.tool_map = {} # Sert à mapper ID Outil <-> Nom Outil

    def check_for_auth_code(self, messages: List[Dict]) -> Optional[str]:
        """Détecte si l'utilisateur vient de coller un code d'authentification."""
        if not messages:
            return None
        last_msg = messages[-1].get("content", "").strip()
        # Regex : 4/ suivi d'au moins 30 caractères (format standard Google)
        match = re.search(r"(4/[a-zA-Z0-9_-]+)", last_msg)
        if match and len(match.group(1)) > 30:
            return match.group(1)
        return None

    def _get_geo_info(self) -> Tuple[str, str]:
        """
        Récupère la localisation du serveur pour donner du contexte au modèle.
        (Ex: "Quelle heure est-il ?" nécessite de connaître le fuseau horaire).
        Utilise un cache fichier pour ne pas spammer l'API de géoloc.
        """
        loc, tz = "Paris, France", "Europe/Paris"
        override = getattr(self.valves, "OVERRIDE_LOCATION", "").strip()
        if override:
            loc = override
        if getattr(self.valves, "ENABLE_AUTO_LOCATION", True):
            if os.path.exists(self.location_cache_file):
                try:
                    if time.time() - os.path.getmtime(self.location_cache_file) < 86400: # Cache 24h
                        with open(self.location_cache_file, "r") as f:
                            c = json.load(f)
                            if not override:
                                loc = c.get("location", loc)
                            return loc, c.get("timezone", tz)
                except:
                    pass
            try:
                # Appel externe (timeout court pour ne pas bloquer)
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
        """Date et heure formatée selon le fuseau horaire local."""
        try:
            if HAS_ZONEINFO:
                now = datetime.now(ZoneInfo(timezone_id))
            else:
                now = datetime.now()
        except:
            now = datetime.now()
        return now.strftime("%A %d %B %Y"), now.strftime("%H:%M")

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """
        Traduit le schéma des outils Open WebUI (inspiré d'OpenAI)
        vers le schéma attendu par Google (FunctionDeclarations).
        """
        if not tools:
            return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                parameters = f.get("parameters")
                # Google exige un objet parameters, même vide
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
        """Construit le Prompt Système enrichi du contexte."""
        sys_prompt_text = self.valves.SYSTEM_PROMPT
        if getattr(self.valves, "ENABLE_DATE_TIME", True):
            loc, tz = self._get_geo_info()
            d, t = self._get_current_time(tz)
            sys_prompt_text += f"\n\n[CONTEXT]\nDate: {d}\nTime: {t}\nLocation: {loc}\n"
        return {"parts": [{"text": sys_prompt_text}]}

    def prepare_context(self, messages: List[Dict]) -> List[Dict]:
        """
        FONCTION CRITIQUE DE RECONSTRUCTION DE L'HISTORIQUE.
        
        POURQUOI CETTE COMPLEXITÉ ?
        1. **Gemini 3 Pro est 'Stateful' (Cognitif)** : Il ne suffit pas de lui renvoyer le texte passé.
           Il faut lui renvoyer sa "Signature de Pensée" (Thought Signature) qui contient l'état de 
           sa mémoire de travail cryptée. Sans cela, il perd le fil de ses raisonnements complexes.
        
        2. **Problème de Persistance Open WebUI** : Open WebUI stocke l'historique en BDD, mais il peut
           altérer le format (truncature, nettoyage HTML). Si la signature (qui est une chaîne texte)
           est perdue, le modèle régresse.
        
        3. **Stratégie "In-Band" (Défense en profondeur)** :
           Nous stockons la signature à deux endroits dans l'historique :
           - A. **Dans le texte** (via un lien Markdown caché `[](context://...)`).
           - B. **Dans les appels d'outils** (via un argument JSON injecté `_gemini_signature`).
           
           Cette fonction scanne ces deux endroits pour retrouver la signature et la remettre
           au bon endroit (champ `thoughtSignature`) pour l'API Google.
        
        4. **Prévention du Bégaiement (Anti-Loop)** :
           On nettoie les balises `<think>` du texte envoyé. Si le modèle relit ses propres
           pensées passées sous forme de texte, il a tendance à recommencer à penser la même chose
           (boucle infinie).
        """
        contents = []
        
        # 1. Indexation des Tool IDs (pour relier FunctionCall à FunctionResponse)
        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if "id" in tc and "function" in tc:
                        self.tool_map[tc["id"]] = tc["function"].get("name")

        # 2. Itération sur les messages bruts
        i = 0
        while i < len(messages):
            m = messages[i]
            role = m["role"]
            
            # Normalisation du contenu (Gestion Multimodal List vs String)
            raw_content = m.get("content", "")
            if isinstance(raw_content, list):
                content = ""
                for part in raw_content:
                    if isinstance(part, dict) and "text" in part:
                        content += part["text"]
            else:
                content = str(raw_content) if raw_content else ""
            
            # --- FILTRE 1 : Messages Système ---
            if role == "system":
                i += 1
                continue
            
            # --- FILTRE 2 : Code d'Auth (Sécurité Anti-Faux Positif) ---
            # IMPORTANT : On ne vérifie le code "4/..." QUE si c'est l'utilisateur.
            # Les signatures Base64 du modèle peuvent contenir "4/" naturellement.
            # Si on filtre aussi le modèle, on supprime son historique par erreur (cause du bégaiement).
            if role == "user" and ("4/" in str(content) and len(str(content)) > 30):
                if re.search(r"(4/[a-zA-Z0-9_-]+)", str(content)):
                    i += 1
                    continue

            # --- CAS 1 : TOOL RESPONSE (Résultat d'outil) ---
            if role == "tool":
                parts = []
                # Regroupement des résultats d'outils consécutifs
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
                # Les réponses d'outils sont toujours émises par l'utilisateur (User)
                if contents and contents[-1]["role"] == "user":
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": "user", "parts": parts})
                continue

            # --- CAS 2 : MODEL (Assistant) ---
            elif role in ["assistant", "model"]:
                parts = []
                text_content = str(content) if content else ""
                
                # A. NETTOYAGE DES PENSÉES
                # On retire le bloc <think>...</think> pour ne garder que la réponse finale.
                text_content = re.sub(r'<think>.*?</think>', '', text_content, flags=re.DOTALL).strip()
                
                # B. EXTRACTION SIGNATURE (SOURCE 1 : TEXTE)
                # On cherche le lien caché Markdown injecté par StreamProcessor.
                # Regex robuste : supporte Base64 complet (a-z, 0-9, +, /, =)
                thought_sig = None
                sig_match = re.search(r"\[\s*\]\(context://thought_signature/([a-zA-Z0-9_\-\.\=\+\/]+)\)", text_content)
                if sig_match:
                    thought_sig = sig_match.group(1)
                    # On nettoie le lien caché pour ne pas l'envoyer à l'API
                    text_content = text_content.replace(sig_match.group(0), "").strip()

                # C. CONSTRUCTION DES PARTS
                if text_content:
                    parts.append({"text": text_content})

                # D. GESTION DES APPELS D'OUTILS & EXTRACTION (SOURCE 2 : JSON)
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            
                            # Extraction de la signature "Passager Clandestin"
                            # Si Open WebUI a coupé le texte mais gardé le JSON, on la retrouve ici !
                            injected_sig = args.pop("_gemini_signature", None)
                            if injected_sig:
                                thought_sig = injected_sig # Cette source est prioritaire car structurée
                            
                            parts.append({
                                "functionCall": {
                                    "name": tc["function"]["name"],
                                    # On envoie les args nettoyés (sans la signature injectée)
                                    "args": args 
                                }
                            })
                        except:
                            pass
                
                # E. FILET DE SÉCURITÉ (Safety Net)
                # Si après nettoyage il ne reste rien (ex: message ne contenant que <think>),
                # on force un espace vide.
                # POURQUOI : Si on envoie un message vide ou si on le supprime, l'alternance
                # User/Model est brisée, et l'API Google rejette la requête ou confond les tours.
                if not parts:
                    parts.append({"text": " "})

                # F. ATTACHEMENT DE LA SIGNATURE
                # Règle d'Or Gemini 3 : La signature doit être sur le PREMIER élément de la liste 'parts'.
                if thought_sig:
                    parts[0]["thoughtSignature"] = thought_sig

                if parts:
                    if contents and contents[-1]["role"] == "model":
                        contents[-1]["parts"].extend(parts)
                    else:
                        contents.append({"role": "model", "parts": parts})

            # --- CAS 3 : USER (Message standard) ---
            else:
                if content:
                    parts = [{"text": str(content)}]
                    if contents and contents[-1]["role"] == "user":
                        contents[-1]["parts"].extend(parts)
                    else:
                        contents.append({"role": "user", "parts": parts})
            
            i += 1

        # 3. DÉDUPLICATION FINALE
        # Optimisation : On fusionne les messages consécutifs du même rôle.
        final_contents = []
        for c in contents:
            if final_contents and final_contents[-1]["role"] == c["role"]:
                final_contents[-1]["parts"].extend(c["parts"])
            else:
                final_contents.append(c)

        return final_contents

# ==============================================================================
# SECTION 6 : ADAPTATEUR API (CONFIGURATION & STATELESS)
# ==============================================================================
class GeminiAdapter:
    def __init__(self, base_url):
        self.base_url = base_url

    def build(self, project_id, contents, system_instr, temp, max_tok, think_level, model_id, session_id_context, tools=None):
        """
        Construit le payload JSON final.
        """
        gen_config = {
            "temperature": temp,
            "maxOutputTokens": max_tok,
        }

        # Configuration conditionnelle "Thinking Mode"
        # Uniquement pour Gemini 3. Gemini 2.5 ne comprend pas ce paramètre.
        if "gemini-3" in model_id:
            t_level = think_level.lower()
            if t_level == "dynamic":
                t_level = "high"
            
            gen_config["thinkingConfig"] = {
                "includeThoughts": True, # Obligatoire pour recevoir les pensées et la signature
                "thinkingLevel": t_level
            }

        # --- FIX STATELESS (Anti-Bégaiement) ---
        # L'API interne Google a une mémoire serveur liée au 'session_id'.
        # Si on réutilise le 'chat_id' d'Open WebUI comme 'session_id', Google *ajoute*
        # l'historique qu'on lui envoie à ce qu'il a déjà en mémoire.
        # Résultat : Le contexte double à chaque tour (A -> AB -> ABC).
        #
        # SOLUTION : On génère un UUID aléatoire à chaque requête.
        # On dit à Google : "Oublie le passé serveur, voici l'historique complet (contents) 
        # tel que je le veux maintenant".
        final_session_id = str(uuid.uuid4())

        payload = {
            "model": model_id,
            "project": project_id,
            "user_prompt_id": hex(random.getrandbits(64))[2:],
            "request": {
                "systemInstruction": system_instr,
                "contents": contents,
                "generationConfig": gen_config,
                "session_id": final_session_id, # UUID Unique = Session Jetable
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
# SECTION 7 : PROCESSEUR DE FLUX (INJECTION & NETTOYAGE)
# ==============================================================================
class StreamProcessor:
    """
    Traite le flux SSE entrant et le transforme pour Open WebUI.
    C'est ici qu'on capture la signature et qu'on l'injecte pour la persistance.
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

                            # --- 1. CAPTURE DE LA SIGNATURE ---
                            # On la met de côté pour l'injecter plus tard
                            if "thoughtSignature" in part:
                                last_sig = part["thoughtSignature"]

                            # --- 2. TRAITEMENT APPEL D'OUTIL ---
                            if func_call:
                                if in_think:
                                    yield "\n</think>\n"
                                    in_think = False

                                # INJECTION IN-BAND (JSON)
                                # On insère la signature directement dans les arguments de l'outil.
                                # Comme Open WebUI stocke l'appel d'outil, il stockera la signature avec.
                                args = func_call["args"]
                                if last_sig:
                                    args["_gemini_signature"] = last_sig

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
                                                            # Les arguments contiennent maintenant la signature cachée
                                                            "arguments": json.dumps(args), 
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

                            # --- 3. TRAITEMENT PENSÉE ---
                            elif is_think:
                                if not in_think:
                                    yield "<think>\n"
                                    in_think = True
                                yield txt

                            # --- 4. TRAITEMENT TEXTE STANDARD ---
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
        
        # --- 5. INJECTION IN-BAND (MARKDOWN) ---
        # Pour les réponses textuelles, on ajoute un lien Markdown caché à la fin.
        # Il sera invisible pour l'utilisateur (rendu vide) mais présent dans l'historique brut.
        if last_sig:
            yield f"\n[ ](context://thought_signature/{last_sig})"

# ==============================================================================
# SECTION 8 : LE PIPE (POINT D'ENTRÉE)
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
        # Nettoyage préventif
        if os.path.exists(self.auth.internal_project_cache):
            try:
                os.remove(self.auth.internal_project_cache)
            except:
                pass

    async def pipe(self, body: dict, __user__: dict = None, __request__: Optional[any] = None) -> AsyncGenerator[Union[str, Dict], None]:
        orch = Orchestrator(self.valves)
        proc = StreamProcessor(self.valves.DEBUG_MODE)

        # 1. Debug
        if self.valves.DEBUG_MODE:
            last_msg_content = "Aucun"
            if body.get("messages"):
                last_msg_content = body["messages"][-1].get("content", "")[:200]
            yield f"🐞 **DEBUG: INPUT**\n`{last_msg_content}...`\n"

        # 2. Auth Flow
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

        # 3. Préparation
        tools = orch.convert_owui_tools(body.get("tools"))
        adapter = GeminiAdapter(self.base_url)
        context = orch.prepare_context(body.get("messages", [])) # Pas de chat_id nécessaire (Zero Disk)
        sys_instr = orch.get_system_instruction()

        # 4. Construction requête
        req = adapter.build(
            pid,
            context,
            sys_instr,
            self.valves.TEMPERATURE,
            self.valves.MAX_TOKENS,
            self.valves.THINKING_LEVEL,
            self.valves.MODEL_SELECTION,
            body.get("chat_id"), 
            tools,
        )
        req["headers"]["Authorization"] = f"Bearer {creds.token}"

        if self.valves.DEBUG_MODE:
            yield f"🐞 **API REQ**\nBody snippet: `{json.dumps(req['json'])[:500]}...`\n"

        # 5. Exécution
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