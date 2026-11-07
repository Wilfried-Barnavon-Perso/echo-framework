---
domain_id: code_and_files
version: 5.235.5.1
updated_at: 2026-11-06
name: Registre Unifié, Explorateur de l'Espace Personnel & Sandbox d'Exécution de Code
description: Consultation du Registre Unifié (query_registry), lecture de fichiers (RAW, Sonde Sémantique, Multimédia, Image, Hash) et exécution isolée de scripts Codex sandbox (.py / .js).
tools: [query_registry, read_raw_file_content, semantic_probe, read_multimedia_file, show_image_to_user, get_file_metadata, calculate_file_hashes, code_executor, control_sandbox_execution, kill_sandbox_execution]
keywords: [registre, query_registry, fichier, file, code, python, node, javascript, sandbox, code_executor, control_sandbox_execution, kill_sandbox_execution, bidi, echo_monitor, image, pdf, audio, video, multimedia, semantic_probe, read_multimedia_file, hash, sha256, plans, generated]
---

# MANUEL OPÉRATIONNEL : REGISTRE UNIFIÉ, FICHIERS & SANDBOX DE CODE (`code_and_files`)

## 1. RÈGLE D'OR PROPRIOCEPTIVE : `query_registry` EN AMONT
Avant toute manipulation d'un fichier, d'un document Codex, d'un plan, d'un média généré ou d'un workflow N8N, le Modèle **DOIT IMPÉRATIVEMENT** consulter `query_registry(resource_type=None, status=None, search_term=None, resource_id=None, workspace=None)` :
- **Types de ressources (`resource_type`) et Statuts valides (`status`)** :
  - Fichiers, Documents & Médias générés (`"codex"`, `"generated"`, `"media"`, `"binary"`, `"weburl"`) :
    - `"pending_ingestion"` : En attente d'ingestion au prochain tour.
    - `"put_in_context"` : Fichier injecté directement dans le contexte.
    - `"vectorized_sum_up"` : Résumé présent dans `<artifact id="AEC_smart_context">`, contenu détaillé interrogeable dans la Mémoire Vectorielle de la session (`search_sessions_context`).
    - `"indexed"` : Fichier enregistré (ID connu), consultable via les outils de `file_content_explorer`.
  - Plans (`"plan"`) : `"proposed"`, `"executing"`, `"success"`, `"partial"`, `"failed"`, `"abandoned"`.
  - Workflows N8N de session (`"n8n_workflow"`) : `"ready"`, `"executing"`, `"error"`, `"deployed"`.
  - Templates N8N globaux (`"n8n_template"`) : **Statique — NE PREND AUCUN STATUT** (toujours laisser `status=None`, sinon une erreur d'interruption est levée).
- `workspace` (`Optional[Literal["main", "plans", "generated"]]`) : Filtre optionnel par espace Codex.
- `resource_id` : Fournir l'ID exact pour inspecter la fiche complète d'une ressource unique.
- **Provenance de Branche (`provenance`)** : Pour toute ressource rattachée à la session, `query_registry` annote déterministement la provenance : `"branche_active"` (associée à un message de l'arbre conversationnel vivant) ou `"hors_branche"` (produite lors d'une branche alternative ou sous un message supprimé, mais conservée sur disque conformément au principe de non-perte de production).

---

## 2. EXPLORATEUR DE L'ESPACE PERSONNEL (`file_content_explorer.py`)
Tous les outils ci-dessous résolvent `file_id` (ou `target`) dans l'ordre : 1) par `workspace` explicite (`"main"`, `"plans"`, `"generated"`, `"sandbox"`) ou préfixe (`"main/..."`, `"plans/..."`, `"generated/..."`, `"sandbox/..."`), 2) par ID Registre (`DOC_...`, `U_...`, `plan-...`, `dlg_img_...`), 3) par Nom de fichier dans le Registre, et 4) par Nom sur disque (`codex/main/`, `codex/plans/`, `codex/generated/`, `codex/sandbox/`, `files/`).

### 2.1 Matrice de Sélection de l'Outil d'Exploration
| Besoin Opérationnel | Outil à Invoquer | Spécifications & Contraintes Critiques |
| :--- | :--- | :--- |
| **Extraction chirurgicale ciblée dans un fichier ou média volumineux (Texte, PDF, Image, Audio, Vidéo)** | `semantic_probe(file_id, query)` | Délègue l'extraction ciblée à un sous-agent (`MODEL_APOLLO`) qui répond précisément au motif `query` (avec segmentation automatique au-delà de 30 Mo ou 45 min). |
| **Rapport descriptif complet et horodaté d'un Média (Image, Audio, Vidéo, PDF)** | `read_multimedia_file(file_id, instruction=None)` | Sonde multimodale (`MODEL_APOLLO`) restituant une distillation complète et horodatée du média. `instruction` optionnel pour surcharger la consigne par défaut. |
| **Lecture brute de lignes ou d'octets** | `read_raw_file_content(file_id, start_line=1, end_line=100, output_mode="text", byte_offset=0)` | `output_mode` : `"text"`, `"base64"` ou `"hex"`. **CONTRAINTE CRITIQUE** : `byte_offset` (> 0) est mutuellement exclusif avec `start_line`/`end_line`. Limiter à ~100 lignes par appel (max 16 Ko). Si un binaire lu en `"text"` est illisible, interdiction de boucler : utiliser `semantic_probe` ou `read_multimedia_file`. |
| **Afficher une image à l'Utilisateur** | `show_image_to_user(target, workspace=None)` | Rend toute image locale (par ID Registre, par Nom de fichier ou par `workspace="main"\|"plans"\|"generated"\|"sandbox"`) ou distante (`http(s)://`) dans une Iframe interactive intégrée au message destiné à L'Utilisateur. **Outil strictement réservé au Modèle principal (Orchestrateur)** : son appel depuis un Sous-Agent headless est ABSOLUMENT INTERDIT (le Sous-Agent DOIT restituer l'identifiant, le nom ou l'URL au Modèle principal qui exécute l'affichage). L'affichage visuel étant assuré nativement par l'Iframe du Système, il est ABSOLUMENT INTERDIT pour Le Modèle d'émettre une balise Markdown `![...](...)` ou un lien d'image redondant dans sa réponse. |
| **Métadonnées & Intégrité cryptographique** | `get_file_metadata(file_id)` / `calculate_file_hashes(file_ids, algorithm="sha256")` | `get_file_metadata` retourne taille et MIME. `calculate_file_hashes` accepte une liste `file_ids` et `algorithm` parmi `"sha256"`, `"md5"`, `"sha1"`, `"sha512"`, `"blake2b"`, `"crc32"`. |

---

## 3. SANDBOX D'EXÉCUTION DE CODE (`code_executor_tool.py`)

### 3.1 Séparation CRUD Fichiers (`Codex`) et Exécution (`Sandbox`)
Il est ABSOLUMENT INTERDIT de passer du code brut à `code_executor`. Toute manipulation de fichier dans `/sandbox` DOIT passer par les outils du Codex avec `workspace="sandbox"` :
1. **Étape 1 — CRUD Fichiers (`workspace="sandbox"`)** :
   - Créer / Écrire : `create_codex(filename="script.py", content="...", summary="...", workspace="sandbox")`.
   - Lire : `read_codex(filename="script.py", workspace="sandbox")`.
   - Modifier : `edit_codex(filename="script.py", ..., workspace="sandbox")`.
   - Supprimer : `delete_codex(filename="script.py", workspace="sandbox")`.
2. **Étape 2 — Exécuter le script** :
   - Appeler `code_executor(file_path="script.py", dependencies=None, timeout_sec=900, fetch_ui_payload=False, background=False)`.

### 3.2 Environnement Système, Quotas & Pilotage des Processus (`run_id`)
- **Montages Bubblewrap (`--ro-bind /proc, --die-with-parent`)** :
  - `/sandbox` (**Lecture & Écriture persistante**) : Répertoire de travail courant.
  - `/main`, `/plans`, `/generated`, `/files` (**Lecture seule**) : Espaces Codex et fichiers téléversés par L'Utilisateur.
  - `/sandbox-agent-readme.md` (**Lecture seule**) : Manuel technique interne de la Sandbox.
- **Quotas & Isolation** : Chaque exécution est strictement isolée par `(user_id, chat_id, run_id)` (extraits des closures serveur non altérables par Le Modèle), à raison d'un maximum de **4 processus simultanés par session** (`8` globalement sur le Worker).
- **Modes d'exécution de `code_executor`** :
  - `background=False` (**défaut REQUIS pour les scripts standards**) : Exécution synchrone bloquante restituant `output`, `error` et `run_id` à la terminaison.
  - `background=True` : Démarre le processus en tâche de fond (serveur Bidi `echo_monitor` ou calcul long), maintient le pont UI <-> Sandbox actif en tâche asynchrone et retourne immédiatement le `run_id` (`run_xxxxxxxx`) et le `pid`.
- **Pilotage via `control_sandbox_execution(action, run_id=None, wait_sec=0)`** :
  - `action="status"` : Liste tous les `run_id` actifs et récemment terminés de la session (ou détaille un `run_id` précis).
  - `action="listen"` : Récupère les nouvelles sorties incrémentales `stdout_chunk` / `stderr_chunk` du `run_id` (en attendant jusqu'à `wait_sec` secondes, max `60s`, si le processus est en cours).
  - `action="kill"` (ou `kill_sandbox_execution(run_id=None)`) : Détruit immédiatement le groupe de processus (`SIGKILL`) du `run_id` cible (ou tous les processus actifs de la session si `run_id` est omis).

### 3.3 Interfaces Interactives & Routeur Bidi Temps Réel (`echo_monitor`)
Pour déployer une application web interactive ou un serveur réactif dans l'`<iframe>` flottante (`EchoFloatingWindow`), Le Modèle DOIT utiliser `echo_monitor`.

> [!IMPORTANT]
> **INTERDICTION DE DÉLÉGATION MANUELLE À L'UTILISATEUR** :
> Il est ABSOLUMENT INTERDIT pour Le Modèle de demander à L'Utilisateur d'ouvrir un fichier local sur son poste, de lancer un serveur HTTP manuellement ou de copier-coller un JSON dans le chat.

#### Pattern A — Routeur RPC Temps Réel sans rechargement DOM (`BidiRouter` + `window.echoRequest`) :
- **Côté Iframe JS** : `const res = await window.echoRequest("mon_action", { valeur: 42 });` et `window.onEchoEvent((data, reqId) => { ... });` (pré-injectés dans l'`<iframe>`).
- **Côté Sandbox (`server.py`)** :
  ```python
  import echo_monitor

  echo_monitor.display(open("app.html", encoding="utf-8").read(), title="Application Réactive", window_id="app_main")
  router = echo_monitor.BidiRouter(window_id="app_main")

  @router.route("mon_action")
  def handle_action(payload):
      return {"resultat": payload.get("valeur", 0) * 2}

  router.serve_forever()
  ```
- **Lancement** : Le Modèle PEUT lancer ce serveur en mode synchrone (`background=False`, bloquant jusqu'à fermeture de la fenêtre) ou en arrière-plan (`background=True`, permettant au Modèle de continuer à interagir et d'appeler `control_sandbox_execution(action="listen", run_id=...)`).

#### Pattern B — Soumission One-Shot par Snapshot (`get_ui_payload`) :
- L'`<iframe>` émet `window.parent.postMessage({ echoUIPayload: { window_id: "app_id", payload: {...} } }, "*");` et le script Sandbox lit `echo_monitor.get_ui_payload("app_id")` dans une boucle `while not echo_monitor.is_window_closed("app_id"):`.

#### Supervision Automatique (Pacemaker Anti-Zombie) :
- L'`<iframe>` émet automatiquement `echoPing` toutes les `1000ms`. Dès que L'Utilisateur ferme la fenêtre flottante, Le Système détruit automatiquement le processus Sandbox (`POST /kill`) associé au `run_id`.
