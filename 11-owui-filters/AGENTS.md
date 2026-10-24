# 🌌 ECHO Framework - Connaissance Sémantique : `11-owui-filters`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `11-owui-filters`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier représente la **Conscience et l'Injection Contextuelle** de l'agent. Il contient les filtres Open WebUI (Inlet pour les requêtes entrantes, Outlet pour les requêtes sortantes). Leur rôle est d'altérer silencieusement la requête ou de réaliser des tâches asynchrones d'indexation sans bloquer la boucle d'événement.

## 2. Cartographie des Fichiers et Algorithmes

### `build_environment_domain.py` (Inlet - Priorité 0)
**Rôle** : Amorçage à froid de l'Environnement ECHO (v5.226.18.0).
- **Sémantique** : Filtre Inlet critique (priorité absolue 0). S'exécute avant toute logique applicative ou requête LLM.
- **Algorithme Clé** : Il est responsable de la création dynamique et de l'injection complète de l'arborescence du Framework en distinguant les Domaines Globaux (`ECHO_GLOBAL_DOMAINS` via `get_echo_global_path` : `skills`, `files`, `chats`, `n8n_workflow_templates`) et les Domaines de Session (`ECHO_SESSION_DOMAINS` via `get_echo_session_path` : `codex/main`, `codex/sandbox`, `files`, `n8n_workflows`, `dependencies`) dès la première interaction de l'utilisateur avec l'API WebUI. Il applique également un masquage strict (`chmod 0o711`) sur le dossier `files` pour empêcher son exploration directe par le LLM au sein de l'environnement isolé (`bwrap`).

### `conversation_rag_filter.py` (Outlet - Priorité 100)
**Rôle** : Pipeline d'Ingestion Conversationnelle Zéro-Latence (v5.226.18.0).
- **Sémantique** : Filtre asynchrone qui s'exécute après la génération de la réponse. Il découpe l'historique en fenêtres de tours de parole (turns) et les envoie au système d'Embedding (Qdrant).
- **Algorithme Clé** : Implémente l'**Upsert Idempotent Zéro-Latence** avec itération à rebours **O(1)** (anti-Fork Bomb lors du branching) en interrogeant `EchoStateManager.is_message_embedded()` (flag booléen `is_embedded` dans SQLite) *avant* toute communication avec Qdrant, et utilise l'ID du message assistant pour garantir l'indexation lors d'une régénération. Il intègre une gestion anti-zombie explicite des erreurs asynchrones via un callback (pour absorber `asyncio.CancelledError`), utilise un `unique_seed` pour éviter les doublons et bloque fermement les payloads Base64 (images).

### `new_context_filter.py` (Inlet - Priorité 20)
**Rôle** : Smart Context RAG Injector (v5.227.1.1).
- **Sémantique** : Intercepte la requête entrante, attend la libération d'un éventuel verrou de clonage de session (`await wait_for_chat_clone(chat_id)`), orchestre l'ingestion des fichiers attachés (`echo_ingestion.py`) et interroge Qdrant pour récupérer les souvenirs persistants.
- **Algorithme Clé** : Fusion sémantique qui préserve le score `memory_importance`. Assemble le `current_user_multipart_payload` selon l'ordre strict PGCU, et injecte les mémoires, la directive dynamique d'initialisation de session (PIS) ou de montée de version (`<artifact id="AEC_directive">` demandant de notifier succinctement le passage de `last_version` à `version`, persistée via `last_framework_version` dans `echo_settings`), ainsi que les événements système dans la structure **XML native et modulaire** (`EchoAEC`, `<artifact id="AEC_smart_context">`, `<artifact id="AEC_evenement_systeme">`) en associant des `source_id` pour la traçabilité. Intègre un fallback `from pytz import timezone` en cas d'absence de `ZoneInfo`.

### `app_drawer_filter.py` (Inlet - Priorité 1000)
**Rôle** : Interface Homme-Machine Flottante (v5.226.18.0).
- **Sémantique** : Filtre Inlet silencieux. Il n'altère pas la requête LLM mais injecte dans le DOM de l'interface Open WebUI un composant *Data Island* (le HUD ECHO App Drawer). Ce HUD communique en temps réel avec l'API WebUI pour déclencher des actions interactives (ECHO Codex, Agent Monitor) sans rechargement de la page, y compris pendant la génération du modèle. Il intègre le support complet du **HUD Mobile** (déplacement tactile, persistance de la position `echo-hud-top`, et différenciation du seuil tactile `15px` vs souris `3px` pour prévenir l'hyper-sensibilité).

