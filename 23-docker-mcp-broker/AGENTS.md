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
- **`core/security.py`** : Maintient le contexte de sécurité. `security.py` gère l'identification de la requête asynchrone courante (ID utilisateur) via un `ContextVar` injecté par le middleware, servant de support sécurisé au proxy sans connexion directe à une base de données.
- **`modules/m3_corporate.py`** (Corporate Sirene/Bodacc) : Intégration active (via `httpx`) avec les API d'entreprise de l'Etat pour la récupération légale d'entités (abandon des mocks JSON).
- **`modules/m4_academic.py`** (Academic) : Connecteurs réels asynchrones (`httpx`) vers arXiv ou d'autres bases documentaires pour la recherche scientifique structurée.
- **`modules/m2_jobs_omnisearch.py`** (Omnisearch Jobs) : Mécanismes d'interrogation multi-sources (ex: APEC HTML parsing).
- **Remote Proxy (`m5_proxy_mcp.py`, v1.16)** : Orchestrateur de requêtes MCP distantes ou locales (stdio). Il gère la transmission JSON, applique un **Error Forwarding natif** (remontant les exceptions transparentes vers le LLM) et sert de Backend pour l'outil `echo_mcp_tool.py`. Il supporte dynamiquement les transports `sse` et `streamable_http`. Aligné sur `mcp` 2.x :
  - **Cache de sessions & Persistance MSAL (`echo-mcp-cache`)** : une session MCP par couple (utilisateur, service), détenue par une tâche propriétaire (`_SessionHolder`) qui ouvre et ferme seule la pile AnyIO. Pour le service `microsoft_365`, `env["HOME"]` est fixé à `/app/mcp_cache/microsoft_365/<user_id>` (monté sur le volume Docker externe `echo-mcp-cache`) afin de persister le cache de jetons MSAL (`.microsoft_mcp_token_cache.json`) à travers les recréations du conteneur, avec invalidation automatique par empreinte MD5 `.cred_hash` si `MICROSOFT_MCP_CLIENT_ID` ou `MICROSOFT_MCP_TENANT_ID` change dans l'Identity Vault (tandis que `google_workspace` injecte ses jetons OAuth rafraîchis dans un `tempfile.TemporaryDirectory()` via `WORKSPACE_MCP_CREDENTIALS_DIR`). Le démarrage est borné par `MCP_SESSION_START_TIMEOUT` (60 s), la fermeture par `MCP_SESSION_CLOSE_TIMEOUT` (15 s).
  - **`TolerantClientSession`** : surcharge `validate_tool_result` et tolère les erreurs de validation du schéma de sortie (ex : `authenticate_account` de `microsoft-mcp` annoté `dict[str,str]` mais renvoyant un entier). Seule l'exception `RuntimeError` est interceptée.
  - **Sérialisation** : `_dump()` utilise `model_dump(by_alias=True, mode="json", exclude_none=True)` pour restituer les champs en camelCase (`isError`, `structuredContent`).
  - **Éviction** : toute erreur transport ou session entraîne la fermeture et le retrait de la session du cache.

## 3. Dépendances Logiques
- Le MCP Broker est déclaré nativement dans l'interface Open WebUI, qui agit comme un **MCP Client**.
- Il se repose sur `ECHO_SESSION_DOMAINS` pour requêter des *Credentials* stockés dans le coffre-fort utilisateur, utilisant l'ID intercepté dans les en-têtes HTTP, et monte le volume externe `echo-mcp-cache` sur `/app/mcp_cache`.
