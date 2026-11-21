---
domain_id: codex_management
version: 6.17.2.1
updated_at: 2026-11-21
name: Gestionnaire Documentaire & Versionnement Git (ECHO Codex)
description: Création, édition directe ou assistée, gestion d'arborescence (dossiers, déplacement/renommage), transferts inter-workspaces sécurisés, suppression contrôlée, lecture paginée/historique, recherche récursive/temporelle et versionnement Git sur main, plans, generated et sandbox.
tools: [create_codex, edit_codex, create_codex_directory, move_codex, transfer_codex, delete_codex, read_codex, restore_codex, search_codex, summarize_codex, list_codex, list_other_chats_ids, history_codex, open_codex_ui]
keywords: [codex, codex_main, document, fichier, git, commit, version, diff, edit_codex, create_codex, open_codex_ui, workspace, plans, generated, sandbox, multi_chat, target_chat_id, list_other_chats_ids, read_only, wrap_tool_output, urn, binary, is_binary, binary_content_omitted, pagination, has_more, next_start_line, create_codex_directory, move_codex, copy_codex, transfer_codex, dossier, repertoire, mkdir, sous_dossier, rename, renommer, deplacer, copier, promouvoir, recursive, overwrite, files, browserviews, skipped_entries, concurrent_modification_conflict]
---

# MANUEL OPÉRATIONNEL : ECHO CODEX (`codex_management`)

## 1. ARCHITECTURE MULTI-WORKSPACES, SÉCURITÉ & REGISTRE
Le Codex ECHO gère des fichiers texte, code et médias répartis en 4 espaces de travail et synchronisés avec le Registre Unifié :
- **Séparation des Espaces (`workspace: Literal["main", "plans", "generated", "sandbox"] = "main"`)** :
  - `"main"` (Défaut, versionné Git, indexé dans le Registre Unifié, écriture ouverte, `resource_type="codex"`) : Espace principal de la session pour les documents, architectures et livrables destinés à L'Utilisateur.
  - `"plans"` (Versionné Git, indexé dans le Registre Unifié, **lecture seule directe**, `resource_type="plan"`) : Espace dédié aux plans stratégiques (`{plan_id}_{slug}.md`) et listes de tâches (`tasks_{plan_id}_{slug}.md`). L'écriture et la suppression passent exclusivement par `build_plan`, `update_plan`, `update_tasks`, `process_plan`, `close_plan` et `delete_plan`.
  - `"generated"` (Versionné Git, indexé dans le Registre Unifié, **lecture seule directe**, `resource_type="generated"`) : Espace dédié aux médias générés (images `<nom_court>.png` versionnées par essai et retouche via `generate_image`).
  - `"sandbox"` (Non versionné, non indexé dans le Registre Unifié, monté `rw` en exécution, écriture ouverte) : Espace de travail technique isolé utilisé pour écrire et exécuter les scripts `.py`, `.js` et `.sh` via `code_executor`.
  - `"files"` et `"browserviews"` : Sources de session externes en lecture seule (fichiers Vault et captures navigateur), utilisables uniquement en copie vers `"sandbox"` via `transfer_codex`.
- **Sécurité d'Arborescence** : Les chemins ciblant `.git`, `.tmp`, les traversées `..`, les octets nuls et les liens symboliques hors Vault utilisateur sont **ABSOLUMENT INTERDITS**.
- **Navigation Transverse Multi-Chat (Lecture Seule)** :
  - Le Modèle PEUT explorer et lire les fichiers de n'importe quelle autre conversation du même Utilisateur sans quitter la session active.
  - **Invariant Inviolable (Read-Only)** : Toute consultation d'une conversation distante (`target_chat_id != current_chat_id`) s'effectue strictement en lecture seule. Aucune action mutative n'est autorisée sur un dépôt distant et aucune ressource n'est injectée dans le Registre Unifié de la session active.

---

## 2. CRÉATION, ÉDITION, ARBORESCENCE, TRANSFERTS & OUVERTURE VISUELLE

