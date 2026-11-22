# Spécification d'Architecture — Option B : Reverse-Proxy HTTP/WebSocket pour Serveurs Web Sandboxés (`bwrap`)

> **Statut** : Spécification de référence pour implémentation ultérieure (post-Option A v6.15.2.1).
> **Périmètre** : `echo-coding-worker` (`21-docker-coding-worker`), `echo-webui` (`16-docker-echo-webui`), `bunkerweb-stack.yml`, `echo_window_manager.py`, `EchoCanvasTab.svelte`.

---

## 1. Objectif & Périmètre

L'Option A (`echo_monitor.py` / `echo_monitor.js` + `BidiRouter` + pont `srcdoc` / `postMessage`) couvre les applications interactives légères pilotées par événements JSONL sans ouverture de socket réseau.

L'**Option B** vise à permettre l'exécution de **serveurs web standards** dans le sandbox `bwrap` de `echo-coding-worker` tout en **cohabitant sans rupture** avec l'Option A :
- Frameworks cibles : FastAPI / Uvicorn, Flask, Express / Fastify, serveurs statiques + API.
- Capacités protocolaires requises : requêtes HTTP/1.1 complètes (GET, POST, PUT, DELETE, multipart, headers personnalisés), **WebSockets** bidirectionnels, **Server-Sent Events (SSE)** en streaming non-bufferisé, et **assets binaires** (images, WASM, polices, téléchargements).
- Cohabitation Option A / Option B : le mode `exec_mode` (ou `server_mode`: `"rpc"` pour Option A, `"http"` pour Option B) sélectionne la route de rendu (`srcdoc` bridgé vs `src` proxifié), tout en partageant le même registre de runs (`_RUNS`), le même cycle de vie (`idle_ttl_sec`, `max_lifetime_sec`), et les mêmes primitives de contrôle (`canvas_stop_run`, `reconcile_canvas_frames`).

---

## 2. Topologie Réseau & Binding Sandbox

### 2.1 Contexte d'exécution `bwrap`
Dans `echo-coding-worker/worker_api.py`, `bwrap` est exécuté **sans `--unshare-net`** (ou avec partage explicite du loopback du conteneur `echo-coding-worker`) afin que le processus serveur enfant puisse exposer un point de terminaison local atteignable par le processus ASGI `worker_api.py` (Starlette/Uvicorn).

### 2.2 Comparaison des modes de binding : Port Loopback vs Unix Domain Socket (UDS)

| Critère | Port Loopback (`127.0.0.1:<port>`) | Unix Domain Socket (`/sandbox/.echo_app_{run_id}.sock`) |
| :--- | :--- | :--- |
| **Allocation & Collisions** | Allocation dynamique via socket éphémère + variable `ECHO_APP_PORT` (risque de race TOCTOU entre probe et bind). | Chemin déterministe unique par `run_id` : zéro collision inter-run, zéro contention de ports. |
| **Isolation Multi-Chat / Multi-Run** | Faible : tout autre processus dans le conteneur peut atteindre `127.0.0.1:<port>` ; nécessite un secret par requête. | **Forte (Recommandé)** : permissions POSIX `0600` / montage ciblé dans le namespace `bwrap` du run ; un autre run n'a pas accès au socket. |
| **Compatibilité Frameworks** | Universelle (tous frameworks écoutent sur `HOST`/`PORT`). | Quasi-universelle (`uvicorn --uds`, `gunicorn --bind unix:...`, `http.createServer().listen(path)`), avec fallback wrapper `socat` si un binaire n'accepte que TCP. |
| **Nettoyage** | Libération automatique par le noyau à la mort du PID. | Suppression explicite du `.sock` lors du cleanup `run_id` (`finally` + GC). |

**Décision recommandée** : **Unix Domain Socket** (`/sandbox/.echo_app_{run_id}.sock`, exposé via `ECHO_APP_SOCKET`) comme transport nominal, avec support optionnel `127.0.0.1:<port>` (`ECHO_APP_PORT`) en repli pour les outils ne supportant pas les UDS.

### 2.3 Readiness Probe (`GET /health`)
Avant de déclarer le run prêt (`status = "running"` et publication de l'URL de proxy), `worker_api.py` exécute une sonde de disponibilité bornée par `ECHO_CANVAS_READY_TIMEOUT_SEC` (ex: `8.0s`, intervalle `100ms`) :
1. Vérification que le processus `bwrap` est toujours vivant (`proc.poll() is None`).
2. Connexion UDS (ou TCP loopback) suivie d'une requête `GET /health` (tolérant `200..399` ou tout code HTTP valide si `/health` n'est pas défini mais que le serveur répond, configurable).
3. En cas d'échec ou d'expiration, terminaison immédiate du groupe de processus et retour structuré des derniers logs `stderr`/`stdout`.

