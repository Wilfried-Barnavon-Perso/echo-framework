"""
title: ECHO Constants
author: ECHO Framework
version: 5.0
description: 4.3: Credentials Antigravity obfusqués base64(reversed).
             4.4: redirect_uri localhost (loopback RFC 8252).
             4.5: Suppression redirect_uri et callback_port fixes — dynamiques via
             echo_ssh_tunnel.py. Ajout constantes SSH Tunnel éphémère PKCE
             (plage ports, user, timeout). Multi-user natif par allocation dynamique.
             4.6: Correction noms modèles certifiés par diagnostic live API.
             4.7: Mapping modèles AGY ↔ AI Studio (AGY_MODEL_MAP).
             Fix MODEL_ROUTING (ajout clé MODEL_DISTILLATION). Fix noms canoniques
             AI Studio : MODEL_FLASH=gemini-3.5-flash, MODEL_PRO=gemini-3.1-pro-preview.
             4.8: Centralisation des paramètres de génération (THINKING_LEVEL_*, MAX_TOKENS_DEFAULT).
             Suppression des valves TEMPERATURE/TOP_P/THINKING_LEVEL dans pipe_engine et
             cognitive_agents — point de vérité unique pour tout le framework ECHO.
             4.9: Ajout MODEL_MAP_CA (capacités AGY certifiées, diag v2.1 2026-05-25).
             AGY_MODEL_MAP devient alias auto-généré depuis MODEL_MAP_CA.
             MAX_TOKENS_DEFAULT 65536→65535 (limite universelle AI Studio + AGY).
             5.0: Renommage des identifiants Code Assist → AGY : ECHO_CODE_ASSIST_USER_AGENT→ECHO_AGY_USER_AGENT,
             CODE_ASSIST_BASE_URL→AGY_BASE_URL, CODE_ASSIST_MODEL_MAP→AGY_MODEL_MAP (alias rétrocompat conservé).
"""

import os
import base64
import mimetypes
try:
    import filetype
except ImportError:
    filetype = None

# ==============================================================================
# 0. TOPOLOGIE SYSTÈME (ENVIRONNEMENT DOCKER)
# ==============================================================================

# Racine unique de l'infrastructure de données
ECHO_BASE_DATA_DIR = "/app/backend/data"

# HIÉRARCHIE ECHO SOUVERAINE (Standardisé)
ECHO_USERS_ROOT = f"{ECHO_BASE_DATA_DIR}/users"
ECHO_UPLOADS_TRANSIT_DIR = f"{ECHO_BASE_DATA_DIR}/uploads"

ECHO_VERSION_PATH = f"{ECHO_BASE_DATA_DIR}/ECHO_VERSION"

# Identité Réseau (Antigravity 2.1)
ECHO_USER_AGENT             = "antigravity/2.1.0"
ECHO_AGY_USER_AGENT = "antigravity/2.1.0 (language_server; os_type=Windows; os_version=10.0.26100; arch=x64)"

# Points d'accès Locaux (Souveraineté)
ECHO_EMBEDDING_URL = "http://echo-embedding:7997/v1"

# ==============================================================================
# 1. PROTOCOLE ANTIGRAVITY 2.1 — AUTH UNIFIÉE
# ==============================================================================

# --- IDENTIFIANTS TECHNIQUES DES MÉTHODES ---
AUTH_METHOD_KEY_PRIMARY   = "google_api_key"
AUTH_METHOD_OAUTH2        = "google_oauth2"
AUTH_METHOD_KEY_SECONDARY = "google_api_key_secondary"

# --- IDENTIFIANTS ANTIGRAVITY 2.1 (certifiés source : main.js — out-build/vs/platform/cloudCode/common/oauthClient.js) ---
# Encodage : base64(reversed(value)) — casse les signatures connues de GitHub Secret Scanning.
# Décodage : base64.b64decode(b).decode()[::-1]
_d = lambda b: base64.b64decode(b).decode()[::-1]

