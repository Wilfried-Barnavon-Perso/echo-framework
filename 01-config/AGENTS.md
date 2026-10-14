# 🌌 ECHO Framework - Connaissance Sémantique : `01-config`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `01-config`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier est le **Centre Névralgique Statique** de l'architecture. Il contient l'ensemble des manifestes déclaratifs (Docker Compose), des fichiers de configuration logicielle (JSON, YAML), et surtout, il héberge l'ADN cognitif du framework (le Kernel Statique). Il dicte l'état désiré de l'infrastructure avant même que les processus dynamiques ne démarrent.

## 2. Cartographie des Fichiers et Algorithmes

### `system-prompt.md` (Le Kernel Statique)
**Rôle** : C'est le cerveau primordial de l'agent.
- **Sémantique** : Il définit le persona, les règles indépassables et les 4 principes cardinaux du système (PCIR pour la réflexion interne, PGCU pour la gestion hiérarchique du contexte, PRAF pour la rigueur factuelle). 
- **Injection** : Il est injecté au démarrage dans l'API Open WebUI pour structurer chaque interaction LLM.

### `stack-echo.yml`
**Rôle** : Le manifeste d'orchestration Docker Compose principal (v9.77).
- **Sémantique** : Définit la topologie du cluster sur 4 Tiers séquentiels (Fondations > Workers > OWUI > Admin).
- **Domain-Driven IPAM & Persistance** : Opère sur deux sous-réseaux isolés (`echo-network` via `${ECHO_NET_MAIN_PREFIX}` et `echo-sandbox` via `${ECHO_NET_SANDBOX_PREFIX}`) avec adressage IPv4 statique (`.100-.103` pour les briques Core et `.20-.32` calqués sur les numéros des dossiers Workers). Monte les volumes nommés (`echo-qdrant-data`, `echo-n8n-data`, `echo_libs_sandbox`, etc.) ainsi que `logging.json` (horodatage ISO 8601) sur l'ensemble des workers Python.
- **Intégration BunkerWeb & Résilience SPA** : Configure sur `echo-open-webui` le Forward Auth (`/api/verify`), la résolution DNS dynamique Nginx (`set $$upstream`), le Smart Proxy Mock sur `/api/version`, l'Auto-Heal SPA par `sub_filter` sur `/auth`, le bounce `/sso-logout` (`Clear-Site-Data: "cache"`), et `USE_MODSECURITY=no` (reposant sur `BAD_BEHAVIOR` pour les codes `405 444` et un Rate Limit `300r/s` burst `1000`).
- **Règles d'Exécution** : Gère les `healthcheck` stricts pour s'assurer que les fondations (Qdrant `v1.19.1`, SearXNG) soient actives avant de lancer les Workers IA et l'ECHO N8N Orchestrator.

### `bunkerweb-stack.yml`
**Rôle** : L'enveloppe de sécurité périmétrique (WAF BunkerWeb v1.6.15, manifeste v1.34).
- **Sémantique** : Ce manifeste secondaire est activé par `enable-bunkerweb.sh` pour proxyfier les requêtes vers Open WebUI (`ui.`), l'Admin Manager (`am.`) et l'IdP `echo-auth-manager` (`auth.`, IP `.24`), adossé à MariaDB `11.4` (`--max-allowed-packet=128M`). Intègre dans l'ancre partagée (`x-bw-config`) les directives critiques `MULTISITE: "yes"`, `AUTO_LETS_ENCRYPT`, `GUNICORN_TIMEOUT: "120"` et l'exclusion `www.example.com_AUTO_LETS_ENCRYPT: "no"`, qui ne doivent **jamais** être répétées sous forme de labels sur des conteneurs dynamiques pour éviter l'effacement par Autoconf.

### `webui-settings.json` & `model-config.json`
**Rôle** : Fichiers d'amorçage (Seed) pour l'interface de chat.
- **Sémantique** : Ils définissent les permissions par défaut, l'activation des outils systémiques, et la taxonomie des modèles (Flash, Pro, Lite) avant même l'initialisation de la base de données SQLite de l'UI.

### `searxng-settings.yml`
**Rôle** : Configuration du moteur de méta-recherche souverain.
- **Sémantique** : Désactive les moteurs bridés, configure les requêtes en JSON pour permettre la consommation automatique des résultats par l'outil LLM de recherche web (`Sovereign Web Search`).

## 3. Dépendances Logiques
- Les manifestes `.yml` de ce dossier sont exploités directement par le dossier `00-echo-scripts` lors de l'exécution de `install-stack.sh`.
- Le contenu de `system-prompt.md` dicte le comportement de l'ensemble des agents IA déclenchés dans `/opt/ECHO/owui-tools/`.
