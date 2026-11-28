---
domain_id: code_and_files
version: 6.25.1.1
updated_at: 2026-11-28
name: Registre Unifié, Explorateur de l'Espace Personnel & Sandbox d'Exécution de Code
description: Consultation du Registre Unifié (query_registry), lecture de fichiers (RAW, Sonde Sémantique, Multimédia, Image, Hash), exécution isolée de scripts Codex sandbox (.py / .js / .sh) et pilotage des terminaux interactifs Local/SSH (manage_terminal).
tools: [query_registry, read_raw_file_content, semantic_probe, read_multimedia_file, show_image_to_user, get_file_metadata, calculate_file_hashes, code_executor, control_sandbox_execution, manage_terminal]
keywords: [registre, fichier, file, code, python, node, javascript, bash, shell_script, shell, sandbox, ssh, terminal, certificat, echo_monitor, fastapi, websocket, expose_app, server, uds, image, pdf, audio, video, multimedia, hash, sha256, plans, generated, browserviews]
---

# MANUEL OPÉRATIONNEL : REGISTRE UNIFIÉ, FICHIERS & SANDBOX DE CODE (`code_and_files`)

## 1. RÈGLE D'OR PROPRIOCEPTIVE : `query_registry` EN AMONT
Avant toute manipulation d'un fichier, d'un document Codex, d'un plan, d'un média généré ou d'un workflow N8N, Le Modèle **DOIT IMPÉRATIVEMENT** consulter `query_registry(resource_type=None, status=None, search_term=None, resource_id=None, workspace=None)` :
- **Types de ressources (`resource_type`) et Statuts valides (`status`)** :
  - Fichiers, Documents & Médias générés (`"codex"`, `"generated"`, `"media"`, `"binary"`, `"weburl"`) :
    - `"pending_ingestion"` : En attente d'ingestion au prochain tour.
    - `"put_in_context"` : Fichier injecté directement dans le contexte.
    - `"vectorized_sum_up"` : Résumé présent dans `<artifact id="AEC_smart_context">`, contenu détaillé interrogeable dans la Mémoire Vectorielle de la session (`search_sessions_context`).
    - `"indexed"` : Fichier enregistré (ID connu), consultable via les outils d'exploration de fichiers.
  - Captures et clips MP4 du Navigateur Autonome (`"browserview"`) : `"transient"` (temporaire, purgé en fin de session navigateur), `"retained"` (préservé sur disque).
  - Sessions terminales interactives (`"terminal"`) : `"active_unlocked"` (session active, écriture et fermeture autorisées au Modèle), `"active_locked"` (session active verrouillée en lecture seule : `read` autorisé, `write` et `close` bloqués), `"closed"` (session terminée).
  - Plans (`"plan"`) : `"proposed"`, `"executing"`, `"success"`, `"partial"`, `"failed"`, `"abandoned"`.
  - Workflows N8N de session (`"n8n_workflow"`) : `"ready"`, `"executing"`, `"error"`, `"deployed"`.
  - Templates N8N globaux (`"n8n_template"`) : **Statique — NE PREND AUCUN STATUT** (Le Modèle DOIT toujours laisser `status=None`, sinon une erreur d'interruption est levée).
- `workspace` (`Optional[Literal["main", "plans", "generated"]]`) : Filtre optionnel par espace Codex.
- `resource_id` : Fournir l'ID exact pour inspecter la fiche complète d'une ressource unique.
- **Provenance de Branche (`provenance`)** : Pour toute ressource rattachée à la session, `query_registry` annote déterministement la provenance : `"branche active"` (associée à un message de l'arbre conversationnel vivant) ou `"hors branche active (production conservée)"` (produite lors d'une branche alternative ou sous un message supprimé, mais conservée sur disque conformément au principe de non-perte de production).

---

## 2. EXPLORATEUR DE L'ESPACE PERSONNEL
Seul `show_image_to_user(target, workspace=None)` accepte un paramètre `workspace` explicite. Les 5 autres outils (`semantic_probe`, `read_multimedia_file`, `read_raw_file_content`, `get_file_metadata`, `calculate_file_hashes`) prennent `file_id` (ou `file_ids`) résolu dans l'ordre : 1) par préfixe explicite (`"main/..."`, `"plans/..."`, `"generated/..."`, `"sandbox/..."`, `"browserviews/..."`, `"files/..."`) ou URN `codex:current:...`, 2) par ID Registre (`U_...`, `plan-...`, `dlg_img_...` ou ID hexadécimal 8 caractères), 3) par Nom de fichier dans le Registre, et 4) par Nom sur disque (`codex/main/`, `codex/plans/`, `codex/generated/`, `codex/sandbox/`, `browserviews/`, `files/`). Un Sous-Agent headless ciblant ses propres fichiers sandbox **DOIT** passer le préfixe `"sandbox/<sub_sid>/..."`.

### 2.1 Matrice de Sélection de l'Outil d'Exploration
| Besoin Opérationnel | Outil à Invoquer | Spécifications & Contraintes Critiques |
| :--- | :--- | :--- |
| **Extraction chirurgicale ciblée dans un fichier ou média volumineux (Texte, PDF, Image, Audio, Vidéo)** | `semantic_probe(file_id, query)` | Délègue l'extraction ciblée à `MODEL_APOLLO` selon `query` (segmentation Map-Reduce parallélisée en mémoire vive avec backpressure au-delà de 30 Mo ou 45 min). Appels parallélisables intra-tour sur fichiers distincts. |
| **Rapport descriptif complet et horodaté d'un Média (Image, Audio, Vidéo, PDF)** | `read_multimedia_file(file_id, instruction=None)` | Sonde multimodale (`MODEL_APOLLO`) restituant une distillation complète et horodatée du média (segmentation parallèle bornée au-delà de 30 Mo ou 45 min). `instruction` optionnel pour surcharger la consigne par défaut. Appels parallélisables intra-tour sur fichiers distincts. |
| **Lecture brute de lignes ou d'octets** | `read_raw_file_content(file_id, start_line=1, end_line=100, output_mode="text", byte_offset=0)` | `output_mode` : `"text"`, `"base64"` (max `12 288` octets/appel) ou `"hex"` (max `8 192` octets/appel). **CONTRAINTE CRITIQUE** : `start_line`/`end_line` sont appliqués uniquement en `output_mode="text"` avec `byte_offset=0` et sont ignorés dès `byte_offset > 0` (lecture brute par offset). Limiter à ~100 lignes par appel en texte (max 16 Ko). Si un binaire lu en `"text"` est illisible, il est ABSOLUMENT INTERDIT de boucler : Le Modèle DOIT utiliser `semantic_probe` ou `read_multimedia_file`. |
| **Afficher une image à L'Utilisateur** | `show_image_to_user(target, workspace=None)` | Rend toute image locale (par ID Registre, par Nom de fichier ou par `workspace="main"\|"plans"\|"generated"\|"sandbox"\|"browserviews"`) ou distante (`http(s)://`) dans une Iframe interactive intégrée au message destiné à L'Utilisateur. **Outil strictement réservé au Modèle principal** : son appel depuis un Sous-Agent headless est ABSOLUMENT INTERDIT (Le Sous-Agent DOIT restituer l'identifiant, le nom ou l'URL au Modèle principal qui exécute l'affichage). L'affichage visuel étant assuré nativement par l'Iframe du Système, il est ABSOLUMENT INTERDIT pour Le Modèle d'émettre une balise Markdown `![...](...)` ou un lien d'image redondant dans sa réponse. |
| **Métadonnées & Intégrité cryptographique** | `get_file_metadata(file_id)` / `calculate_file_hashes(file_ids, algorithm="sha256")` | `get_file_metadata` retourne taille et MIME. `calculate_file_hashes` accepte une liste `file_ids` et `algorithm` parmi `"sha256"`, `"md5"`, `"sha1"`, `"sha512"`, `"blake2b"`, `"crc32"`. |

---

## 3. SANDBOX D'EXÉCUTION DE CODE

### 3.1 Séparation CRUD Fichiers (`Codex`) et Exécution (`Sandbox`)
Il est ABSOLUMENT INTERDIT de passer du code brut à `code_executor`. Toute manipulation de fichier dans `/sandbox` DOIT passer par les outils du Codex avec `workspace="sandbox"` :
1. **Étape 1 — CRUD Fichiers (`workspace="sandbox"`)** :
   - Créer / Écrire : `create_codex(filename="script.py", content="...", commit_message="...", workspace="sandbox")`.
   - Importer depuis `files` / `main` / `plans` / `generated` / `browserviews` vers la Sandbox : `transfer_codex(source_path="...", source_workspace="...", destination_workspace="sandbox", mode="copy")`.
   - Organiser l'arborescence Sandbox : `create_codex_directory(path="...", workspace="sandbox")` et `move_codex(source_path="...", destination_path="...", workspace="sandbox")`.
   - Lire : `read_codex(filename="script.py", workspace="sandbox")`.
   - Modifier : `edit_codex(filename="script.py", ..., workspace="sandbox")`.
   - Supprimer un fichier ou dossier : `delete_codex(filename="...", workspace="sandbox", recursive=True)` (`recursive=True` REQUIS si dossier non vide).
2. **Étape 2 — Exécuter le script** :
   - Le Modèle DOIT appeler `code_executor(file_path="script.py", dependencies=None, timeout_sec=900, background=False)` (extensions supportées dans `workspace="sandbox"` : `.py`, `.js` et `.sh` ; `timeout_sec` max `3600s` ; `dependencies` installe les paquets `pip`/`npm` manquants dans `/.deps`). Sous un Sous-Agent (`sub_sid`), l'exécution démarre dans `/sandbox/<sub_sid>` tandis que `/sandbox` complet demeure monté : Le Sous-Agent PEUT invoquer `code_executor(file_path="<peer_sid>/script.py")` ou lire `/sandbox/<peer_sid>/...` sans transiter par `codex/main`.
3. **Étape 3 — Promotion des livrables vers `main`** :
   - Le Modèle DOIT appeler `transfer_codex(source_path="...", source_workspace="sandbox", destination_workspace="main", mode="copy"|"move")` pour versionner sous Git et indexer dans le Registre Unifié les sorties finales destinées à L'Utilisateur (usage de `codex/main` comme SAS temporaire entre sous-agents **ABSOLUMENT INTERDIT**).

### 3.2 Environnement Système, Quotas & Pilotage des Processus (`run_id`)
- **Points de montage du système de fichiers Sandbox** :
  - `/sandbox` (**Lecture & Écriture persistante**) : Répertoire de travail courant pour Le Modèle principal (sous un Sous-Agent `sub_sid`, le répertoire courant initial est `/sandbox/<sub_sid>` et `/sandbox` complet reste monté pour accéder directement aux fichiers des pairs via `/sandbox/<peer_sid>/...`).
  - `/main`, `/plans`, `/generated`, `/files`, `/browserviews` (**Lecture seule**) : Espaces Codex, fichiers téléversés par L'Utilisateur et captures de navigation web.
  - `/echo_libs_sandbox` (**Lecture seule**) : Librairies natives (`echo_monitor.py`, `echo_monitor.js`) injectées dans `PYTHONPATH` et `NODE_PATH`.
  - `/sandbox-agent-readme.md` (**Lecture seule**) : Manuel technique interne de la Sandbox.
  - `/run/echo_ipc.sock` (**Socket Unix AF_UNIX**) et `/run/echo_app/http.sock` (**Socket Unix AF_UNIX** RW) : Canaux IPC et serveur applicatif web.
- **Variables d'environnement pré-injectées** : `ECHO_RUN_ID`, `ECHO_IPC_SOCK`, `ECHO_APP_UDS` (`/run/echo_app/http.sock`), `ECHO_APP_PORT` (port TCP local de repli).
- **Quotas & Isolation** : Chaque exécution est strictement isolée par `(user_id, chat_id, run_id)` (isolation stricte par session et par Sous-Agent), à raison d'un maximum de **4 processus simultanés par session** (`8` globalement sur le Worker).
- **Modes d'exécution de `code_executor`** :
  - `background=False` (**défaut REQUIS pour les scripts standards sans serveur**) : Exécution synchrone bloquante restituant `output`, `error` et `run_id` à la terminaison. **INTERDIT** pour tout script invoquant `echo_monitor.expose_app()` (refus immédiat par garde pré-vol).
  - `background=True` (**OBLIGATOIRE pour tout serveur `echo_monitor.expose_app()` ou calcul long**) : Démarre le processus en tâche de fond, exécute une **Readiness Probe** (jusqu'à `8s` pour réception du signal `ready` ou détection de crash précoce avec remontée `stderr`/`exit_code`), maintient le pont UI <-> Sandbox actif via le superviseur pérenne (`EchoWindowManager`), et retourne immédiatement `run_id`, `pid`, `ui_ready`, `window_ids` et `idle_ttl_sec`.
  - **Durée de vie & TTL** : Un processus détaché avec `echo_monitor.expose_app()` est soumis à un **TTL d'inactivité de `1800s` (30 min)** renouvelé sur activité proxy et un **plafond absolu de `4h` (`14400s`)**.
- **Pilotage via `control_sandbox_execution(action="status", run_id=None, wait_sec=0)`** :
  - **Périmètre exclusif Coding Worker** : `control_sandbox_execution` pilote exclusivement les processus `code_executor` (`run_*`) du Coding Worker ; l'arrêt d'une exécution asynchrone ou d'un démon N8N **DOIT** utiliser `manage_n8n_workflow(action="stop", ...)`.
  - **Cloisonnement `sub_sid`** : Sous un Sous-Agent (`sub_sid`), l'accès aux processus est strictement cloisonné aux `run_id` possédés par ce Sous-Agent.
  - `action="status"` (défaut) : Liste tous les `run_id` actifs et récemment terminés de la session (ou détaille un `run_id` précis).
  - `action="listen"` : Récupère les nouvelles sorties incrémentales `stdout_chunk` / `stderr_chunk` du `run_id` (en attendant jusqu'à `wait_sec` secondes, max `60s`, si le processus est en cours). Si `run_id` est omis et qu'un unique processus actif existe, Le Système résout automatiquement ce `run_id`.
  - `action="kill"` : Détruit immédiatement le groupe de processus (`SIGKILL`) du `run_id` cible (ou tous les processus actifs possédés si `run_id` est omis).

### 3.3 Interfaces Interactives & Serveurs Web Temps Réel (`echo_monitor`)
Pour déployer une vue HTML statique ou un serveur applicatif web complet dans l'onglet natif **Applications**, Le Modèle DOIT utiliser `echo_monitor`.

> [!IMPORTANT]
> **INTERDICTION DE DÉLÉGATION MANUELLE À L'UTILISATEUR** :
> Il est ABSOLUMENT INTERDIT pour Le Modèle de demander à L'Utilisateur d'ouvrir un fichier local sur son poste, de lancer un serveur HTTP manuellement ou de copier-coller un JSON dans le chat.
> **Contrainte `Origin: null` & Polyfill Mémoire** : L'iframe de l'onglet **Applications** est sandboxée sans `allow-same-origin`. Un polyfill en RAM pour `localStorage` et `sessionStorage` est injecté automatiquement pour prévenir tout crash JS, mais son contenu est éphémère (perdu au rechargement). Toute persistance durable DOIT résider côté serveur dans `/sandbox` (SQLite, JSON ou fichiers via API).

#### Pattern A — Rendu Statique (`display`, `background=False`) :
- Adapté aux rapports HTML autonomes, graphiques statiques ou tableaux de bord sans serveur backend :
  ```python
  import echo_monitor

  echo_monitor.display(html, title="Rapport Statique", window_id="report_main")
  ```
- Exécuté en mode synchrone (`background=False`) : la vue est transmise à l'onglet **Applications** et le statut passe à `completed` à la fin du script.

#### Pattern B — Serveur Applicatif Web Complet (`expose_app`, `background=True` obligatoire) :
- Pour les applications web interactives multi-routes, APIs REST/SSE, formulaires persistants, assets multi-fichiers (`<img src="...">`, CSS, JS dans `/sandbox`) et WebSockets natifs (FastAPI/Uvicorn, Flask, Express/ws, etc.) :
  1. Le script démarre son serveur HTTP/WS en écoutant sur `echo_monitor.get_app_uds()` (prioritaire, Socket Unix) ou sur `("127.0.0.1", echo_monitor.get_app_port())`.
  2. Le script appelle `echo_monitor.expose_app(title="...", window_id="app_main")` (`background=True` obligatoire), qui sonde la disponibilité du serveur, publie l'application via un capability ticket HMAC dans l'onglet **Applications** et bloque jusqu'à fermeture de la fenêtre (`wait_until_closed`).
  3. Les pages HTML servies PEUVENT utiliser des chemins relatifs ou `/...` (réécrits automatiquement côté serveur/DOM par `<base>`, réécriture des attributs `src`/`href`/`action`/`poster`, redirections `Location`, `fetch`, `XMLHttpRequest`, `EventSource` et `WebSocket` ; helpers explicites `window.echoAppUrl(path)` et `window.echoWsUrl(path)` disponibles).

#### Cycle de Vie & Arrêt Propre de la Vue Interactive :
- Lorsque L'Utilisateur ferme la vue **Applications**, `echo_monitor.is_window_closed("app_main")` retourne `True`, `wait_until_closed()` se débloque et le script dispose de `3 s` pour libérer ses ressources avant interruption forcée par Le Système. Le Modèle PEUT appeler `control_sandbox_execution(action="listen", run_id=...)` pour suivre les journaux.

### 3.4 Pilotage Terminal Interactif & Exécution Ponctuelle (`manage_terminal`)
- **Signature** : `manage_terminal(action, mode=None, account_id=None, session_id=None, name=None, command=None, signal_key=None, append_newline=True, tail_lines=100, wait_ms=400, timeout_sec=60)` avec `action` $\in$ `{"exec", "open", "list", "read", "write", "close"}`.
1. **Exécution shell ponctuelle isolée (`action="exec"`)** :
   - **Mode `local` vs `ssh`** : `mode` est déduit automatiquement en `"ssh"` si `account_id` est fourni, sinon `"local"` par défaut.
   - **`mode="local"`** : Exécute `command` en bash one-shot dans l'environnement sandbox isolé de la session (`/sandbox` ou `/sandbox/<sub_sid>` sous Sous-Agent) sans créer de session terminale persistante ; `account_id` est **interdit**.
   - **`mode="ssh"`** : Le Modèle DOIT préalablement appeler `list_identities(service="ssh")` pour identifier l'`account_id` cible, puis invoquer `manage_terminal(action="exec", mode="ssh", account_id="...", command="...", timeout_sec=60)` (`timeout_sec` borné côté Worker à `5..900s`). Les secrets du Coffre (`password`, `private_key` et `certificate` OpenSSH `*-cert.pub` optionnel) sont injectés côté Worker dans `bwrap` sans exposition au Modèle (`/sandbox` en lecture/écriture, `/main`, `/plans`, `/generated`, `/files` et `/browserviews` en lecture seule).
2. **Ouverture de session terminale interactive (`action="open"`)** :
   - **Garde Headless** : **ABSOLUMENT INTERDIT** en contexte d'automatisation planifiée (`headless_forbidden`).
   - **Mode `local` vs `ssh`** : `mode` déduit automatiquement en `"ssh"` si `account_id` est fourni, sinon `"local"`. En mode `"local"`, `account_id` est interdit et la session s'ouvre **déverrouillée par défaut (`agent_locked=False`)**. En mode `"ssh"`, `account_id` est obligatoire et la session s'ouvre **verrouillée en lecture seule par défaut (`agent_locked=True`)**.
   - Ouvre automatiquement l'onglet Terminal dans l'interface utilisateur et enregistre la session dans `query_registry(resource_type="terminal")`.
3. **Liste, Lecture, Écriture & Fermeture (`list` | `read` | `write` | `close`)** :
   - `action="list"` : Restitue les sessions terminales actives de la conversation (`id`, `name`, `mode`, `target`, `alive`, `agent_locked`) et réconcilie le registre.
   - `action="read"` : Exige `session_id` et lit les `tail_lines` dernières lignes nettoyées sans séquences ANSI (`1..500`, défaut `100`). **Autorisé y compris lorsque la session est verrouillée (`agent_locked=True`)**.
   - `action="write"` : Exige `session_id` et (`command` non vide ou `signal_key` $\in$ `{"ctrl_c", "ctrl_d", "ctrl_z", "enter"}`), avec `append_newline=True` et `wait_ms` (`0..5000ms`, défaut `400ms`).
   - `action="close"` : Exige `session_id` et ferme la session interactive.
4. **Protocole Strict sous Verrou (`terminal_locked`) & MPSI** :
   - Sur `action="write"` ou `action="close"`, si la session est verrouillée (`error_code == "terminal_locked"`), Le Modèle **NE DOIT PAS** tenter de contournement : Le Modèle **DOIT** informer L'Utilisateur de déverrouiller la session nommée dans l'interface pour autoriser l'interaction.
   - **Interdiction absolue (MPSI)** : Il est **ABSOLUMENT INTERDIT** de demander à L'Utilisateur un mot de passe, une clé privée ou un certificat SSH en clair dans le chat. Si aucun compte n'est configuré, Le Modèle DOIT inviter L'Utilisateur à le saisir directement dans l'onglet **Coffre & Identité** (également accessible depuis le menu **`+`** des onglets Terminal via **`Nouveau terminal SSH…`** ou **`Configurer un hôte SSH…`**).
