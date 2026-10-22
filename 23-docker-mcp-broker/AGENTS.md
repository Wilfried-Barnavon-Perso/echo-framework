# 🌌 ECHO Framework - Connaissance Sémantique : `23-docker-mcp-broker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `23-docker-mcp-broker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le **MCP Broker** (Model Context Protocol). Il s'agit d'un serveur natif isolé dans son propre conteneur Docker. Son rôle est d'exposer des outils externes sécurisés au modèle via le protocole standardisé MCP sur le port **8000** (StreamableHTTP).

## 2. Cartographie des Fichiers et Algorithmes

### `server.py` (v5.228.9.2) & `core/cache_manager.py`
Fichier d'entrée du Broker instanciant le serveur `FastMCP` propulsé par `Uvicorn`.
- **Sémantique ASGI & Proxy** : Le serveur expose ses outils via le protocole natif MCP (`streamable-http` sur `/mcp`) ainsi que les routes REST `/schemas`, `/proxy_mcp` et `/health`. Il agit comme un **Proxy HTTP complet** sans accès direct aux bases de données d'Open WebUI.
- **WebUI User Middleware (`WebUIUserMiddleware`)** : Middleware pur ASGI interceptant l'en-tête HTTP `x-openwebui-user-id` (ou fallback `x-echo-user-id`) afin d'injecter l'identifiant utilisateur dans le `ContextVar` `user_id_var` (`core/security.py`).
- **Cache Manager** : `core/cache_manager.py` met en cache les résultats d'exécution des outils par empreinte MD5 (`tool_name` + arguments JSON triés) dans la base SQLite locale `/app/data/mcp_cache.db` (table `cache_entries`).

