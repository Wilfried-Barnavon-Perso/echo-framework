# 🌌 ECHO Framework - Connaissance Sémantique : `00-echo-scripts`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `00-echo-scripts`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'intégralité de l'outillage Bash d'infrastructure (Scripts d'Ops). Son rôle est d'orchestrer le déploiement, la configuration post-déploiement, la synchronisation du code depuis l'hôte Windows vers la cible, et la gestion du périmètre de sécurité de l'instance ECHO.

## 2. Cartographie des Fichiers et Algorithmes

### `install-stack.sh`
**Rôle** : Déploiement central de l'architecture Docker.
- **`ensure_docker_autosafety()`** : Configure de manière globale le démon Docker (`/etc/docker/daemon.json`) pour imposer une limite de taille des logs (max 10Mo, 3 fichiers) et injecte un cron hebdomadaire (`/etc/cron.weekly/docker-prune`) pour l'élagage système, évitant ainsi la saturation du disque de la VM.
- **`ensure_volume()` / `ensure_network()`** : Création idempotente des ressources Docker (réseaux `echo-network`, volumes `echo-qdrant-data`, etc.).
- **`wait_for_docker()`** : Algorithme de blocage asynchrone vérifiant l'état du démon avant déploiement.
- **`generate_secret()`** : Générateur cryptographique aléatoire de clés de sécurité.

### `config-owui.sh`
**Rôle** : Configuration post-déploiement automatisée de l'interface Open WebUI via son API REST locale.
- **`refresh_token()`** : Récupère le jeton JWT d'administration.
- **`api_upsert()`** : Upsert idempotent des modèles (ex: configuration de Harrier-OSS, Flash, Lite).
- **`toggle_state()`** : Active ou désactive des réglages spécifiques dans l'interface OWUI.

### `sync-echo.sh`
**Rôle** : Pont de distribution entre l'hôte Windows (qui édite) et la cible Ubuntu (qui exécute).
- **`sync_resource()`** : Synchronise dynamiquement les répertoires d'action, de filtres, d'outils, et relance les conteneurs si nécessaire.

### Sécurité Périmétrique (`enable-bunkerweb.sh` & `disable-bunkerweb.sh`)
**Rôle** : Bascule de l'infrastructure réseau derrière le WAF BunkerWeb.
- Le script `enable-bunkerweb.sh` gère l'injection des secrets SSL et reconfigure les ports (fermeture de l'accès brut, ouverture du port HTTPS) via `update_env()` et `generate_secret()`.
- Le script `disable-bunkerweb.sh` révoque la proxyfication et expose directement les ports d'Open WebUI et de l'Admin Manager (Port 3001).

### Utilitaires
- **`echo-globals.sh`** : Définition des variables d'environnement globales sourcées par les autres scripts.
- **`show-echo-admin.sh`** : Affichage sécurisé dans le terminal des identifiants et des URL d'accès de l'instance pour l'utilisateur.
- **`update-echo.sh` / `upgrade-echo.sh`** : Scripts pour le tirage des modifications Git et le redéploiement progressif ou majeur de l'architecture.

## 3. Dépendances Logiques
- Les scripts sont exécutés majoritairement sur la machine **cible** (Ubuntu), à l'exception du tunnel de synchronisation déclenché depuis l'hôte.
- Les scripts exploitent les fichiers `.env` et les manifestes de `01-config` (Docker Compose).
