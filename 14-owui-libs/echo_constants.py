"""
title: ECHO Constants
author: ECHO Framework
version: 1.4
description: Master Source of Truth for ECHO Infrastructure. Corrected version path.
"""

import os
import mimetypes

# ==============================================================================
# 0. TOPOLOGIE SYSTÈME (ENVIRONNEMENT DOCKER)
# ==============================================================================

# Racine unique de l'infrastructure de données
ECHO_BASE_DATA_DIR = "/app/backend/data"

# Imbrication stricte des chemins physiques
ECHO_UPLOADS_DIR = f"{ECHO_BASE_DATA_DIR}/uploads"
ECHO_USER_DBS_DIR = f"{ECHO_BASE_DATA_DIR}/user_dbs"
ECHO_VERSION_FILE = f"{ECHO_BASE_DATA_DIR}/ECHO_VERSION"

# Source de vérité de la version (Lien Docker)
ECHO_VERSION_PATH = "/app/backend/data/ECHO_VERSION"

# Identité Réseau
ECHO_USER_AGENT = "GeminiCLI/0.32.1"

# ==============================================================================
# 1. PROTOCOLE GOOGLE CLOUD
# ==============================================================================

GOOGLE_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REDIRECT_URI = "https://codeassist.google.com/authcode"

GOOGLE_API_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
GOOGLE_SSE_URL = f"{GOOGLE_API_BASE_URL}:streamGenerateContent?alt=sse"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

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
    ext = os.path.splitext(file_path)[1].lower().strip()
    filename = os.path.basename(file_path).lower()

    for mime, extensions in MIME_MAPPING_TXT.items():
        if ext in extensions or filename in extensions: return mime, True

    for mime, extensions in MIME_MAPPING_BIN.items():
        if ext in extensions: return mime, True

    standard_mime, _ = mimetypes.guess_type(file_path)
    if standard_mime:
        if standard_mime.startswith("text/") or \
           standard_mime.startswith("image/") or \
           standard_mime.startswith("audio/") or \
           standard_mime.startswith("video/") or \
           standard_mime == "application/json":
            return standard_mime, True

    return None, False
