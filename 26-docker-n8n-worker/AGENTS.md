# 🌌 ECHO Framework - Connaissance Sémantique : `26-docker-n8n-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `26-docker-n8n-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le sous-système **Headless N8N Worker**. Il s'agit d'un conteneur hybride qui embarque à la fois l'orchestrateur d'automatisation *n8n* et une API Python de pilotage (sur le port 5003). Son rôle est d'exécuter, de déployer et de monitorer des workflows automatisés (API, Webhooks, Crons, Scrapers) générés par le LLM.

## 2. Cartographie des Fichiers et Algorithmes

### `n8n_api.py`
Le contrôleur Python (FastAPI).
- **Sémantique** : Il agit comme une couche d'abstraction (middleware) entre l'outil `n8n_orchestrator_tool.py` exécuté par l'agent et l'API interne de n8n.
- **Rôle Actif** : Il permet à l'agent IA de pousser des workflows JSON (déploiement), de requêter l'état d'une exécution, de récupérer les logs d'erreurs d'un nœud spécifique, et de purger les exécutions.

### `n8n_architecture.md`
Le manifeste des règles de conception N8N imposées à l'agent.
- **Sémantique** : Ce document définit la distinction radicale entre deux modes :
  1. **Sandbox (Éphémère)** : Workflows de test qui *doivent obligatoirement* démarrer par un nœud `Execute Workflow Trigger` pour pouvoir être déclenchés à la volée par l'API Python avec des payloads de mock.
  2. **Démon (Permanent)** : Workflows autonomes déclenchés par le monde extérieur (Webhook, Cron, Email) sans intervention directe.
- Interdit l'usage de credentials en dur, imposant le passage par le Vault (via des variables injectées).

### `Dockerfile` & `start.sh`
- **Build Hybride** : Le `Dockerfile` part de l'image officielle n8n (Node.js) mais installe un environnement Python 3 en parallèle.
- **`start.sh`** : Script d'amorçage asynchrone lançant le processus principal n8n en tâche de fond et l'API Python (`n8n_api.py`) au premier plan.

## 3. Dépendances Logiques
- Persistance locale : Ce Worker utilise la base de données **SQLite native** de n8n, montée via le volume Docker `echo-n8n-data`. Il n'utilise *jamais* Postgres.
- Il collabore exclusivement avec l'outil `12-owui-tools/n8n_orchestrator_tool.py` pour étendre les "mains" de l'agent dans le monde réel (SaaS, APIs externes).
