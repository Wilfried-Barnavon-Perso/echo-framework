# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO USER MANAGER - CORE CONFIG
VERSION : 5.236.5.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.236.5.1: Création initiale de la configuration centrale du User Manager.

import os
import secrets
import sys
from flask import Flask  # pyright: ignore[reportMissingImports]

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    httpx = None
    HAS_HTTPX = False

ECHO_LIBS_DIR = "/app/echo_libs"
if ECHO_LIBS_DIR not in sys.path:
    sys.path.append(ECHO_LIBS_DIR)

try:
    from echo_constants import (
        COLLECTION_META_ARTIFACTS,
        COLLECTION_SESSION_RAG,
        ECHO_SYNC_EXCLUDE_LIST,
    )
    from echo_paths import sanitize_echo_id
    HAS_ECHO_LIBS = True
except ImportError:
    COLLECTION_META_ARTIFACTS = "echo_meta_artifacts"
    COLLECTION_SESSION_RAG = "echo_session_rag"
    ECHO_SYNC_EXCLUDE_LIST = [
        ".venv",
        "node_modules",
        "__pycache__",
        ".git",
        ".pytest_cache",
        "venv",
    ]
    HAS_ECHO_LIBS = False

    def sanitize_echo_id(raw_id: str, fallback: str = "default", default=None) -> str:
        eff = default if default is not None else fallback
        cleaned = "".join(x for x in str(raw_id or "") if x.isalnum() or x in "-_")
        return cleaned if cleaned else eff

app = Flask(__name__, static_folder="/app/static")
app.secret_key = secrets.token_hex(32)
app.config["JSON_AS_ASCII"] = False
app.config["SESSION_COOKIE_NAME"] = "echo_um_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ECHO_DOMAIN = os.environ.get("ECHO_DOMAIN", "localhost")
SYSTEM_ADMIN_EMAIL = "admin@echo.local"

OWUI_DATA_ROOT = "/app/backend/data"
ECHO_USERS_ROOT = os.path.join(OWUI_DATA_ROOT, "users")
WEBUI_DB_PATH = os.path.join(OWUI_DATA_ROOT, "webui.db")

AUTH_DATA_DIR = "/app/auth-data"
AUTH_DB_PATH = os.path.join(AUTH_DATA_DIR, "auth.db")
AUTH_SETTINGS_PATH = os.path.join(AUTH_DATA_DIR, "auth-settings.json")

WEBUI_INTERNAL_URL = "http://echo-open-webui:8080"
QDRANT_URL = "http://echo-qdrant:6333"

DEFAULT_AUTH_SETTINGS = {
    "pwd_min_length": 12,
    "pwd_require_upper": True,
    "pwd_require_lower": True,
    "pwd_require_digit": True,
    "pwd_require_special": True,
    "temp_pass_hours": 24,
    "totp_tolerance": 1,
    "untrusted_session_timeout": 24,
    "session_timeout": 2160,
}
