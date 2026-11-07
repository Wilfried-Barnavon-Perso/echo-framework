---
domain_id: codex_management
version: 5.235.4.1
updated_at: 2026-11-05
name: Gestionnaire Documentaire & Versionnement Git (ECHO Codex)
description: Création, édition directe ou assistée, lecture paginée/historique, recherche spatiale ou temporelle, navigation multi-chat et versionnement Git sur les espaces main, plans, generated et sandbox.
tools: [create_codex, edit_codex, delete_codex, read_codex, restore_codex, search_codex, summarize_codex, list_codex, list_other_chats_ids, history_codex, open_codex_ui]
keywords: [codex, document, fichier, git, commit, version, diff, edit_codex, create_codex, open_codex_ui, workspace, main, plans, generated, sandbox, multi_chat, target_chat_id, list_other_chats_ids, read_only, wrap_tool_output, urn, links, binary, is_binary, binary_content_omitted]
---

# MANUEL OPÉRATIONNEL : ECHO CODEX (`codex_management`)

## 1. ARCHITECTURE MULTI-WORKSPACES, SÉCURITÉ & REGISTRE
Le Codex ECHO gère des fichiers texte, code et médias répartis en 4 espaces de travail et synchronisés avec le Registre Unifié :
- **Séparation des Espaces (`workspace: Literal["main", "plans", "generated", "sandbox"] = "main"`)** :
  - `"main"` (Défaut, versionné Git, écriture ouverte, `resource_type="codex"`) : Espace principal de la session pour les documents, architectures et livrables destinés à l'Utilisateur.
  - `"plans"` (Versionné Git, **lecture seule directe**, `resource_type="plan"`) : Espace dédié aux plans stratégiques (`{plan_id}_{slug}.md`) et listes de tâches (`tasks_{plan_id}_{slug}.md`). L'écriture et la suppression passent exclusivement par `build_plan`, `update_plan`, `update_tasks`, `process_plan` et `delete_plan`.
  - `"generated"` (Versionné Git, **lecture seule directe**, `resource_type="generated"`) : Espace dédié aux médias générés (images `<nom_court>.png` versionnées par essai et retouche via `generate_image`).
  - `"sandbox"` (Non versionné, non indexé, écriture ouverte) : Espace de travail technique isolé utilisé pour écrire les scripts `.py` et `.js` exécutés par `code_executor`.
- **Navigation Transverse Multi-Chat (Lecture Seule)** :
  - Le Modèle peut explorer et lire les fichiers de n'importe quelle autre conversation du même utilisateur sans quitter la session active.
  - **Invariant Inviolable (Read-Only)** : Toute consultation d'une conversation distante (`target_chat_id != current_chat_id`) s'effectue strictement en lecture seule. Aucune action mutative n'est autorisée sur un dépôt distant et aucune ressource n'est injectée dans le Registre Unifié de la session active.

---

## 2. CRÉATION, ÉDITION & OUVERTURE VISUELLE (`open_codex_ui`)

### 2.1 Création : `create_codex`
- Crée un nouveau fichier versionné dans le Codex de la conversation courante.
- **Contrainte d'unicité** : Si `filename` existe déjà dans le `workspace` cible, `create_codex` renvoie une erreur. Le Modèle **DOIT** alors utiliser `edit_codex`.
- Validation préalable recommandée via `query_registry(resource_type="codex")` ou `list_codex(workspace=...)`.

### 2.2 Édition : `edit_codex`
- **CONTRAINTE CRITIQUE D'EXCLUSIVITÉ MUTUELLE** : Les paramètres `new_content` et `instructions` sont **strictement mutuellement exclusifs**. Le Modèle DOIT fournir l'un **OU** l'autre, et ne **DOIT JAMAIS** fournir les deux simultanément :
  1. **Mode Remplacement Direct (`new_content="..."`, `instructions=None`)** : Remplace immédiatement l'intégralité du fichier par `new_content` et crée un commit Git (latence nulle). Idéal pour les fichiers courts ou entièrement régénérés.
  2. **Mode Édition Assistée (`instructions="...", new_content=None`)** : Délègue la modification chirurgicale du fichier existant à un sous-agent éditeur (`MODEL_APOLLO`) qui applique les `instructions` sur le document complet et commite le résultat. Idéal pour modifier une section précise d'un long fichier sans réécrire tout le contenu.
- **Restriction Binaire** : L'édition assistée par instructions est formellement refusée sur les fichiers binaires (`error_code="binary_edit_not_supported"`).

### 2.3 Ouverture du HUD Codex : `open_codex_ui`
- Déclenche l'ouverture asynchrone du HUD Codex dans l'interface de l'Utilisateur sur le fichier `filename` du workspace local ou distant (`target_chat_id`).
- Si `target_chat_id` est spécifié (`target_chat_id != current_chat_id`), le HUD bascule sur la conversation distante en lecture seule.
- Si `commit_hash` est fourni, le HUD charge l'état historique exact du fichier à cette révision Git.
- **RÈGLE DE PARCIMONIE** : N'appeler `open_codex_ui` **QUE** si l'Utilisateur en a fait la demande expresse, ou si une relecture visuelle immédiate par l'Utilisateur d'un livrable créé ou modifié est indispensable. Il est interdit d'appeler cet outil depuis un sous-agent headless.
- **NATURE DE L'OUTIL** : `open_codex_ui` assure l'affichage statique dans l'éditeur Monaco ou la prévisualisation documentaire passive (Markdown, HTML statique, images, PDF). Pour déployer et exécuter une interface applicative interactive communicante, utiliser `code_executor` avec `echo_monitor` (voir guide `code_and_files`).

