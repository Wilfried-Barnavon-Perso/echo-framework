# 🌌 ECHO Framework - Connaissance Sémantique : `25-docker-download-broker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `25-docker-download-broker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le service **ECHO Download Broker**. C'est un démon asynchrone de surveillance du système de fichiers (Drop-Zone) chargé d'ingérer atomiquement dans le Vault Utilisateur et le Registre SQLite les fichiers produits par le **N8N Worker** ou téléchargés par le **Browser Worker** (Playwright).

## 2. Cartographie des Fichiers et Algorithmes

### `download_broker.py` (v1.0)
Démon Python asynchrone exécutant une boucle de scrutation (`asyncio.sleep(3)`).
- **Garbage Collection des Branches Mortes** : Parcourt `/app/downloads/{uid}/{cid}` et supprime immédiatement (`shutil.rmtree`) tout répertoire de téléchargement dont le dossier de chat correspondant n'existe plus dans le Vault (`/app/backend/data/users/{uid}/chats/{cid}`).
- **Collecte des Flux N8N (`/n8n/{wf_id}/`) & Browser (`/browser/`)** :
  - Applique une **Sécurité MTime (`time.time() - st_mtime >= 3.0s`)** et ignore les fichiers `.part` pour garantir que l'écriture du fichier par N8N ou Playwright est totalement achevée.
  - Déplace atomiquement (`shutil.move`) le fichier vers le Vault Global de l'utilisateur (`/users/{uid}/files/`) avec préfixage UUID anti-collision, et crée un **lien symbolique (`os.symlink`)** dans le dossier `files` du chat.
  - Enregistre la ressource dans la base SQLite de la session via `EchoStateManager.save_resource()` avec le type `binary`, le MIME détecté par `get_gemini_mime()`, et le statut **`pending_ingestion`** (`FILE_INGESTION_STATUS['PENDING_INGESTION']`).

### `Dockerfile` & `requirements.txt`
- Hérite de l'image mutualisée **`FROM echo-python-base:latest`** et monte en lecture seule `/opt/ECHO/owui-libs:/app/backend/echo_libs:ro`.

## 3. Dépendances Logiques
- Consomme les fichiers déposés dans le volume partagé `echo-download-broker-data` (`/app/downloads`) par `echo-browser-worker` et `echo-n8n-worker`.
- Alimente le pipeline d'ingestion (`new_context_filter.py` / `echo_ingestion.py`) via le statut `pending_ingestion` dans SQLite.