### 2.1 Création : `create_codex`
- Crée un nouveau fichier (`filename`, `content`, `language=None`, `commit_message=None`, `workspace="main"` ; versionné sur `main`, disque seul sur `sandbox`) dans le Codex de la conversation courante.
- **Contrainte d'unicité** : Si `filename` existe déjà dans le `workspace` cible, `create_codex` renvoie une erreur. Le Modèle **DOIT** alors utiliser `edit_codex`. En cas de collision avec un dossier existant, `error_code="path_conflict"` est retourné.
- Le Modèle DOIT vérifier l'absence de collision préalable via `query_registry(resource_type="codex")` ou `list_codex(workspace=...)`.

### 2.2 Édition : `edit_codex`
- **CONTRAINTE CRITIQUE D'EXCLUSIVITÉ MUTUELLE** : Les paramètres `new_content` et `instructions` sont **strictement mutuellement exclusifs**. Le Modèle DOIT fournir l'un **OU** l'autre, et il est **ABSOLUMENT INTERDIT** de fournir les deux simultanément :
  1. **Mode Remplacement Direct (`new_content="..."`, `instructions=None`)** : Remplace immédiatement l'intégralité du fichier par `new_content` et crée un commit Git sur `main` (latence nulle). À utiliser pour les fichiers courts ou entièrement régénérés.
  2. **Mode Édition Assistée (`instructions="...", new_content=None`)** : Délègue la modification chirurgicale du fichier existant à un Sous-Agent éditeur (`MODEL_APOLLO`) qui applique les `instructions` sur le document complet et commite le résultat. À utiliser pour modifier une section précise d'un long fichier sans réécrire tout le contenu. Protégé par contrôle optimiste SHA-256 : en cas de modification concurrente, retourne `error_code="concurrent_modification_conflict"` (Le Modèle DOIT relire le fichier avant réessai).
- **Restriction Binaire** : L'édition assistée par instructions est formellement refusée sur les fichiers binaires (`error_code="binary_edit_not_supported"`).

### 2.3 Arborescence & Réorganisation Intra-Workspace
- **`create_codex_directory(path, workspace="main")`** :
  - Crée un dossier et ses sous-dossiers parents manquants dans `main` ou `sandbox`.
  - Opération idempotente : retourne `already_existed: True` si le dossier existe déjà, ou `error_code="path_conflict"` si un fichier occupe le chemin cible.
  - Aucun fichier factice `.gitkeep`, aucun commit Git vide et aucune entrée factice dans le Registre Unifié ne sont créés.
- **`move_codex(source_path, destination_path, workspace="main", overwrite=False, commit_message=None)`** :
  - Renomme ou déplace un fichier ou un dossier entier **exclusivement à l'intérieur d'un même espace (`main` ou `sandbox`)**.
  - Crée automatiquement les dossiers parents manquants de `destination_path` et interdit l'auto-imbrication (`dir -> dir/sub`).
  - Limite `overwrite=True` aux collisions `fichier -> fichier` (`error_code="path_conflict"` si un dossier est impliqué côté source ou destination).
  - Génère 1 commit Git sur `main` (sauf pour un dossier vide) et met à jour atomiquement le Registre Unifié en préservant `message_id`, `summary`, `mime` et les éventuelles métadonnées d'uploads.

