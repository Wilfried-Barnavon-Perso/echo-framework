# 🌌 Spécifications Opérationnelles ECHO Sandbox (Protocole PAS)

Le Système exécute chaque script dans un conteneur isolé Bubblewrap (`bwrap`, `--ro-bind /proc`, `--die-with-parent`) disposant d'un environnement bi-langage (Python 3.14 / Node.js 22) et scopé par `(user_id, chat_id, run_id)`.

---

## 1. Topologie du Système de Fichiers & Isolation
- **Identité d'exécution** : Utilisateur non-privilégié `nobody` (`uid=65534`, `gid=65534`).
- **Points de montage** :
  - `/sandbox` (**Lecture & Écriture persistante**) : Répertoire de travail courant. Le Modèle DOIT utiliser les outils Codex (`create_codex`, `read_codex`, `edit_codex`, `delete_codex`, `list_codex` avec `workspace="sandbox"`) pour créer ou modifier les scripts avant exécution.
  - `/main` (**Lecture seule**) : Fichiers du workspace Codex `main`.
  - `/plans` (**Lecture seule**) : Plans stratégiques et listes de tâches du workspace Codex `plans`.
  - `/generated` (**Lecture seule**) : Médias générés du workspace Codex `generated`.
  - `/files` (**Lecture seule**) : Fichiers téléversés par L'Utilisateur.
  - `/echo_libs_sandbox` (**Lecture seule**) : Librairies natives (`echo_monitor.py`, `echo_monitor.js`).
  - `/sandbox-agent-readme.md` (**Lecture seule**) : Présent manuel de référence.
- **Quotas de concurrence** : Le Système autorise jusqu'à `4` processus simultanés par session (`chat_id`) et `8` processus globaux sur le Worker. Chaque exécution reçoit un `run_id` unique (`run_xxxxxxxx`) et ses propres canaux IPC (`.echo_monitor_{run_id}.jsonl`, `.echo_ui_payload_{run_id}.json`, `.echo_ui_inbox_{run_id}.jsonl`).

---

## 2. Librairies Pré-Installées
- **Python 3.14** : `requests`, `httpx`, `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn`, `beautifulsoup4`, `lxml`, `Pillow`, `pydantic`, `sqlalchemy`, `openpyxl`, `PyPDF2`, `reportlab`, `python-docx`, `scikit-learn`, `sympy`, `networkx`, `orjson`, `pyyaml`, `tqdm`, `tabulate`.
- **Node.js 22** : `axios`, `cheerio`, `js-yaml`, `lodash`, `papaparse`, `mathjs`, `zod`, `dotenv`, `moment`.
- Pour tout paquet tiers non listé, Le Modèle DOIT renseigner l'argument `dependencies` lors de l'appel à `code_executor`.

---

## 3. Modes d'Exécution & Pilotage du Cycle de Vie (`run_id`)
1. **Mode Synchrone (`background=False`, valeur par défaut)** :
   - Le Système attend la terminaison du processus (jusqu'à `timeout_sec`) et restitue la sortie complète (`stdout`/`stderr`) ainsi que le `run_id`.
2. **Mode Arrière-Plan / Serveur (`background=True`)** :
   - Le Système démarre le processus en arrière-plan, maintient le pont bidirectionnel UI <-> Sandbox actif et retourne immédiatement le `run_id` et le `pid`.
   - Le Modèle DOIT utiliser `control_sandbox_execution(action, run_id, wait_sec)` pour superviser le processus :
     - `action="status"` : Inspecte l'état (`running`, `completed`, `error`, `timeout`, `killed`) d'un `run_id` ou liste tous les jobs de la session si `run_id` est omis.
     - `action="listen"` : Lit de manière incrémentale les nouveaux flux `stdout_chunk` et `stderr_chunk` du `run_id` (avec attente optionnelle `wait_sec` de `0` à `60` secondes).
     - `action="kill"` : Détruit immédiatement le groupe de processus (`SIGKILL`) du `run_id` cible (ou tous les jobs actifs de la session si `run_id` est omis).

---

## 4. Librairie Native `echo_monitor` & Routeur Bidi Temps Réel

L'interface visuelle est projetée dans une véritable `<iframe>` HTML5 (`sandbox="allow-scripts allow-forms allow-modals allow-popups"`) encapsulée dans une fenêtre flottante `EchoFloatingWindow` du DOM Open WebUI.

### 4.1 Primitives `echo_monitor` (Python & Node.js)
- `display(html_content, title="ECHO Sandbox Monitor", width="100%", height="400px", window_id="default")` : Crée ou remplace intégralement l'`<iframe>` de la fenêtre `window_id`.
- `push_event(data, window_id="default", req_id=None)` : Pousse un événement JSON temps réel vers l'`<iframe>` existante via `postMessage` **sans recharger le DOM**.
- `get_ui_payload(window_id="default") -> dict | None` : Lit le dernier état snapshot émis par l'`<iframe>`.
- `get_ui_requests(window_id=None) -> list[dict]` : Lit de manière incrémentale (par curseur d'offset sans perte) la file FIFO des requêtes RPC émises par l'`<iframe>`.
- `is_window_closed(window_id="default") -> bool` : Retourne `True` si L'Utilisateur a fermé la fenêtre.
- `BidiRouter(window_id="default")` : Routeur RPC déclaratif exposant `@router.route(action)` (Python) / `router.route(action, handler)` (Node.js), `dispatch_once()` et `serve_forever(poll_interval=0.2)`.

### 4.2 Primitives JavaScript Pré-Injectées dans l'`<iframe>`
Le Système injecte automatiquement dans chaque `<iframe>` créée par `display()` deux helpers globaux :
- `await window.echoRequest(action, payload)` : Envoie une requête RPC `{ window_id, action, payload, req_id }` au `BidiRouter` du script Sandbox et retourne une `Promise` résolue dès que la Sandbox répond via `push_event(..., req_id)`.
- `window.onEchoEvent((data, reqId) => { ... })` : Abonne l'interface aux événements poussés spontanément par la Sandbox via `push_event(data, window_id)`.

### 4.3 Exemple de Serveur Réactif Bidi (Python, `background=True`)
```python
import echo_monitor

HTML = """
<div style="padding:16px;font-family:sans-serif;">
  <button id="btn">Calculer</button>
  <pre id="out"></pre>
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

---

## 5. Supervision Automatique (Pacemaker Anti-Zombie)
- Chaque `<iframe>` émet un battement de cœur (`echoPing`) toutes les `1000ms`.
- Si L'Utilisateur ferme la fenêtre flottante, Le Système détecte l'arrêt du battement de cœur et exécute automatiquement la destruction du groupe de processus (`POST /kill`) associé au `run_id`.
