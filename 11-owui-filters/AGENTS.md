# 🌌 ECHO Framework - Connaissance Sémantique : `11-owui-filters`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `11-owui-filters`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier représente la **Conscience et l'Injection Contextuelle** de l'agent. Il contient les filtres Open WebUI (Inlet pour les requêtes entrantes, Outlet pour les requêtes sortantes). Leur rôle est d'altérer silencieusement la requête ou de réaliser des tâches asynchrones d'indexation sans bloquer la boucle d'événement.

## 2. Cartographie des Fichiers et Algorithmes

### `conversation_rag_filter.py` (Outlet)
**Rôle** : Pipeline d'Ingestion Conversationnelle Zéro-Latence.
- **Sémantique** : Filtre asynchrone qui s'exécute après la génération de la réponse. Il découpe l'historique en fenêtres de tours de parole (turns) et les envoie au système d'Embedding (Qdrant).
- **Algorithme Clé** : Implémente l'**Upsert Idempotent Zéro-Latence**. Pour garantir une performance **O(1)** et empêcher la réindexation redondante, il interroge systématiquement la méthode `EchoStateManager.is_message_embedded()` (qui vérifie le flag booléen `is_embedded` dans SQLite) *avant* d'initier toute communication avec Qdrant. Il utilise également un `unique_seed` pour éviter les doublons et bloque fermement les payloads Base64 (images).

### `new_context_filter.py` (Inlet)
**Rôle** : Smart Context RAG Injector.
- **Sémantique** : Intercepte la requête entrante et interroge Qdrant pour récupérer les souvenirs persistants.
- **Algorithme Clé** : Fusion sémantique qui préserve le score `memory_importance`. Injecte les mémoires récupérées ainsi que les événements systèmes formatés en **YAML plat** (via `_dict_to_yaml_aec` de `echo_utils`) dans la balise native `<smart_context>`, associant des `source_id` pour la traçabilité.

### `app_drawer_filter.py` (Inlet - Priorité 1000)
**Rôle** : Interface Homme-Machine Flottante.
- **Sémantique** : Filtre Inlet silencieux. Il n'altère pas la requête LLM mais injecte dans le DOM de l'interface Open WebUI un composant *Data Island* (le HUD ECHO App Drawer). Ce HUD communique en temps réel avec l'API WebUI pour déclencher des actions interactives (ECHO Codex, Agent Monitor) sans rechargement de la page.

### `edge_embed_bridge_filter.py` (Inlet/Outlet)
**Rôle** : Déport de la puissance de calcul (Offload) et WebUI Bridge.
- **Sémantique** : Intercepte les requêtes nécessitant une vectorisation massive et déporte l'inférence (Harrier-OSS) vers le navigateur client via la technologie WebGPU/WASM et WebSocket.
- **Algorithme Clé** : Injecte un **HUD d'initialisation WebGPU** natif dans l'interface. Gère rigoureusement l'état asynchrone des **onglets multiples** via des `client_id` uniques et la détection de visibilité (`visibility`). Il implémente un mécanisme de **Fallback CPU asynchrone instantané** en annulant les requêtes `asyncio.Future` dès la perte de connexion WebSocket.

### `tcp_keepalive_filter.py` (Inlet)
**Rôle** : Maintien de connexion.
- **Sémantique** : Filtre Inlet dédié au maintien des sessions (Keep-Alive) sur les connexions WebSocket longues, empêchant le WAF (BunkerWeb) de couper silencieusement la connexion.

### `user_native_context_filter.py` (Inlet - Priorité 10)
**Rôle** : Grounding Utilisateur.
- **Sémantique** : Injecte silencieusement le nom, la date, et la localisation de l'utilisateur pour ancrer l'agent dans la réalité temporelle et personnelle de l'utilisateur, protégeant ainsi le moteur système des prompt injections.

## 3. Dépendances Logiques
- Ces filtres interagissent nativement avec le moteur de base de données Qdrant et SQLite.
- Ils manipulent la structure `body` de l'API Open WebUI (FastAPI) et sont chargés de manière asynchrone par l'application.
