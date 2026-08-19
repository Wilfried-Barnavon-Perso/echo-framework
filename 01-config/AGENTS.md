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
**Rôle** : Le manifeste d'orchestration Docker Compose principal.
- **Sémantique** : Définit la topologie du cluster sur 4 Tiers séquentiels (Fondations > Workers > OWUI > Admin).
- **Réseau & Persistance** : Monte les volumes nommés (`echo-qdrant-data`, `echo-n8n-data`, etc.) et orchestre la communication inter-conteneurs sur le réseau bridgé `echo-network`.
- **Règles d'Exécution** : Gère les `healthcheck` stricts pour s'assurer que les fondations (Qdrant, SearXNG) soient actives avant de lancer les Workers IA et l'Orchestrateur N8N.

### `bunkerweb-stack.yml`
**Rôle** : L'enveloppe de sécurité périmétrique (WAF).
- **Sémantique** : Ce manifeste secondaire est activé par `enable-bunkerweb.sh` pour proxyfier les requêtes vers l'Open WebUI (port 80/443), gérant le TLS et filtrant les attaques malveillantes via l'Auth Manager.

### `webui-settings.json` & `model-config.json`
**Rôle** : Fichiers d'amorçage (Seed) pour l'interface de chat.
- **Sémantique** : Ils définissent les permissions par défaut, l'activation des outils systémiques, et la taxonomie des modèles (Flash, Pro, Lite) avant même l'initialisation de la base de données SQLite de l'UI.

### `searxng-settings.yml`
**Rôle** : Configuration du moteur de méta-recherche souverain.
- **Sémantique** : Désactive les moteurs bridés, configure les requêtes en JSON pour permettre la consommation automatique des résultats par l'outil LLM de recherche web (`Sovereign Web Search`).

## 3. Dépendances Logiques
- Les manifestes `.yml` de ce dossier sont exploités directement par le dossier `00-echo-scripts` lors de l'exécution de `install-stack.sh`.
- Le contenu de `system-prompt.md` dicte le comportement de l'ensemble des agents IA déclenchés dans `/opt/ECHO/owui-tools/`.
