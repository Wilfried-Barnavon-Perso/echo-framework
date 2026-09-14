# 🌌 ECHO Framework - Connaissance Sémantique : `21-docker-coding-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `21-docker-coding-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le code source de l'**API d'Exécution Multi-Langages Sécurisée (Coding Worker)**. Il s'agit d'un conteneur Docker isolé du reste du système. Son rôle unique est de recevoir du code (Python, JavaScript/Node.js) généré par le LLM, d'installer dynamiquement ses dépendances (pip, npm), de l'exécuter dans un environnement *Sandbox* restreint, et de renvoyer le résultat (stdout/stderr).

## 2. Cartographie des Fichiers et Algorithmes

### `worker_api.py`
Fichier monolithique exposant une API Flask très légère.
- **Sémantique** : Le serveur écoute sur le port **5000** et expose l'endpoint HTTP POST `/execute`.
- **Exécution Isolée** : Il utilise `subprocess` pour exécuter le payload envoyé, gérant nativement le langage (Python ou Node.js) et les timeouts.
- **Sécurité (Sandbox)** : L'isolation repose sur le fait que ce processus tourne dans son propre conteneur Docker (sans accès aux clés maîtresses ni aux bases de données SQLite/Qdrant de l'infrastructure ECHO).

### `Dockerfile` & `requirements.txt`
- **Build** : Image hybride basée sur Python (`python:3.11-slim`) embarquant également Node.js et npm via les dépôts officiels.
- **Dépendances** : Installe `Flask` pour l'API, ainsi que des librairies d'analyse lourdes fréquemment requêtées par l'agent (`pandas`, `numpy`, `matplotlib`).

## 3. Dépendances Logiques
- Ce Worker est invoqué exclusivement par l'outil LLM `code_executor_tool.py` (situé dans `12-owui-tools`).
- Il n'a aucune persistance d'état : chaque requête d'exécution démarre dans un contexte vierge (hormis les dépendances installées globalement pendant le cycle de vie du conteneur).
