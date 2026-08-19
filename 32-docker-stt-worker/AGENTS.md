# 🌌 ECHO Framework - Connaissance Sémantique : `32-docker-stt-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `32-docker-stt-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service **STT Worker** (Speech-To-Text). Il s'agit d'un microservice dédié à la transcription vocale. Son rôle est de recevoir un flux audio (provenant du microphone de l'utilisateur via l'interface Open WebUI) et de le transcrire en texte pur (prompt) pour alimenter le modèle LLM.

## 2. Cartographie des Fichiers et Algorithmes

### `stt_api.py`
Le cœur du service de transcription.
- **API Compatible OpenAI** : Expose un endpoint HTTP (souvent `/v1/audio/transcriptions`) mimant l'API Whisper d'OpenAI. L'interface WebUI s'y connecte nativement sans savoir qu'il s'agit d'un modèle local.
- **Sémantique de Traitement** : Il charge le modèle de reconnaissance vocale en mémoire (souvent via `faster-whisper` ou `whisper.cpp`), traite les buffers audio asynchrones (WAV/WEBM) et renvoie le texte brut ou segmenté avec horodatage.

### `Dockerfile`
- Construit une image Docker optimisée pour l'inférence audio. Ce conteneur nécessite l'installation des dépendances systèmes comme `ffmpeg` (pour la conversion des codecs audio à la volée) et des bibliothèques Python de Deep Learning.

## 3. Dépendances Logiques
- Interagit directement avec le client Web (Open WebUI) qui envoie les requêtes de transcription.
- Tout comme le TTS Worker, il fonctionne hors ligne pour garantir qu'aucune donnée vocale ne fuite vers le cloud.
