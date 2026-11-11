# 🌌 ECHO Framework - Connaissance Sémantique : `00-echo-scripts`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `00-echo-scripts`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'intégralité de l'outillage Bash d'infrastructure (Scripts d'Ops). Son rôle est d'orchestrer le déploiement, la configuration post-déploiement, la synchronisation du code depuis l'hôte Windows vers la cible, et la gestion du périmètre de sécurité de l'instance ECHO.

## 2. Cartographie des Fichiers et Algorithmes

### `install-stack.sh`
**Rôle** : Déploiement central de l'architecture Docker (v6.0.0.1).
- **`ensure_docker_autosafety()`** : Configure de manière globale le démon Docker (`/etc/docker/daemon.json`) pour imposer une limite de taille des logs (max 10Mo, 3 fichiers) et injecte un cron quotidien (`/etc/cron.daily/echo-clean`) appelant `echo-clean.sh`, évitant ainsi la saturation du disque de la VM.
- **Compilation `echo-python-base:latest` & Throttle Mensuel** : Compile en amont l'image socle mutualisée (`01-config/Dockerfile.python-base`) et applique un verrou de 30 jours (`.last_base_pull`) sur le pull des images `:latest` (`alpine:latest`, `n8nio/n8n:latest`) tout en limitant le parallélisme (`CMAKE_BUILD_PARALLEL_LEVEL=2`).
- **`ensure_volume()` / `ensure_network()`** : Création idempotente des volumes et des sous-réseaux IPAM dynamiques (`echo-network` via `ECHO_NET_MAIN_PREFIX` en `10.20.40.0/24` et `echo-sandbox` via `ECHO_NET_SANDBOX_PREFIX` en `10.20.50.0/24`).
- **`wait_for_docker()`** : Algorithme de blocage asynchrone vérifiant l'état du démon avant déploiement.
- **`ensure_echo_secrets()`** : Appelle la fonction factorisée de `echo-globals.sh` pour générer les clés cryptographiques aléatoires dans `/opt/ECHO/.env` (dont `ECHO_SSO_SECRET`, `ECHO_VAULT_ENCRYPTION_KEY` et `ECHO_ADMIN_PASSWORD` synchronisé avec `.owui-admin-secret`).
- **Provisioning Déclaratif** : Le provisioning d'Open WebUI (modèle, outils, filtres, bannières, settings) est désormais assuré nativement au démarrage du conteneur par son lifespan (`open_webui.echo.bootstrap`), rendant obsolète tout script cURL externe.

### `echo-sync.sh`
**Rôle** : Pont de distribution déclaratif (v5.231.39.1) entre le dépôt source et `/opt/ECHO`.
- **`sync_resource()` & Boucle Déclarative** : Synchronise dynamiquement les répertoires (`00-*` à `30-*`, `_assets`, `docker-python-base`), supprime les fichiers fantômes obsolètes, crée les liens symboliques globaux dans `/usr/local/bin/` (dont `/usr/local/bin/echo-rebuild`), applique un nettoyage conditionnel `sed` des caractères CRLF/BOM (préservant l'horodatage `mtime` et le cache Docker BuildKit), et affiche un encart de versioning Git (Avant/Après).

### Sécurité Périmétrique (`enable-bunkerweb.sh` & `disable-bunkerweb.sh`)
**Rôle** : Bascule de l'infrastructure réseau derrière le WAF BunkerWeb.
- Le script `enable-bunkerweb.sh` (v6.0.0.1) gère l'injection des secrets SSL, sécurise la création IPAM dynamique (`echo-network`, `echo-sandbox`, `bw-universe`), assure la présence de l'image de base partagée (`echo-python-base`), expose les points d'entrée dont l'interface utilisateur `https://my.$DOMAIN` (`echo-user-manager`), et reconfigure les ports via `update_env()` et `ensure_echo_secrets()` (y compris lors d'une sortie idempotente).
- Le script `disable-bunkerweb.sh` (v6.0.0.1) révoque la proxyfication et expose directement les ports d'Open WebUI et de l'Admin Manager (Port 3001).

### Utilitaires
- **`echo-globals.sh` (v6.0.0.2)** : Définition des variables d'environnement globales sourcées par les autres scripts (incluant `ECHO_OWUI_EVENTS="$ECHO_ROOT/owui-events"`, `ECHO_DOCKER_USER="$ECHO_ROOT/docker-user-manager"`, `ECHO_ADMIN_EMAIL`) et factorisation de `generate_secret()` (sans `SIGPIPE` sous `set -euo pipefail`, avec `umask 077` et recouvrement des valeurs vides) et `ensure_echo_secrets()` (`BW_DB_PASSWORD`, `SEARXNG_SECRET`, `ECHO_SSO_SECRET`, `N8N_ENCRYPTION_KEY`, `ECHO_VAULT_ENCRYPTION_KEY`, et synchronisation bidirectionnelle `.owui-admin-secret` <-> `ECHO_ADMIN_PASSWORD`).
- **`echo-clean.sh` (v5.239.1.1)** : Script automatisé de maintenance système journalisé dans `/var/log/echo-clean.log` (purge APT, `journalctl --vacuum-size=100M`, `docker system prune` gradué selon les seuils d'occupation disque — routine 168h, urgence $\ge 85\%$, critique $\ge 90\%$ — et purge quotidienne du Page Cache Linux `sync && echo 1 > /proc/sys/vm/drop_caches`).
- **`echo-logs.sh` (v5.231.42.1) & `echo-ps.sh` (v5.231.42.1)** : Utilitaires de diagnostic rapide en ligne de commande pour le suivi temps-réel des journaux Docker Compose (`logs -f --tail=100` avec `--env-file "$ECHO_ENV_FILE"`) et l'affichage consolidé de l'état des conteneurs de la stack ECHO.
- **`echo-show-admin.sh` (v5.231.39.1)** : Affichage sécurisé dans le terminal des identifiants et des URL d'accès de l'instance pour l'utilisateur.
- **`echo-update.sh` (v6.0.0.1) / `echo-upgrade.sh` (v5.231.39.1)** : Scripts pour le tirage des modifications Git, le redémarrage Hot-Reload des conteneurs labellisés `echo.hot-reload=true` (`echo-update.sh`), ou la mise à niveau majeure (`echo-upgrade.sh`). Lorsqu'il est appelé via `/usr/local/bin/echo-rebuild`, `echo-upgrade.sh` impose une double confirmation stricte (`CONFIRMER` puis `DETRUIRE`) avant de purger les volumes et secrets.

## 3. Dépendances Logiques
- Les scripts sont exécutés majoritairement sur la machine **cible** (Ubuntu), à l'exception du tunnel de synchronisation déclenché depuis l'hôte.
- Les scripts exploitent les fichiers `.env` et les manifestes de `01-config` (Docker Compose).
