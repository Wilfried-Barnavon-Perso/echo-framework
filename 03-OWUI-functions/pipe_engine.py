"""
title: Gemini Pro Unified System (Platinum Agentic V132.00 - Master Documented)
author: ECHO Architecture
version: 132.00
description: Version de référence. Architecture Hybride (Sidecar Disk + In-Band + Magic Key). Intègre la gestion avancée des métadonnées et une configuration centralisée.
"""

# ==============================================================================
# SECTION 0 : IMPORTATIONS & CONSTANTES GLOBALES
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

# --- CONSTANTES DE CONFIGURATION GOOGLE ---
# Ces identifiants correspondent au client public "Google Cloud SDK".
# Ils permettent d'utiliser le flow "Installed App" pour obtenir des tokens légitimes
# donnant accès à l'API interne Cloud Code (optimisée pour le contexte long).
GOOGLE_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REDIRECT_URI = "https://codeassist.google.com/authcode"
GOOGLE_API_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# --- CONSTANTES MAGIQUES (PROTOCOLE GEMINI 3) ---
# "Magic Key" : Valeur spéciale reconnue par l'API Google pour bypasser la validation
# stricte de la signature de pensée lors d'une perte de contexte (ex: New Chat, Redémarrage).
# Source : Documentation Gemini API > Function Calling > FAQs.
MAGIC_KEY_SKIP_VALIDATION = "skip_thought_signature_validator"

# ==============================================================================
# SECTION 1 : DÉPENDANCES OPTIONNELLES (GRACEFUL DEGRADATION)
# ==============================================================================
# Permet au Pipe de charger même si les libs Google manquent (évite le crash de OWUI).
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
# SECTION 2 : CLIENT CONFIG
# ==============================================================================
# Configuration structurée pour la librairie google-auth
OFFICIAL_CLIENT_CONFIG = {
    "installed": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": GOOGLE_AUTH_URI,
        "token_uri": GOOGLE_TOKEN_URI,
        "redirect_uris": [GOOGLE_REDIRECT_URI],
    }
}