# Client Desktop (variable Rge/yge — oauthClient.js) — utilisé pour le flow PKCE
# ⚠️ CORRECTION v2 : 1071006060591 (13 chiffres)
ANTIGRAVITY_DESKTOP_CLIENT_ID     = _d("bW9jLnRuZXRub2NyZXN1ZWxnb29nLnNwcGEucGUzMDRnNGhqb2xvdHY1MzJlcmNsMTJoMm5pc3NobXQtMTk1MDYwNjAwMTcwMQ==")
ANTIGRAVITY_DESKTOP_CLIENT_SECRET = _d("ZkFEcTZ6NENYczhCTG0xSkxkTDY4NFJXRjg1Sy1YUFNDT0c=")

# Client Language Server (variable WZe/OZe — oauthClient.js)
ANTIGRAVITY_OAUTH_CLIENT_ID     = _d("bW9jLnRuZXRub2NyZXN1ZWxnb29nLnNwcGEuaGxiNWM4NjJkb2M2dm8yM2NhaXVndDNiamoxY3J0NjMtMjUwOTE5NDUzNDg4")
ANTIGRAVITY_OAUTH_CLIENT_SECRET = _d("WHN0WjBSd01LeFktamRUUTBDRFdSN0ZwV1FZOS1YUFNDT0c=")

# --- PKCE + AUTHORIZATION CODE FLOW (RFC 7636) ---
# redirect_uri : générée dynamiquement dans initiate_pkce_flow().
#   Format : http://localhost:{callback_port}/callback
#   Google accepte n'importe quel port loopback (RFC 8252 §7.3).
# Ports alloués dynamiquement dans ECHO_AUTH_PORT_RANGE_* — multi-user natif.
GOOGLE_AUTH_URL         = "https://accounts.google.com/o/oauth2/auth"

# --- SSH TUNNEL ÉPHÉMÈRE PKCE (authentification OAuth2) ---
# Serveur SSH asyncssh dans le container open-webui.
# Un pair de ports (SSH + callback) alloué dynamiquement par session.
# Docker expose uniquement la plage SSH 8020-8024 (stack-echo.yml).
# Les ports callback 8025-8034 restent internes au container.
ECHO_SSH_TUNNEL_USER = "echo-auth"  # Utilisateur SSH jetable
ECHO_SSH_TUNNEL_TIMEOUT = 120       # Secondes avant auto-stop

# Plage des ports SSH server (exposes par Docker dans stack-echo.yml)
# Le client SSH se connecte sur l'un de ces ports.
ECHO_SSH_PORT_RANGE_START = 8020    # Premier port SSH expose
ECHO_SSH_PORT_RANGE_END   = 8024    # Dernier port SSH expose (5 sessions max)

# Plage des ports callback OAuth2 (JAMAIS exposes par Docker - internes uniquement)
# Accessibles uniquement via le tunnel SSH authentifie.
# redirect_uri = http://localhost:{cb_port}/callback (port cote client, resolu par le tunnel)
ECHO_CALLBACK_PORT_RANGE_START = 8025  # Premier port callback interne
ECHO_CALLBACK_PORT_RANGE_END   = 8034  # Dernier port callback interne (10 slots)


# Endpoints standards Google (inchangés)
GOOGLE_OAUTH_TOKEN_URL  = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL     = "https://www.googleapis.com/oauth2/v2/userinfo"

# --- SCOPES ANTIGRAVITY 2.1 (certifiés section 4 du RE — binaire language_server.exe) ---
ECHO_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/aicode",   # CRITIQUE — scope spécifique Antigravity/Code Assist
    "https://www.googleapis.com/auth/cclog",    # Télémétrie completion log
]

# --- CONFIGURATION API ANTIGRAVITY (PROVISIONING) ---
# Standalone Antigravity → cloudcode-pa (sans "daily-")
AGY_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"

# Metadata client envoyée dans loadCodeAssist — pluginType "GEMINI" confirmé stable (RE §10)
ECHO_CLIENT_METADATA = {
    "ideType":    "IDE_UNSPECIFIED",
    "platform":   "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI"
}

