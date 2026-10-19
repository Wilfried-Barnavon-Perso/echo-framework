# 🌌 ECHO Framework - Connaissance Sémantique : `30-docker-embedding-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `30-docker-embedding-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service d'**Embedding Worker**. C'est un microservice spécialisé dans la conversion de texte en vecteurs mathématiques (Embeddings) pour l'indexation dans Qdrant (le système RAG). 

## 2. Cartographie des Fichiers et Algorithmes

### `app.py` (v5.226.18.0)
Fichier Python (FastAPI) hébergeant le service de vectorisation et le concentrateur WebSocket Edge.
- **Hub WebSocket Edge Embedding (`/ws/edge-embed`) & Fallback CPU (`llama.cpp` GGUF)** : Maintient le registre `active_edge_clients` (indexé par `user_id` et `client_id` avec statuts `connecting`, `active`, `incompatible`) et route prioritairement les requêtes de vectorisation vers le navigateur Desktop via des `asyncio.Future` (`pending_edge_requests`). Si aucun client WebGPU actif n'est disponible ou en cas de déconnexion/incompatibilité, bascule instantanément sur l'inférence CPU locale via `llama-cpp-python` (`microsoft/Harrier-OSS-v1-0.6B`, `harrier-oss-v1-0.6b.f16.gguf` dans `stack-echo.yml`, `MAX_LENGTH=4096`) avec **CLS Token Pooling (index 0)** et normalisation L2.
- **API Compatible OpenAI (`/v1/embeddings` & `/health`)** : Expose des endpoints HTTP compatibles OpenAI (1024 dimensions), sérialise l'inférence CPU par un `asyncio.Lock()`, expose l'état Edge par utilisateur dans `/health`, et limite les logs de healthcheck via `RateLimitHealthCheckFilter` (1/5min).

### `Dockerfile` & `requirements.txt`
- Build basé sur `python:3.11-slim` compilant `llama-cpp-python` (`CMAKE_BUILD_PARALLEL_LEVEL=2`) sans PyTorch afin de minimiser l'empreinte RAM (plafonnée à 3 Go dans `stack-echo.yml`).

## 3. Dépendances Logiques
- Ce Worker est invoqué par les filtres RAG de `11-owui-filters` et par les outils de persistance de `12-owui-tools` (`memory_and_rag_tool.py`).
- Il n'a pas besoin d'accès à Internet ; il travaille de manière totalement *Offline* et souveraine avec son modèle intégré.
