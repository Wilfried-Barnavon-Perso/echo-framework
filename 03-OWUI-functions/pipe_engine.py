"""
title: Gemini Pro Unified System (Platinum Agentic V128.05 - CAS Memory)
author: ECHO Architecture
version: 128.05
description: Architecture "Content-Addressable Storage" (CAS).
- Rôle : Préserver la mémoire cognitive (Signatures) générée par le modèle.
- Mécanisme : Sauvegarde l'association {Hash du Contenu -> Signature} dans un fichier JSON.
- Restauration : Au tour suivant, utilise le contenu de l'historique pour retrouver et réinjecter la signature correspondante.
- Conformité : Respecte le cycle de vie Gemini 3 (Thinking -> Signature -> Tool -> Signature Recall).
"""

# ==============================================================================
# SECTION 0 : IMPORTATIONS ET UTILITAIRES DE BASE
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
# SECTION 1 : GESTION DES DÉPENDANCES
# ==============================================================================
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
# SECTION 2 : CONFIGURATION OAUTH2
# ==============================================================================
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
# SECTION 3 : AUTHENTIFICATION
# ==============================================================================
class AuthService:
    def __init__(self, data_dir: str):
        self.token_path = f"{data_dir}/gemini_official_token.json"
        self.pkce_path = f"{data_dir}/gemini_pkce_verifier.txt"
        self.internal_project_cache = f"{data_dir}/gemini_internal_project.txt"
        self.base_url = "https://cloudcode-pa.googleapis.com/v1internal"

    def _generate_pkce(self):
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        import base64
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(self) -> str:
        if not HAS_GOOGLE_LIBS:
            return "❌ **Erreur** : Librairies `google-auth` manquantes."

        should_generate_new = True
        if os.path.exists(self.pkce_path):
            try:
                if time.time() - os.path.getmtime(self.pkce_path) < 300:
                    with open(self.pkce_path, "r") as f:
                        if len(f.read().strip()) > 10:
                            should_generate_new = False
            except: pass

        if should_generate_new:
            verifier, challenge = self._generate_pkce()
            try:
                with open(self.pkce_path, "w") as f: f.write(verifier)
            except Exception as e: return f"❌ Erreur IO: {str(e)}"
        else:
            verifier, challenge = self._generate_pkce()
            with open(self.pkce_path, "w") as f: f.write(verifier)

        flow = Flow.from_client_config(
            OFFICIAL_CLIENT_CONFIG, scopes=SCOPES, autogenerate_code_verifier=False
        )
        flow.redirect_uri = "https://codeassist.google.com/authcode"
        url, _ = flow.authorization_url(prompt="consent", access_type="offline", code_challenge=challenge, code_challenge_method="S256")

        return (
            f"### 🔐 Authentification Requise\n\n"
            f"1. **[Cliquez ici]({url})**\n"
            f"2. Connectez-vous.\n"
            f"3. Copiez le code `4/...`.\n"
            f"4. **Collez-le ici**."
        )

    def exchange_code(self, code: str) -> Tuple[bool, str]:
        if not HAS_GOOGLE_LIBS: return False, "Libs manquantes."
        if not os.path.exists(self.pkce_path): return False, "Session expirée."

        try:
            with open(self.pkce_path, "r") as f: verifier = f.read().strip()
            flow = Flow.from_client_config(OFFICIAL_CLIENT_CONFIG, scopes=SCOPES, autogenerate_code_verifier=False)
            flow.redirect_uri = "https://codeassist.google.com/authcode"
            flow.fetch_token(code=code.strip(), code_verifier=verifier)
            with open(self.token_path, "w") as f: f.write(flow.credentials.to_json())
            if os.path.exists(self.pkce_path): os.remove(self.pkce_path)
            return True, "Succès."
        except Exception as e: return False, str(e)

    def get_valid_credentials(self):
        creds = None
        if os.path.exists(self.token_path):
            try: creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except: pass
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleAuthRequest())
                with open(self.token_path, "w") as f: f.write(creds.to_json())
            except: return None
        return creds if (creds and creds.valid) else None

    def get_project_id(self, creds, debug_mode: bool = False) -> Tuple[Optional[str], str]:
        if os.path.exists(self.internal_project_cache) and not debug_mode:
            with open(self.internal_project_cache, "r") as f: return f.read().strip(), "Cache."
        
        headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
        payload = {"metadata": {"ideType": "IDE_UNSPECIFIED", "pluginType": "GEMINI"}}
        try:
            url = f"{self.base_url}:loadCodeAssist"
            resp = httpx.post(url, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                pid = resp.json().get("cloudaicompanionProject", {}).get("id")
                if pid:
                    pid = pid.replace("projects/", "")
                    with open(self.internal_project_cache, "w") as f: f.write(pid)
                    return pid, ""
        except: pass
        return None, "Erreur API."

    def reset_storage(self):
        for p in [self.token_path, self.pkce_path, self.internal_project_cache]:
            if os.path.exists(p): os.remove(p)

# ==============================================================================
# SECTION 4 : SIGNATURE MANAGER (CAS - Stockage Associatif)
# ==============================================================================
class SignatureManager:
    """
    Gestionnaire de Mémoire "Content Addressable Storage".
    
    Problème à résoudre :
    - Gemini génère une signature liée à une réponse spécifique (Texte ou Outil).
    - Open WebUI stocke la réponse (le contenu) mais perd la signature (métadonnée).
    
    Solution :
    - On crée un dictionnaire persistant : { HASH(Contenu) : Signature }.
    - Quand Open WebUI nous renvoie le contenu au tour suivant, on recalcule son HASH.
    - On retrouve ainsi la Signature originale que le modèle avait générée.
    """
    def __init__(self, data_dir: str):
        self.sig_dir = os.path.join(data_dir, "signatures")
        os.makedirs(self.sig_dir, exist_ok=True)

    def _compute_hash(self, content: Union[str, Dict]) -> str:
        """
        Calcule une empreinte unique (SHA256) pour identifier un contenu.
        - Pour les outils (Dict) : Tri des clés pour garantir la stabilité du hash.
        - Pour le texte (Str) : Nettoyage basique (strip) pour éviter les écarts d'espaces.
        """
        if isinstance(content, dict):
            encoded = json.dumps(content, sort_keys=True).encode('utf-8')
        else:
            encoded = str(content).strip().encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def load_store(self, chat_id: str) -> Dict[str, str]:
        """Charge le fichier JSON contenant toutes les signatures de cette conversation."""
        if not chat_id: return {}
        path = os.path.join(self.sig_dir, f"{chat_id}.json")
        if os.path.exists(path):
            try:
                # Update access time pour le LRU cleaner (Admin Manager)
                os.utime(path, None)
                with open(path, "r") as f:
                    return json.load(f)
            except: pass
        return {}

    def save_signature(self, chat_id: str, content: Union[str, Dict], signature: str):
        """
        Archive une nouvelle signature générée par le modèle.
        Lie la signature à l'empreinte du contenu qui l'a provoquée.
        """
        if not chat_id or not content or not signature: return
        
        content_hash = self._compute_hash(content)
        store = self.load_store(chat_id)
        
        # Optimisation : Si cette pensée est déjà connue, on ne réécrit pas le disque
        if store.get(content_hash) == signature:
            return

        store[content_hash] = signature
        
        try:
            path = os.path.join(self.sig_dir, f"{chat_id}.json")
            with open(path, "w") as f:
                json.dump(store, f)
        except: pass

    def get_signature_for_content(self, chat_id: str, content: Union[str, Dict]) -> Optional[str]:
        """
        Retrouve la signature associée à un contenu donné.
        Utilisé lors de la reconstruction de l'historique pour l'API.
        """
        store = self.load_store(chat_id)
        content_hash = self._compute_hash(content)
        return store.get(content_hash)

# ==============================================================================
# SECTION 5 : ORCHESTRATEUR (RECONSTRUCTION ET REHYDRATATION)
# ==============================================================================
class Orchestrator:
    def __init__(self, valves):
        self.valves = valves
        self.location_cache_file = "/app/backend/data/gemini_geo_cache_v2.json"
        self.tool_map = {}
        self.sig_manager = SignatureManager("/app/backend/data")

    def check_for_auth_code(self, messages: List[Dict]) -> Optional[str]:
        if not messages: return None
        last_msg = messages[-1].get("content", "").strip()
        match = re.search(r"(4/[a-zA-Z0-9_-]+)", last_msg)
        if match and len(match.group(1)) > 30: return match.group(1)
        return None

    def _get_geo_info(self) -> Tuple[str, str]:
        loc, tz = "Paris, France", "Europe/Paris"
        if getattr(self.valves, "OVERRIDE_LOCATION", ""): return self.valves.OVERRIDE_LOCATION, tz
        if getattr(self.valves, "ENABLE_AUTO_LOCATION", True):
             if os.path.exists(self.location_cache_file):
                try:
                    if time.time() - os.path.getmtime(self.location_cache_file) < 86400:
                        with open(self.location_cache_file, "r") as f:
                            c = json.load(f)
                            return c.get("location", loc), c.get("timezone", tz)
                except: pass
        return loc, tz

    def _get_current_time(self, timezone_id: str) -> Tuple[str, str]:
        try:
            if HAS_ZONEINFO: now = datetime.now(ZoneInfo(timezone_id))
            else: now = datetime.now()
        except: now = datetime.now()
        return now.strftime("%A %d %B %Y"), now.strftime("%H:%M")

    def convert_owui_tools(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        if not tools: return None
        funcs = []
        for t in tools:
            if t.get("type") == "function":
                f = t.get("function", {})
                params = f.get("parameters", {"type": "object", "properties": {}})
                funcs.append({"name": f.get("name"), "description": f.get("description", ""), "parameters": params})
        return [{"functionDeclarations": funcs}] if funcs else None

    def get_system_instruction(self) -> Dict:
        sys_txt = self.valves.SYSTEM_PROMPT
        if getattr(self.valves, "ENABLE_DATE_TIME", True):
            loc, tz = self._get_geo_info()
            d, t = self._get_current_time(tz)
            sys_txt += f"\n\n[CONTEXT]\nDate: {d}\nTime: {t}\nLocation: {loc}\n"
        return {"parts": [{"text": sys_txt}]}

    def prepare_context(self, messages: List[Dict], chat_id: str = None) -> List[Dict]:
        """
        Reconstruction de l'historique avec réhydratation des signatures (Logique CAS).
        
        Processus pour chaque message :
        1. Identification du contenu (Texte ou Arguments d'outil).
        2. Calcul du Hash de ce contenu.
        3. Interrogation du SignatureManager pour retrouver la signature archivée.
        4. Injection de la signature dans l'objet API 'thoughtSignature'.
        
        Cette méthode garantit que l'API reçoit tout le contexte de pensée nécessaire,
        sans avoir besoin de stocker la signature dans Open WebUI.
        """
        contents = []
        
        # Indexation des Outils
        for m in messages:
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if "id" in tc and "function" in tc:
                        self.tool_map[tc["id"]] = tc["function"].get("name")

        i = 0
        while i < len(messages):
            m = messages[i]
            role = m["role"]
            raw_content = m.get("content", "")
            
            # Normalisation du contenu texte
            if isinstance(raw_content, list):
                content = ""
                for part in raw_content:
                    if isinstance(part, dict) and "text" in part: content += part["text"]
            else: content = str(raw_content) if raw_content else ""
            
            # Filtres Système & Auth (User Only)
            if role == "system": i += 1; continue
            if role == "user" and ("4/" in str(content) and len(str(content)) > 30):
                if re.search(r"(4/[a-zA-Z0-9_-]+)", str(content)): i += 1; continue

            # CAS 1 : TOOL RESPONSE (Résultat d'exécution)
            if role == "tool":
                parts = []
                while i < len(messages) and messages[i]["role"] == "tool":
                    tm = messages[i]
                    tool_name = self.tool_map.get(tm.get("tool_call_id"), "unknown")
                    try: val = json.loads(tm.get("content", "{}"))
                    except: val = {"result": str(tm.get("content", ""))}
                    parts.append({"functionResponse": {"name": tool_name, "response": val}})
                    i += 1
                if contents and contents[-1]["role"] == "user": contents[-1]["parts"].extend(parts)
                else: contents.append({"role": "user", "parts": parts})
                continue

            # CAS 2 : MODEL (Assistant)
            elif role in ["assistant", "model"]:
                parts = []
                
                # A. Nettoyage du Texte (Suppression des pensées affichées)
                text_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                # Nettoyage des anciens liens cachés (Legacy)
                text_content = re.sub(r'\[\s*\]\(context://thought_signature/[^\)]+\)', '', text_content).strip()

                # B. Réhydratation Signature (TEXTE)
                # On utilise le texte nettoyé comme clé pour retrouver la signature
                thought_sig = None
                if chat_id and text_content:
                    thought_sig = self.sig_manager.get_signature_for_content(chat_id, text_content)

                if text_content:
                    parts.append({"text": text_content})

                # C. Gestion Outils & Réhydratation (OUTILS)
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        try:
                            raw_args = tc["function"]["arguments"]
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                            
                            # Si on n'a pas trouvé de signature via le texte, on essaie via les args de l'outil
                            if not thought_sig and chat_id:
                                tool_content_for_hash = {"name": tc["function"]["name"], "args": args}
                                thought_sig = self.sig_manager.get_signature_for_content(chat_id, tool_content_for_hash)

                            parts.append({"functionCall": {"name": tc["function"]["name"], "args": args}})
                        except: pass
                
                # Safety Net
                if not parts: parts.append({"text": " "})

                # D. Injection de la Signature Retrouvée
                if thought_sig and parts:
                    parts[0]["thoughtSignature"] = thought_sig

                if parts:
                    if contents and contents[-1]["role"] == "model": contents[-1]["parts"].extend(parts)
                    else: contents.append({"role": "model", "parts": parts})

            # CAS 3 : USER
            else:
                if content:
                    parts = [{"text": str(content)}]
                    if contents and contents[-1]["role"] == "user": contents[-1]["parts"].extend(parts)
                    else: contents.append({"role": "user", "parts": parts})
            
            i += 1

        final_contents = []
        for c in contents:
            if final_contents and final_contents[-1]["role"] == c["role"]:
                final_contents[-1]["parts"].extend(c["parts"])
            else: final_contents.append(c)

        return final_contents

# ==============================================================================
# SECTION 6 : ADAPTATEUR API
# ==============================================================================
class GeminiAdapter:
    def __init__(self, base_url):
        self.base_url = base_url

    def build(self, project_id, contents, system_instr, temp, max_tok, think_level, model_id, session_id_context, tools=None):
        gen_config = {"temperature": temp, "maxOutputTokens": max_tok}
        
        # Configuration spécifique Gemini 3
        if "gemini-3" in model_id:
            t_level = think_level.lower()
            if t_level == "dynamic": t_level = "high"
            gen_config["thinkingConfig"] = {"includeThoughts": True, "thinkingLevel": t_level}

        # Mode Stateless : UUID unique pour chaque appel API
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
# SECTION 7 : PROCESSEUR DE FLUX (CAPTURE & ARCHIVAGE)
# ==============================================================================
class StreamProcessor:
    def __init__(self, chat_id, sig_manager, debug=False):
        self.debug = debug
        self.chat_id = chat_id
        self.sig_manager = sig_manager

    async def process(self, response) -> AsyncGenerator[Union[str, Dict], None]:
        in_think = False
        current_tool_id = None
        tool_index = 0
        
        # Buffers pour reconstruire le contenu complet à hasher
        full_text_buffer = ""
        current_tool_data = None
        current_sig = None

        async for chunk in response.aiter_bytes():
            try: text_chunk = chunk.decode("utf-8", errors="ignore")
            except: continue

            for line in text_chunk.split("\n"):
                line = line.strip()
                if not line.startswith("data:"): continue
                try:
                    json_str = line[5:].strip()
                    if not json_str: continue
                    data = json.loads(json_str)
                    
                    if self.debug: yield f"\n`[SSE] {json.dumps(data, ensure_ascii=False)}`\n"

                    cand = data.get("response", {}).get("candidates", [])
                    if not cand: continue
                    
                    candidate = cand[0]
                    content = candidate.get("content", {})
                    parts = content.get("parts", [])

                    for part in parts:
                        # 1. Capture Signature (On la garde en mémoire jusqu'à la fin du flux)
                        # Le modèle génère la signature, nous la capturons ici.
                        if "thoughtSignature" in part:
                            current_sig = part["thoughtSignature"]

                        # 2. Gestion Pensées
                        is_thought = part.get("thought", False)
                        text_val = part.get("text", "")

                        if is_thought:
                            if not in_think: yield "<think>\n"; in_think = True
                            yield text_val; continue

                        if in_think and (text_val or part.get("functionCall")):
                            yield "\n</think>\n"; in_think = False

                        # 3. Gestion Outils
                        func_call = part.get("functionCall")
                        if func_call:
                            if not current_tool_id: current_tool_id = f"call_{secrets.token_hex(8)}"
                            args = func_call.get("args", {})
                            
                            # On mémorise l'outil pour pouvoir l'associer à la signature plus tard
                            current_tool_data = {"name": func_call["name"], "args": args}
                            
                            tool_payload = {
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "content": None,
                                        "tool_calls": [{
                                            "index": tool_index,
                                            "id": current_tool_id,
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
                            yield tool_payload
                            tool_index += 1
                            current_tool_id = None 

                        # 4. Texte Standard
                        elif text_val:
                            full_text_buffer += text_val # On accumule le texte pour le hashage
                            yield text_val

                except: pass

        if in_think: yield "\n</think>\n"

        # 5. ARCHIVAGE FINAL (MAPPING CONTENT -> SIGNATURE)
        # Une fois le stream fini, on a le contenu complet ET la signature générée par le modèle.
        # On sauvegarde l'association dans le fichier JSON pour le retrouver au prochain tour.
        if current_sig and self.chat_id:
            
            # Cas A : Signature associée à du Texte
            if full_text_buffer.strip():
                self.sig_manager.save_signature(self.chat_id, full_text_buffer.strip(), current_sig)
            
            # Cas B : Signature associée à un Outil
            if current_tool_data:
                self.sig_manager.save_signature(self.chat_id, current_tool_data, current_sig)

# ==============================================================================
# SECTION 8 : LE PIPE
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
            try: os.remove(self.auth.internal_project_cache)
            except: pass

    async def pipe(self, body: dict, __user__: dict = None, __request__: Optional[any] = None) -> AsyncGenerator[Union[str, Dict], None]:
        orch = Orchestrator(self.valves)
        chat_id = body.get("chat_id")
        proc = StreamProcessor(chat_id, orch.sig_manager, self.valves.DEBUG_MODE)

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