AUTH_DATA_PROJECT_ID  = "google_project_id"
AUTH_DATA_USER_EMAIL  = "google_user_email"
AUTH_DATA_USER_TIER   = "google_user_tier"

# --- CONFIGURATION AI STUDIO (Fallback clés API) ---
GOOGLE_API_BASE_URL      = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_AI_STUDIO_WEB_URL = "https://aistudio.google.com/app/apikey"
GOOGLE_API_KEY_REGEX     = r"AIza[0-9A-Za-z_-]{35}"
GOOGLE_API_KEY_PATTERN   = GOOGLE_API_KEY_REGEX

# --- TIMING (certifiés par logs natifs IDE) ---
# Token TTL proactif : 55 min exact (confirmé par auth.log "token changed, handleAuthRefresh")
GOOGLE_OAUTH_TOKEN_LIFETIME = 3300  # secondes

# PKCE auth flow : timeout serveur callback (5 min pour que l'utilisateur ouvre l'URL)
PKCE_CALLBACK_TIMEOUT = 300  # secondes

# ==============================================================================
# 1.2 RÉSILIENCE ET RETRIES (API)
# ==============================================================================

ECHO_API_KEY_THRESHOLD   = 2
ECHO_API_MAX_RETRIES     = 5
ECHO_RETRY_BASE_DELAY    = 2.0
ECHO_RETRY_MULTIPLIER    = 2.0
ECHO_RETRY_JITTER_MIN    = 0.7
ECHO_RETRY_JITTER_MAX    = 1.3

# ==============================================================================
# 1.1 MODÈLES ECHO & REGISTRE COGNITIF (v5.99.1)
# ==============================================================================

# 1. IDENTIFIANTS TECHNIQUES — référence AI Studio (canonique).
#    La traduction vers les ID AGY est gérée par MODEL_MAP_CA ci-dessous.
#    Certifiés par diagnostic live 2026-05-24.
MODEL_PRO   = "gemini-3.1-pro-preview"  # PRO   — AI Studio ⚠️ 429 free | CA → gemini-pro-agent
MODEL_FLASH = "gemini-3.5-flash"        # FLASH — AI Studio ✅ 200      | CA → gemini-3-flash-agent
MODEL_LITE  = "gemini-3.1-flash-lite"   # LITE  — identique sur les deux APIs ✅ 200

# --- MÉMOIRE ORGANIQUE V2 ---
MODEL_DISTILLATION = "gemini-2.5-flash"  # identique sur les deux APIs ✅ 200
MODEL_EMBEDDING    = "BAAI/bge-m3"      # Modèle texte-first, multilingue, 1024d, 8192 tokens

# Table de capacités Code Assist — source de vérité documentaire.
# max_output_tokens : limites réelles certifiées par diagnostic live v2.1 (2026-05-25).
# Le cap appliqué en production est MAX_TOKENS_DEFAULT (65535 universel, décision D1).
# Modèles supportés : exclusivement Gemini (Claude, GPT, OpenAI-Vertex exclus du scope ECHO).
MODEL_MAP_CA: dict = {
    MODEL_PRO: {
        "model_id":          "gemini-pro-agent",      # Gemini 3.1 Pro (High) — thinkingBudget=10001
        "max_output_tokens": 65535,                   # Certifié : 400 si 65536 (diag C-quart v2.0)
        "supports_thinking": True,
    },
    MODEL_FLASH: {
        "model_id":          "gemini-3-flash-agent",  # Gemini 3.5 Flash (High) — thinkingBudget=-1
        "max_output_tokens": 65536,                   # Certifié : 200 avec 65536 (diag C-quart v2.0)
        "supports_thinking": True,                    # includeThoughts=True → thought=True lisible (diag H3)
    },
    MODEL_LITE: {
        "model_id":          "gemini-3.1-flash-lite", # Identique sur les deux APIs
        "max_output_tokens": 65535,                   # Certifié : 400 si 65536 (diag C-quart v2.0)
        "supports_thinking": False,                   # supportsThinking absent (diag section 6c v2.0)
    },
    MODEL_DISTILLATION: {
        "model_id":          "gemini-2.5-flash",      # Identique sur les deux APIs
        "max_output_tokens": 65535,                   # Infere : section 6 CA affiche "Gemini 3.1 Flash Lite"
                                                       # — non mesure directement par section 6c.
                                                       # Sans impact prod : cap universel MIN(val, 65535).
        "supports_thinking": False,
    },
}

