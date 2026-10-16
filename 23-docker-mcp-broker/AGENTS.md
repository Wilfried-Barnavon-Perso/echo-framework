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
- **Adaptateurs de Fournisseurs Résidents (`modules/m5_providers.py`, v1.0)** : Isole la logique de cycle de vie spécifique aux fournisseurs MCP résidents derrière l'interface `MCPServiceProvider` (`prepare_env`, `before_tool_call`, `after_tool_call`) :
  - **`Microsoft365Provider`** : dirige `env["HOME"]` vers `/app/mcp_cache/microsoft_365/<user_id>` (volume `echo-mcp-cache`) avec invalidation `.cred_hash` sur rotation de `MICROSOFT_MCP_CLIENT_ID`/`MICROSOFT_MCP_TENANT_ID`. Sauvegarde le `_flow_cache` brut de `authenticate_account` dans `.microsoft_mcp_device_flow.json` et le restaure avec `expires_at = 0` lors de `complete_authentication` (single-poll MSAL immédiat évitant le blocage de 900 s et la corruption du `device_code` par le LLM). Avant tout appel d'outil métier, vérifie la présence de `.microsoft_mcp_token_cache.json` (court-circuitant l'appel avant que `get_token()` ne lance un Device Code Flow bloquant sur `stdout`) et résout automatiquement `account_id` (email `username` ou compte unique vers `home_account_id`).
  - **`GoogleWorkspaceProvider`** : rafraîchit le jeton OAuth2 Google et l'injecte dans un répertoire temporaire via `WORKSPACE_MCP_CREDENTIALS_DIR`.
- **Remote Proxy (`modules/m5_proxy_mcp.py`, v1.17)** : Orchestrateur de requêtes MCP distantes (`sse`, `streamable_http`) ou locales (`stdio`) agnostique des fournisseurs, déléguant les hooks de cycle de vie à `get_service_provider(service)` :
  - **Cache de sessions (`_SessionHolder`)** : une session MCP par couple `(user_id, service)`, détenue par une tâche propriétaire qui ouvre et ferme seule la pile AnyIO. Le démarrage est borné par `MCP_SESSION_START_TIMEOUT` (60 s), la fermeture par `MCP_SESSION_CLOSE_TIMEOUT` (15 s).
  - **`TolerantClientSession`** : surcharge `validate_tool_result` et tolère les erreurs de validation du schéma de sortie (`RuntimeError`).
  - **Sérialisation** : `_dump()` utilise `model_dump(by_alias=True, mode="json", exclude_none=True)` pour restituer les champs en camelCase (`isError`, `structuredContent`).
  - **Éviction** : toute exception (`except BaseException:`, incluant `asyncio.CancelledError` sur timeout HTTP client) entraîne le retrait de la session du cache et sa fermeture protégée par `asyncio.shield(holder.close())`.

## 3. Dépendances Logiques
- Le MCP Broker est déclaré nativement dans l'interface Open WebUI, qui agit comme un **MCP Client**.
- Il se repose sur `ECHO_SESSION_DOMAINS` pour requêter des *Credentials* stockés dans le coffre-fort utilisateur, utilisant l'ID intercepté dans les en-têtes HTTP, et monte le volume externe `echo-mcp-cache` sur `/app/mcp_cache`.
