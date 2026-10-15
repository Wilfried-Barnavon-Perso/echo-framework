# 🌌 ECHO Framework - Connaissance Sémantique : `11-owui-filters`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `11-owui-filters`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier représente la **Conscience et l'Injection Contextuelle** de l'agent. Il contient les filtres Open WebUI (Inlet pour les requêtes entrantes, Outlet pour les requêtes sortantes). Leur rôle est d'altérer silencieusement la requête ou de réaliser des tâches asynchrones d'indexation sans bloquer la boucle d'événement.

## 2. Cartographie des Fichiers et Algorithmes

### `build_environment_domain.py` (Inlet - Priorité 0)
**Rôle** : Amorçage à froid de l'Environnement ECHO.
- **Sémantique** : Filtre Inlet critique (priorité absolue 0). S'exécute avant toute logique applicative ou requête LLM.
- **Algorithme Clé** : Il est responsable de la création dynamique et de l'injection complète de l'arborescence du Framework (dossiers virtuels Codex, RAG, uploads, n8n_workflows) en distinguant les Domaines Globaux (`get_echo_global_path`) et les Domaines de Session (`get_echo_session_path`) dès la première interaction de l'utilisateur avec l'API WebUI. Il applique également un masquage strict (chmod 0o711) sur le dossier `files` pour empêcher son exploration directe par le LLM au sein de l'environnement isolé (bwrap).

### `conversation_rag_filter.py` (Outlet - Priorité 100)
**Rôle** : Pipeline d'Ingestion Conversationnelle Zéro-Latence (v1.14).
- **Sémantique** : Filtre asynchrone qui s'exécute après la génération de la réponse. Il découpe l'historique en fenêtres de tours de parole (turns) et les envoie au système d'Embedding (Qdrant).
- **Algorithme Clé** : Implémente l'**Upsert Idempotent Zéro-Latence** avec itération à rebours **O(1)** (anti-Fork Bomb lors du branching) en interrogeant `EchoStateManager.is_message_embedded()` (flag booléen `is_embedded` dans SQLite) *avant* toute communication avec Qdrant, et utilise l'ID du message assistant pour garantir l'indexation lors d'une régénération. Il intègre une gestion anti-zombie explicite des erreurs asynchrones via un callback (pour absorber `asyncio.CancelledError`), utilise un `unique_seed` pour éviter les doublons et bloque fermement les payloads Base64 (images).

### `new_context_filter.py` (Inlet - Priorité 20)
**Rôle** : Smart Context RAG Injector (v7.60).
- **Sémantique** : Intercepte la requête entrante, orchestre l'ingestion des fichiers attachés (`echo_ingestion.py`) et interroge Qdrant pour récupérer les souvenirs persistants.
- **Algorithme Clé** : Fusion sémantique qui préserve le score `memory_importance`. Assemble le `current_user_multipart_payload` selon l'ordre strict PGCU et injecte les mémoires, la directive dynamique d'initialisation de session (PIS) ou de montée de version (`<artifact id="AEC_directive">` demandant de notifier succinctement le passage de `last_version` à `version`, persistée via `last_framework_version` dans `echo_settings`), ainsi que les événements système dans la structure **XML native et modulaire** (`EchoAEC`, `<artifact id="AEC_smart_context">`, `<artifact id="AEC_evenement_systeme">`) en associant des `source_id` pour la traçabilité. Intègre un fallback `from pytz import timezone` en cas d'absence de `ZoneInfo`.

### `app_drawer_filter.py` (Inlet - Priorité 1000)
**Rôle** : Interface Homme-Machine Flottante (v1.11).
- **Sémantique** : Filtre Inlet silencieux. Il n'altère pas la requête LLM mais injecte dans le DOM de l'interface Open WebUI un composant *Data Island* (le HUD ECHO App Drawer). Ce HUD communique en temps réel avec l'API WebUI pour déclencher des actions interactives (ECHO Codex, Agent Monitor) sans rechargement de la page, y compris pendant la génération du modèle. Il intègre le support complet du **HUD Mobile** (déplacement tactile, persistance de la position `echo-hud-top`, et différenciation du seuil tactile `15px` vs souris `3px` pour prévenir l'hyper-sensibilité).

### `edge_embed_bridge_filter.py` (Inlet - Priorité 2)
**Rôle** : Déport de la puissance de calcul (Offload) et WebUI Bridge (v1.25).
- **Sémantique** : Intercepte les requêtes sur Desktop et déporte l'inférence vectorielle (`onnx-community/harrier-oss-v1-0.6b-ONNX`, quantification `q4f16`, 1024D) vers le navigateur client via WebGPU/WASM et WebSocket (`/ws/edge-embed`).
- **Algorithme Clé** : Applique un **Bypass Serveur-Side sur Mobile** (inspection du header `User-Agent` court-circuitant toute injection JS sur mobile au profit du fallback CPU immédiat). Sur Desktop, injecte un **HUD d'initialisation WebGPU** avec rechargement automatique de la SPA en cas de mise à jour du script, gère les **onglets multiples** via des `client_id` UUID et la détection de visibilité (`visibility`), et implémente un **Fallback CPU asynchrone instantané** (`sendIncompatibleAndExit`) rejetant proactivement le matériel incompatible.

### `tcp_keepalive_filter.py` (Inlet - Priorité 1)
**Rôle** : Maintien de connexion.
- **Sémantique** : Filtre Inlet dédié au maintien des sessions (Keep-Alive) sur les connexions WebSocket longues, empêchant le WAF (BunkerWeb) de couper silencieusement la connexion.

### `user_native_context_filter.py` (Inlet - Priorité 10)
**Rôle** : Grounding Utilisateur.
- **Sémantique** : Injecte silencieusement le nom, la date, et la localisation de l'utilisateur pour ancrer l'agent dans la réalité temporelle et personnelle de l'utilisateur, protégeant ainsi le moteur système des prompt injections.

## 3. Dépendances Logiques
- Ces filtres interagissent nativement avec le moteur de base de données Qdrant et SQLite.
- Ils manipulent la structure `body` de l'API Open WebUI (FastAPI) et sont chargés de manière asynchrone par l'application.
