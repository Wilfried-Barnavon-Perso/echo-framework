# 🌌 ECHO Framework - Connaissance Sémantique : `21-docker-coding-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `21-docker-coding-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le code source de l'**API d'Exécution Multi-Langages Sécurisée (Coding Worker)**. Il s'agit d'un conteneur Docker isolé du reste du système. Son rôle unique est de recevoir du code (Python, JavaScript/Node.js) généré par le LLM, d'installer dynamiquement ses dépendances (pip, npm), de l'exécuter dans un environnement *Sandbox* restreint, et de renvoyer le résultat (stdout/stderr).

## 2. Cartographie des Fichiers et Algorithmes

### `worker_api.py` (v5.234.1.3)
Fichier monolithique exposant une API Flask très légère.
- **Sémantique** : Le serveur écoute sur le port **5000** et expose les endpoints HTTP `/execute` (modes synchrone `background=False` par défaut et détaché `background=True`), `/status` (inspection d'état, liste des jobs du chat et écoute incrémentale `stdout_chunk`/`stderr_chunk` avec `wait_sec`), `/kill`, `/poll_monitor` et `/update_payload`.
- **Exécution depuis le Codex Sandbox & Pre-execution Linting** : Le script est exécuté depuis un fichier physique préalablement enregistré dans l'espace `sandbox` du Codex, après validation syntaxique immédiate (`py_compile` pour Python, `node -c` pour Node.js). La sortie textuelle est bornée dynamiquement par `max_output_length` (131 Ko par défaut, protection anti-OOM LLM).
- **Exécution Parallèle, Quotas & Canal Bidi Isolé par `run_id`** : Utilise `subprocess.Popen(..., start_new_session=True)` couplé à 2 daemon threads de collecte `stdout`/`stderr` et un thread `_job_lifecycle_watcher` dans le registre `_JOBS_REGISTRY` indexé par `f"{safe_uid}:{safe_cid}:{run_id}"`. Applique des quotas stricts (`MAX_CONCURRENT_JOBS_PER_CHAT = 4`, `MAX_CONCURRENT_JOBS_GLOBAL = 8`, rétention FIFO de 20 jobs terminés / TTL 900s) et restreint le verrou `_get_session_deps_lock(user_id, chat_id)` à l'installation `pip`/`npm`. Les fichiers IPC sont isolés par `run_id` (`.echo_monitor_{run_id}.jsonl`, `.echo_ui_payload_{run_id}.json`, `.echo_ui_inbox_{run_id}.jsonl`) et nettoyés automatiquement en fin de vie.
- **Sécurité (Anti-Zombie & Sandbox)** : Isolation totale via `bwrap` (`--ro-bind /proc /proc`, `--die-with-parent`, `--unshare-user` `nobody` `65534:65534`) avec montages sémantiques (`/sandbox` RW, `/main` RO, `/plans` RO, `/generated` RO, `/files` RO, `/echo_libs_sandbox`, `/sandbox-agent-readme.md` et bind-mount physique `.tmp` anti-OOM-DoS). Destruction atomique de l'arbre de processus via `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)` sur timeout ou appel `/kill`.

### ECHO Sandbox Monitor (`echo_monitor.py` & `echo_monitor.js` v5.234.1.1)
- **Sémantique** : La librairie native `echo_monitor` permet au modèle de transmettre des interfaces HTML/JS complexes vers Open WebUI via UCTP.
- **Canaux Bidirectionnels Temps-Réel & `BidiRouter`** : Outre `display()` (projection d'`<iframe>` en mode Upsert) et `get_ui_payload()` (snapshot d'état), la librairie expose `push_event(data, window_id, req_id)` (envoi d'événements JSON par `postMessage` vers l'`<iframe>` sans rechargement DOM), `get_ui_requests(window_id)` (lecture incrémentale par offset de `.echo_ui_inbox_{run_id}.jsonl`) et la classe `BidiRouter` (`route`, `dispatch_once`, `serve_forever`) répondant aux appels `window.echoRequest(action, payload)` de l'`<iframe>`.
- **Supervisor Pattern & JS Heartbeat** : L'infrastructure injecte dynamiquement un *Pacemaker JS* dans les interfaces générées. Le composant maître (`code_executor_tool`) superpose une logique de Timeout d'amorçage (Boot) et de mort (Ping). Si l'utilisateur ferme la croix d'une fenêtre, le ping s'arrête, et le Superviseur exécute un Kill brutal de la sandbox (PID) via la route `/kill`, évitant toute fuite CPU sans exiger de logique `abort` côté LLM.
- **Multiplexage Isolé** : Exploite les variables d'environnement `ECHO_MONITOR_FILE`, `ECHO_UI_PAYLOAD_FILE` et `ECHO_UI_INBOX_FILE` injectées par `worker_api.py` pour isoler chaque `run_id` tout en gérant un fenêtrage multiple simultané (`window_id`).

### `Dockerfile` & `requirements.txt`
- **Build** : Image hybride basée sur **`FROM python:3.14`** embarquant **Node.js 22.x LTS** (patché CVE-2026-111497 Brotli), `npm` (packages globaux `axios`, `cheerio`, `js-yaml`, `lodash`, `papaparse`, `mathjs`, `zod`, `dotenv`, `moment`) et `bubblewrap`.
- **Dépendances** : Installe `Flask` pour l'API (avec `CMAKE_BUILD_PARALLEL_LEVEL=2`), ainsi que des librairies d'analyse lourdes fréquemment requêtées par l'agent (`pandas`, `numpy`, `matplotlib`).

## 3. Dépendances Logiques
- Ce Worker est invoqué exclusivement par l'outil LLM `code_executor_tool.py` (situé dans `12-owui-tools`).
- Il n'a aucune persistance d'état : chaque requête d'exécution démarre dans un contexte vierge (hormis les dépendances installées globalement pendant le cycle de vie du conteneur).
