# 🌐 16-docker-echo-webui - Serveur & Interface Natifs ECHO V6

> **Source de Vérité Locale** : Ce document définit le rôle, la gouvernance technique, les invariants d'intégration et le cycle de maintenance du composant `16-docker-echo-webui`.

---

## 1. 🎯 Rôle & Architecture du Composant

Le répertoire `16-docker-echo-webui` héberge l'arborescence complète du serveur FastAPI et de l'interface SvelteKit native d'ECHO V6. Il remplace définitivement l'image Docker externe `ghcr.io/open-webui/open-webui`.

Cette intégration native permet :
- La compilation locale et le build optimisé du frontend SvelteKit (`src`, `static`).
- L'exécution directe et la personnalisation chirurgicale du backend FastAPI (`backend`).
- L'éradication des dépendances externes au runtime et des divergences de cycle de vie en production.

---

## 2. 🔒 Référence Amont & Veille Différentielle

La base de code de ce composant est verrouillée sur une version amont d'Open WebUI référencée dans [`UPSTREAM_LOCK.json`](file:///D:/GitHub/echo-v6/16-docker-echo-webui/UPSTREAM_LOCK.json) :
- **Dépôt amont :** `https://github.com/open-webui/open-webui.git`
- **Tag de base :** `v0.13.2`
- **Commit SHA :** `b7a005e5a5c0c0d869f2aebf4b8f051c5fb37245`
- **Date d'extraction :** `2026-11-08`

### Protocole de Veille & Rebase
- Le sous-répertoire `.tmp_audit/open-webui` sert de miroir amont pour les audits de nouveautés et les comparaisons de code.
- Le script `check-owui-news.ps1` orchestre la veille différentielle continue vis-à-vis des nouvelles releases et commits amont.
- Toute intégration de correctif ou d'évolution amont doit être tracée et documentée en respectant la liste des chemins suivis (`tracked_paths`).

---

## 3. ⚓ Invariants Inter-Services Obligatoires

Le conteneur bâti depuis ce composant s'insère au cœur de l'architecture multi-services d'ECHO V6. Les contrats d'interface suivants sont des invariants stricts :

1. **Réseau & Service :**
   - Nom de service : `echo-open-webui`.
   - Assignation réseau interne : `172.28.0.100:8080` (réseau `echo-net`).
2. **Persistance des Données :**
   - Montage du volume persistant : `echo-webui-data:/app/backend/data`.
3. **Schéma SSoT `webui.db` :**
   - La base SQLite `/app/backend/data/webui.db` demeure la source unique de vérité partagée pour les entités clés :
     - `user` (identités utilisateurs)
     - `auth` (credentials et sessions)
     - `chat` (fils conversationnels et métadonnées)
     - `folder` (organisation hiérarchique)
     - `tag` (taxonomie et étiquetage)
     - `file` (registre des métadonnées de fichiers)
     - `config` (configurations applicatives persistantes)
     - `calendar` (gestion calendaire)
     - `automation` (définition des flux automatisés)
   - Ces tables sont interrogées et manipulées directement par les microservices satellites :
     - `20-docker-admin-manager`
     - `24-docker-echo-auth-manager`
     - `27-docker-user-manager`
     - `generalist_tools.py`

---

## 4. 🔌 Pont d'Intégration & Moteur Natif ECHO V6 (`backend/open_webui/echo/`)

Le backend s'interface nativement avec les briques cognitives et fonctionnelles du framework ECHO via le package compartimenté `backend/open_webui/echo/` :

### 4.1 Modules Natifs
- **`__init__.py` (v6.0.0.1) :** Compartimentation modulaire souveraine d'ECHO V6 au sein d'Open WebUI.
- **`runtime.py` (v6.0.0.1) :** Chargeur direct sur disque des Plugins (Pipes, Filters, Tools, Actions, Events) depuis `/opt/ECHO/owui-*` via `importlib.util.spec_from_file_location`. Élimine l'écriture en fichier temporaire éphémère (`NamedTemporaryFile`) et `os.unlink`, garantissant des stacktraces Python natives. Maintient un cache mémoire indexé sur `st_mtime_ns` pour le hot-reload instantané sans redémarrage de conteneur, avec resynchronisation automatique des schémas OpenAPI (`specs`) des Tools en base SQLite.
- **`bootstrap.py` (v6.0.0.1) :** Moteur de provisioning déclaratif asynchrone exécuté dans le `lifespan` FastAPI. Remplace intégralement le script externe `config-owui.sh` :
  - Création/résolution de l'administrateur propriétaire (`WEBUI_ADMIN_EMAIL`, `WEBUI_ADMIN_PASSWORD`).
  - Purge automatique des ghosts et synchronisation déclarative des Tools (`owui-tools`) et Functions (`owui-pipes`, `owui-filters`, `owui-actions`, `owui-events`) avec attribution des `AccessGrants`.
  - Enregistrement déclaratif du modèle maître `echo_orchestrator` synchronisé avec `system-prompt.md` et `logo-echo.png` (Data-URI base64).
  - Injection directe de `webui-settings.json` et `webui-banners.json` via `Config.upsert` et `initialize_runtime_config(app)`.
