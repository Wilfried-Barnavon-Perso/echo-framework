# 🌌 ECHO Framework - Connaissance Sémantique : `22-docker-browser-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `22-docker-browser-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le code source de l'**API Playwright**. Il s'agit d'un conteneur Docker (Worker) qui héberge un navigateur web *headless* (Playwright) piloté par FastAPI. Son rôle est de naviguer de manière autonome sur internet, d'exécuter des actions DOM complexes (clics, scroll, formulaires) pour le compte de l'outil `navigation_engine_tool.py`, et d'enregistrer le flux vidéo des sessions.

## 2. Cartographie des Fichiers et Algorithmes

### `browser_api.py`
Fichier monolithique (environ 57Ko) exposant l'API de contrôle du navigateur asynchrone (FastAPI).

#### A. Pilotage Asynchrone (Playwright)
- **Sémantique** : Initialise des contextes Playwright (`async_playwright()`). Il crée un onglet par session et isole les cookies et le cache.
- **Endpoints d'Action** : Expose des routes REST (`/navigate`, `/click`, `/type`, `/scroll`, `/extract`) permettant au moteur principal de déclencher des manipulations sur le DOM ciblé.

#### B. Streaming Vidéo Bridé & Rejeu
- **Sémantique** : Enregistre le flux d'interactions de la page.
- **Optimisation FPS** : L'API asynchrone est bridée à **9 FPS** (Frames Per Second) pour réduire drastiquement la charge CPU et la bande passante du serveur lors du traitement et du renvoi du flux vidéo, sans perdre l'intelligibilité de la session pour l'utilisateur.
- Ce flux est ensuite exploitable par le *Cockpit de Rejeu* (HUD `web_navigation_replay_action.py`).

## 3. Dépendances Logiques
- Ce Worker est invoqué exclusivement par la bibliothèque `echo_browser_lib.py` et l'outil `navigation_engine_tool.py`.
- Il fonctionne de manière asynchrone pour ne pas saturer l'Event Loop globale d'Open WebUI. Il s'appuie sur une image Docker massive contenant les dépendances Chromium/Webkit.
