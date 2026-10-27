# 🌌 ECHO Framework - Connaissance Sémantique : `26-docker-n8n-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `26-docker-n8n-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le sous-système **Headless N8N Worker**. Il s'agit d'un conteneur hybride qui embarque à la fois l'orchestrateur d'automatisation *n8n* et une API Python de pilotage (sur le port 5003). Son rôle est d'exécuter, de déployer et de monitorer des workflows automatisés (API, Webhooks, Crons, Scrapers) générés par le LLM.

## 2. Cartographie des Fichiers et Algorithmes

### `n8n_api.py` (v5.231.20.1)
Le contrôleur Python (FastAPI).
- **Sémantique** : Il agit comme une couche d'abstraction (middleware) entre l'outil `n8n_orchestrator_tool.py` exécuté par l'agent et l'API/CLI interne de n8n.
- **Auto-Configuration du Service Account (`get_or_create_n8n_api_key`)** : Protégée par le verrou asynchrone `_N8N_AUTH_LOCK` contre les conditions de course au démarrage, s'authentifie sur `/rest/login` (`emailOrLdapLoginId: "system@echo.local"`), interroge dynamiquement les scopes autorisés via `GET /rest/api-keys/scopes` (compatibilité N8N v2.8.4+ Community Edition), génère la clé maîtresse et extrait le secret non masqué via `rawApiKey`.
- **Rôle Actif & Déploiement en 2 Étapes** : Permet d'exécuter des workflows One-Shot isolés par `execution_id` (`N8N_USER_FOLDER = .n8n_isolated_<execution_id>` avec purge `shutil.rmtree` dans `finally:`) et de déployer des démons permanents (`/deploy`) préfixés par `[ECHO:<user_id>] ` en deux étapes conformes à N8N v2.34+ (`POST /api/v1/workflows` sans champ `active` mais avec `"settings": {}`, suivi de `POST /api/v1/workflows/{id}/activate`). Écrit systématiquement un `deployment_report.json` dans la drop-zone du Download Broker.
- **Garbage Collection (`/prune` & `DELETE /workflow/{id}`)** : Stoppe proprement les triggers actifs via l'API REST puis purge la base SQLite native de N8N (y compris les `staticData` orphelines par Whitelist lors de l'élagage nocturne déclenché par l'Admin Manager). Intègre le filtre `RateLimitHealthCheckFilter` (1 log / 5 min sur `/health`).

### `n8n_architecture.md`
Le manifeste des règles de conception N8N imposées à l'agent.
- **Sémantique** : Ce document définit la distinction radicale entre deux modes :
  1. **Sandbox (Éphémère)** : Workflows de test qui *doivent obligatoirement* démarrer par un nœud `Execute Workflow Trigger` pour pouvoir être déclenchés à la volée par l'API Python avec des payloads de mock.
  2. **Démon (Permanent)** : Workflows autonomes déclenchés par le monde extérieur (Webhook, Cron, Email) sans intervention directe.
- Interdit l'usage de credentials en dur, imposant le passage par le Vault (via des variables injectées).

### `Dockerfile` & `start.sh`
- **Build Multi-Stage Hybride** : Le `Dockerfile` extrait `/usr/local/lib/node_modules/n8n` depuis `n8nio/n8n:latest` (stage `n8n_extractor`) vers une image finale `node:26-alpine` équipée d'un environnement Python 3 / FastAPI (`uvicorn`, `httpx`).
- **`start.sh`** : Script d'amorçage asynchrone lançant le processus principal n8n en tâche de fond et l'API Python (`n8n_api.py`) au premier plan.
- **Volumes Externes (Hot-Reload)** : Les fichiers critiques `n8n_api.py` et `start.sh` sont désormais montés en Read-Only (ro) depuis le volume hôte via `stack-echo.yml`. Cela active le mécanisme de Hot-Reload et accélère considérablement le développement en s'affranchissant de la recompilation du conteneur.

## 3. Dépendances Logiques
- Persistance locale : Ce Worker utilise la base de données **SQLite native** de n8n, montée via le volume Docker `echo-n8n-data`. Il n'utilise *jamais* Postgres.
- Il collabore exclusivement avec l'outil `12-owui-tools/n8n_orchestrator_tool.py` pour étendre les "mains" de l'agent dans le monde réel (SaaS, APIs externes).
