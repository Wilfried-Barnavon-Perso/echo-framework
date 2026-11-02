---
domain_id: code_and_files
version: 5.231.53.1
updated_at: 2026-11-02
name: Registre Unifié, Explorateur de l'Espace Personnel & Sandbox d'Exécution de Code
description: Consultation du Registre Unifié (query_registry), lecture de fichiers (RAW, Sonde Sémantique, Multimédia, Image, Hash) et exécution isolée de scripts Codex sandbox (.py / .js).
tools: [query_registry, read_raw_file_content, semantic_probe, read_multimedia_file, show_image_to_user, get_file_metadata, calculate_file_hashes, code_executor, kill_sandbox_execution]
keywords: [registre, query_registry, fichier, file, code, python, node, javascript, sandbox, code_executor, image, pdf, audio, video, multimedia, semantic_probe, read_multimedia_file, hash, sha256]
---

# MANUEL OPÉRATIONNEL : REGISTRE UNIFIÉ, FICHIERS & SANDBOX DE CODE (`code_and_files`)

## 1. RÈGLE D'OR PROPRIOCEPTIVE : `query_registry` EN AMONT
Avant toute manipulation d'un fichier, d'un document Codex, d'un plan ou d'un workflow N8N, le Modèle **DOIT IMPÉRATIVEMENT** consulter `query_registry(resource_type=None, status=None, search_term=None, resource_id=None)` :
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
Tous les outils ci-dessous résolvent `file_id` (ou `target`) dans l'ordre : 1) par `workspace` explicite (`"main"` ou `"sandbox"`) ou préfixe (`"sandbox/..."`, `"main/..."`), 2) par ID Registre (`DOC_...`, `U_...` ou UUID), 3) par Nom de fichier dans le Registre, et 4) par Nom sur disque (`codex/main/`, `codex/sandbox/`, `files/`).

### 2.1 Matrice de Sélection de l'Outil d'Exploration
| Besoin Opérationnel | Outil à Invoquer | Spécifications & Contraintes Critiques |
| :--- | :--- | :--- |
| **Extraction chirurgicale ciblée dans un fichier ou média volumineux (Texte, PDF, Image, Audio, Vidéo)** | `semantic_probe(file_id, query)` | Délègue l'extraction ciblée à un sous-agent (`MODEL_APOLLO`) qui répond précisément au motif `query` (avec segmentation automatique au-delà de 30 Mo ou 45 min). |
| **Rapport descriptif complet et horodaté d'un Média (Image, Audio, Vidéo, PDF)** | `read_multimedia_file(file_id, instruction=None)` | Sonde multimodale (`MODEL_APOLLO`) restituant une distillation complète et horodatée du média. `instruction` optionnel pour surcharger la consigne par défaut. |
| **Lecture brute de lignes ou d'octets** | `read_raw_file_content(file_id, start_line=1, end_line=100, output_mode="text", byte_offset=0)` | `output_mode` : `"text"`, `"base64"` ou `"hex"`. **CONTRAINTE CRITIQUE** : `byte_offset` (> 0) est mutuellement exclusif avec `start_line`/`end_line`. Limiter à ~100 lignes par appel (max 16 Ko). Si un binaire lu en `"text"` est illisible, interdiction de boucler : utiliser `semantic_probe` ou `read_multimedia_file`. |
| **Afficher une image à l'Utilisateur** | `show_image_to_user(target, workspace=None)` | Rend toute image locale (par ID Registre, par Nom de fichier ou par `workspace="main"\|"sandbox"`) ou distante (`http(s)://`) dans une Iframe interactive intégrée au message destiné à L'Utilisateur. **Outil strictement réservé au Modèle principal (Orchestrateur)** : son appel depuis un Sous-Agent headless est ABSOLUMENT INTERDIT (le Sous-Agent DOIT restituer l'identifiant, le nom ou l'URL au Modèle principal qui exécute l'affichage). L'affichage visuel étant assuré nativement par l'Iframe du Système, il est ABSOLUMENT INTERDIT pour Le Modèle d'émettre une balise Markdown `![...](...)` ou un lien d'image redondant dans sa réponse. |
| **Métadonnées & Intégrité cryptographique** | `get_file_metadata(file_id)` / `calculate_file_hashes(file_ids, algorithm="sha256")` | `get_file_metadata` retourne taille et MIME. `calculate_file_hashes` accepte une liste `file_ids` et `algorithm` parmi `"sha256"`, `"md5"`, `"sha1"`, `"sha512"`, `"blake2b"`, `"crc32"`. |

---

## 3. SANDBOX D'EXÉCUTION DE CODE (`code_executor_tool.py`)

### 3.1 Protocole d'Exécution en 2 Étapes (`create_codex(workspace="sandbox")` ➔ `code_executor`)
`code_executor` exécute **un fichier `.py` (Python) ou `.js` (Node.js) préalablement écrit dans le workspace `"sandbox"` du Codex** (et non du code brut passé en argument) :
1. **Étape 1 — Écrire le script dans la Sandbox Codex** :
   - Appeler `create_codex(filename="script.py", content="...", workspace="sandbox")` (ou `edit_codex(..., workspace="sandbox")`).
2. **Étape 2 — Exécuter le script** :
   - Appeler `code_executor(file_path="script.py", dependencies=None, timeout_sec=120, fetch_ui_payload=False)`.

### 3.2 Environnement Système de la Sandbox & Paramètres
- **Système de fichiers monté dans la Sandbox** :
  - `/files` (**Lecture seule**) : Fichiers uploadés par l'Utilisateur (présents dans le Registre).
  - `/main` (**Lecture seule**) : Fichiers du Codex workspace `"main"`.
  - `/sandbox` (**Lecture & Écriture persistante**) : Répertoire de travail courant où se trouve `file_path`. Tout fichier écrit dans `/sandbox` persiste et devient lisible via `read_codex(..., workspace="sandbox")` ou `file_content_explorer`.
- **Paramètres de `code_executor`** :
  - `file_path` : Chemin relatif du script dans `/sandbox` (ex: `"analyse.py"` ou `"app.js"`).
  - `dependencies` : Liste optionnelle de paquets `pip` ou `npm` additionnels à installer avant exécution (ex: `["pandas", "scipy"]`).
  - `timeout_sec` : Délai maximal d'exécution en secondes.
  - `fetch_ui_payload` : Si `True`, injecte l'état initial des composants UI actifs dans la Sandbox au démarrage.
- **Arrêt d'urgence (`kill_sandbox_execution`)** :
  - `kill_sandbox_execution(run_id=None)` : Détruit immédiatement le processus Sandbox actif de la session (ou l'exécution ciblée par `run_id`) en cas de blocage ou boucle infinie.
