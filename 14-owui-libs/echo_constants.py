"""
title: ECHO Constants & Utils
author: ECHO Framework
version: 1.0
description: Centralized constants and helper functions for ECHO components (Filters, Pipes, Tools).
"""

import os
import mimetypes

# ==============================================================================
# 1. MAPPING MIME TYPES (WHITELIST STRICTE)
# ==============================================================================

# Fichiers TEXTE (Code, Config, Logs) -> Traités comme text/plain ou spécifique
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

# Fichiers BINAIRES (Multimodal : Images, Audio, Vidéo, PDF)
MIME_MAPPING_BIN = {
    "application/pdf": [".pdf"],
    "image/png": [".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".bmp", ".gif", ".tiff"],
    "audio/mp3": [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".opus"],
    "video/mp4": [".mp4", ".mov", ".mpeg", ".mpg", ".webm", ".wmv", ".flv", ".3gpp"]
}

# ==============================================================================
# 2. HELPER FUNCTIONS
# ==============================================================================

def get_gemini_mime(file_path: str) -> tuple[str, bool]:
    """
    Détermine le type MIME Gemini pour un fichier donné.
    Retourne : (mime_type, is_supported)
    
    Règle : 
    1. Vérification stricte dans les Mappings ECHO (Priorité).
    2. Fallback mimetypes standard (Uniquement si type connu et compatible).
    3. Si inconnu -> (None, False).
    """
    ext = os.path.splitext(file_path)[1].lower().strip()
    filename = os.path.basename(file_path).lower()

    # 1. Mapping TEXTE (Code)
    for mime, extensions in MIME_MAPPING_TXT.items():
        if ext in extensions or filename in extensions:
            return mime, True

    # 2. Mapping BINAIRE (Media)
    for mime, extensions in MIME_MAPPING_BIN.items():
        if ext in extensions:
            return mime, True

    # 3. Fallback Standard (mimetypes)
    # On accepte uniquement si ça commence par text/, image/, audio/, video/
    standard_mime, _ = mimetypes.guess_type(file_path)
    if standard_mime:
        if standard_mime.startswith("text/") or \
           standard_mime.startswith("image/") or \
           standard_mime.startswith("audio/") or \
           standard_mime.startswith("video/") or \
           standard_mime == "application/json":
            return standard_mime, True

    return None, False
