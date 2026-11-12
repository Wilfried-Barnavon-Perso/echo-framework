# -*- coding: utf-8 -*-
r"""
# ================================================================================
# MODULE : ECHO ADMIN MANAGER SERVER
# VERSION : 6.5.0.1
# ================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.5.0.1: Définition des constantes SECRETS_DIR, HOST_ENV_FILE et HOST_ECHO_ROOT pour l'archivage et la restauration des secrets hôtes.
# 5.239.2.1: Ajout de login_retry_delay_sec (5s), unenrolled_max_attempts (5) et unenrolled_lockout_min (10m) dans DEFAULT_AUTH_SETTINGS.
# 5.236.1.1: Ajout de untrusted_session_timeout (24h par défaut) dans DEFAULT_AUTH_SETTINGS.
# 5.235.5.1: Chargement dynamique optionnel de /app/echo_libs et ajout de l'option purge_dead_branches dans DEFAULT_MAINT_CONFIG.
# 5.226.18.0: Alignement sur le versioning global ECHO.

import os
import sys
import secrets
from flask import Flask  # pyright: ignore[reportMissingImports]

# ==============================================================================
# SECTION 1 : GESTION DES DÉPENDANCES
# ==============================================================================
try:
    import docker
    import paramiko
    DOCKER_AVAILABLE = True
except ImportError:
    docker = None
    paramiko = None
    DOCKER_AVAILABLE = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    httpx = None
    HAS_HTTPX = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    BackgroundScheduler = None
    HAS_SCHEDULER = False

ECHO_LIBS_DIR = "/app/echo_libs"
if ECHO_LIBS_DIR not in sys.path:
    sys.path.append(ECHO_LIBS_DIR)
try:
    from echo_constants import ECHO_GC_GRACE_PERIOD_SEC
    HAS_ECHO_LIBS = True
except ImportError:
    ECHO_GC_GRACE_PERIOD_SEC = None
    HAS_ECHO_LIBS = False

# ==============================================================================
# SECTION 2 : CONFIGURATION & CHEMINS
# ==============================================================================
app = Flask(__name__, static_folder='/app/static')
app.secret_key = secrets.token_hex(32)
app.config['JSON_AS_ASCII'] = False

backup_scheduler = BackgroundScheduler() if HAS_SCHEDULER else None

# Traqueur d'État Global pour les tâches asynchrones (Sauvegarde, Pruning, Consolidation)
MAINTENANCE_STATE = {
    "is_running": False,
    "task_type": "",
    "status": "",
    "percent": 0,
    "start_time": None
}
SYSTEM_ADMIN_EMAIL = "admin@echo.local"
TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'echo-open-webui')
BACKUP_DIR = "/backups"
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")

OWUI_DATA_ROOT = "/app/backend/data"
ECHO_USERS_ROOT = os.path.join(OWUI_DATA_ROOT, "users") 
UPLOADS_DIR = os.path.join(OWUI_DATA_ROOT, "uploads")
WEBUI_DB_PATH = os.path.join(OWUI_DATA_ROOT, "webui.db")
RCLONE_CONF_DIR = os.path.join(OWUI_DATA_ROOT, "rclone")

RCLONE_CONF_PATH = os.path.join(RCLONE_CONF_DIR, "rclone.conf")
OWUI_ADMIN_SECRET_PATH = "/app/secrets/.owui-admin-secret"
SECRETS_DIR = "/app/secrets"
HOST_ENV_FILE = "/app/host_env/.env"
HOST_ECHO_ROOT = "/opt/ECHO"
DOWNLOADS_DIR = "/app/downloads"
N8N_DATA_DIR = "/app/n8n-data"
N8N_DB_PATH = os.path.join(N8N_DATA_DIR, "database.sqlite")
MCP_CACHE_DIR = "/app/mcp_cache"

WEBUI_URL = "http://echo-open-webui:8080"
QDRANT_URL = "http://echo-qdrant:6333"
N8N_WORKER_URL = "http://echo-n8n-worker:5003"
COLLECTION_META_ARTIFACTS = "echo_meta_artifacts"
COLLECTION_SESSION_RAG = "echo_session_rag"

DIRS = {
    "uploads": UPLOADS_DIR,
    "echo_vault": ECHO_USERS_ROOT,
    "debug_logs": os.path.join(OWUI_DATA_ROOT, "debug_logs"),
    "rclone": RCLONE_CONF_DIR
}

DEFAULT_BACKUP_CONFIG = {
    "auto_backup": True, "auto_cleanup": True, "cleanup_mode": "count",
    "cleanup_value": 5, "backup_time": "03:00", "interval_days": 1,
    "ext_mode": "local", "ext_cleanup_mode": "count", "ext_cleanup_value": 5,
    "sftp_host": "", "sftp_port": "22", "sftp_user": "", "sftp_pass": "", "sftp_path": "/backups/echo",
    "rclone_remote": ""
}

DEFAULT_MAINT_CONFIG = {
    "cleanup_hour": "03:00", "last_run": "Never",
    "retention": {},
    "memory_ttl": { "lvl1": 30, "lvl2": 60, "lvl3": 180, "lvl4": 365, "lvl5": 540 },
    "consolidation": {
        "enabled": True,
        "trigger_threshold": 10,   # Nb de points lvl1 par user déclenchant la consolidation
        "min_cluster_size": 3,     # Nb minimum de points similaires pour fusionner
        "similarity_threshold": 0.75  # Score cosinus minimal pour appartenir à un cluster
    },
    "purge_orphaned_chats": True,
    "purge_dead_branches": True,
    "purge_orphaned_users": True
}

AUTH_SETTINGS_PATH = "/app/auth-data/auth-settings.json"
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
    "login_retry_delay_sec": 5,
    "unenrolled_max_attempts": 5,
    "unenrolled_lockout_min": 10
}

MAINT_HISTORY_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_history.json")
