# 🌌 ECHO Framework - Connaissance Sémantique : `31-docker-tts-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `31-docker-tts-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service **TTS Worker** (Text-To-Speech). C'est un microservice isolé dont l'unique mission est de générer de l'audio (synthèse vocale) à partir du texte produit par le modèle IA, de manière totalement asynchrone.

## 2. Cartographie des Fichiers et Algorithmes

### `tts_api.py`
Fichier Python hébergeant le service de synthèse vocale.
- **Moteur Kokoro ONNX Quantisé** : Utilise désormais `kokoro-v1.0.int8.onnx` pour une optimisation drastique de la RAM et du CPU (INT8).
- **Découpage Syntaxique** : Implémente un algorithme hybride de découpage par propositions (clauses) préservant la ponctuation, divisant la charge CPU par 10 par rapport à l'ancien découpage par mot de Lingua.
- **Anti-Zombie & Multithreading** : L'inférence est isolée via `asyncio.to_thread` pour empêcher le blocage de l'Event Loop (FastAPI). Intègre une annulation explicite des tâches de génération asynchrones (zombies) lors d'une déconnexion HTTP prématurée.
- **API Compatible OpenAI** : Le service expose des endpoints HTTP (`/v1/audio/speech`) structurés selon le standard OpenAI, couplé à un streaming binaire asynchrone direct via FFmpeg.

### `Dockerfile`
- Build d'une image Docker contenant le moteur de synthèse vocale Kokoro ONNX, ainsi que FFmpeg et espeak-ng. L'installation pip est désormais optimisée en limitant le parallélisme de compilation (`CMAKE_BUILD_PARALLEL_LEVEL=2`).

## 3. Dépendances Logiques
- Connecté en tant que "Audio Engine" dans les paramètres de l'interface Open WebUI.
- Exécution purement hors ligne garantissant la confidentialité absolue de la voix.
