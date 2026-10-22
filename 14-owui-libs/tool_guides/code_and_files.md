---
domain_id: code_and_files
version: 5.229.0.0
updated_at: 2026-10-22
name: Registre Unifié, Explorateur de l'Espace Personnel & Sandbox d'Exécution de Code
description: Consultation du Registre Unifié (query_registry), lecture de fichiers (RAW, Sonde Sémantique, Multimédia, Image, Hash) et exécution isolée de scripts Codex sandbox (.py / .js).
tools: [query_registry, read_raw_file_content, semantic_probe, read_multimedia_file, show_image_to_user, get_file_metadata, calculate_file_hashes, code_executor, kill_sandbox_execution]
keywords: [registre, query_registry, fichier, file, code, python, node, javascript, sandbox, code_executor, image, pdf, audio, hash, sha256]
---

# MANUEL OPÉRATIONNEL : REGISTRE UNIFIÉ, FICHIERS & SANDBOX DE CODE (`code_and_files`)

## 1. RÈGLE D'OR PROPRIOCEPTIVE : `query_registry` EN AMONT
Avant toute manipulation d'un fichier, d'un document Codex, d'un plan ou d'un workflow N8N, le Modèle **DOIT IMPÉRATIVEMENT** consulter `query_registry` :
`query_registry(resource_type=None, status=None, search_term=None, resource_id=None)` :
- **Types de ressources (`resource_type`) et Statuts valides (`status`)** :
  - Fichiers & Documents (`"codex"`, `"media"`, `"binary"`, `"weburl"`) :
    - `"pending_ingestion"` : En attente d'ingestion au prochain tour.
    - `"put_in_context"` : Fichier injecté directement dans le contexte.
    - `"vectorized_sum_up"` : Résumé présent dans `<artifact id="AEC_smart_context">`, contenu détaillé interrogeable dans la Mémoire Vectorielle de la session (`search_sessions_context`).
    - `"indexed"` : Fichier enregistré (ID connu), consultable via les outils de `file_content_explorer`.
  - Plans (`"plan"`) : `"proposed"`, `"executing"`, `"success"`, `"partial"`, `"failed"`, `"abandoned"`.
  - Workflows N8N de session (`"n8n_workflow"`) : `"ready"`, `"executing"`, `"error"`, `"deployed"`.
  - Templates N8N globaux (`"n8n_template"`) : **Statique — NE PREND AUCUN STATUT** (toujours laisser `status=None`, sinon une erreur d'interruption est levée).
- `resource_id` : Fournir l'ID exact pour inspecter la fiche complète d'une ressource unique.

---

## 2. EXPLORATEUR DE L'ESPACE PERSONNEL (`file_content_explorer.py`)
Tous les outils ci-dessous résolvent automatiquement `file_id` (ou `target`) parmi : 1) le Codex `"main"` (via ID Registre), 2) les Uploads de la session/utilisateur, et 3) le Codex `"sandbox"` (par nom de fichier).

### 2.1 Matrice de Sélection de l'Outil d'Exploration
| Besoin Opérationnel | Outil à Invoquer | Spécifications & Contraintes Critiques |
| :--- | :--- | :--- |
| **Analyse ciblée d'un fichier volumineux (Texte, PDF, Doc)** | `semantic_probe(file_id, query)` | Délègue la lecture Zéro-RAM à un sous-agent (`MODEL_APOLLO`, limite 30 Mo) qui répond précisément au motif `query`. |
| **Rapport descriptif complet d'un Média (Image, Audio, Vidéo, PDF)** | `read_multimedia_file(file_id)` | Sonde sensorielle multimodale Zéro-RAM (`MODEL_APOLLO`, limite 30 Mo) restituant une distillation complète du média. |
| **Lecture brute de lignes ou d'octets** | `read_raw_file_content(file_id, start_line=1, end_line=100, output_mode="text", byte_offset=0)` | `output_mode` : `"text"`, `"base64"` ou `"hex"`. **CONTRAINTE CRITIQUE** : `byte_offset` (> 0) est mutuellement exclusif avec `start_line`/`end_line`. Limiter à ~100 lignes par appel (max 16 Ko). Si un binaire lu en `"text"` est illisible, interdiction de boucler : utiliser `semantic_probe` ou `read_multimedia_file`. |
| **Afficher une image à l'Utilisateur** | `show_image_to_user(target)` | Si `target` est un fichier local (ID Registre ou nom), l'affiche dans l'interface dédiée (viewer ECHO). Si `target` est une URL `http(s)://`, vérifie la validité de l'image et retourne la syntaxe Markdown exacte à insérer dans la réponse. |
| **Métadonnées & Intégrité cryptographique** | `get_file_metadata(file_id)` / `calculate_file_hashes(file_ids, algorithm="sha256")` | `get_file_metadata` retourne taille et MIME. `calculate_file_hashes` accepte une liste `file_ids` et `algorithm` parmi `"sha256"`, `"md5"`, `"sha1"`, `"sha512"`, `"blake2b"`, `"crc32"`. |

---

## 3. SANDBOX D'EXÉCUTION DE CODE (`code_executor_tool.py`)

### 3.1 Protocole d'Exécution en 2 Étapes (`create_codex(workspace="sandbox")` ➔ `code_executor`)
`code_executor` n'exécute pas une chaîne de code passée en argument direct : il exécute **un fichier `.py` (Python) ou `.js` (Node.js) préalablement écrit dans le workspace `"sandbox"` du Codex**.
1. **Étape 1 — Écrire le script dans la Sandbox Codex** :
   - Appeler `create_codex(filename="script.py", content="...", workspace="sandbox")` (ou `edit_codex(..., workspace="sandbox")`).
2. **Étape 2 — Exécuter le script** :
   - Appeler `code_executor(file_path="script.py", dependencies=None, timeout_sec=120, fetch_ui_payload=False)`.

### 3.2 Environnement Système de la Sandbox & Paramètres
- **Système de fichiers monté dans la Sandbox** :
  - `/files` (**Lecture seule**) : Contient les fichiers uploadés par l'Utilisateur (présents dans le Registre).
  - `/main` (**Lecture seule**) : Contient les fichiers du Codex workspace `"main"`.
  - `/sandbox` (**Lecture & Écriture persistante**) : Répertoire de travail courant où se trouve `file_path`. Tout fichier écrit dans `/sandbox` persiste et devient lisible via `read_codex(..., workspace="sandbox")` ou `file_content_explorer`.
  - `/sandbox-agent-readme.txt` (**Lecture seule**) : Documentation interne de la librairie d'interface temps-réel **ECHO Monitor** (fenêtres flottantes interactives Bidi Polling UI $\leftrightarrow$ Sandbox) et liste des paquets pré-installés.
- **Paramètres de `code_executor`** :
  - `file_path` : Chemin relatif du script dans `/sandbox` (ex: `"analyse.py"` ou `"app.js"`).
  - `dependencies` : Liste optionnelle de paquets `pip` ou `npm` additionnels à installer avant exécution (ex: `["pandas", "scipy"]`).
  - `timeout_sec` : Délai maximal d'exécution en secondes.
  - `fetch_ui_payload` : Si `True`, injecte l'état initial des composants UI actifs dans la Sandbox au démarrage.
- **Arrêt d'urgence (`kill_sandbox_execution`)** :
  - `kill_sandbox_execution()` : Détruit immédiatement le processus Sandbox actif de la session en cas de blocage ou boucle infinie.