### `core/` & `modules/`
Dossiers contenant la logique métier des outils exposés par le Broker.
- **`core/security.py`** : Maintient le contexte de sécurité. `security.py` gère l'identification de la requête asynchrone courante (ID utilisateur) via un `ContextVar` injecté par le middleware, servant de support sécurisé au proxy sans connexion directe à une base de données.
- **`modules/m3_corporate.py` (v5.229.1.1)** (Corporate Sirene/Bodacc) : Intégration active (via `httpx`) avec les API d'entreprise de l'Etat pour la récupération légale d'entités (abandon des mocks JSON).
- **`modules/m4_academic.py` (v5.229.1.1)** (Academic) : Connecteurs réels asynchrones (`httpx`) vers arXiv ou d'autres bases documentaires pour la recherche scientifique structurée.
- **`modules/m2_jobs_omnisearch.py` (v5.228.9.4)** (Omnisearch Jobs) : Mécanismes d'interrogation multi-sources (APEC avec pacing humanisé séquentiel et absorption DataDome, LinkedIn avec filtrage dynamique de séniorité `f_E`, France Travail OAuth2 v2 persisté sur volume `echo-mcp-cache`, et Welcome to the Jungle WTTJ via index Algolia officiel et extraction O(1)).
- **Adaptateurs de Fournisseurs Résidents (`modules/m5_providers.py`, v5.229.1.3)** : Isole la logique de cycle de vie spécifique aux fournisseurs MCP résidents derrière l'interface `MCPServiceProvider` (`prepare_env`, `after_list_tools`, `before_tool_call`, `after_tool_call`, `_make_tool_response`) et l'exception partagée `AuthRequiredError` :
  - **`Microsoft365Provider`** : dirige `env["HOME"]` vers `/app/mcp_cache/microsoft_365/<user_id>` (volume `echo-mcp-cache`) avec invalidation `.cred_hash` sur rotation de `MICROSOFT_MCP_CLIENT_ID`/`MICROSOFT_MCP_TENANT_ID`. Dans `after_list_tools`, retire le paramètre `flow_cache` de `complete_authentication`, injecte les outils virtuels `list_mail_folders` (exploration récursive), `trash_email` (corbeille réversible `deleteditems`), `batch_trash_emails` (suppression par lots via Graph `$batch`), `batch_get_emails` (lecture par lot jusqu'à 100 e-mails via `$batch` avec en-tête `Prefer text`, assainisseur anti-HTML `_clean_html_to_text`, troncature sécurisée et stripping des pièces jointes), enrichit `search_emails` en recherche multi-axes (mots-clés KQL, temporalité OData, expéditeur, dossier, statut, tri, `bodyPreview`), patche `list_emails` avec pagination `skip`, tri `order_by` et `bodyPreview`, et appose un avertissement Hard Delete sur `delete_email`. Dans `before_tool_call`, auto-résout les dossiers récursivement (`WELL_KNOWN_PATHS`), arbitre automatiquement entre KQL (`$search`) et OData (`$filter`), injecte l'en-tête serveur `Prefer: outlook.body-content-type="text"`, assainit les corps HTML et capture systématiquement les mutations d'identifiants MAPI post-déplacement en retournant `{old_id, new_id}` (`trash_email`, `move_email`) et `id_mapping` (`batch_trash_emails`). Dans `after_tool_call`, sauvegarde et occulte `_flow_cache` brut de `authenticate_account` dans `.microsoft_mcp_device_flow.json`. Avant tout appel métier, vérifie `.microsoft_mcp_token_cache.json` et résout automatiquement `account_id` (`username` vers `home_account_id`).
  - **`GoogleWorkspaceProvider`** : rafraîchit le jeton OAuth2 Google en mode Fail-Closed dans `prepare_env` (lève immédiatement `AuthRequiredError` en cas d'échec HTTP/`invalid_grant` sans écrire de jeton factice) et l'injecte dans un répertoire temporaire via `WORKSPACE_MCP_CREDENTIALS_DIR`. Dans `after_tool_call`, intercepte toute fuite de flux interactif local (`localhost:8001`, `oauth2callback`, `invalid_grant`, `RefreshError`) pour lever `AuthRequiredError`, évinçant la session corrompue du cache et renvoyant les directives PAS (`user_required_instructions`) au Modèle.
- **Remote Proxy (`modules/m5_proxy_mcp.py`, v5.228.0.1)** : Orchestrateur de requêtes MCP distantes (`sse`, `streamable_http`) ou locales (`stdio`) agnostique des fournisseurs, déléguant les hooks de cycle de vie (`prepare_env`, `after_list_tools`, `before_tool_call`, `after_tool_call`) à `get_service_provider(service)` :
  - **Cache de sessions (`_SessionHolder`)** : une session MCP par couple `(user_id, service)`, détenue par une tâche propriétaire qui ouvre et ferme seule la pile AnyIO. Le démarrage est borné par `MCP_SESSION_START_TIMEOUT` (60 s), la fermeture par `MCP_SESSION_CLOSE_TIMEOUT` (15 s).
  - **`TolerantClientSession`** : surcharge `validate_tool_result` et tolère les erreurs de validation du schéma de sortie (`RuntimeError`).
  - **Sérialisation** : `_dump()` utilise `model_dump(by_alias=True, mode="json", exclude_none=True)` pour restituer les champs en camelCase (`isError`, `structuredContent`).
  - **Éviction** : toute exception (`except BaseException:`, incluant `asyncio.CancelledError` sur timeout HTTP client) entraîne le retrait de la session du cache et sa fermeture protégée par `asyncio.shield(holder.close())`.

### `Dockerfile`
- **Build & Runtime** : Hérite de `echo-python-base:latest`. Embarque **Node.js 22.x LTS** (`setup_22.x`, patché CVE-2026-111497) et Git afin de supporter l'exécution sécurisée de serveurs MCP JavaScript/TypeScript (via `npx` / `node`) et de packages distants.

## 3. Dépendances Logiques
- Le MCP Broker est interrogé par l'outil `12-owui-tools/echo_mcp_tool.py` et l'action `13-owui-actions/echo_identity_vault_action.py` via ses endpoints HTTP REST (`http://echo-mcp-broker:8000/proxy_mcp` et `/schemas`).
- N'ayant aucun accès direct aux bases SQLite d'Open WebUI, il reçoit les *Credentials* (extraits en amont de `identity.db` par `echo_mcp_tool.py` via `EchoStateManager`) directement dans le corps de la requête `/proxy_mcp`, et monte le volume externe `echo-mcp-cache` sur `/app/mcp_cache`.
