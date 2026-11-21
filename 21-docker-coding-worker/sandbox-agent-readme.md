# 🌌 Spécifications Opérationnelles ECHO Sandbox (Protocole PAS)

Le Système exécute chaque script dans un conteneur isolé Bubblewrap (`bwrap`, `--ro-bind /proc`, `--die-with-parent`, `--unshare-ipc`, `--unshare-uts`, `--unshare-cgroup`, `--unshare-user --uid 65534 --gid 65534`) disposant d'un environnement tri-langage (Python 3.14 / Node.js 22 / Bash `.sh`) et scopé par `(user_id, chat_id, run_id)`.

---

## 1. Topologie du Système de Fichiers & Environnement Injecté
- **Identité d'exécution** : Utilisateur non-privilégié `nobody` (`uid=65534`, `gid=65534`).
- **Points de montage** :
  - `/sandbox` (**Lecture & Écriture persistante**) : Répertoire de travail courant (`--chdir /sandbox`). Le Modèle DOIT utiliser les outils Codex (`create_codex`, `read_codex`, `edit_codex`, `delete_codex`, `list_codex` avec `workspace="sandbox"`) pour créer ou modifier les scripts avant exécution.
  - `/main` (**Lecture seule**) : Fichiers du workspace Codex `main`.
  - `/plans` (**Lecture seule**) : Plans stratégiques et listes de tâches du workspace Codex `plans`.
  - `/generated` (**Lecture seule**) : Médias générés du workspace Codex `generated`.
  - `/files` (**Lecture seule**) : Fichiers téléversés par L'Utilisateur dans la session courante.
  - `/browserviews` (**Lecture seule**) : Captures d'écran de navigation web de la session.
  - `/echo_libs_sandbox` (**Lecture seule**) : Librairies natives (`echo_monitor.py`, `echo_monitor.js`).
  - `/sandbox-agent-readme.md` (**Lecture seule**) : Présent manuel de référence.
  - `/run/echo_ipc.sock` (**Socket Unix AF_UNIX**) : Bus IPC mémoire binaire `< 2 ms` entre la Sandbox et le Hub Worker.
  - `/run/echo_app/http.sock` (**Socket Unix AF_UNIX** dans répertoire `/run/echo_app` RW) : Point d'écoute privilégié pour les serveurs web complets (FastAPI, Uvicorn, Express, `ws`).
- **Variables d'environnement pré-injectées** :
  - `ECHO_RUN_ID` : Identifiant unique de l'exécution (`run_xxxxxxxx`).
  - `ECHO_IPC_SOCK` : Chemin du bus IPC (`/run/echo_ipc.sock` ou `tcp://127.0.0.1:<port>` en repli).
  - `ECHO_APP_UDS` : Chemin du socket Unix serveur web (`/run/echo_app/http.sock`).
  - `ECHO_APP_PORT` : Port TCP local éphémère attribué en repli (jamais `5000`).
- **Quotas de concurrence** : Le Système autorise jusqu'à `4` processus simultanés par session (`chat_id`) et `8` processus globaux sur le Worker.

---

## 2. Librairies & Outils Pré-Installés
- **Python 3.14** :
  - Web & Asynchrone : `fastapi`, `uvicorn`, `starlette`, `websockets`, `flask`, `httpx`, `requests`.
  - Calcul, Data & ML : `pandas`, `numpy`, `scipy`, `scikit-learn`, `statsmodels`, `sympy`.
  - Visualisation & Documents : `matplotlib`, `seaborn`, `plotly`, `bokeh`, `Pillow`, `openpyxl`, `python-docx`, `PyPDF2`, `pdfplumber`, `reportlab`.
  - Parsing, Données & Utilitaires : `beautifulsoup4`, `lxml`, `sqlalchemy`, `cryptography`, `pydantic`, `orjson`, `pyyaml`, `tqdm`, `tabulate`.
- **Node.js 22** :
  - Serveurs & Temps réel : `express`, `ws`.
  - Client, Parsing & Utilitaires : `axios`, `cheerio`, `js-yaml`, `lodash`, `papaparse`, `mathjs`, `zod`, `dotenv`, `moment`.
- **Shell & Utilitaires CLI (`/bin/bash`)** : `curl`, `git`, `jq`, `less`, `nano`, `ping`, `dig`/`nslookup`, `tar`, `gzip`, `ssh`, `scp`, `sshpass`.
- Pour tout paquet tiers absent de cette liste, Le Modèle DOIT renseigner le paramètre `dependencies` lors de l'appel à `code_executor` (installé dans `/.deps/python` ou `/.deps/node`).
- Pour l'administration SSH sur des hôtes distants via l'Identity Vault, Le Modèle DOIT utiliser l'outil dédié `ssh_executor(account_id, command, timeout_sec)`.

