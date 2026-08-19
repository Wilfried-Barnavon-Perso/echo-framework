# 🌌 ECHO Framework - Connaissance Sémantique : `25-docker-download-broker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `25-docker-download-broker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service **ECHO Download Broker**. C'est un microservice isolé chargé de centraliser et d'orchestrer la collecte asynchrone des téléchargements de fichiers générés par le LLM ou récupérés lors d'une session de navigation web. 

## 2. Cartographie des Fichiers et Algorithmes

### `download_broker.py`
Fichier Python léger exposant un démon asynchrone.
- **Service de Collecte Asynchrone** : Il intercepte les requêtes de téléchargements et gère les flux réseau vers le stockage local (volume monté) de l'utilisateur. 
- **Prévention du Blocage** : En déléguant le téléchargement de fichiers potentiellement volumineux à ce courtier dédié (broker), Open WebUI et les autres Workers (comme le Browser Worker) évitent d'être bloqués sur des I/O réseau lentes.
- **Nettoyage Automatique** : Intègre des routines de nettoyage temporaire pour éviter l'accumulation de fichiers orphelins dans les dossiers de cache.

### `Dockerfile` & `requirements.txt`
- Build minimaliste d'une image Python contenant `aiohttp` ou `fastapi` pour la gestion hautement concurrente des téléchargements.

## 3. Dépendances Logiques
- Principalement exploité par l'outil de navigation web (`navigation_engine_tool.py`) et l'orchestrateur de fichiers.
- S'intègre dans le réseau Docker via le nom de service `echo-download-broker`.