- **`identity_bridge.py` (v6.1.0.1) :** Pont déterministe Email SSO (`X-Webui-User`) $\leftrightarrow$ User UUID (`webui.db`) $\leftrightarrow$ `identity.db` (Schéma v2). Matérialise de manière idempotente `/app/backend/data/users/{user_id}/identity.db` avec ses 4 tables et répertoires globaux (`files`, `skills`), et gère le cache mémoire de synchronisation `_IDENTITY_SYNC_CACHE`.
- **`storage.py` (v6.1.0.1) :** Fournisseur de stockage unifié `EchoStorageProvider(LocalStorageProvider)` écrivant directement dans l'arborescence souveraine ECHO (`/users/{uid}/chats/{cid}/files/{filename}` si `chat_id` présent avec miroirs global et `UPLOAD_DIR`, ou `/users/{uid}/files/{filename}` hors chat) et indexant immédiatement le fichier dans `echo_resources` (`session.db` et/ou `identity.db`) avec `status="pending_ingestion"`. Gère la suppression physique multizone et l'élagage dans `echo_resources`.
- **`routers/skills.py` (v6.2.0.1) :** Routeur FastAPI souverain pour la gestion CRUD des Skills ECHO (`/echo/v1/skills` et `/api/v1/skills`), délégant à `echo_skills.py` et protégé par le verrou `get_user_skill_lock(user.id)`.
- **`routers/meta_artifacts.py` (v6.2.0.1) :** Routeur FastAPI souverain pour les Méta-Artéfacts Qdrant (`/echo/v1/meta-artifacts`), avec scroll, recherche sémantique/textuelle, promotion en axiome et vectorisation native 1024-d.
- **`routers/wm.py` (v6.3.0.1) :** Routeur FastAPI souverain pour le gestionnaire de fenêtres (`/echo/v1/wm`), assurant le streaming SSE (`/stream`), la restitution de snapshot enrichi de `context_stats` (`/chat/{chat_id}`) et le traitement des retours RPC (`/rpc`).
- **`routers/cognition.py` (v6.3.0.1) :** Routeur FastAPI souverain pour l'observabilité cognitive et multi-agents (`/echo/v1/cognition`), exposant en lecture seule (`mode=ro`) l'instantané (`/{chat_id}/snapshot`) et le flux SSE réactif (`/{chat_id}/stream`) des étapes de délibération des sous-agents (`cognitive_threads`) et du journal d'appels d'outils (`call_bridge`), sans matérialisation de `session.db` orpheline.
- **`routers/codex.py` (v6.3.0.1) :** Routeur FastAPI souverain pour les dépôts Codex et conteneurs de session (`/echo/v1/codex`), interfaçant `CodexRepo` (Git Dulwich) sur les workspaces (`main`, `plans`, `generated`, `sandbox`) et le stockage `files`, avec contrôle strict des permissions d'écriture (`is_codex_workspace_generic_writable`), arborescence (`/tree`), lecture (`/file`), édition (`/file`), suppression (`/file`), historique (`/log`), diff unifié (`/diff`) et téléchargement direct sécurisé (`/download`).
- **`routers/vault.py` (v6.3.0.1) :** Routeur FastAPI souverain pour le coffre de secrets chiffrés et la gestion d'identité (`/echo/v1/vault`), exposant le statut des clés et quotas masqués (`/status`), le registre des comptes chiffrés (`/secrets`), la persistance AES-256-GCM (`PUT /secrets`) et la suppression (`DELETE /secrets/{service}/{account_id}`) sans jamais divulguer de valeur en clair.

