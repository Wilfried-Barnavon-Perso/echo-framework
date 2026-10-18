# 🌌 ECHO Framework - Connaissance Sémantique : `01-config`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `01-config`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier est le **Centre Névralgique Statique** de l'architecture. Il contient l'ensemble des manifestes déclaratifs (Docker Compose), des fichiers de configuration logicielle (JSON, YAML), et surtout, il héberge l'ADN cognitif du framework (le Kernel Statique). Il dicte l'état désiré de l'infrastructure avant même que les processus dynamiques ne démarrent.

## 2. Cartographie des Fichiers et Algorithmes

### `system-prompt.md` (Le Kernel Statique)
**Rôle** : C'est le cerveau primordial de l'agent.
- **Sémantique** : Il définit le persona, les 4 méta-principes indépassables (`MPDI`, `MPAH`, `MPSI`, `MPNR`), les 5 principes opérationnels du système (`PGCU` pour la gestion hiérarchique du contexte incluant le vecteur de continuité cognitive `<artifact id="AEC_context_checkpoint">` en Priorité 5, `PARE` pour l'agentivité et le raisonnement efficients, `PRAF` pour la rigueur analytique et factuelle, `PACP` pour l'alignement préférentiel, `PRAC` pour l'amélioration continue — ces deux derniers suivant une graduation déterministe par itérations vers les niveaux d'importance obligatoires `1 - Trivial` sur 1 occurrence, `2 - Mineur` sur 2 occurrences, `3 - Utile` ou `4 - Majeur` sur 3 occurrences, et `5 - Axiome` sur `Injonction Explicite` ou `Invariant Stratégique`) et les protocoles (`PAS`, `PTD` sur `MODEL_ZEPHYR`, `MODEL_APOLLO`, `MODEL_PROMETHEUS`).
- **Injection** : Il est injecté au démarrage dans l'API Open WebUI pour structurer chaque interaction LLM.

### `stack-echo.yml`
**Rôle** : Le manifeste d'orchestration Docker Compose principal (v9.83).
- **Sémantique** : Définit la topologie du cluster sur 4 Tiers séquentiels (Fondations > Workers > OWUI > Admin). Désactive explicitement le compactage natif d'Open WebUI (`ENABLE_CONTEXT_COMPACTION=false`) au profit du moteur de Checkpoint ECHO.
- **Domain-Driven IPAM & Persistance** : Opère sur deux sous-réseaux isolés (`echo-network` via `${ECHO_NET_MAIN_PREFIX}` et `echo-sandbox` via `${ECHO_NET_SANDBOX_PREFIX}`) avec adressage IPv4 statique (`.100-.103` pour les briques Core et `.20-.30` calqués sur les numéros des dossiers Workers, après suppression des anciens conteneurs `.31`/`.32` au profit du pont audio in-process `edge-tts` dans `echo-open-webui`). Monte les volumes nommés (`echo-qdrant-data`, `echo-n8n-data`, `echo-mcp-cache`, `echo_libs_sandbox`, etc.), les 4 packages Hot-Reload de `echo-admin-manager` (`core`, `services`, `routes`, `templates` en `:ro` + `echo-n8n-data` et `echo-mcp-cache` en `:rw`), ainsi que `logging.json` (horodatage ISO 8601) sur l'ensemble des workers Python.
- **Intégration BunkerWeb & Résilience SPA** : Configure sur `echo-open-webui` le Forward Auth (`/api/verify`), la résolution DNS dynamique Nginx (`set $$upstream`), le Smart Proxy Mock sur `/api/version`, l'Auto-Heal SPA par `sub_filter` sur `/auth`, le bounce `/sso-logout` (`Clear-Site-Data: "cache"`), `bunkerweb.MAX_CLIENT_SIZE=4096m`, et `USE_MODSECURITY=no` (reposant sur `BAD_BEHAVIOR` pour les codes `405 444` et un Rate Limit `300r/s` burst `1000`).
- **Règles d'Exécution** : Gère les `healthcheck` stricts pour s'assurer que les fondations (Qdrant `v1.19.1`, SearXNG) soient actives avant de lancer les Workers IA et l'ECHO N8N Orchestrator.

