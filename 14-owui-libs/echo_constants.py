"""
title: ECHO Constants
author: ECHO Framework
version: 2.9
description: 2.8: Migration vers Embedding local (SigLIP 2 + Infinity). 2.9: Ajout RAG Éphémère.
"""

import os
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

# Identité Réseau
ECHO_USER_AGENT = "gemini-cli/0.42.0"

# Points d'accès Locaux (Souveraineté)
ECHO_EMBEDDING_URL = "http://echo-embedding:7997/v1"

# ==============================================================================
# 1. PROTOCOLE GOOGLE AI STUDIO (GEMINI API) & AUTH UNIFIÉE
# ==============================================================================

GOOGLE_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_AI_STUDIO_WEB_URL = "https://aistudio.google.com/app/apikey"

# --- IDENTIFIANTS TECHNIQUES DES MÉTHODES ---
AUTH_METHOD_KEY_PRIMARY = "google_api_key"
AUTH_METHOD_OAUTH2 = "google_oauth2"
AUTH_METHOD_KEY_SECONDARY = "google_api_key_secondary"

# Priorité par défaut : OAuth2 > Clé 1 > Clé 2
DEFAULT_AUTH_PRIORITY = f"{AUTH_METHOD_OAUTH2}, {AUTH_METHOD_KEY_PRIMARY}, {AUTH_METHOD_KEY_SECONDARY}"

# --- CONFIGURATION GOOGLE OAUTH2 (HÉRITAGE GEMINI-CLI) ---
# Note: Ces identifiants sont publics pour les applications "Desktop" Google.
ECHO_OAUTH_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
ECHO_OAUTH_CLIENT_SECRET = "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"

GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# --- CONFIGURATION CODE ASSIST (PROVISIONING) ---
CODE_ASSIST_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
ECHO_CLIENT_METADATA = {
    "ideType": "IDE_UNSPECIFIED",
    "platform": "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI"
}
AUTH_DATA_PROJECT_ID = "google_project_id"
AUTH_DATA_USER_EMAIL = "google_user_email"
AUTH_DATA_USER_TIER = "google_user_tier"

ECHO_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile"
]

# --- SÛRETÉ PKCE ---
PKCE_REUSE_WINDOW = 300 # Fenêtre de 5 minutes pour réutiliser un challenge PKCE
GOOGLE_OAUTH_TOKEN_LIFETIME = 3000 # Durée de validité proactive du jeton (50 min)

# Regex de validation et extraction de clé API Google (AIza...)
GOOGLE_API_KEY_REGEX = r"AIza[0-9A-Za-z_-]{35}"
GOOGLE_API_KEY_PATTERN = GOOGLE_API_KEY_REGEX

# Regex pour l'Authorization Code Google (commence généralement par 4/)
GOOGLE_OAUTH_CODE_REGEX = r"4/[0-9A-Za-z_-]+"

# ==============================================================================
# 1.2 RÉSILIENCE ET RETRIES (API GEMINI)
# ==============================================================================

ECHO_API_KEY_THRESHOLD = 2
ECHO_API_MAX_RETRIES = 5
ECHO_RETRY_BASE_DELAY = 2.0
ECHO_RETRY_MULTIPLIER = 2.0
ECHO_RETRY_JITTER_MIN = 0.7
ECHO_RETRY_JITTER_MAX = 1.3

# ==============================================================================
# 1.1 MODÈLES ECHO & REGISTRE COGNITIF (v5.99.1)
# ==============================================================================

# 1. IDENTIFIANTS TECHNIQUES (STRICTS)
MODEL_PRO = "gemini-3.1-pro-preview"
MODEL_FLASH = "gemini-3-flash-preview"
MODEL_LITE = "gemini-3.1-flash-lite-preview"

# --- MÉMOIRE ORGANIQUE V2 ---
MODEL_DISTILLATION = "gemini-2.5-flash"
MODEL_EMBEDDING    = "BAAI/bge-m3"      # Modèle texte-first, multilingue, 1024d, 8192 tokens
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
TEMP_DEFAULT = 1.0
TEMP_DISTILLATION = 0.0

TOP_P_DEFAULT = 0.90
TOP_P_DISTILLATION = 0.10
# ----------------------------

# 2. REGISTRE COGNITIF ECHO (UNIFIÉ & STATIQUE)

# ROUTAGE : Clé Entrée (UI ou Cascade) -> ID Technique
MODEL_ROUTING = {
    "MODEL_LITE": MODEL_LITE,
    "MODEL_FLASH": MODEL_FLASH,
    "MODEL_PRO": MODEL_PRO
}

# IDENTITÉ : ID Technique -> Label de Catégorie (Pour le badge ##MODEL_ID##)
MODEL_IDENTITY = {
    MODEL_LITE: "MODEL_LITE",
    MODEL_FLASH: "MODEL_FLASH",
    MODEL_PRO: "MODEL_PRO"
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