---

## 3. Modes d'Exécution & Pilotage du Cycle de Vie (`run_id`)
1. **Mode Synchrone (`background=False`, valeur par défaut)** :
   - Le Système attend la terminaison du processus (jusqu'à `timeout_sec`) et restitue la sortie complète (`stdout`/`stderr`) ainsi que le `run_id`.
2. **Mode Arrière-Plan / Serveur (`background=True`)** :
   - **ABSOLUMENT OBLIGATOIRE** pour tout serveur persistant : `BidiRouter.serve_forever()` (Pattern A) et `echo_monitor.expose_app()` (Pattern B).
   - Le Système démarre le processus en arrière-plan, exécute une sonde de disponibilité (`Readiness Probe` jusqu'à `8s` via `/wait_ready`) et retourne immédiatement le `run_id` et le `pid`.
   - Cycle de vie gouverné par un **TTL d'inactivité** (`1800s` par défaut, renouvelé à chaque interaction UI / proxy HTTP-WS / `/canvas_signal`) et un **plafond absolu** de `4h` (`14400s`).
   - Le Modèle DOIT utiliser `control_sandbox_execution(action, run_id, wait_sec)` pour superviser le processus :
     - `action="status"` : Inspecte l'état (`running`, `completed`, `error`, `timeout`, `killed`) d'un `run_id` ou liste tous les jobs de la session si `run_id` est omis.
     - `action="listen"` : Lit de manière incrémentale les nouveaux flux `stdout_chunk` et `stderr_chunk` du `run_id` (avec attente optionnelle `wait_sec` de `0` à `60` secondes).
     - `action="kill"` : Détruit immédiatement le groupe de processus (`SIGKILL`) du `run_id` cible (ou tous les jobs actifs de la session si `run_id` est omis).

---

## 4. Interfaces & Serveurs dans l'Onglet Applications (`EchoCanvasTab`)

Toute interface visuelle Sandbox est projetée dans l'onglet natif **Applications** (`EchoCanvasTab`) à l'intérieur d'une `<iframe sandbox="allow-scripts allow-forms allow-modals allow-popups">` (**sans** `allow-same-origin`).
- **Contrainte de Sécurité `Origin: null`** : Dans une iframe sandboxée sans `allow-same-origin`, `localStorage`, `sessionStorage` et `document.cookie` sont **inutilisables** (`SecurityError`). Il est **ABSOLUMENT INTERDIT** de stocker un état persistant côté navigateur ; toute persistance d'état DOIT résider côté serveur dans `/sandbox` (SQLite, JSON ou fichiers).

### 4.1 Pattern A — Widget Réactif Mono-Fichier (`display` + `BidiRouter`, Latence `< 2 ms`)
Adapté aux widgets interactifs mono-HTML nécessitant des échanges RPC JSON ou binaires (`ArrayBuffer`) à très faible latence sur `/run/echo_ipc.sock`.
- **Primitives `echo_monitor` (Python & Node.js)** :
  - `display(html_content, title="ECHO Sandbox Monitor", width="100%", height="400px", window_id="default")` : Transmet l'interface HTML brute UTF-8 sans rechargement préalable.
  - `push_event(data, window_id="default", req_id=None, binary=None)` : Pousse un événement JSON (avec charge binaire `bytes`/`Buffer` optionnelle) sans recréer le DOM.
  - `push_ready(window_id="default", actions=None)` : Déclare les actions RPC prêtes (appelé automatiquement par `BidiRouter.serve_forever()`).
  - `get_ui_requests(window_id=None)` / `is_window_closed(window_id="default")`.
  - `BidiRouter(window_id="default", max_workers=4)` : Routeur concurrent exposant `@router.route(action)` (Python) / `router.route(action, handler)` (Node.js) et `serve_forever()`. Un handler acceptant `(payload, binary)` reçoit le binaire brut ; retourner `(data, bytes)` émet une réponse mixte JSON+binaire.
- **Helpers JS pré-injectés dans l'`<iframe>`** :
  - `await window.echoRequest(action, payload, arrayBuffer?)` : Appelle le `BidiRouter` (JSON + `ArrayBuffer` optionnel) et retourne une `Promise` résolue à la réponse.
  - `window.onEchoEvent((data, reqId, arrayBuffer) => { ... })` : Écoute les événements poussés spontanément par le script Sandbox.

```python
# Exemple Pattern A (Python, background=True)
import echo_monitor

HTML = """
<div style="padding:16px;font-family:sans-serif;">
  <button id="btn">Calculer</button><pre id="out"></pre>
  <script>
    document.getElementById('btn').onclick = async () => {
      const res = await window.echoRequest('compute', { x: 21 });
      document.getElementById('out').textContent = JSON.stringify(res);
    };
  </script>
</div>
"""

echo_monitor.display(HTML, title="Calculateur Bidi", window_id="calc_ui")
router = echo_monitor.BidiRouter(window_id="calc_ui")

@router.route("compute")
def handle_compute(payload):
    return {"double": payload.get("x", 0) * 2}

router.serve_forever()
```

### 4.2 Pattern B — Serveur Web Complet Multi-Routes & WebSockets (`expose_app`)
Adapté aux applications web multi-pages, APIs REST, téléchargements de fichiers, assets statiques et WebSockets temps réel. Le Système proxifie automatiquement le trafic HTTP (`/app_proxy/{run_id}/...`) et WebSocket (`/ws/app/{run_id}/...`) vers le Socket Unix `/run/echo_app/http.sock` (ou port TCP éphémère vérifié).
- **Primitives `echo_monitor` (Python & Node.js)** :
  - `get_app_uds() -> str` : Retourne `ECHO_APP_UDS` (`/run/echo_app/http.sock`).
  - `get_app_port() -> int` : Retourne `ECHO_APP_PORT`.
  - `wait_until_closed(window_id="default")` : Bloque événementiellement (Python `Condition` / Node `Promise<void>`) jusqu'à fermeture de la fenêtre.
  - `expose_app(target=None, title="Application Sandbox", width="100%", height="600px", window_id="default", health_path="/", wait_timeout=15.0, block=True)` : Attend l'ouverture du socket serveur (UDS prioritaire puis TCP), publie l'application dans **Applications** et bloque par défaut (`block=True`) jusqu'à fermeture. En Node.js, accepte un objet d'options `{ target, title, ... }` ou les arguments positionnels.
- **Règle d'adressage Frontend dans l'iframe** : Les pages HTML servies DOIVENT utiliser des URLs relatives (`fetch('./api/items')`, assets relatifs sans `/` initial) pour HTTP et dériver le WebSocket vers `/ws/app/{run_id}/...` (`location.pathname.replace('/app_proxy/', '/ws/app/').replace(/\/?$/, '') + '/ws'`) afin que le routage par `<base>` / proxy `/app_proxy/{run_id}/` opère sans altération.

```python
# Exemple Pattern B — Python FastAPI + WebSocket (background=True)
import threading, uvicorn, echo_monitor
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html><html><body>
      <button onclick="ws.send('ping')">Ping WS</button><pre id="log"></pre>
      <script>
        const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsPath = location.pathname.replace('/app_proxy/', '/ws/app/').replace(/\\/?$/, '') + '/ws';
        const ws = new WebSocket(wsProto + '//' + location.host + wsPath);
        ws.onmessage = (e) => { document.getElementById('log').textContent += e.data + '\\n'; };
      </script></body></html>"""

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    while True:
        msg = await ws.receive_text()
        await ws.send_text(f"echo:{msg}")

threading.Thread(
    target=uvicorn.run,
    kwargs={"app": app, "uds": echo_monitor.get_app_uds(), "log_level": "warning"},
    daemon=True,
).start()
echo_monitor.expose_app(title="App FastAPI + WS")
```

```javascript
// Exemple Pattern B — Node.js Express + ws (background=True)
const fs = require('fs');
const http = require('http');
const express = require('express');
const { WebSocketServer } = require('ws');
const echoMonitor = require('echo_monitor');

const app = express();
app.get('/', (_req, res) => res.send('<h1>App Express + WS</h1>'));

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });
wss.on('connection', (ws) => ws.on('message', (d) => ws.send(`echo:${d}`)));

const uds = echoMonitor.get_app_uds();
try { fs.unlinkSync(uds); } catch (_) {}
server.listen(uds, async () => {
    await echoMonitor.expose_app({ title: 'App Express + WS' });
    server.close();
});
```

### 4.3 Pattern C — Snapshot One-Shot (`display` + `get_ui_payload`)
Adapté aux restitutions visuelles statiques ou interactives sans boucle serveur persistante (`background=False`). Le script appelle `echo_monitor.display(html, ...)` puis se termine ; `get_ui_payload(window_id)` reste disponible pour lire le dernier état synchronisé lors d'une ré-exécution ultérieure.

---

## 5. Supervision Automatique (Pacemaker Anti-Zombie)
- L'hôte UI émet un battement de cœur batch (`echoPings`) toutes les `5000ms` pour les fenêtres actives, avec tolérances de `25s` au démarrage (`boot_timeout`) et `45s` en fonctionnement (`ping_timeout`).
- Si L'Utilisateur ferme la vue de l'application (`window_closed` $\rightarrow$ `_is_closed: true`), Le Système accorde un délai de grâce de `3s` pour une sortie propre du script avant d'exécuter `POST /kill` associé au `run_id`.
