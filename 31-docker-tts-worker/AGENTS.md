# 🌌 ECHO Framework - Connaissance Sémantique : `31-docker-tts-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `31-docker-tts-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service **TTS Worker** (Text-To-Speech). C'est un microservice isolé dont l'unique mission est de générer de l'audio (synthèse vocale) à partir du texte produit par le modèle IA, de manière totalement asynchrone.

## 2. Cartographie des Fichiers et Algorithmes

### `tts_api.py`
Fichier Python hébergeant le service de synthèse vocale.
- **API Compatible OpenAI** : Le service expose des endpoints HTTP (généralement `/v1/audio/speech`) structurés selon le standard de l'API audio d'OpenAI. Cela permet à Open WebUI d'interagir avec ce service sans aucune modification de son code natif.
- **Streaming Audio** : Il implémente souvent un streaming binaire direct (Chunking) pour permettre la lecture de la voix par l'interface utilisateur avant même que la totalité de la phrase ne soit générée.

### `Dockerfile`
- Build d'une image Docker contenant les moteurs de synthèse vocale (comme `coqui-tts`, `piper`, ou `xtts_v2`). Ces moteurs nécessitant souvent des dépendances lourdes (PyTorch, espeak-ng), ils sont isolés dans ce Tier 2.

## 3. Dépendances Logiques
- Connecté en tant que "Audio Engine" dans les paramètres de l'interface Open WebUI.
- Exécution purement hors ligne garantissant la confidentialité absolue de la voix.