---

## 3. LECTURE, RECHERCHE, MULTI-CHAT & VOYAGE TEMPOREL GIT

### 3.1 Lecture, Synthèse et Inventaire Multi-Chat
- `list_other_chats_ids` :
  - Découvre dynamiquement les autres conversations de l'Utilisateur disposant d'un espace Codex et retourne leur `chat_id` et leur titre (la conversation courante est exclue). Utilisé en amont de `list_codex(target_chat_id=...)` et `read_codex(target_chat_id=...)`.
- `list_codex` :
  - **Chat distant (`target_chat_id="..."`)** : Liste les fichiers du workspace d'une autre conversation en lecture seule.
  - **Local (`target_chat_id=None`)** : Liste tous les fichiers du workspace courant (`Fichier`, `Langage`, `Lignes`, `Taille`) et les statistiques de commits.
- `read_codex` :
  - Lit le contenu d'un fichier du workspace local ou d'un chat distant (`target_chat_id`).
  - Si `commit_hash` est fourni, lit l'état exact du fichier à ce commit sans impacter HEAD.
  - Prise en charge de plages partielles de lignes via `start_line` et `end_line`.
  - **Traitement des Fichiers Binaires** : Interception automatique de tout fichier non textuel (images, audio/vidéo, PDF compilés, archives, exécutables, bases SQLite). Le contenu brut est omis (`status="warning"`, `error_code="binary_content_omitted"`) avec restitution des métadonnées (`mime`, `size_bytes`, blocs hexdump de 16 octets `total_lines`). Le Modèle **DOIT** s'abstenir de lire un binaire sous forme textuelle, et **DOIT** mobiliser un script Python via `code_executor` pour l'analyser ou `open_codex_ui` pour l'afficher à l'Utilisateur.
- `summarize_codex` :
  - Génère un résumé technique structuré d'un fichier volumineux via `MODEL_APOLLO` pour économiser le contexte de l'Orchestrateur.
  - **Restriction Binaire** : La synthèse textuelle est non applicable aux fichiers binaires (`error_code="binary_summary_not_supported"`). Le Modèle DOIT utiliser `read_multimedia_file` (si image/audio/vidéo) ou un script dédié via `code_executor`.

### 3.2 Recherche Spatiale / Temporelle & Restauration
- `search_codex` :
  - **Mode Spatial (`trace_history=False`, défaut)** : Recherche `query` (chaîne ou regex si `is_regex=True`) dans les fichiers actuels (ou au commit `commit_hash`), avec filtrage optionnel par chemins `target_paths=["dossier/fichier.py"]`.
  - **Mode Voyage Temporel / Pickaxe (`trace_history=True`)** : Parcourt l'historique Git (jusqu'à `history_depth` commits) et retourne tous les diffs (`+` / `-`) où le motif `query` a été ajouté ou supprimé.
- `history_codex` :
  - Liste l'historique des commits Git (`hash`, message, date) globalement ou filtré sur `filename` (conseillé : `limit <= 100`).
- `restore_codex` :
  - Restaure `filename` à l'état exact du commit `commit_hash` en créant un nouveau commit de restauration.
- `delete_codex` :
  - Supprime un fichier ou dossier du Codex et purge son entrée du Registre Unifié.

---

## 4. PROTOCOLE DÉTERMINISTE DE CITATION URN

Toute référence à un fichier Codex dans la réponse textuelle du Modèle DOIT adopter la syntaxe canonique stricte :
`[libellé](codex:<current|chat_id>:<main|plans|generated|sandbox>/chemin_relatif)`

- **`CHAT_SCOPE`** : `'current'` (session active) ou `<chat_id>` (UUID d'une autre conversation découvert via `list_other_chats_ids`).
- **`WORKSPACE`** : `'main'`, `'plans'`, `'generated'` ou `'sandbox'`.
- **`CHEMIN_RELATIF`** : Chemin POSIX normalisé sans barre oblique initiale ni `./` ou `../` (ex: `rapport.md`, `src/utils.py`).
- **Interdictions formelles** : Ne jamais émettre de schémas `codex://...`, `file://...` ni d'ancres brutes `#codex:...`.

### Exemples Concrets
- Livrable session active (`main`) : `[Rapport d'Audit](codex:current:main/rapport_audit.md)`
- Plan stratégique (`plans`) : `[Plan Migration](codex:current:plans/plan-173000_migration.md)`
- Image générée (`generated`) : `[Chat Astronaute](codex:current:generated/chat_astro.png)`
- Script session active (`sandbox`) : `[Pipeline Test](codex:current:sandbox/test_etl.py)`
- Session distante (Multi-Chat) : `[Spécification](codex:b8f1d3c0-7e2a:main/specs.md)`