### `bunkerweb-stack.yml`
**Rôle** : L'enveloppe de sécurité périmétrique (WAF BunkerWeb v1.6.15, manifeste v1.35).
- **Sémantique** : Ce manifeste secondaire est activé par `enable-bunkerweb.sh` pour proxyfier les requêtes vers Open WebUI (`ui.`), l'Admin Manager (`am.`) et l'IdP `echo-auth-manager` (`auth.`, IP `.24`), adossé à MariaDB `11.4` (`--max-allowed-packet=128M`). Intègre dans l'ancre partagée (`x-bw-config`) les directives critiques `DISABLE_DEFAULT_SERVER: "yes"`, `AUTO_LETS_ENCRYPT`, `GUNICORN_TIMEOUT: "120"` et l'exclusion `www.example.com_AUTO_LETS_ENCRYPT: "no"`, tandis que `MULTISITE: "yes"` est injecté dans le bloc `environment:` de `bunkerweb` et `bw-scheduler`. Ces variables globales ne doivent **jamais** être répétées sous forme de labels sur des conteneurs dynamiques pour éviter l'effacement par Autoconf.

### `webui-settings.json` & `model-config.json`
**Rôle** : Fichiers d'amorçage (Seed) pour l'interface de chat.
- **Sémantique** : `model-config.json` déclare le modèle maître unique `echo_orchestrator` (`"name": "Écho"`, `"description": "ECHO - Orchestrateur"`), tandis que `webui-settings.json` épingle `echo_orchestrator` par défaut (`ui.default_models`, `ui.default_pinned_models`, `ui.model_order_list`, `task.model.default`), définit les permissions, désactive le compactage natif d'Open WebUI (`"chat.context_compaction.enable": false`), et configure le routage vers le pont audio in-process (`http://127.0.0.1:8080/echo/v1` : STT sur `MODEL_ZEPHYR`, TTS `edge-tts` sur la voix `fr-FR-VivienneMultilingualNeural` avec `speed: 1.0`, `pitch: -2Hz`) avant même l'initialisation de la base SQLite de l'UI.

### `webui-banners.json`
**Rôle** : Déclaration déclarative des bannières et annonces d'en-tête de l'interface Open WebUI.
- **Sémantique** : Définit la collection ordonnée des bannières affichées aux utilisateurs (`id`, `type`, `title`, `content`, `dismissible`, `timestamp`, `i18n`).
- **Typologies Validées** : Mappe strictement les 4 types acceptés et stylisés par le composant `Banner.svelte` : `info` (badge bleu), `warning` (badge jaune), `error` (badge rouge), `success` (badge vert).
- **Persistance** : Le drapeau `dismissible: true` enregistre l'identifiant fermé dans le `localStorage` du client (`dismissedBannerIds`), tandis que `dismissible: false` limite l'occultation à la session courante en mémoire.

### `searxng-settings.yml`
**Rôle** : Configuration du moteur de méta-recherche souverain.
- **Sémantique** : Désactive les moteurs bridés, configure les requêtes en JSON pour permettre la consommation automatique des résultats par l'outil LLM de recherche web (`Sovereign Web Search`).

### `logging.json` & `Dockerfile.python-base`
**Rôle** : Standardisation des journaux et de l'image socle Python.
- **`logging.json`** : Configuration centralisée du formateur de logs Python (`%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s` au format ISO-8601) montée en lecture seule (`/app/logging.json:ro`) sur les conteneurs workers Python.
- **`Dockerfile.python-base`** : Définit l'image socle mutualisée `echo-python-base:latest` (`FROM python:3.12-slim`) embarquant les dépendances communes (`fastapi`, `uvicorn`, `httpx`, `pydantic`, `requests`) utilisées par les workers `20`, `22`, `23`, `24` et `25`.

## 3. Dépendances Logiques
- Les manifestes `.yml` de ce dossier sont exploités directement par le dossier `00-echo-scripts` lors de l'exécution de `install-stack.sh`.
- Le contenu de `system-prompt.md` dicte le comportement de l'ensemble des agents IA déclenchés dans `/opt/ECHO/owui-tools/`.