---

## 3. Chaîne Reverse-Proxy ASGI & Contrôle Anti-IDOR

### 3.1 Architecture de bout en bout
```text
Navigateur (Iframe / Sous-domaine)
  │  HTTP / WebSocket / SSE
  ▼
BunkerWeb (TLS, CSP, No-Buffering, WS Upgrade)
  │
  ▼
echo-webui (FastAPI : /echo/v1/apps/{chat_id}/{run_id}/{path:path})
  │  1. Authentification + Vérification stricte (user_id, chat_id, run_id)
  │  2. Résolution worker + signature interne HMAC/Bearer inter-conteneurs
  ▼
echo-coding-worker (Starlette : /proxy/{user_id}/{chat_id}/{run_id}/{path:path})
  │  Proxy HTTP/1.1 streaming (httpx AsyncClient / UDS transport)
  │  + Proxy WebSocket bidirectionnel (websockets / uvicorn)
  ▼
Sandbox bwrap (/sandbox/.echo_app_{run_id}.sock ou 127.0.0.1:<port>)
```

### 3.2 Contrats de routes & Vérification anti-IDOR
1. **Côté `echo-webui` (`backend/open_webui/echo/routers/apps.py`)** :
   - Route HTTP (`@router.api_route("/apps/{chat_id}/{run_id}/{path:path}", methods=[...])`) et route WebSocket (`@router.websocket("/apps/{chat_id}/{run_id}/{path:path}")`).
   - Vérification **obligatoire** avant tout forwarding :
     - Extraction de l'identité vérifiée (`user_id` depuis la session/token scopé).
     - Validation d'appartenance du `chat_id` à `user_id` en base Open WebUI (`Chats.get_chat_by_id_and_user_id`).
     - Validation de l'existence active du `run_id` dans le `WindowManager` pour `(user_id, chat_id)`.
   - Rejet immédiat `403 Forbidden` / `404 Not Found` en cas de non-correspondance (protection anti-IDOR stricte).
2. **Côté `echo-coding-worker` (`worker_api.py`)** :
   - Route Starlette native HTTP + WebSocket : `/proxy/{user_id}/{chat_id}/{run_id}/{path:path}`.
   - Vérifie l'en-tête d'authentification interne partagé (`X-Echo-Worker-Token`) ET que le `run_id` appartient bien au couple `(user_id, chat_id)` enregistré dans `_RUNS`.
   - Réécrit `Host`, `X-Forwarded-Prefix`, `X-Forwarded-Proto` et streame les corps de requête/réponse sans mise en mémoire tampon (support natif SSE `text/event-stream` et gros binaires).

---

## 4. Modèle de Sécurité Critique

### 4.1 Risque XSS & Vol de JWT (`localStorage.token`)
Si une application générée par le LLM est servie depuis la **même origine** que `echo-webui` (ex: `https://echo.example.com/echo/v1/apps/...`) et rendue dans une `<iframe>` avec `allow-same-origin`, tout script exécuté dans cette application a un accès complet au DOM parent, aux cookies SSO et au JWT Open WebUI stocké dans `localStorage.token`. **Ce couplage est critique et proscrit.**

### 4.2 Comparaison des architectures d'isolation

| Critère | Option (a) : Sous-domaine Isolé (`apps.<domain>` / `*.apps.<domain>`) — **RECOMMANDÉ** | Option (b) : Iframe Sandboxée (`sans allow-same-origin`) + CSP stricte |
| :--- | :--- | :--- |
| **Origine (SOP)** | Origine distincte (`https://<run_id>.apps.<domain>`). Isolation SOP native totale vis-à-vis de `echo-webui`. | Origine opaque (`null`). Empêche l'accès à `localStorage` et aux cookies du parent. |
| **Cookies & Auth** | Aucun cookie SSO partagé. Authentification par **token éphémère HMAC scopé `(user_id, chat_id, run_id, exp)`** passé via cookie `HttpOnly; SameSite=Lax; Path=/` posé lors du bootstrap ou query param initial échangé contre cookie scopé. | Pas de cookies possibles (origine `null`). Nécessite un token éphémère en query string ou header personnalisé (complexe pour les sous-ressources `<img>`, `<script>`, WebSocket). |
| **Compatibilité Apps Web** | **100 % compatible** (`localStorage` isolé par app, service workers, relative URLs `/api/...`, WebSockets natifs). | Limitée : `localStorage`/`IndexedDB` bloqués en origine `null`, échecs fréquents sur les SPAs modernes. |
| **Surface XSS inter-apps** | Wildcard `*.apps.<domain>` isole même les apps entre elles (`run_a` ne lit pas `run_b`). | Isolé entre apps via origine `null`. |