### 2.4 Transferts Inter-Workspaces (`transfer_codex`)
- **`transfer_codex(source_path, source_workspace, destination_workspace, destination_path=None, mode="copy", overwrite=False, commit_message=None)`** :
  - **Strictement inter-workspaces** : un appel avec `source_workspace == destination_workspace` est refusé (`error_code="intra_workspace_forbidden"`). Le Modèle DOIT utiliser `move_codex`.
  - **Promotion `sandbox -> main`** : autorise `mode="copy"` ou `mode="move"`.
    - Filtre automatiquement les entrées exclues (`.git`, `.venv`, `node_modules`, `__pycache__`, `.tmp` et fichiers cachés `.*`) qui sont restituées dans `skipped_entries`.
    - En `mode="move"`, supprime de `sandbox` uniquement les fichiers effectivement transférés et les sous-dossiers devenus vides, en conservant les éléments ignorés dans `sandbox`.
    - Quotas de destination `main` : maximum `200` fichiers, `25 Mo` par fichier et `100 Mo` cumulés (`error_code="quota_exceeded"` en cas de dépassement).
  - **Import `{main, plans, generated, files, browserviews} -> sandbox`** : **`mode="copy"` uniquement** (`mode="move"` est refusé : la suppression dans `main` reste indépendante via `delete_codex` ; `plans`, `generated`, `files` et `browserviews` sont strictement en lecture seule).
    - Quotas de destination `sandbox` : maximum `500` fichiers, `512 Mo` par fichier et `1 Go` cumulés (`error_code="quota_exceeded"` en cas de dépassement).
    - Pour `files` et `browserviews`, `source_path` accepte un chemin relatif ou l'`id`/`name` exact issu de `query_registry`.

### 2.5 Ouverture de l'Onglet Codex : `open_codex_ui`
- Déclenche l'ouverture asynchrone de l'onglet natif `Codex & Fichiers` dans l'interface de L'Utilisateur sur le fichier `filename` du workspace local ou distant (`target_chat_id`).
- Si `target_chat_id` est spécifié (`target_chat_id != current_chat_id`), l'onglet bascule sur la conversation distante en lecture seule.
- Si `commit_hash` est fourni, l'onglet charge l'état historique exact du fichier à cette révision Git.
- **RÈGLE DE PARCIMONIE** : Le Modèle DOIT appeler `open_codex_ui` **UNIQUEMENT** si L'Utilisateur en a fait la demande expresse, ou si une relecture visuelle immédiate par L'Utilisateur d'un livrable créé ou modifié est indispensable. Il est ABSOLUMENT INTERDIT d'appeler cet outil depuis un Sous-Agent headless.
- **NATURE DE L'OUTIL** : `open_codex_ui` assure l'affichage dans l'onglet natif `Codex & Fichiers` (éditeur de code, visionneuse hexadécimale et prévisualisation documentaire Markdown, HTML statique, SVG, images). Pour déployer et exécuter une interface applicative interactive communicante, Le Modèle DOIT utiliser `code_executor` avec `echo_monitor` (voir guide `code_and_files`).

---

## 3. LECTURE, RECHERCHE, MULTI-CHAT & VOYAGE TEMPOREL GIT

### 3.1 Lecture, Synthèse et Inventaire Multi-Chat
- `list_other_chats_ids` :
  - Découvre dynamiquement les autres conversations de L'Utilisateur disposant d'un espace Codex et retourne leur `chat_id` et leur titre (la conversation courante est exclue). Utilisé en amont de `list_codex(target_chat_id=...)` et `read_codex(target_chat_id=...)`.
- `list_codex(workspace="main", path="", recursive=False, target_chat_id=None)` :
  - **Local (`target_chat_id=None`)** ou **Chat distant (`target_chat_id="..."`, lecture seule)** : explore la racine ou un sous-dossier `path` avec option `recursive=True`. Chaque appel est plafonné à `200` entrées (indicateur `truncated` dans la sortie structurée).
- `read_codex` :
  - Lit le contenu d'un fichier du workspace local ou d'un chat distant (`target_chat_id`).
  - Si `commit_hash` est fourni, lit l'état exact du fichier à ce commit sans impacter HEAD.
  - **Pagination Bornée Unifiée (`HEAD` & `commit_hash`)** : Chaque appel retourne au plus `800` lignes et `46 080` octets (~45 Ko) via `start_line` et `end_line`. Le retour inclut `total_lines`, `has_more`, `next_start_line` (et retourne un statut `"warning"` si `start_line > total_lines`). Le délestage `tools_output` est exempté sur `read_codex`.
  - **Traitement des Fichiers Binaires** : Interception automatique de tout fichier non textuel (images, audio/vidéo, PDF compilés, archives, exécutables, bases SQLite). Le contenu brut est omis (`status="warning"`, `error_code="binary_content_omitted"`) avec restitution des métadonnées (`mime`, `size_bytes`, blocs hexdump de 16 octets `total_lines`). Le Modèle **DOIT** s'abstenir de lire un binaire sous forme textuelle, et **DOIT** mobiliser un script Python via `code_executor` pour l'analyser ou `open_codex_ui` pour l'afficher à L'Utilisateur.
