"""
title: ECHO Constants
author: ECHO Framework
version: 1.8
description: 1.8: Fixed API Key Regex to properly accept dashes.
"""

import os
import mimetypes
import filetype

# ==============================================================================
# 0. TOPOLOGIE SYSTÈME (ENVIRONNEMENT DOCKER)
# ==============================================================================

# Racine unique de l'infrastructure de données
ECHO_BASE_DATA_DIR = "/app/backend/data"

# NOUVELLE HIÉRARCHIE ECHO (v5.76.0)
ECHO_USERS_ROOT = f"{ECHO_BASE_DATA_DIR}/users"
ECHO_UPLOADS_TRANSIT_DIR = f"{ECHO_BASE_DATA_DIR}/uploads"

# ALIAS DE COMPATIBILITÉ CRITIQUE (ANTI-RÉGRESSION)
ECHO_UPLOADS_DIR = ECHO_UPLOADS_TRANSIT_DIR
ECHO_OLD_USER_DBS_DIR = f"{ECHO_BASE_DATA_DIR}/user_dbs"
ECHO_USER_DBS_DIR = ECHO_OLD_USER_DBS_DIR

ECHO_VERSION_PATH = f"{ECHO_BASE_DATA_DIR}/ECHO_VERSION"

# Identité Réseau
ECHO_USER_AGENT = "GeminiCLI/0.33.1"

# ==============================================================================
# 1. PROTOCOLE GOOGLE AI STUDIO (GEMINI API)
# ==============================================================================

GOOGLE_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_AI_STUDIO_URL = GOOGLE_API_BASE_URL

# Regex de validation de clé API Google (AIza...)
# Le tiret doit être à la fin de la classe de caractères pour éviter les erreurs de plage
GOOGLE_API_KEY_REGEX = r"^AIza[0-9A-Za-z_-]{35}$"

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
    "audio/mp3": [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".opus"],
    "video/mp4": [".mp4", ".mov", ".mpeg", ".mpg", ".webm", ".wmv", ".flv", ".3gpp"]
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