### 4.2 Points d'Ancrage Amont (`# [ECHO-V6-HOOK]`)
Dix-sept points d'ancrage minimaux et isolés sont insérés dans le code amont d'Open WebUI :
1. **`backend/open_webui/config.py` :** `VECTOR_DB` par défaut sur `'qdrant'` et protection `ImportError` sur `chromadb`.
2. **`backend/open_webui/utils/plugin.py` :** Routage direct vers `load_echo_tool_from_disk` et `load_echo_function_from_disk` dans `load_tool_module_by_id`, `load_function_module_by_id`, `get_tool_module_from_cache` et `get_function_module_from_cache`.
3. **`backend/open_webui/main.py` :** Déclenchement de `await bootstrap_echo_runtime(app)` lors de la phase de démarrage `lifespan`, désactivation des routeurs superflus (`ollama`, `channels`, `notes`, `models`, `knowledge`, `memories`, `evaluations`) et montage des routeurs souverains ECHO (`skills`, `meta-artifacts`, `wm`, `cognition`, `codex`, `vault`).
4. **`backend/open_webui/utils/middleware.py` :** Fusion serveur native de `model.info.meta.toolIds` dans `tool_ids` avant exécution, résolvant définitivement à la racine l'anomalie `[BUG-OWUI-01]`.
5. **`backend/open_webui/utils/auth.py` :** Résolution canonique déterministe `resolve_canonical_user(request, user)` dans `get_current_user` (JWT) et `get_current_user_by_api_key`.
6. **`backend/open_webui/routers/auths.py` :** Matérialisation initiale et idempotente de `identity.db` via `ensure_user_identity_db` lors de la connexion (`signin`) et de la création de compte (`signup_handler`).
7. **`backend/open_webui/storage/provider.py` :** Substitution de `LocalStorageProvider` par `EchoStorageProvider`.
8. **`backend/open_webui/routers/files.py` :** Enrichissement contextuel des tags d'upload (`Echo-Chat-Id`, `Echo-Message-Id`, `Echo-Original-Name`, `Echo-Mime-Type`).
9. **`src/lib/apis/files/index.ts` :** Auto-injection du `chat_id` actif depuis l'URL SPA `/c/{chat_id}` dans les métadonnées de requête d'upload.
10. **`src/lib/components/layout/Sidebar.svelte` :** Épuration du menu latéral (masquage de Notes, Channels, Playground ; maintien de Workspace, Automations, Calendar).
11. **`src/routes/(app)/workspace/+layout.svelte` :** Remplacement des onglets natifs par les 3 onglets souverains V6 (`Skills`, `Méta-Artéfacts`, `Prompts`).
12. **`src/lib/components/chat/ModelSelector.svelte` :** Verrouillage réactif strict sur le modèle souverain `Écho` (`echo_orchestrator`).
13. **`src/app.html` :** Éradication de `/static/loader.js` et des attributs `crossorigin="use-credentials"` incompatibles avec Service Worker et BunkerWeb.
14. **`src/lib/stores/echo/index.ts` :** Stores réactifs souverains pour la jauge contextuelle (`echoContextGauge`), les sous-agents (`echoCognitionActiveCount`), les applications Canvas (`echoCanvasFrames`, `echoCanvasOpenRequest`) et l'inférence WebGPU (`echoEdgeEmbedState`).
15. **`src/lib/components/echo/` :** Implémentation des HUDs (`EchoContextGauge` monté dans `Navbar.svelte`, `EchoEdgeEmbedding` monté dans `(app)/+layout.svelte` avec keep-alive TCP natif) et des 4 onglets de session (`EchoCognitionTab`, `EchoCodexTab`, `EchoVaultTab`, `EchoCanvasTab`).
16. **`src/lib/components/chat/ChatTabs/` :** Intégration des 4 kinds d'onglets dans `tabs.ts` et `ChatTabs.svelte` avec support Split View et désactivation stricte de `canSideChat = false`.
17. **`src/lib/components/chat/Chat.svelte` :** Forçage de `sideChatAllowed = false` sous `# [ECHO-V6-HOOK]`, garantissant la stricte linéarité de l'arbre conversationnel.
18. **`src/routes/(app)/+layout.svelte` & `ChatTabs.svelte` :** Auto-sélection déterministe du terminal serveur souverain `echo-coding-worker` dans `$selectedTerminalId` dès le chargement de `$terminalServers`, activant en un clic le terminal PTY interactif Xterm.js dans `ChatTabs`.

### 4.3 Dégraissage & Dépendances
- **Maintien de `USE_SLIM=false` :** Préservation obligatoire pour garantir le support des parsers de documents locaux (PDF, DOCX via `pypdf`, `pandoc`), `pydub` et les wheels Pyodide MPSI locaux sans dépendance de CDN externe.
- **Purge ML/CUDA/VectorDB :** Suppression de 16 bibliothèques lourdes (`chromadb`, `weaviate-client`, `opensearch-py`, `transformers`, `sentence-transformers`, `accelerate`, `einops`, `unstructured`, `preshed`, `opencv-python-headless`, `rapidocr`, `onnxruntime`, `faster-whisper`, `pymilvus`, `playwright`, `elasticsearch`, `pinecone`, `oracledb`, `colbert-ai`).
- **Dépendances Natives ECHO V6 :** Intégration directe au build des 9 dépendances nécessaires aux opérations système et réseau (`asyncssh`, `pybase64`, `mgzip`, `dulwich`, `h2`, `edge-tts`, `markitdown[all]`, `imageio-ffmpeg`, `filetype`).

---

## 5. 📂 Cartographie des Composants ECHO Raccordés

- **`10-owui-pipes` :** Pipeline d'orchestration cognitive et cortex LLM (`echo_orchestrator.py`).
- **`11-owui-filters` :** Chaîne de filtres d'entrée et de sortie (`new_context_filter.py`, `window_manager_filter.py`, `context_gauge_filter.py`, etc.).
- **`12-owui-tools` :** Outils métier et fonctions d'outillage agents (`agent_orchestration_tool.py`, `memory_and_rag_tool.py`, etc.).
- **`13-owui-actions` :** Déclencheurs d'actions utilisateur et intégrations d'interface.
- **`14-owui-libs` :** Bibliothèques partagées, protocoles et gestionnaires d'état (`echo_core.py`, `echo_state_manager.py`, `echo_window_manager.py`, etc.).
- **`15-owui-events` :** Gestionnaire unifié d'événements et de diffusion (`echo_events.py`).