- `summarize_codex` :
  - Génère un résumé technique structuré d'un fichier volumineux via `MODEL_APOLLO` pour économiser le contexte du Modèle principal.
  - **Restriction Binaire** : La synthèse textuelle est non applicable aux fichiers binaires (`error_code="binary_summary_not_supported"`). Le Modèle DOIT utiliser `read_multimedia_file` (si image/audio/vidéo) ou un script dédié via `code_executor`.

### 3.2 Recherche Spatiale / Temporelle & Restauration
- `search_codex` :
  - **Mode Spatial (`trace_history=False`, défaut)** : Parcourt récursivement tous les sous-dossiers et recherche `query` (chaîne ou regex si `is_regex=True`) dans les fichiers actuels (ou au commit `commit_hash`), avec filtrage optionnel par chemins normalisés `target_paths=["dossier/fichier.py"]`.
  - **Mode Voyage Temporel / Pickaxe (`trace_history=True`)** : Parcourt récursivement l'arborescence et l'historique Git (jusqu'à `history_depth` commits) et retourne tous les diffs (`+` / `-`) où le motif `query` a été ajouté ou supprimé.
- `history_codex` :
  - Liste l'historique des commits Git (`hash`, message, date) globalement ou filtré sur `filename` (Le Modèle DOIT borner `limit <= 100`).
- `restore_codex` :
  - Restaure `filename` à l'état exact du commit `commit_hash` en créant un nouveau commit de restauration.
  - **Exclusion des espaces non versionnés** : Tout appel sur un espace non versionné (ex: `sandbox`) est refusé avec `error_code="workspace_not_versioned"`.
- `delete_codex(filename, workspace="main", recursive=False)` :
  - Supprime un fichier ou un dossier vide dans `main` ou `sandbox`.
  - Exige `recursive=True` pour supprimer un dossier non vide (`error_code="directory_not_empty"` sinon).
  - Purge récursivement le Registre Unifié dans `main` tout en préservant les éventuels fichiers uploadés liés au Vault.

---

## 4. PROTOCOLE DÉTERMINISTE DE CITATION URN

Toute référence à un fichier Codex dans la réponse textuelle du Modèle DOIT adopter la syntaxe canonique stricte :
`[libellé](codex:<current|chat_id>:<main|plans|generated|sandbox>/chemin_relatif)`

- **`CHAT_SCOPE`** : `'current'` (session active) ou `<chat_id>` (UUID d'une autre conversation découvert via `list_other_chats_ids`).
- **`WORKSPACE`** : `'main'`, `'plans'`, `'generated'` ou `'sandbox'`.
- **`CHEMIN_RELATIF`** : Chemin POSIX normalisé sans barre oblique initiale ni `./` ou `../` (ex: `rapport.md`, `src/utils.py`).
- **Interdictions formelles** : Il est ABSOLUMENT INTERDIT d'émettre des schémas `codex://...`, `file://...` ou des ancres brutes `#codex:...`.

### Exemples Concrets
- Livrable session active (`main`) : `[Rapport d'Audit](codex:current:main/rapport_audit.md)`
- Plan stratégique (`plans`) : `[Plan Migration](codex:current:plans/plan-173000_migration.md)`
- Image générée (`generated`) : `[Chat Astronaute](codex:current:generated/chat_astro.png)`
- Script session active (`sandbox`) : `[Pipeline Test](codex:current:sandbox/test_etl.py)`
- Session distante (Multi-Chat) : `[Spécification](codex:b8f1d3c0-7e2a:main/specs.md)`
