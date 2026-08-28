# 🌌 ECHO Framework - Connaissance Sémantique : `23-docker-mcp-broker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `23-docker-mcp-broker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le **MCP Broker** (Model Context Protocol). Il s'agit d'un serveur natif isolé dans son propre conteneur Docker. Son rôle est d'exposer des outils externes sécurisés au modèle via le protocole standardisé MCP sur le port **8000** (StreamableHTTP).

## 2. Cartographie des Fichiers et Algorithmes

### `server.py` & `core/cache_manager.py`
Fichier d'entrée du Broker instanciant le serveur `FastMCP` propulsé par `Uvicorn`.
- **Sémantique ASGI & Proxy** : Le serveur expose ses outils non pas en SSE classique, mais via le protocole natif MCP sur HTTP. Il agit désormais comme un **Proxy HTTP complet** avec gestion du cycle de vie et authentification.
- **WebUI Auth Middleware** : Il intègre un middleware d'authentification pur ASGI conçu pour intercepter et valider silencieusement le JWT WebUI et l'en-tête `x-openwebui-user-id`. Cette interception permet au serveur de propager l'identité sécurisée de l'utilisateur à l'intérieur des appels d'outils (Data Broker, Vault).
- **Cache Manager** : `core/cache_manager.py` permet au Broker de mémoriser les schémas et identifiants temporaires de la session en cours.

### `core/` & `modules/`
Dossiers contenant la logique métier des outils exposés par le Broker.
- **Outils Corporatifs (Corporate Sirene/Bodacc)** : Intégration avec les API d'entreprise pour la récupération légale d'entités.
- **Outils Académiques (Academic)** : Connecteurs vers arXiv ou d'autres bases documentaires pour la recherche scientifique structurée.
- **Omnisearch Jobs** : Mécanismes d'interrogation multi-sources.
- **Remote Proxy (`m5_proxy_mcp.py`)** : [NOUVEAU] Orchestrateur de requêtes MCP distantes. Il gère la transmission JSON, applique un **Error Forwarding natif** (remontant les exceptions transparentes vers le LLM) et sert de Backend pour l'outil `remote_mcp_tool.py`.

## 3. Dépendances Logiques
- Le MCP Broker est déclaré nativement dans l'interface Open WebUI, qui agit comme un **MCP Client**.
- Il se repose sur `ECHO_SESSION_DOMAINS` pour requêter des *Credentials* stockés dans le coffre-fort utilisateur, utilisant l'ID intercepté dans les en-têtes HTTP.
