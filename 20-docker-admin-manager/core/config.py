# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
r"""
# ================================================================================
# MODULE : ECHO ADMIN MANAGER SERVER
# VERSION : 6.20.4.1
# ================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.20.4.1: Ajout de TMP_UPLOADS_DIR (/app/backend/data/.tmp_uploads) et exposition dans DIRS["tmp_uploads"].
# 6.18.5.1: Ajout de la constante MAX_ECHO_USERS = 50 (plafond Clause 4 Open WebUI).
# 6.18.0.1: Ajout des constantes SSOT BACKUP_AUX_CONTAINERS (couvrant tous les écrivains :rw ECHO v6) et BROWSER_WORKER_URL.
# 6.5.3.1: Alignement de l'URL interne et du nom de conteneur sur echo-webui.
# 6.5.0.1: Définition des constantes SECRETS_DIR, HOST_ENV_FILE et HOST_ECHO_ROOT pour l'archivage et la restauration des secrets hôtes.

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
TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'echo-webui')
# Conteneurs auxiliaires montant les volumes persistants en écriture (:rw) lors des sauvegardes/restaurations
BACKUP_AUX_CONTAINERS = (
    'echo-qdrant',
    'echo-n8n-worker',
    'echo-mcp-broker',
    'echo-download-broker',
    'echo-coding-worker',
    'echo-user-manager',
)
BACKUP_DIR = "/backups"
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")

OWUI_DATA_ROOT = "/app/backend/data"
ECHO_USERS_ROOT = os.path.join(OWUI_DATA_ROOT, "users") 
UPLOADS_DIR = os.path.join(OWUI_DATA_ROOT, "uploads")
TMP_UPLOADS_DIR = os.path.join(OWUI_DATA_ROOT, ".tmp_uploads")
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

WEBUI_URL = "http://echo-webui:8080"
QDRANT_URL = "http://echo-qdrant:6333"
N8N_WORKER_URL = "http://echo-n8n-worker:5003"
BROWSER_WORKER_URL = os.environ.get("NAVIGATION_ENGINE_URL", "http://echo-browser-worker:5002").rstrip("/")
COLLECTION_META_ARTIFACTS = "echo_meta_artifacts"
COLLECTION_SESSION_RAG = "echo_session_rag"

DIRS = {
    "uploads": UPLOADS_DIR,
    "tmp_uploads": TMP_UPLOADS_DIR,
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

# Plafond technique par défaut
# (conformité Open WebUI License Clause 4 : <= 50 utilisateurs)
MAX_ECHO_USERS = 50

MAINT_HISTORY_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_history.json")
