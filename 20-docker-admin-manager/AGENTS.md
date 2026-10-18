# 🌌 ECHO Framework - Connaissance Sémantique : `20-docker-admin-manager`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `20-docker-admin-manager`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'application **ECHO Admin Manager**. Il s'agit d'un conteneur Docker isolé (Tier 4) offrant un panneau de contrôle (Dashboard) interactif. Son rôle est de surveiller l'état de santé du cluster Docker, de réguler l'infrastructure de base de données (SQLite/Qdrant) et de gérer les autorisations en temps réel de manière totalement découplée de l'interface de chat Open WebUI.

## 2. Cartographie des Fichiers et Algorithmes

L'application est structurée en **4 couches modulaires** (`v5.137`) orchestrées par le point d'entrée léger `server.py` (injection directe de l'instance Flask `app` sans `Blueprint` pour préserver les résolutions `url_for`).

### `server.py` (v5.137 - Point d'Entrée)
Initialise l'amorçage CPU (`psutil.cpu_percent`), démarre le singleton `backup_scheduler` (`BackgroundScheduler`), synchronise la planification (`update_system_schedules`) et lance le serveur Flask (`port=3001, debug=False, threaded=True`).

### Couche 1 : `core/` (Socle Technique)
- **`core/config.py` (v5.137)** : Instancie `app` (`Flask`), `backup_scheduler` (`BackgroundScheduler`), `MAINTENANCE_STATE` (`is_running`, `task_type`, `status`, `percent`, `start_time`) et centralise l'ensemble des constantes de chemins (`WEBUI_DB_PATH`, `N8N_DATA_DIR`, `N8N_DB_PATH`, `MCP_CACHE_DIR`, `DOWNLOADS_DIR`, `RCLONE_CONF_PATH = "/app/backend/data/rclone/rclone.conf"`, `MAINT_HISTORY_FILE`, `QDRANT_URL`, `N8N_WORKER_URL`, `DEFAULT_MAINT_CONFIG` avec `purge_orphaned_chats=True` et `purge_orphaned_users=True` par défaut, `DEFAULT_AUTH_SETTINGS`).
- **`core/utils.py` (v5.136)** : Fonctions analytiques sans état (`human_size`, `get_cpu_model_name`, `get_dir_stats`, `get_echo_version`, `_format_bytes`, `_get_tree_size`).
- **`core/settings_io.py` (v5.136)** : Persistance JSON des configurations (`get_auth_settings`, `save_auth_settings`, `load_maint_config`, `save_maint_config`, `load_maint_history`, `save_maint_report`, `load_settings` avec auto-heal `auto_cleanup=True`, `save_settings`, `get_rclone_remotes` via `configparser`).
- **`core/security.py` (v5.136)** : Barrière SSO Forward Auth (`@app.before_request check_sso_auth`), génération de mots de passe complexes (`generate_complex_temp_password`) et rotation SSH (`change_system_password`).

### Couche 2 : `services/` (Logique Métier & Tâches Asynchrones)
- **`services/semantic_pruning.py` (v5.136)** : Algorithme d'élagage temporel (`run_semantic_pruning`, `_run_semantic_pruning`) et de purge synchrone par utilisateur (`purge_user_artifacts`) reposant sur `/app/backend/data/webui.db` (`table user`) comme **Single Source of Truth (SSoT)** avec une **politique Fail-Closed stricte** (3 verrous cumulatifs : Wait-for-it post-backup vérifiant `user` et `chat`, API Safeguard OWUI, et vérification `PRAGMA quick_check(1)` + présence de `admin@echo.local` + filtrage de `db_valid_chats` par `valid_ids` ; abandon immédiat sans suppression ni `VACUUM` si la base SSoT est injoignable ou verrouillée, ou si `pruning_error` capture une exception). Intègre en outre le verrou d'intégrité `n8n_whitelist_complete` (si la lecture d'un dossier de workflows ou d'une base `session.db` échoue lors de la construction de `n8n_active_ids`, l'appel `POST /prune` vers `echo-n8n-worker` et la GC N8N des Drop Zones sont annulés pour prévenir la destruction de démons actifs). Assure la réconciliation des 5 volumes (`echo-webui-data`, `echo-qdrant-data`, `echo-download-broker-data`, `echo-n8n-data` via `/prune`, `echo-mcp-cache`), la purge des tables SQLite `webui.db` orphelines (`user_id NOT IN user`), et le `VACUUM` sur `session.db`, `identity.db`, `webui.db`, `auth.db` et `database.sqlite` (N8N).
- **`services/memory_consolidation.py` (v5.136)** : Consolidation vectorielle Lvl1 → Lvl2 (`_cosine_sim`, `_centroid`, `consolidate_memories_for_user`) par clustering greedy et centroïde L2-normalisé (`"with_vector": True` sur `/points/scroll`, `"exact": True` sur `/points/count`) sans appel au worker d'embedding.
- **`services/maintenance_backup.py` (v5.137)** : Sauvegardes `tar.xz` incluant `echo-webui-data`, `echo-qdrant-data`, `echo-auth-data`, `echo-n8n-data` et `echo-mcp-cache` avec arrêt/redémarrage coordonné (`echo-open-webui`, `echo-qdrant`, `echo-n8n-worker`, `echo-mcp-broker`), suivi de progression temps-réel dans `MAINTENANCE_STATE` (`tar -cJf` via `psutil.Process.io_counters().read_bytes`, `sftp.put` via callback natif `_sftp_cb`, `rclone copy` via lecture de `/proc/<pid>/fdinfo` dans `_get_proc_file_offset`), création récursive `_sftp_mkdir_p`, construction unifiée du statut d'externalisation (`_build_ext_status_html`), retour structuré `backup_info` dans `perform_backup_task` (permettant au Séquenceur Nocturne `run_nightly_maintenance_cycle` d'inclure les détails de l'archive, la durée, la rétention locale, le statut d'externalisation Cloud/NAS et le bilan complet d'élagage) et programmation APScheduler (`update_system_schedules`).

### Couche 3 : `routes/` (Contrôleurs HTTP & API)
- **`routes/routes.py` (v5.137)** : Héberge l'ensemble des contrôleurs Flask (`@app.route`), incluant le Dashboard (`/` avec comptage strict de `real_session_count` sur `/users/*/chats/*/session.db`), le polling d'état (`/api/task_status`), l'historique (`/api/maint/history`), les métriques temps-réel (`/api/stats`, `/api/containers` avec `ThreadPoolExecutor`), la création directe de profils Rclone INI (`/action/rclone/create` via `configparser`, nettoyage des `\r\n` sur les tokens JSON Google Drive et `rclone obscure` pour WebDAV), la gouvernance SSoT/SSO Fail-Closed (`/action/auth/delete` exécutant la transaction de purge sur toutes les tables `user_id` de `webui.db` avant `auth.db` et `purge_user_artifacts`, `/action/auth_reset/<user_id>` purgeant simultanément `identity.db` et `/app/mcp_cache/<service>/<user_id>`) et le pilotage WAF BunkerWeb (`/api/bunkerweb/*`).

### Couche 4 : `templates/` (Rendu UI)
- **`templates/templates.py` (v5.136)** : Constantes HTML/Jinja2 inline (`HTML_LOGIN`, `HTML_DASHBOARD`), incluant le bandeau de progression temps-réel `#task-progress-card`, le journal des opérations dans l'onglet Sauvegardes (`#backupHistoryList`) et l'assistant `#rcloneWizardModal` (avec désactivation dynamique `disabled` des champs masqués via `toggleWizardFields()`).

### `Dockerfile` & `requirements.txt`
- Image héritant de `echo-python-base:latest`, installant `rclone` et les dépendances Python (`flask`, `docker`, `psutil`, `paramiko`, `APScheduler`, `passlib[argon2]`, `httpx`), et copiant `server.py` ainsi que les 4 packages (`core`, `services`, `routes`, `templates`).

## 3. Dépendances Logiques
- L'Admin Manager s'exécute sur le port **3001** et monte directement le socket Docker (`/var/run/docker.sock`) ainsi que les bind-mounts `:ro` de ses 4 packages pour supporter le Hot-Reload (`echo.hot-reload=true`).
- Il partage les volumes `echo-webui-data`, `echo-qdrant-data`, `echo-auth-data`, `echo-backups`, `echo-download-broker-data`, `echo-n8n-data` et `echo-mcp-cache` afin d'exécuter ses routines de sauvegarde, restauration, réconciliation SSoT et d'optimisation SQLite/Qdrant/FS.
