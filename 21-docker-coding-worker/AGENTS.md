# 🌌 ECHO Framework - Connaissance Sémantique : `21-docker-coding-worker`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `21-docker-coding-worker`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le code source de l'**API d'Exécution Multi-Langages Sécurisée & Terminal Interactif Sandbox (Coding Worker)**. Il s'agit d'un conteneur Docker isolé du reste du système. Son double rôle est :
1. **Exécution Batch Multi-Langages** : Recevoir du code (Python, JavaScript/Node.js, Bash `.sh`) généré par le LLM, installer dynamiquement ses dépendances (pip, npm), l'exécuter dans un environnement *Sandbox* restreint Bubblewrap (`bwrap`), et renvoyer le résultat (stdout/stderr).
2. **Terminal Interactif PTY WebSocket** : Fournir un shell pseudo-terminal interactif sécurisé (`/bin/bash --norc -i`) sous `bwrap` connecté nativement au composant Xterm.js d'Open WebUI (`XTerminal.svelte` via `routers/terminals.py`) sur `/api/terminals`.

## 2. Cartographie des Fichiers et Algorithmes

### `worker_api.py` (v6.15.2.1)
Fichier hybride combinant une API WSGI Flask et un serveur ASGI Starlette propulsé par `uvicorn` sur le port **5000**.
- **Architecture Hybride ASGI/WSGI (`asgi_app`)** : Enveloppe l'application Flask via `WSGIMiddleware` pour servir sans friction l'ensemble des routes HTTP existantes, tout en exposant une route native WebSocket asynchrone (`WebSocketRoute("/api/terminals/{session_id}")`).
- **Endpoints HTTP Terminal & Découverte** :
  - `GET /api/config` : Retourne la déclaration des fonctionnalités terminales (`{"features": {"terminal": True, "files": False, "desktop": False, "browser": False}}`).
  - `GET /openapi.json` : Fournit la spécification OpenAPI minimale requise pour l'auto-découverte et la validation de connexion par Open WebUI.
  - `POST /api/terminals` : Résout `user_id` et `chat_id` depuis les en-têtes (`X-User-Id`, `X-Session-Id` ou `X-Terminal-Context-Id`) ou le corps JSON, instancie une session via `TerminalSessionManager` et retourne `{"id": session.id}`.
  - `GET /api/terminals/<session_id>` : Vérifie la vivacité et la liaison de session (`{"id": session.id, "chat_id": session.chat_id, "alive": bool}`).
  - `DELETE /api/terminals/<session_id>` : Termine la session terminale et libère les ressources.
- **PTY WebSocket Bidirectionnel (`ws_terminal_endpoint`)** :
  - Handshake initial : Réception de la trame JSON `auth`, confirmation `chat` (`{"type": "chat", "chat_id": session.chat_id}`), vidage atomique d'`out_queue` et retransmission différentielle du buffer existant selon `resume`/`cursor` (sans doublon), puis fermeture WebSocket `code=1000` en fin de shell.
  - Protocole `XTerminal.svelte` : Émission de trames de contrôle `output` avec curseur de flux, streaming binaire des sorties du shell, support du redimensionnement de fenêtre (`resize` $\rightarrow$ `TIOCSWINSZ`), du heartbeat `ping`/`pong` et du re-binding dynamique `chat`.
- **Gestionnaire `TerminalSessionManager` & Fallback Multi-Plateforme** :
  - Sous Linux (`bwrap` + `pty`) : Ouvre un PTY maître/esclave via `pty.openpty()`, applique la taille initiale (80x24), configure le descripteur maître en non-bloquant, rattache `slave_fd` comme terminal de contrôle (`os.setsid()` + `TIOCSCTTY`) via `preexec_fn` et exécute le shell interactif `bwrap`.
  - Fallback portable : En l'absence de PTY ou bwrap (ex: exécution de test en `.venv` Windows), lance un sous-processus interactif (`cmd.exe` ou `/bin/sh`) avec pipes standards et lecteur en thread dédié, assurant une testabilité fonctionnelle intégrale en environnement local.
  - Maintient un buffer circulaire de 64 Ko (`max_buffer = 65536`) et un curseur incrémental pour la reprise après déconnexion.
