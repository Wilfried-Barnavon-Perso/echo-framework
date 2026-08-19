# 🌌 ECHO Framework - Connaissance Sémantique : `30-docker-embedding-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `30-docker-embedding-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service d'**Embedding Worker**. C'est un microservice spécialisé dans la conversion de texte en vecteurs mathématiques (Embeddings) pour l'indexation dans Qdrant (le système RAG). 

## 2. Cartographie des Fichiers et Algorithmes

### `app.py`
Fichier Python hébergeant le service de vectorisation.
- **Stratégie de Fallback (Harrier-OSS / GGUF CPU)** : Dans l'architecture ECHO, le travail de vectorisation est prioritairement déporté vers le client via l'Edge Embedding Bridge (WebGPU/WASM). Si le client ne peut pas traiter la charge, ce Worker prend le relais (Fallback). Il utilise un modèle optimisé (ex: `llama.cpp` ou GGUF CPU) pour générer les vecteurs directement sur le serveur.
- **API Compatible OpenAI** : Le service expose des endpoints HTTP (généralement `/v1/embeddings`) structurés selon le standard de l'API OpenAI, permettant à Open WebUI et aux filtres (comme `conversation_rag_filter.py`) de s'y connecter nativement sans modification de code.

### `Dockerfile` & `requirements.txt`
- Build d'une image Docker contenant les bibliothèques d'inférence ML (`sentence-transformers`, `llama-cpp-python` ou équivalent). Ce conteneur est souvent le plus lourd du Tier 2 en raison des dépendances mathématiques.

## 3. Dépendances Logiques
- Ce Worker est invoqué par les filtres RAG de `11-owui-filters` et par les outils de persistance de `12-owui-tools` (`memory_and_rag_tool.py`).
- Il n'a pas besoin d'accès à Internet ; il travaille de manière totalement *Offline* et souveraine avec son modèle intégré.
