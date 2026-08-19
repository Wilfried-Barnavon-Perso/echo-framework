# 🌌 ECHO Framework - Connaissance Sémantique : `11-owui-filters`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `11-owui-filters`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier représente la **Conscience et l'Injection Contextuelle** de l'agent. Il contient les filtres Open WebUI (Inlet pour les requêtes entrantes, Outlet pour les requêtes sortantes). Leur rôle est d'altérer silencieusement la requête ou de réaliser des tâches asynchrones d'indexation sans bloquer la boucle d'événement.

## 2. Cartographie des Fichiers et Algorithmes

### `conversation_rag_filter.py` (Outlet)
**Rôle** : Pipeline d'Ingestion Conversationnelle Zéro-Latence.
- **Sémantique** : Filtre asynchrone qui s'exécute après la génération de la réponse. Il découpe l'historique en fenêtres de tours de parole (turns) et les envoie au système d'Embedding (Qdrant).
- **Algorithme Clé** : Implémente l'**Upsert Idempotent Zéro-Latence**. Il utilise un `unique_seed` basé sur l'ID du message ou son timestamp pour garantir que le rechargement de la page ne génère pas de doublons dans la base vectorielle. Bloque les payloads Base64 (images).

### `new_context_filter.py` (Inlet)
**Rôle** : Smart Context RAG Injector.
- **Sémantique** : Intercepte la requête entrante et interroge Qdrant pour récupérer les souvenirs persistants.
- **Algorithme Clé** : Fusion sémantique qui préserve le score `memory_importance`. Injecte les mémoires récupérées dans un bloc XML natif `<smart_context>` et associe des `source_id` pour la traçabilité.

### `app_drawer_filter.py` (Inlet - Priorité 1000)
**Rôle** : Interface Homme-Machine Flottante.
- **Sémantique** : Filtre Inlet silencieux. Il n'altère pas la requête LLM mais injecte dans le DOM de l'interface Open WebUI un composant *Data Island* (le HUD ECHO App Drawer). Ce HUD communique en temps réel avec l'API WebUI pour déclencher des actions interactives (ECHO Codex, Agent Monitor) sans rechargement de la page.

### `edge_embed_bridge_filter.py` (Inlet/Outlet)
**Rôle** : Déport de la puissance de calcul (Offload).
- **Sémantique** : Intercepte les requêtes nécessitant une vectorisation massive et déporte l'inférence (Harrier-OSS) vers le navigateur client via la technologie WebGPU/WASM et WebSocket, allégeant ainsi le CPU du serveur.

### `user_native_context_filter.py` (Inlet - Priorité 10)
**Rôle** : Grounding Utilisateur.
- **Sémantique** : Injecte silencieusement le nom, la date, et la localisation de l'utilisateur pour ancrer l'agent dans la réalité temporelle et personnelle de l'utilisateur, protégeant ainsi le moteur système des prompt injections.

## 3. Dépendances Logiques
- Ces filtres interagissent nativement avec le moteur de base de données Qdrant et SQLite.
- Ils manipulent la structure `body` de l'API Open WebUI (FastAPI) et sont chargés de manière asynchrone par l'application.