- **Factorisation Bubblewrap (`_build_base_bwrap_cmd` & `_build_interactive_bwrap_cmd`)** :
  - Mutualise l'ensemble des montages de sécurité : isolation namespaces (`--unshare-ipc`, `--unshare-uts`, `--unshare-cgroup`, `--die-with-parent`), bascule utilisateur non privilégié (`--unshare-user --uid 65534 --gid 65534`), génération/montage `--ro-bind-try` de `/etc/passwd` et `/etc/group` (`_ensure_bwrap_identity_files`) pour `uid=65534`, montage RW de `/sandbox` (`--chdir /sandbox`), montages RO stricts de `/main`, `/plans`, `/generated`, `/files`, `/browserviews` et RW de `/.deps`. `_build_interactive_bwrap_cmd` configure également `PS1` (`sandbox:\w\$ `) et `PATH` (`/.deps/python/bin:/.deps/node/.bin:{PATH}`).
- **Endpoints HTTP Batch & SSH** : `/execute` (synchrone/background pour `.py`, `.js` et `.sh` avec validation syntaxique `/bin/bash -n`, gestion du cycle de vie background par `idle_ttl_sec=1800s` et `max_lifetime_sec=14400s`), `/ssh` (`POST`, validation stricte `host`/`user`/`port`, `_normalize_ssh_private_key()` et exécution isolée sous `bwrap` avec clé privée `0o600` ou `sshpass -e`), `/status`, `/kill`, `/poll_monitor` (exposant `job_state`, `exit_code`, `alive`, long-polling `wait_sec` jusqu'à `5.0s` et repli `monitor_raw_bytes` post-nettoyage), `/update_payload` (renouvelant `last_activity_at` du job actif) et `/health`.

### ECHO Sandbox Monitor (`echo_monitor.py` & `echo_monitor.js` v6.15.2.1)
- **Sémantique** : La librairie native `echo_monitor` permet au modèle de transmettre des interfaces HTML/JS complexes vers Open WebUI via UCTP.
- **Canaux Bidirectionnels Temps-Réel & `BidiRouter`** : Outre `display()` (projection d'`<iframe>` en mode Upsert) et `get_ui_payload()` (snapshot d'état), la librairie expose `push_event(data, window_id, req_id)`, `push_ready(window_id, actions)`, `get_ui_requests(window_id)` et la classe `BidiRouter(window_id, max_workers=4)` à exécution concurrente bornée, support natif `__echo_ping__` et rejet immédiat des routes inconnues.
- **Supervisor Pattern & Heartbeat Hôte** : Supervision par heartbeat hôte batch toutes les `5s` (`25s` boot / `45s` ping) et fermeture propre `_is_closed` avec délai de grâce `3s` avant `/kill` du groupe de processus associé au `run_id`.

### `Dockerfile` & `requirements.txt`
- **Build** : Image hybride basée sur **`FROM python:3.14`** embarquant **Node.js 22.x LTS**, `npm`, `bubblewrap`, les utilitaires CLI (`openssh-client`, `sshpass`, `jq`, `less`, `nano`, `iputils-ping`, `dnsutils`) et les dépendances Python (`flask`, `flask-cors`, `requests`, `httpx`, `starlette`, `uvicorn`, `wsproto`, `pandas`, `numpy`, etc.).

## 3. Dépendances Logiques
- Ce Worker est invoqué par l'outil LLM `code_executor_tool.py` (situé dans `12-owui-tools`) pour les exécutions de scripts, et par le routeur backend Open WebUI `routers/terminals.py` pour les sessions interactives de `XTerminal.svelte`.
- Il n'a aucune persistance d'état propre : l'état est matérialisé dans le système de fichiers Codex de la session utilisateur.
