# 🌌 ECHO Framework - Connaissance Sémantique : `21-docker-python-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `21-docker-python-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le code source de l'**API d'Exécution Python Sécurisée**. Il s'agit d'un conteneur Docker (Worker) isolé du reste du système. Son rôle unique est de recevoir du code Python généré par le LLM, de l'exécuter dans un environnement *Sandbox* restreint, et de renvoyer le résultat (stdout/stderr).

## 2. Cartographie des Fichiers et Algorithmes

### `worker_api.py`
Fichier monolithique exposant une API Flask très légère.
- **Sémantique** : Le serveur écoute sur le port **5000** et expose l'endpoint HTTP POST `/execute`.
- **Exécution Isolée** : Il utilise la fonction `exec()` ou `subprocess` pour exécuter le payload envoyé sous la clé JSON `{"code": "print('hello')"}`.
- **Sécurité (Sandbox)** : L'isolation repose sur le fait que ce processus tourne dans son propre conteneur Docker sans accès aux clés maîtresses ni aux bases de données SQLite/Qdrant de l'infrastructure ECHO. 

### `Dockerfile` & `requirements.txt`
- **Build** : Image basée sur Python (souvent `python:3.11-slim`).
- **Dépendances** : Installe `Flask` pour l'API, ainsi que des librairies d'analyse lourdes fréquemment requêtées par l'agent (`pandas`, `numpy`, `matplotlib`) pour l'exécution d'algorithmes mathématiques ou d'analyse de données en toute sécurité.

## 3. Dépendances Logiques
- Ce Worker est invoqué exclusivement par l'outil LLM `python_code_executor.py` (situé dans `12-owui-tools`).
- Il n'a aucune persistance d'état : chaque requête d'exécution démarre dans un contexte vierge.
