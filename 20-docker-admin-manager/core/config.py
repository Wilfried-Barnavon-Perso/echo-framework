# -*- coding: utf-8 -*-
r"""
# ================================================================================
# MODULE : ECHO ADMIN MANAGER SERVER
# VERSION : 5.137 (Rapport Nocturne Complet Sauvegarde/Externalisation & Compteur session.db)
# --- CHANGELOG 5.137 ---
# - Feature : Enrichissement du rapport nocturne (`run_nightly_maintenance_cycle`) avec les métadonnées complètes de sauvegarde (`archive_name`, taille, durée, rétention locale), le statut explicite de l'externalisation Cloud Rclone / NAS SFTP (`_build_ext_status_html`) et le bilan complet d'élagage (`Fichiers FS`, `Drop Zones` détaillées, `SQLite`).
# - Fix : Restriction du compteur physique `real_session_count` (`Sessions Actives / Totales`) aux seuls fichiers `/users/<uid>/chats/<chat_id>/session.db` afin d'exclure les bases SQLite tierces créées dans les sous-dossiers `codex/` ou `files/`.
# --- CHANGELOG 5.136 ---
# - Feature : Intégration complète des volumes `echo-n8n-data` (`/app/n8n-data`) et `echo-mcp-cache` (`/app/mcp_cache`) dans le cycle de Sauvegarde/Restauration `tar.xz`, le calcul de progression temps-réel, le `VACUUM` SQLite et la Garbage Collection.
# - Sécurité (Critique / Fail-Closed) : Verrouillage strict sur la SSoT `webui.db::user` (« Base injoignable = Zéro Action »). Vérification préalable `PRAGMA quick_check(1)`, présence de `admin@echo.local`, filtrage de `db_valid_chats` par `valid_ids`, verrou d'intégrité `n8n_whitelist_complete` (empêchant la suppression de démons N8N si un `session.db` est verrouillé), et transaction Fail-Closed dans `auth_delete_user`.
# --- CHANGELOG 5.135 ---
# - Feature : Suivi en temps réel (`MAINTENANCE_STATE` : `task_type`, `percent`, `status`) de toutes les phases de sauvegarde (`tar -cJf` via `psutil.io_counters()`, `rclone copy` via `/proc/<pid>/fdinfo`, `sftp.put` via callback natif Paramiko), d'élagage sémantique et de consolidation.
# - Feature : Bandeau de progression Bootstrap animé (`#task-progress-card`) dans le Dashboard et enregistrement systématique d'un rapport final HTML (`save_maint_report`) lors des sauvegardes manuelles, consultable dans Système & Maintenance et dans l'onglet Sauvegardes.
# --- CHANGELOG 5.134 ---
# - Fix (UI/HTML5) : Désactivation dynamique (`disabled`) des champs dans les sections masquées (`#wiz_s3`, `#wiz_webdav`) via `toggleWizardFields()`, passage de `webdav_url` en `type="text" autocomplete="off"`, et échappement du tiret dans `pattern="[a-zA-Z0-9_\-]+"` (requis par le moteur RegExp Unicode `/v` de Chrome). Empêche le blocage silencieux du bouton "Générer le Profil" (`An invalid form control with name='webdav_url' is not focusable`).
# --- CHANGELOG 5.133 ---
# - Fix (Critique) : Remplacement de la machine à états interactive `rclone config create` par une écriture INI directe (`configparser`) dans `/backups/rclone/rclone.conf` (et `rclone obscure` pour WebDAV), éliminant le blocage ou la sortie sans écriture sur Google Drive (`--non-interactive`).
# - Fix : Nettoyage automatique des sauts de ligne (`\r\n`) injectés par le copier-coller PowerShell au milieu du Token JSON OAuth Google Drive, extraction de l'objet `{...}` et validation JSON stricte.
# ================================================================================
"""

import os
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
    "session_timeout": 2160
}

MAINT_HISTORY_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_history.json")
