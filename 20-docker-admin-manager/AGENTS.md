# 🌌 ECHO Framework - Connaissance Sémantique : `20-docker-admin-manager`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `20-docker-admin-manager`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient l'application **ECHO Admin Manager**. Il s'agit d'un conteneur Docker isolé (Tier 4) offrant un panneau de contrôle (Dashboard) interactif. Son rôle est de surveiller l'état de santé du cluster Docker, de réguler l'infrastructure de base de données (SQLite/Qdrant) et de gérer les autorisations en temps réel de manière totalement découplée de l'interface de chat Open WebUI.

## 2. Cartographie des Fichiers et Algorithmes

### `server.py`
Fichier monolithique (plus de 160Ko) contenant l'ensemble de la logique serveur (Flask) et le rendu HTML inline du Dashboard.

#### A. Gouvernance & Sécurité
- **Révocations Granulaires & Kill-Switch** : Permet à l'administrateur de forcer l'arrêt de conteneurs (Workers ou OWUI) et de révoquer l'accès aux utilisateurs via la purge ciblée des bases de données d'authentification.
- **Autosafety & Maintenance** : Lance de manière programmatique des commandes de nettoyage (`docker system prune`) ou l'optimisation des bases de données SQLite (requêtes `VACUUM`, `PRAGMA wal_checkpoint`).

#### B. Purge Vectorielle Asynchrone
- **`run_semantic_pruning`** : Algorithme critique d'élagage temporel (TTL) qui purge automatiquement les vecteurs orphelins dans Qdrant et les vieilles sessions SQLite.
- **Multithreading** : Afin de ne jamais bloquer l'interface web Flask, cet élagage est exécuté en arrière-plan via `threading.Thread(target=run_semantic_pruning)`.
- **API `pollTaskStatus()`** : L'interface web (JavaScript) utilise le *long-polling* (`setInterval` à 3000ms) en requêtant `/api/task_status` pour mettre à jour la barre de progression en temps réel pendant que le thread d'élagage tourne.

#### C. Sauvegarde à Chaud (Hot Backups)
- Lance des processus de sauvegarde intégrale des volumes de données (`perform_backup_task`) sans interrompre le fonctionnement des Tiers 1 à 3.

### `Dockerfile` & `requirements.txt`
- Build standard du conteneur en Python (image légère). Installation des dépendances Flask, requêtes HTTP, et client Qdrant.

## 3. Dépendances Logiques
- Le Admin Manager s'exécute sur le port **3001** et monte directement les sockets Docker (`/var/run/docker.sock`) pour interagir avec les autres conteneurs.
- Il partage les volumes `echo-qdrant-data` et `echo-auth-data` afin de pouvoir exécuter ses routines d'optimisation SQLite et Qdrant.