**Recommandation Production** : **Option (a)** avec wildcard `*.apps.<domain>` (ou à défaut `apps.<domain>` avec chemin `/r/{run_id}/` et CSP stricte), couplée à un token éphémère scopé `run_id` sans aucun accès au JWT maître ni aux cookies SSO. En développement local sans wildcard DNS, repli possible sur l'Option (b) uniquement pour des apps sans persistance `localStorage`.

---

## 5. Intégration BunkerWeb & Cycle de Vie

### 5.1 Configuration BunkerWeb
Pour le virtual host ou la location dédiée au proxy d'applications (`apps.<domain>` ou `/echo/v1/apps/`) :
- `REVERSE_PROXY_BUFFERING=no` et `REVERSE_PROXY_REQUEST_BUFFERING=no` (indispensable pour SSE et streaming upload/download).
- Support WebSocket explicite : `proxy_http_version 1.1`, transmission de `Upgrade` et `Connection "upgrade"`, timeout de lecture étendu (`proxy_read_timeout 3600s` sur les routes WS/SSE).
- `USE_MODSECURITY=no` et `USE_ANTIBOT=no` sur le périmètre sandbox (les payloads JSON/code/WebSocket d'apps utilisateur déclencheraient de faux positifs CRS), tout en conservant le Rate Limiting et la taille max de corps configurée.

### 5.2 Réutilisation du Cycle de Vie Option A
L'Option B réutilise intégralement l'infrastructure de supervision et de réconciliation introduite par l'Option A :
- **TTL & GC** : `idle_ttl_sec` (`ECHO_CANVAS_IDLE_TTL_SEC = 1800.0`) réinitialisé à chaque requête HTTP/WS proxifiée (remplace/complète le heartbeat `echoPings`) et `max_lifetime_sec` (`ECHO_CANVAS_MAX_LIFETIME_SEC = 14400.0`).
- **Réconciliation F5** : `reconcile_canvas_frames` interroge `/runs/{run_id}` pour corriger les statuts `running` obsolètes après redémarrage ou expiration.
- **UI `EchoCanvasTab.svelte`** :
  - Rendu via `src={frame.src}` (pointant vers l'URL proxifiée isolée) au lieu de `srcdoc`.
  - Conservation du bouton **Arrêter** (`canvas_stop_run`) et ajout optionnel d'un bouton **Redémarrer** (`canvas_restart_run`) dans la barre supérieure.

---

## 6. Plan de Migration Incrémental en Lots

1. **Lot B1 — Transport Sandbox & Proxy Worker (`21-docker-coding-worker`)** :
   - Support UDS (`/sandbox/.echo_app_{run_id}.sock`) + sonde `GET /health` dans `worker_api.py`.
   - Implémentation des routes Starlette `/proxy/{user_id}/{chat_id}/{run_id}/{path:path}` (HTTP streaming + WebSocket).
2. **Lot B2 — Routeur Souverain & Auth Scopée (`16-docker-echo-webui` + `echo_window_manager.py`)** :
   - Ajout du routeur `apps.py` (`/echo/v1/apps/{chat_id}/{run_id}/{path:path}`) avec vérification anti-IDOR et émission de jetons éphémères scopés `run_id`.
   - Extension de `code_executor_tool.py` / `echo_window_manager.py` avec le mode `exec_mode="http"`.
3. **Lot B3 — Infrastructure BunkerWeb & Frontend (`bunkerweb-stack.yml` + `EchoCanvasTab.svelte`)** :
   - Déploiement du vhost isolé `*.apps.<domain>` (ou location dédiée) avec `no-buffering` et upgrade WS.
   - Adaptation de `EchoCanvasTab.svelte` (gestion `frame.src` avec origine isolée, bouton Redémarrer, propagation du heartbeat sur trafic HTTP/WS).