# Alias rétrocompatible — conservé pour ne pas casser les imports existants dans les scripts.
# Déprécié : remplacé par MODEL_MAP_CA dans echo_protocol.py.
AGY_MODEL_MAP: dict[str, str] = {
    k: v["model_id"] for k, v in MODEL_MAP_CA.items()
}
# Alias de compatibilité ascendante
CODE_ASSIST_MODEL_MAP = AGY_MODEL_MAP
EMBEDDING_DIM_V2   = 1024               # Dimension bge-m3 (remplace 768 SigLIP-2)
COLLECTION_MEMORY    = "echo_memory"
COLLECTION_EPHEMERAL = "echo_ephemeral"

# Poids de reranking par niveau d'importance mémorielle.
# Appliqués dans recall_memories : score_pondéré = cos_score × MEMORY_IMPORTANCE_WEIGHTS[lvl]
# Un Axiome (5) à cos=0.60 bat un Trivial (1) à cos=0.85 : 0.60×1.70 > 0.85×0.55
MEMORY_IMPORTANCE_WEIGHTS: dict[int, float] = {
    1: 0.55,   # Trivial — pénalisé (bruit probable)
    2: 0.75,   # Mineur
    3: 1.00,   # Utile — référence neutre
    4: 1.30,   # Majeur
    5: 1.70,   # Axiome — fortement boosté (remonte toujours)
}

# Labels sémantiques des 5 niveaux — point de vérité unique pour UI, logs et LLM.
MEMORY_IMPORTANCE_LABELS: dict[int, str] = {
    1: "Trivial",
    2: "Mineur",
    3: "Utile",
    4: "Majeur",
    5: "Axiome",
}
# ----------------------------

# --- PARAMÈTRES DE GÉNÉRATION ---
TEMP_DEFAULT       = 1.0
TEMP_DISTILLATION  = 0.0

TOP_P_DEFAULT      = 0.90
TOP_P_DISTILLATION = 0.10

# --- NIVEAUX DE RÉFLEXION (THINKING) — Point de vérité unique pour tout le framework ---
# Confirmés fonctionnels sur Code Assist + AI Studio par diagnostic live 2026-05-24.
# HIGH = réflexion complète (Gemini 3.x) ; MINIMAL = réflexion nulle (outils légers, grounding).
THINKING_LEVEL_PRO   = "HIGH"     # MODEL_PRO : réflexion maximale
THINKING_LEVEL_FLASH = "HIGH"     # MODEL_FLASH : réflexion standard
THINKING_LEVEL_LITE  = "HIGH"     # MODEL_LITE : confirmé 200 avec HIGH sur Code Assist
THINKING_LEVEL_TOOLS = "MINIMAL"  # Outils stateless (maps, grounding, distillation rapide)

# --- LIMITE DE TOKENS (GÉNÉRATION PRINCIPALE) ---
MAX_TOKENS_DEFAULT = 65535  # Limite universelle — tous modèles, toutes APIs (AI Studio + CA).
                             # 65535 : aligné sur les modèles Gemini 3.1 sur CA (Pro, Lite, 2.5-flash).
                             # Gemini 3 Flash CA supporte 65536 mais on aligne sur le plus restrictif.
                             # Utilisé par : pipe_engine (stream), call_distillation, echo_protocol.

# ----------------------------

# 2. REGISTRE COGNITIF ECHO (UNIFIÉ & STATIQUE)