# ==============================================================================
# SECTION 3 : SERVICE D'AUTHENTIFICATION (IDENTITY PROVIDER)
# ==============================================================================
class AuthService:
    """
    Gère le cycle de vie de l'authentification OAuth2 avec Google.
    
    ALGORITHME :
    1. Vérifie la présence d'un token sur le disque.
    2. Si présent mais expiré, tente un rafraîchissement automatique.
    3. Si absent, génère une URL d'authentification PKCE pour l'utilisateur.
    4. Échange le code fourni par l'utilisateur contre une paire de tokens (Access + Refresh).
    
    POURQUOI :
    L'API Cloud Code nécessite un token utilisateur réel, pas une simple clé API.
    Cela permet d'accéder aux modèles "Preview" et aux quotas élevés.
    """
    def __init__(self, data_dir: str):
        self.token_path = f"{data_dir}/gemini_official_token.json"
        self.pkce_path = f"{data_dir}/gemini_pkce_verifier.txt"
        self.internal_project_cache = f"{data_dir}/gemini_internal_project.txt"
        self.base_url = GOOGLE_API_BASE_URL

    def _generate_pkce(self):
        """Génère le couple (Verifier, Challenge) pour sécuriser l'échange de code (RFC 7636)."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        import base64
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        """Construit l'URL que l'utilisateur doit visiter pour autoriser l'application."""
        if not HAS_GOOGLE_LIBS:
            return "❌ **Erreur** : Librairies `google-auth` manquantes."

        verifier, challenge = self._generate_pkce()
        try:
            with open(self.pkce_path, "w") as f: f.write(verifier)
        except Exception as e: return f"❌ Erreur IO: {str(e)}"

        flow = Flow.from_client_config(
            OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False
        )
        flow.redirect_uri = GOOGLE_REDIRECT_URI
        url, _ = flow.authorization_url(prompt="consent", access_type="offline", code_challenge=challenge, code_challenge_method="S256")

        return (
            f"### 🔐 Authentification Requise\n\n"
            f"1. **[Cliquez ici]({url})**\n"
            f"2. Connectez-vous.\n"
            f"3. Copiez le code `4/...`.\n"
            f"4. **Collez-le ici**."
        )

    def exchange_code(self, code: str) -> Tuple[bool, str]:
        """Finalise le handshake OAuth2 : Code + Verifier -> Token."""
        if not HAS_GOOGLE_LIBS: return False, "Libs manquantes."
        
        # Fallback de robustesse : si le fichier PKCE est perdu (restart container),
        # on vérifie si un token valide existe déjà en cache.
        if not os.path.exists(self.pkce_path):
             for _ in range(3):
                if self.get_valid_credentials(): return True, "Succès (Récupéré via cache)."
                time.sleep(0.5)
             return False, "Session expirée (PKCE introuvable)."

        try:
            with open(self.pkce_path, "r") as f: verifier = f.read().strip()
            flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=GOOGLE_SCOPES, autogenerate_code_verifier=False)
            flow.redirect_uri = GOOGLE_REDIRECT_URI
            flow.fetch_token(code=code.strip(), code_verifier=verifier)
            
            # Sauvegarde persistante
            with open(self.token_path, "w") as f: f.write(flow.credentials.to_json())
            
            # Nettoyage
            if os.path.exists(self.pkce_path): os.remove(self.pkce_path)
            return True, "Succès."
        except Exception as e:
            return False, str(e)

    def get_valid_credentials(self):
        """Retourne des credentials utilisables, rafraîchis si nécessaire."""
        creds = None
        if os.path.exists(self.token_path):
            try: creds = Credentials.from_authorized_user_file(self.token_path, GOOGLE_SCOPES)
            except: pass
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                with open(self.token_path, "w") as f: f.write(creds.to_json())
            except: return None
        return creds if (creds and creds.valid) else None

    def get_project_id(self, creds, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        """Récupère l'ID du projet Google Cloud par défaut de l'utilisateur."""
        if os.path.exists(self.internal_project_cache) and not debug_mode:
            with open(self.internal_project_cache, "r") as f: return f.read().strip(), "Cache."
        
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
        payload = {"metadata": {"ideType": "IDE_UNSPECIFIED", "pluginType": "GEMINI"}}
        
        try:
            resp = httpx.post(f"{self.base_url}:loadCodeAssist", headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("cloudaicompanionProject")
                pid = raw.get("id") if isinstance(raw, dict) else raw
                if pid:
                    pid = pid.replace("projects/", "")
                    with open(self.internal_project_cache, "w") as f: f.write(pid)
                    return pid, "API OK."
        except Exception as e: return None, str(e)
        return None, "Fail."

    def reset_storage(self):
        """Purge tous les tokens et caches."""
        for p in [self.token_path, self.pkce_path, self.internal_project_cache]:
            if os.path.exists(p): os.remove(p)

# ==============================================================================
# SECTION 4 : SIGNATURE MANAGER (MÉMOIRE SIDECAR)
# ==============================================================================
class SignatureManager:
    """
    Gère la persistance des Signatures de Pensée (Thought Signatures) sur le disque.
    
    POURQUOI :
    Gemini 3 est "Stateful" (il garde un état cognitif interne). Pour simuler une continuité
    dans une API Stateless, nous devons sauvegarder cet état crypté (la signature) et le
    renvoyer à chaque tour. Le stockage disque est plus fiable que le stockage dans l'historique texte.
    
    EMPLACEMENT : /app/backend/data/signatures/{chat_id}.txt
    """
    def __init__(self, data_dir: str):
        self.sig_dir = os.path.join(data_dir, "signatures")
        os.makedirs(self.sig_dir, exist_ok=True)

    def save_signature(self, chat_id: str, signature: str):
        """Sauvegarde atomique de la signature pour ce chat_id."""
        if not chat_id or not signature: return
        try:
            path = os.path.join(self.sig_dir, f"{chat_id}.txt")
            with open(path, "w") as f: f.write(signature)
        except Exception as e:
            # En prod, on silence les erreurs d'IO non critiques pour ne pas polluer le chat
            pass

    def get_signature(self, chat_id: str) -> Optional[str]:
        """Récupère la dernière signature connue."""
        if not chat_id: return None
        try:
            path = os.path.join(self.sig_dir, f"{chat_id}.txt")
            if os.path.exists(path):
                # On met à jour le mtime pour que l'Admin Manager (script de nettoyage)
                # sache que ce fichier est actif et ne le supprime pas.
                os.utime(path, None)
                with open(path, "r") as f: return f.read().strip()
        except: pass
        return None

# ==============================================================================
# SECTION 5 : ORCHESTRATEUR (LOGIQUE CÉRÉBRALE DU PIPE)
# ==============================================================================
class Orchestrator:
    """
    Prépare et assainit le contexte de conversation avant l'envoi à l'API.
    C'est ici que la stratégie "Hybride" (Disque + In-Band + Magic Key) est implémentée.
    """
    def __init__(self, valves, data_dir):
        self.valves = valves
        self.location_cache_file = "/app/backend/data/gemini_geo_cache_v2.json"
        self.tool_map = {}
        self.sig_manager = SignatureManager(data_dir)

    def check_for_auth_code(self, messages: List[Dict]) -> Optional[str]:
        """Détecte si l'utilisateur a collé un code d'authentification Google (4/...)."""
        if not messages: return None
        last_msg = messages[-1].get("content", "").strip()
        match = re.search(r"(4/[a-zA-Z0-9_-]+)", last_msg)
        if match and len(match.group(1)) > 30: return match.group(1)
        return None

    def _get_geo_info(self) -> Tuple[str, str]:
        """Récupère la localisation approximative du serveur pour le contexte."""
        loc, tz = "Paris, France", "Europe/Paris"
        if getattr(self.valves, "OVERRIDE_LOCATION", ""): return self.valves.OVERRIDE_LOCATION, tz
        if getattr(self.valves, "ENABLE_AUTO_LOCATION", True) and os.path.exists(self.location_cache_file):
            try:
                if time.time() - os.path.getmtime(self.location_cache_file) < 86400:
                    with open(self.location_cache_file, "r") as f:
                        c = json.load(f)
                        return c.get("location", loc), c.get("timezone", tz)
            except: pass
        return loc, tz

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """Convertit les outils du format Open WebUI (OpenAI-like) vers le format Google."""
        if not tools: return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                funcs.append({
                    "name": f.get("name"),
                    "description": f.get("description", ""),
                    "parameters": f.get("parameters", {"type": "object", "properties": {}})
                })
        return [{"functionDeclarations": funcs}] if funcs else None

    def get_system_instruction(self) -> Dict:
        """Construit le prompt système enrichi (Date, Heure, Lieu)."""
        sys_prompt_text = self.valves.SYSTEM_PROMPT
        if getattr(self.valves, "ENABLE_DATE_TIME", True):
            loc, tz = self._get_geo_info()
            try: now = datetime.now(ZoneInfo(tz)) if HAS_ZONEINFO else datetime.now()
            except: now = datetime.now()
            sys_prompt_text += f"\n\n[CONTEXT]\nDate: {now.strftime('%A %d %B %Y')}\nTime: {now.strftime('%H:%M')}\nLocation: {loc}\n"
        return {"parts": [{"text": sys_prompt_text}]}

    def prepare_context(self, messages: List[Dict], chat_id: str) -> List[Dict]:
        """
        ALGORITHME DE RECONSTRUCTION DU CONTEXTE (CRITIQUE)
        
        Objectif :
        Transformer l'historique brut d'Open WebUI en une séquence de messages valide pour Gemini 3,
        en gérant la mémoire (Signatures) et les contraintes structurelles (User/Model alternation).
        
        Étapes :
        1. Mapping : Indexer les IDs des appels d'outils pour les relier à leurs réponses.
        2. Itération : Parcourir les messages un par un.
        3. Nettoyage : Retirer les balises <think> (pensées passées) pour éviter les hallucinations.
        4. Injection Hybride :
           - Chercher une signature "In-Band" cachée dans les arguments des outils (priorité haute).
           - Chercher une signature "In-Disk" (priorité moyenne).
           - Appliquer la "Magic Key" si aucune signature n'est trouvée et qu'un outil est appelé (Safety Net).
        5. Correction : Insérer des messages modèles vides ("...") si deux messages utilisateurs se suivent.
        """
        contents = []
        
        # 1. Mapping Outils (Tool Call ID -> Tool Name)
        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if "id" in tc and "function" in tc:
                        self.tool_map[tc["id"]] = tc["function"].get("name")

        i = 0
        while i < len(messages):
            m = messages[i]
            role = m["role"]
            content = ""
            raw_content = m.get("content", "")
            
            # Normalisation du contenu
            if isinstance(raw_content, list):
                for part in raw_content:
                    if isinstance(part, dict) and "text" in part: content += part["text"]
            else:
                content = str(raw_content) if raw_content else ""

            # Filtres (Système & Codes Auth)
            if role == "system": i+=1; continue
            if role == "user" and ("4/" in str(content) and len(str(content)) > 30):
                if re.search(r"(4/[a-zA-Z0-9_-]+)", str(content)): i += 1; continue

            # --- CAS 1 : TOOL RESPONSE (Résultat d'un outil) ---
            # Dans Gemini, c'est un "User Part". On regroupe les réponses consécutives.
            if role == "tool":
                parts = []
                while i < len(messages) and messages[i]["role"] == "tool":
                    tm = messages[i]
                    tool_name = self.tool_map.get(tm.get("tool_call_id"), "unknown_tool")
                    try: val = json.loads(tm.get("content", "{}"))
                    except: val = {"result": str(tm.get("content", ""))}
                    parts.append({"functionResponse": {"name": tool_name, "response": val}})
                    i += 1
                
                # Fusion avec le dernier message User (requis par l'API)
                if contents and contents[-1]["role"] == "user":
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": "user", "parts": parts})
                continue

            # --- CAS 2 : MODEL (Réponse de l'assistant) ---
            elif role in ["assistant", "model"]:
                parts = []
                # Nettoyage visuel : On ne renvoie pas les anciennes pensées au modèle
                text_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                # On retire aussi les anciens hacks Markdown s'il en reste
                text_content = re.sub(r'\[\s*\]\(context://thought_signature/[^\)]+\)', '', text_content).strip()

                if text_content: parts.append({"text": text_content})

                # Extraction de la signature In-Band (depuis les args de l'outil)
                found_in_band_sig = None
                tool_calls_in_msg = False
                
                if m.get("tool_calls"):
                    tool_calls_in_msg = True
                    for tc in m["tool_calls"]:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            # On récupère la signature cachée et on nettoie l'argument
                            if "_thought_signature" in args:
                                found_in_band_sig = args.pop("_thought_signature")
                            
                            parts.append({"functionCall": {"name": tc["function"]["name"], "args": args}})
                        except: pass
                
                if not parts: parts.append({"text": " "})

                # --- STRATÉGIE D'INJECTION DE SIGNATURE ---
                # Règle : La signature est obligatoire sur le premier FunctionCall d'un tour.
                
                is_last_model_msg = True
                for j in range(i + 1, len(messages)):
                    if messages[j]["role"] in ["assistant", "model"]:
                        is_last_model_msg = False
                        break
                
                if tool_calls_in_msg or is_last_model_msg:
                    # 1. On tente la signature In-Band (la plus précise pour ce message)
                    sig_to_use = found_in_band_sig
                    
                    # 2. Sinon, on tente la signature Disque (Sidecar)
                    if not sig_to_use and chat_id:
                        sig_to_use = self.sig_manager.get_signature(chat_id)
                    
                    # 3. MAGIC KEY (Safety Net)
                    # Si on n'a RIEN et qu'on DOIT avoir une signature (FunctionCall),
                    # on utilise le joker pour éviter l'erreur 400.
                    if not sig_to_use and tool_calls_in_msg:
                        sig_to_use = MAGIC_KEY_SKIP_VALIDATION

                    if sig_to_use and parts and "thoughtSignature" not in parts[0]:
                        parts[0]["thoughtSignature"] = sig_to_use

                contents.append({"role": "model", "parts": parts})

            # --- CAS 3 : USER (Message standard) ---
            else:
                if content:
                    # FIX CONCATÉNATION : Si le message précédent était DÉJÀ un User,
                    # on insère un message Model vide pour respecter l'alternance.
                    if contents and contents[-1]["role"] == "user":
                         contents.append({"role": "model", "parts": [{"text": "..."}]})
                    
                    contents.append({"role": "user", "parts": [{"text": str(content)}]})
            
            i += 1

        return contents

# ==============================================================================
# SECTION 6 : ADAPTATEUR API (GEMINI REST BUILDER)
# ==============================================================================
class GeminiAdapter:
    def __init__(self, base_url):
        self.base_url = base_url

    def build(self, project_id, contents, system_instr, temp, max_tok, think_level, model_id, tools=None):
        """Construit le payload JSON final pour l'API REST de Google."""
        gen_config = {"temperature": temp, "maxOutputTokens": max_tok}
        
        # Configuration spécifique Gemini 3 (Thinking Mode)
        if "gemini-3" in model_id:
            t_level = think_level.lower()
            if t_level == "dynamic": t_level = "high"
            gen_config["thinkingConfig"] = {"includeThoughts": True, "thinkingLevel": t_level}

        # Stateless pur : UUID unique à chaque requête.
        # On force Google à traiter chaque requête comme nouvelle, car on gère
        # nous-mêmes l'historique complet via 'contents'.
        final_session_id = str(uuid.uuid4())

        payload = {
            "model": model_id,
            "project": project_id,
            "user_prompt_id": hex(random.getrandbits(64))[2:],
            "request": {
                "systemInstruction": system_instr,
                "contents": contents,
                "generationConfig": gen_config,
                "session_id": final_session_id,
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
# SECTION 7 : PROCESSEUR DE FLUX (STREAMING & CAPTURE)
# ==============================================================================
class StreamProcessor:
    """
    Traite le flux SSE (Server-Sent Events) entrant.
    
    ALGORITHME DE FLUX :
    1. Buffering : Accumule les octets jusqu'à obtenir une ligne JSON complète (évite les erreurs de parsing).
    2. Parsing : Décode le JSON de Google.
    3. Capture : Intercepte le champ 'thoughtSignature' et le sauvegarde (Disque).
    4. Injection : Si la réponse contient un appel d'outil, injecte la signature dans les arguments
       de l'outil (In-Band) pour assurer sa survie au prochain tour.
    5. Formatting : Formate le texte et les pensées pour l'affichage Open WebUI.
    """
    def __init__(self, debug=False, chat_id=None, sig_manager=None):
        self.debug = debug
        self.chat_id = chat_id
        self.sig_manager = sig_manager
        self.current_sig = None

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False
        tool_index = 0
        buffer = ""

        # Lecture asynchrone du flux binaire
        async for chunk in response.aiter_bytes():
            try:
                buffer += chunk.decode("utf-8", errors="ignore")
            except:
                continue

            # Traitement ligne par ligne avec gestion du buffer (critique pour la stabilité)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line.startswith("data:"): continue
                try:
                    data = json.loads(line[6:]) # Skip "data: " prefix
                    if self.debug: yield f"\n`[SSE] {json.dumps(data, ensure_ascii=False)}`\n"

                    cand = data.get("response", {}).get("candidates", [])
                    if cand and "content" in cand[0]:
                        parts = cand[0]["content"].get("parts", [])
                        for part in parts:
                            txt = part.get("text", "")
                            is_think = part.get("thought", False)
                            func_call = part.get("functionCall")

                            # --- 1. CAPTURE DE LA SIGNATURE ---
                            if "thoughtSignature" in part:
                                self.current_sig = part["thoughtSignature"]
                                if self.chat_id and self.sig_manager:
                                    self.sig_manager.save_signature(self.chat_id, self.current_sig)

                            # --- 2. TRAITEMENT PENSÉES ---
                            if is_think:
                                if not in_think: yield "<think>\n"; in_think = True
                                yield txt
                            
                            # --- 3. TRAITEMENT OUTILS (INJECTION IN-BAND) ---
                            elif func_call:
                                if in_think: yield "\n</think>\n"; in_think = False
                                
                                args = func_call.get("args", {})
                                # INJECTION : C'est ici qu'on "cache" la signature pour le futur
                                if self.current_sig:
                                    args["_thought_signature"] = self.current_sig
                                
                                yield {
                                    "choices": [{
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [{
                                                "index": tool_index,
                                                "id": f"call_{secrets.token_hex(8)}",
                                                "type": "function",
                                                "function": {
                                                    "name": func_call["name"],
                                                    "arguments": json.dumps(args)
                                                }
                                            }]
                                        },
                                        "finish_reason": "tool_calls"
                                    }]
                                }
                                tool_index += 1

                            # --- 4. TRAITEMENT TEXTE STANDARD ---
                            else:
                                if in_think: yield "\n</think>\n"; in_think = False
                                if txt: yield txt

                except: pass

        if in_think: yield "\n</think>\n"

# ==============================================================================
# SECTION 8 : LE PIPE (POINT D'ENTRÉE & METADATA)
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
        self.base_url = GOOGLE_API_BASE_URL

    # --- POINT D'ENTRÉE AVEC MÉTHODE ÉTENDUE (__metadata__) ---
    # L'ajout de __metadata__ permet de recevoir le vrai chat_id même s'il n'est pas dans le body.
    async def pipe(self, body: dict, __user__: dict = None, __metadata__: dict = None, __request__: Optional[any] = None) -> AsyncGenerator[Union[str, Dict], None]:
        
        # 1. RÉCUPÉRATION INTELLIGENTE DU CHAT_ID
        chat_id = body.get("chat_id")
        
        # Stratégie de Fallback pour l'ID (Priorité : Body > Meta ChatID > Meta SessionID)
        if not chat_id and __metadata__:
            chat_id = __metadata__.get("chat_id")
        if not chat_id and __metadata__:
            chat_id = __metadata__.get("session_id")

        orch = Orchestrator(self.valves, self.data_dir)
        proc = StreamProcessor(self.valves.DEBUG_MODE, chat_id, orch.sig_manager)

        if self.valves.DEBUG_MODE:
            yield f"🐞 **DEBUG**\nChatID: `{chat_id}`\nMeta: `{str(__metadata__)}`\n"

        # 2. FLUX D'AUTHENTIFICATION
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

        # 3. CONSTRUCTION ET EXÉCUTION
        tools = orch.convert_owui_tools(body.get("tools"))
        adapter = GeminiAdapter(self.base_url)
        # On passe le chat_id récupéré (potentiellement via metadata)
        context = orch.prepare_context(body.get("messages", []), chat_id)
        sys_instr = orch.get_system_instruction()

        req = adapter.build(
            pid,
            context,
            sys_instr,
            self.valves.TEMPERATURE,
            self.valves.MAX_TOKENS,
            self.valves.THINKING_LEVEL,
            self.valves.MODEL_SELECTION,
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