# 🌌 ECHO Framework - Connaissance Sémantique : `00-echo-scripts`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `00-echo-scripts`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'intégralité de l'outillage Bash d'infrastructure (Scripts d'Ops). Son rôle est d'orchestrer le déploiement, la configuration post-déploiement, la synchronisation du code depuis l'hôte Windows vers la cible, et la gestion du périmètre de sécurité de l'instance ECHO.

## 2. Cartographie des Fichiers et Algorithmes

### `install-stack.sh`
**Rôle** : Déploiement central de l'architecture Docker (v6.33).
- **`ensure_docker_autosafety()`** : Configure de manière globale le démon Docker (`/etc/docker/daemon.json`) pour imposer une limite de taille des logs (max 10Mo, 3 fichiers) et injecte un cron hebdomadaire (`/etc/cron.weekly/docker-prune`) appelant `clean-echo.sh`, évitant ainsi la saturation du disque de la VM.
- **Compilation `echo-python-base:latest` & Throttle Mensuel** : Compile en amont l'image socle mutualisée (`docker-python-base`) et applique un verrou de 30 jours (`.last_base_pull`) sur le pull des images `:latest` (`alpine:latest`, `n8nio/n8n:latest`) tout en limitant le parallélisme (`CMAKE_BUILD_PARALLEL_LEVEL=2`).
- **`ensure_volume()` / `ensure_network()`** : Création idempotente des volumes et des sous-réseaux IPAM dynamiques (`echo-network` via `ECHO_NET_MAIN_PREFIX` en `10.20.40.0/24` et `echo-sandbox` via `ECHO_NET_SANDBOX_PREFIX` en `10.20.50.0/24`).
- **`wait_for_docker()`** : Algorithme de blocage asynchrone vérifiant l'état du démon avant déploiement.
- **`generate_secret()`** : Générateur cryptographique aléatoire de clés de sécurité (incluant `ECHO_SSO_SECRET` pour le Forward Auth hybride).

### `config-owui.sh`
**Rôle** : Configuration post-déploiement automatisée de l'interface Open WebUI via son API REST locale.
- **`refresh_token()`** : Récupère le jeton JWT d'administration.
- **`api_upsert()`** : Upsert idempotent des modèles (ex: configuration de Harrier-OSS, Flash, Lite).
- **`toggle_state()`** : Active ou désactive des réglages spécifiques dans l'interface OWUI.

### `sync-echo.sh`
**Rôle** : Pont de distribution déclaratif (v4.11) entre le dépôt source et `/opt/ECHO`.
- **`sync_resource()` & Boucle Déclarative** : Synchronise dynamiquement les répertoires (`00-*` à `32-*`), supprime les fichiers fantômes obsolètes, applique un nettoyage conditionnel `sed` des caractères CRLF/BOM (préservant l'horodatage `mtime` et le cache Docker BuildKit), et affiche un encart de versioning Git (Avant/Après).

### Sécurité Périmétrique (`enable-bunkerweb.sh` & `disable-bunkerweb.sh`)
**Rôle** : Bascule de l'infrastructure réseau derrière le WAF BunkerWeb (v4.2).
- Le script `enable-bunkerweb.sh` gère l'injection des secrets SSL, sécurise la création IPAM dynamique (`echo-network`, `echo-sandbox`, `bw-universe`) et reconfigure les ports via `update_env()` et `generate_secret()`.
- Le script `disable-bunkerweb.sh` révoque la proxyfication et expose directement les ports d'Open WebUI et de l'Admin Manager (Port 3001).

### Utilitaires
- **`echo-globals.sh`** : Définition des variables d'environnement globales sourcées par les autres scripts.
- **`clean-echo.sh`** : Script automatisé de maintenance système (purge APT, `journalctl --vacuum-size=100M`, et `docker system prune` gradué selon les seuils d'occupation disque : routine 168h, urgence $\ge 85\%$, critique $\ge 90\%$).
- **`show-echo-admin.sh`** : Affichage sécurisé dans le terminal des identifiants et des URL d'accès de l'instance pour l'utilisateur.
- **`update-echo.sh` (v6.3.1) / `upgrade-echo.sh` (v6.10)** : Scripts pour le tirage des modifications Git, le redémarrage Hot-Reload des conteneurs labellisés `echo.hot-reload=true` (`update-echo.sh`), ou la mise à niveau majeure (`upgrade-echo.sh`). Lorsqu'il est appelé via `/usr/local/bin/rebuild-echo`, `upgrade-echo.sh` impose une double confirmation stricte (`CONFIRMER` puis `DETRUIRE`) avant de purger les volumes et secrets.

## 3. Dépendances Logiques
- Les scripts sont exécutés majoritairement sur la machine **cible** (Ubuntu), à l'exception du tunnel de synchronisation déclenché depuis l'hôte.
- Les scripts exploitent les fichiers `.env` et les manifestes de `01-config` (Docker Compose).