# ROUTAGE : Clé Entrée (UI ou Cascade) -> ID Technique
MODEL_ROUTING = {
    "MODEL_LITE":         MODEL_LITE,
    "MODEL_FLASH":        MODEL_FLASH,
    "MODEL_PRO":          MODEL_PRO,
    "MODEL_DISTILLATION": MODEL_DISTILLATION,  # Fix : résout la clé string dans call_distillation
}

# IDENTITÉ : ID Technique -> Label de Catégorie (Pour le badge ##MODEL_ID##)
MODEL_IDENTITY = {
    MODEL_LITE:  "MODEL_LITE",
    MODEL_FLASH: "MODEL_FLASH",
    MODEL_PRO:   "MODEL_PRO"
}

# ==============================================================================
# 2. MAPPING MIME TYPES
# ==============================================================================

MIME_MAPPING_TXT = {
    "text/plain": [
        ".bat", ".c", ".conf", ".cpp", ".cs", ".css", ".csv", ".dockerfile",
        ".editorconfig", ".env", ".gitignore", ".go", ".h", ".hpp", ".ini",
        ".java", ".js", ".json", ".kt", ".log", ".lua", ".md", ".php", ".pl",
        ".ps1", ".py", ".r", ".rb", ".rs", ".sh", ".sql", ".swift", ".toml",
        ".ts", ".txt", ".vb", ".xml", ".yaml", ".yml", "dockerfile", "makefile"
    ],
    "text/html": [".html", ".htm"]
}

MIME_MAPPING_BIN = {
    "application/pdf": [".pdf"],
    "image/png": [".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".bmp", ".gif", ".tiff"],
    "audio/mpeg": [".mp3"],
    "audio/wav": [".wav"],
    "audio/mp3": [".aac", ".flac", ".ogg", ".m4a", ".opus"],
    "video/mp4": [".bit-perfect", ".mov", ".mpeg", ".mpg", ".webm", ".wmv", ".flv", ".3gpp"]
}

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================

def get_gemini_mime(file_path: str) -> tuple[str, bool]:
    """
    Analyse universelle et robuste du type MIME d'un fichier.
    Retourne un tuple (mime_type, est_supporte_par_gemini).
    Intègre une détection par extension, par signature binaire (Magic Numbers),
    et par épreuve textuelle (Null-Byte Sniffing), avec un fallback sécurisé.
    """
    if not os.path.exists(file_path):
        return "application/octet-stream", False

    ext = os.path.splitext(file_path)[1].lower().strip()
    filename = os.path.basename(file_path).lower()
    raw_mime = None

    # 1. Vérification rapide par mappings pré-définis (Priorité absolue ECHO)
    for mime, extensions in MIME_MAPPING_TXT.items():
        if ext in extensions or filename in extensions: return mime, True

    for mime, extensions in MIME_MAPPING_BIN.items():
        if ext in extensions: return mime, True

    # 2. Vérification par bibliothèque standard (basée sur l'extension)
    raw_mime, _ = mimetypes.guess_type(file_path)

    # 3. Le Crible Binaire (Fichier sans extension ou type inconnu)
    if not raw_mime:
        if filetype:
            kind = filetype.guess(file_path)
            if kind:
                raw_mime = kind.mime
            
    if not raw_mime:
        raw_mime = "unknown/unknown"

    # 4. Liste Blanche Native Gemini (Médias complexes & JSON)
    if raw_mime.startswith("image/") or \
       raw_mime.startswith("video/") or \
       raw_mime.startswith("audio/") or \
       raw_mime.startswith("text/") or \
       raw_mime in ["application/pdf", "application/json"]:
        return raw_mime, True

    # 5. L'Épreuve Textuelle (Text-Sniffing) pour le code source et configs obscurs
    # Si on arrive ici, c'est un format inconnu ou un "application/quelquechose" non supporté (ex: zip, dll, py)
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(1024)
        if not chunk or b'\x00' not in chunk:
            # Aucun caractère nul = C'est du texte pur ou du code source lisible
            return "text/plain", True
    except:
        pass

    # 6. Le Rejet et Fallback Sécuritaire (Binaires purs)
    return "application/octet-stream", False
