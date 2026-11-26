# 🌌 ECHO Framework - Connaissance Sémantique : `20-docker-admin-manager`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `20-docker-admin-manager`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'application **ECHO Admin Manager**. Il s'agit d'un conteneur Docker isolé (Tier 4) offrant un panneau de contrôle (Dashboard) interactif. Son rôle est de surveiller l'état de santé du cluster Docker, de réguler l'infrastructure de base de données (SQLite/Qdrant) et de gérer les autorisations en temps réel de manière totalement découplée de l'interface de chat Open WebUI.

## 2. Cartographie des Fichiers et Algorithmes

L'application est structurée en **4 couches modulaires** orchestrées par le point d'entrée léger `server.py` (injection directe de l'instance Flask `app` sans `Blueprint` pour préserver les résolutions `url_for`).

### `server.py` (v5.226.18.0 - Point d'Entrée)
Initialise l'amorçage CPU (`psutil.cpu_percent`), démarre le singleton `backup_scheduler` (`BackgroundScheduler`), synchronise la planification (`update_system_schedules`) et lance le serveur Flask (`port=3001, debug=False, threaded=True`).

### Couche 1 : `core/` (Socle Technique)
- **`core/config.py` (v6.22.1.1)** : Instancie `app` (`Flask` avec `SESSION_COOKIE_NAME = "echo_am_session"`, `HttpOnly`, `SameSite=Lax`, et `app.secret_key` dérivée par SHA-256 depuis le secret d'instance), `backup_scheduler` (`BackgroundScheduler`), `MAINTENANCE_STATE`, centralise `MAX_ECHO_USERS = 50`, `BACKUP_AUX_CONTAINERS`, `RESTORE_EXTRA_CONTAINERS` (`echo-auth-manager`), `BROWSER_WORKER_URL`, `SECRETS_DIR = "/app/secrets"`, `HOST_ENV_FILE = "/app/secrets/.env"` (repli `LEGACY_HOST_ENV_FILE`), `parse_env_content`, `read_host_env_dict`, `RCLONE_CONF_PATH = "/backups/rclone/rclone.conf"`, etc.
- **`core/utils.py` (v5.226.18.0)** : Fonctions analytiques sans état (`human_size`, `get_cpu_model_name`, `get_dir_stats`, `get_echo_version`, `_format_bytes`, `_get_tree_size`).
- **`core/settings_io.py` (v6.22.1.1)** : Persistance JSON des configurations (`get_auth_settings`, `save_auth_settings`, `load_maint_config`, `save_maint_config`, `load_maint_history`, `save_maint_report`, `load_settings` déchiffrant `sftp_pass` en mémoire avec auto-heal `auto_cleanup=True`, `save_settings` chiffrant `sftp_pass` en AEAD `enc:v1:...` sur disque en `0o600` tout en préservant l'existant si vide, `get_rclone_remotes`).
- **`core/security.py` (v6.22.1.1)** : Barrière SSO Forward Auth (`@app.before_request check_sso_auth` validant `X-Echo-Sso-Secret` en temps constant via `hmac.compare_digest`), lecture dynamique `.secrets/.env` (`get_instance_secret`, `get_admin_settings_crypto_key`), helper SQLite `open_sqlite_db`, `generate_complex_temp_password` et `change_system_password`.

### Couche 2 : `services/` (Logique Métier & Tâches Asynchrones)
- **`services/semantic_pruning.py` (v6.22.1.1)** : Algorithme d'élagage temporel (`run_semantic_pruning`, `_run_semantic_pruning` via `open_sqlite_db`), de purge synchrone par utilisateur (`purge_user_artifacts`), de purge des captures/clips `browserviews` via `cleanup_expired_browserviews_sync`, de filet de sécurité GC sur les sous-agents purgés expirés (`cleanup_expired_purged_subagents_sync`) et de Garbage Collection des branches mortes (`gc_session_db`) reposant sur `/app/backend/data/webui.db` avec une **politique Fail-Closed stricte** (API Safeguard ECHO-UI utilisant `get_instance_secret("ECHO_ADMIN_PASSWORD")` et `get_instance_secret("ECHO_DOMAIN")` au lieu de `.owui-admin-secret`). Assure le GC fichiers FS sur la topologie unifiée v6, la purge table `file`, `auth`, `access_grant`, `automation_run`, la réconciliation des 5 volumes et les `VACUUM` SQLite.
- **`services/memory_consolidation.py` (v5.226.18.0)** : Consolidation vectorielle Lvl1 → Lvl2 (`_cosine_sim`, `_centroid`, `consolidate_memories_for_user`) par clustering greedy et centroïde L2-normalisé (`"with_vector": True` sur `/points/scroll`, `"exact": True` sur `/points/count`) sans appel au worker d'embedding.
- **`services/maintenance_backup.py` (v6.22.1.1)** : Sauvegardes chiffrées en streaming 1-passe `EBKP1` (`AES-256-GCM` par blocs 4 MiB sans fichier `.tar.xz` intermédiaire, Fail-Closed si `ECHO_BACKUP_ENCRYPTION_KEY` absente, `.tar.xz.enc` via `.part` atomique) à priorité minimale (`nice -n 19`, `ionice -c 3`, `XZ_OPT="-T2 -1"`), incluant `echo-webui-data`, `echo-qdrant-data`, `echo-auth-data`, `echo-n8n-data`, `echo-mcp-cache`, `RCLONE_CONF_DIR`, `SETTINGS_FILE` et `SECRETS_DIR` (`/app/secrets/.env`) avec checkpoint WAL sur `auth.db`, détection `encrypted` dans `get_backup_list`, suivi temps-réel dans `MAINTENANCE_STATE` et programmation APScheduler.

### Couche 3 : `routes/` (Contrôleurs HTTP & API)
- **`routes/routes.py` (v6.22.1.1)** : Contrôleurs Flask (`@app.route`, connexions SQLite via `open_sqlite_db`), incluant l'exposition whitelistée et copiable des 2 secrets admin (`GET /api/admin/secrets/<secret_id>` pour `backup_key` et `admin_password`, zéro `sftp_pass` injecté dans le DOM HTML), l'import PRA en streaming (`POST /action/backup/upload` vérifiant les magic bytes `EBKP1` / XZ), la restauration strictement **Fail-Closed en 4 barrières** (`POST /action/restore` : clé requise -> validation en-tête `<2ms` -> vérification AEAD intégrale + `tar -tf` avant tout arrêt/purge -> arrêt incluant `echo-auth-manager`, fusion sélective de `.secrets/.env` via conteneur éphémère `echo-python-base:latest` préservant `BW_DB_PASSWORD`/`ECHO_DOMAIN` hôte et recréant le symlink `/opt/ECHO/.env -> .secrets/.env`), la gestion Rclone INI (`0o700`/`0o600`), le plafond `MAX_ECHO_USERS = 50`, le genre `M`/`F`/`X`, les paramètres Dual-Tier/Anti-Bruteforce et le pilotage WAF BunkerWeb.

### Couche 4 : `templates/` (Rendu UI)
- **`templates/templates.py` (v6.22.1.1)** : Constantes HTML/Jinja2 inline (`HTML_LOGIN`, `HTML_DASHBOARD`), incluant l'encart **Clé de Chiffrement des Sauvegardes** copiable avec empreinte SHA-256 et alerte de garde hors-serveur dans `Sécurité & Accès`, le bouton **Importer** (`#uploadBackupModal`), les badges `🔒 Chiffrée` / `⚠️ Clair` dans `refreshBackups()`, la modale `#restoreBackupModal` exigeant la clé avant soumission, et le masquage de `sftp_pass` dans `#extBackupModal`.

### `Dockerfile` & `requirements.txt`
- Image héritant de `echo-python-base:latest`, installant `rclone` et les dépendances Python (`flask`, `docker`, `psutil`, `paramiko`, `APScheduler`, `passlib[argon2]`, `httpx`), et copiant `server.py` ainsi que les 4 packages (`core`, `services`, `routes`, `templates`), avec bloc `LABEL` (`version="6.22.1.1"`) positionné tout en bas avant `EXPOSE 3001`.

## 3. Dépendances Logiques
- L'Admin Manager s'exécute sur le port **3001** et monte directement le socket Docker (`/var/run/docker.sock`) ainsi que les bind-mounts `:ro` de ses 4 packages pour supporter le Hot-Reload (`echo.hot-reload=true`).
- Il partage les volumes `echo-webui-data`, `echo-qdrant-data`, `echo-auth-data`, `echo-backups`, `echo-download-broker-data`, `echo-n8n-data` et `echo-mcp-cache` afin d'exécuter ses routines de sauvegarde, restauration, réconciliation SSoT et d'optimisation SQLite/Qdrant/FS.
