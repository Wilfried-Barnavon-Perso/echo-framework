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
**Rôle** : Smart Context RAG Injector (v5.226.18.0).
- **Sémantique** : Intercepte la requête entrante, orchestre l'ingestion des fichiers attachés (`echo_ingestion.py`) et interroge Qdrant pour récupérer les souvenirs persistants.
- **Algorithme Clé** : Fusion sémantique qui préserve le score `memory_importance`. Assemble le `current_user_multipart_payload` selon l'ordre strict PGCU, sépare `<artifact id="AEC_context_checkpoint">` de `<REQUETE_UTILISATEUR>`, compense `tour_conversation` via l'offset `_echo_compacted_user_turns` fourni par `context_compaction_filter.py`, et injecte les mémoires, la directive dynamique d'initialisation de session (PIS) ou de montée de version (`<artifact id="AEC_directive">` demandant de notifier succinctement le passage de `last_version` à `version`, persistée via `last_framework_version` dans `echo_settings`), ainsi que les événements système dans la structure **XML native et modulaire** (`EchoAEC`, `<artifact id="AEC_smart_context">`, `<artifact id="AEC_evenement_systeme">`) en associant des `source_id` pour la traçabilité. Intègre un fallback `from pytz import timezone` en cas d'absence de `ZoneInfo`.

### `context_compaction_filter.py` (Inlet - Priorité 15)
**Rôle** : Rejeu des Checkpoints Cognitifs et Compactage Automatique In-Place (v5.226.18.0).
- **Sémantique** : S'exécute entre `user_native_context_filter.py` (priorité 10) et `new_context_filter.py` (priorité 20).
- **Algorithme Clé** : Rejoue systématiquement tout Checkpoint actif (`compaction_state` dans `echo_settings` de `session.db`) en tronquant les tours antérieurs au `pivot_msg_id`, en injectant `<artifact id="AEC_context_checkpoint">` en tête du message pivot, en renseignant `_echo_compacted_user_turns` / `_echo_active_pivot_msg_id` dans `body["metadata"]` et en émettant le statut UI `🗜️ Checkpoint Cognitif #{cp_num} actif ({evicted_count} tours condensés)`. Si la `UserValve` `ENABLE_AUTO_COMPACTION` est activée, que la fenêtre comporte plus de `ECHO_COMPACTION_MIN_TURNS_TO_KEEP` (`2`) tours et que les tokens actifs atteignent `45%` (`CONTEXT_COMPACTION_TRIGGER_RATIO = 0.45`, soit `450 000` tokens), invoque `EchoCompactionEngine.execute_compaction()` via `MODEL_APOLLO` (`max_tokens=32768`) pour redescendre à `25%` (`CONTEXT_COMPACTION_TARGET_RATIO = 0.25`, soit `250 000` tokens).

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
**Rôle** : Grounding Utilisateur & Préférences Contextuelles (v5.226.18.0).
- **Sémantique** : Injecte silencieusement dans `body["metadata"]` les préférences de partage du nom (`ENABLE_USER_NAME`), la surcharge de localisation (`OVERRIDE_LOCATION`) et l'état d'activation du compactage contextuel automatique (`ENABLE_AUTO_COMPACTION`, désactivé par défaut via `ECHO_COMPACTION_ENABLED_DEFAULT = False`).

## 3. Dépendances Logiques
- Ces filtres interagissent nativement avec le moteur de base de données Qdrant et SQLite.
- Ils manipulent la structure `body` de l'API Open WebUI (FastAPI) et sont chargés de manière asynchrone par l'application.
