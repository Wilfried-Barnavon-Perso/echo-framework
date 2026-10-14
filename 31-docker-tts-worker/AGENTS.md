# 🌌 ECHO Framework - Connaissance Sémantique : `31-docker-tts-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `31-docker-tts-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service **TTS Worker** (Text-To-Speech). C'est un microservice isolé dont l'unique mission est de générer de l'audio (synthèse vocale) à partir du texte produit par le modèle IA, de manière totalement asynchrone.

## 2. Cartographie des Fichiers et Algorithmes

### `tts_api.py` (v1.5)
Fichier Python (FastAPI) hébergeant le service de synthèse vocale sur le port **7996**.
- **Moteur Kokoro ONNX Quantisé** : Utilise `kokoro-v1.0.int8.onnx` (`voices-v1.0.bin`) pour une optimisation drastique de la RAM et du CPU (INT8).
- **Détection Multilingue (`Lingua`) & G2P Hybride Franglais** : Charge `lingua-language-detector` (`LanguageDetectorBuilder` sur 8 langues : FR, EN, ES, PT, IT, JA, ZH, HI) et applique un découpage par phrases (`split_text_into_sentences`) combiné à un parseur G2P hybride (`hybrid_g2p_parse`) pour phonémiser finement les incises anglaises dans un texte français (et inversement) avant d'invoquer `kokoro.create(..., is_phonemes=True)`.
- **Anti-Zombie & Multithreading** : L'inférence Kokoro est isolée via `asyncio.to_thread` pour empêcher le blocage de l'Event Loop (FastAPI). Intègre une annulation explicite de la tâche de génération asynchrone (`generate_audio_task`) et l'arrêt du processus `ffmpeg` lors d'une déconnexion HTTP prématurée. Intègre `RateLimitHealthCheckFilter` (1 log / 5 min sur `/health`).
- **API Compatible OpenAI** : Le service expose l'endpoint HTTP (`/v1/audio/speech`) structuré selon le standard OpenAI, couplé à un streaming MP3 binaire asynchrone direct via un sous-processus `ffmpeg` (`s16le` 24kHz $\rightarrow$ MP3 128k).

### `Dockerfile`
- Build d'une image Docker contenant le moteur de synthèse vocale Kokoro ONNX, ainsi que FFmpeg et espeak-ng. L'installation pip est désormais optimisée en limitant le parallélisme de compilation (`CMAKE_BUILD_PARALLEL_LEVEL=2`).

## 3. Dépendances Logiques
- Connecté en tant que "Audio Engine" dans les paramètres de l'interface Open WebUI.
- Exécution purement hors ligne garantissant la confidentialité absolue de la voix.
