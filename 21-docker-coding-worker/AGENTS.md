# 🌌 ECHO Framework - Connaissance Sémantique : `21-docker-coding-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `21-docker-coding-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le code source de l'**API d'Exécution Multi-Langages Sécurisée (Coding Worker)**. Il s'agit d'un conteneur Docker isolé du reste du système. Son rôle unique est de recevoir du code (Python, JavaScript/Node.js) généré par le LLM, d'installer dynamiquement ses dépendances (pip, npm), de l'exécuter dans un environnement *Sandbox* restreint, et de renvoyer le résultat (stdout/stderr).

## 2. Cartographie des Fichiers et Algorithmes

### `worker_api.py`
Fichier monolithique exposant une API Flask très légère.
- **Sémantique** : Le serveur écoute sur le port **5000** et expose les endpoints HTTP `/execute`, `/kill`, `/poll_monitor` et `/update_payload`.
- **Exécution Isolée & Temps Réel (Bidi Polling)** : Utilise `subprocess.Popen` + `multiprocessing` pour exécuter le payload de façon non-bloquante. Le système implémente un "Pipe Bidirectionnel" en exploitant la porosité du *Bind Mount* de Bubblewrap sur `/sandbox` : le parent lit `.echo_monitor.jsonl` (Flux Remontant) et met à jour atomiquement `.echo_ui_payload.json` (Flux Descendant) via `os.replace`.
- **Sécurité (Anti-Zombie & Sandbox)** : Isolation totale via `bwrap`. Intègre le tracking par PID dans le registre `_active_sandboxes` permettant la destruction de processus orphelins (`os.kill`) via la route `/kill`.

### ECHO Sandbox Monitor (`echo_monitor`)
- **Sémantique** : La librairie native `echo_monitor` permet au modèle de transmettre des interfaces HTML/JS complexes (Data Islands) vers Open WebUI.
- **Canaux Bidirectionnels Temps-Réel** : Contrairement aux anciennes versions, l'Agent peut lire dynamiquement (en boucle) les payloads émis par l'UI via `get_ui_payload()` grâce au multiplexage frontal (`postMessage`).
- **Multiplexage** : Elle exploite le fichier `/sandbox/.echo_monitor.jsonl` en mode "append" (Multiplexage) pour gérer un fenêtrage multiple simultané (paramètre `window_id`).

### `Dockerfile` & `requirements.txt`
- **Build** : Image hybride basée sur Python (`python:3.11-slim`) embarquant également Node.js et npm via les dépôts officiels.
- **Dépendances** : Installe `Flask` pour l'API, ainsi que des librairies d'analyse lourdes fréquemment requêtées par l'agent (`pandas`, `numpy`, `matplotlib`).

## 3. Dépendances Logiques
- Ce Worker est invoqué exclusivement par l'outil LLM `code_executor_tool.py` (situé dans `12-owui-tools`).
- Il n'a aucune persistance d'état : chaque requête d'exécution démarre dans un contexte vierge (hormis les dépendances installées globalement pendant le cycle de vie du conteneur).