### `edge_embed_bridge_filter.py` (Inlet - Priorité 2)
**Rôle** : Déport de la puissance de calcul (Offload) et WebUI Bridge (v5.226.18.0).
- **Sémantique** : Intercepte les requêtes sur Desktop et déporte l'inférence vectorielle (`onnx-community/harrier-oss-v1-0.6b-ONNX`, quantification `q4f16`, 1024D) vers le navigateur client via WebGPU/WASM et WebSocket (`/ws/edge-embed`).
- **Algorithme Clé** : Applique un **Bypass Serveur-Side sur Mobile** (inspection du header `User-Agent` court-circuitant toute injection JS sur mobile au profit du fallback CPU immédiat). Sur Desktop, injecte un **HUD d'initialisation WebGPU** (décalé horizontalement à `calc(50% + 125px)` pour ne jamais chevaucher la jauge de contexte) avec rechargement automatique de la SPA en cas de mise à jour du script, gère les **onglets multiples** via des `client_id` UUID et la détection de visibilité (`visibility`), et implémente un **Fallback CPU asynchrone instantané** (`sendIncompatibleAndExit`) rejetant proactivement le matériel incompatible.

### `tcp_keepalive_filter.py` (Inlet - Priorité 1)
**Rôle** : Maintien du pool de connexions HTTP/TCP Keep-Alive (v5.226.18.0).
- **Sémantique** : Filtre Inlet injectant un `setInterval` JavaScript (`fetch('/health', { method: 'HEAD', cache: 'no-store', keepalive: true })` toutes les 120s) afin de maintenir actif le pool de connexions HTTP/TCP Keep-Alive du navigateur face aux timeouts des proxys inverses et du WAF BunkerWeb (`proxy_read_timeout`).

### `user_native_context_filter.py` (Inlet - Priorité 10)
**Rôle** : Grounding Utilisateur & Préférences Contextuelles (v5.227.1.1).
- **Sémantique** : Injecte silencieusement dans `body["metadata"]` les préférences de partage du nom (`ENABLE_USER_NAME`) et la surcharge de localisation (`OVERRIDE_LOCATION`).

### `codex_links_filter.py` (Inlet / Outlet - Priorité 30)
**Rôle** : Navigation Interactive & Protocole URN Codex (v5.230.0.1).
- **Sémantique** : Filtre bidirectionnel s'exécutant à la priorité 30 (en Inlet immédiatement après `new_context_filter` à 20, et en Outlet avant `conversation_rag_filter` à 100).
- **Algorithme Clé** :
  - **Inlet** : Injecte silencieusement et de manière idempotente l'écouteur DOM côté client via `EchoEvents.emit_execute` (`window._echoCodexLinkHandlerAttached`). Cet écouteur intercepte tout clic sur les ancres `#codex:<chat_id>:<workspace>:<path>`, prévient la navigation par défaut (`preventDefault`) et déclenche soit le pont RPC existant (`window.sendCodexAction`) si le HUD est actif, soit l'action native (`POST /api/chat/actions/echo_codex_action` avec `window._echoCodexTarget`) si le HUD est fermé.
  - **Outlet** : Sanctuarise les blocs de code multilignes (` ```...``` `) par masquage réversible. Compile dynamiquement une Regex basée sur les clés de `ECHO_CODEX_WORKSPACES` (`main`, `sandbox`) pour capturer les URNs `[label](codex:scope:workspace/path)`. Valide physiquement l'existence du fichier dans l'arbre Git Dulwich (`CodexRepo.read_file()`) selon une politique Fail-Closed absolue : si le fichier existe, l'URN est mutée en ancre navigable `[📄 label](#codex:chat_id:workspace:path)` (ou `[🔒 label]` si multi-chat) ; si le fichier est absent ou fictif (anti-hallucination), le lien est neutralisé au profit du texte brut seul.

## 3. Dépendances Logiques
- Ces filtres interagissent nativement avec le moteur de base de données Qdrant et SQLite.
- Ils manipulent la structure `body` de l'API Open WebUI (FastAPI) et sont chargés de manière asynchrone par l'application.

