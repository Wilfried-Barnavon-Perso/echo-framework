---
domain_id: codex_management
version: 5.229.7.1
updated_at: 2026-10-24
name: Gestionnaire Documentaire & Versionnement Git (ECHO Codex)
description: Création, édition directe ou assistée, lecture paginée/historique, recherche spatiale ou temporelle, navigation multi-chat et versionnement Git sur les espaces main et sandbox.
tools: [create_codex, edit_codex, delete_codex, read_codex, restore_codex, search_codex, summarize_codex, list_codex, history_codex, open_codex_ui]
keywords: [codex, document, fichier, git, commit, version, diff, edit_codex, create_codex, open_codex_ui, workspace, sandbox, multi_chat, target_chat_id, list_all_chats, read_only]
---

# MANUEL OPÉRATIONNEL : ECHO CODEX (`codex_management`)

## 1. ARCHITECTURE MULTI-WORKSPACES, SÉCURITÉ & REGISTRE
Le Codex ECHO gère des fichiers texte et code versionnés sous Git et synchronisés avec le Registre Unifié (`resource_type="codex"`).
- **Séparation des Espaces (`workspace: Literal["main", "sandbox"] = "main"`)** :
  - `"main"` (Défaut) : Espace principal de la session pour les documents, architectures et livrables destinés à l'Utilisateur (indexés dans le Registre Unifié).
  - `"sandbox"` : Espace de travail technique isolé (non indexé dans le Registre principal) utilisé notamment pour écrire les scripts `.py` et `.js` destinés à être exécutés par `code_executor`.
- **Navigation Transverse Multi-Chat (Lecture Seule)** :
  - Chaque conversation de l'Utilisateur possède son propre dépôt Codex isolé sous `chats/{chat_id}/codex/`.
  - Le Modèle et l'Interface peuvent explorer et lire les fichiers de n'importe quelle autre conversation du même utilisateur sans quitter la session active.
  - **Invariant Inviolable (Read-Only)** : Toute consultation d'une conversation distante (`target_chat_id != current_chat_id`) s'effectue strictement en lecture seule. Aucune action mutative n'est autorisée sur un dépôt distant et aucune ressource n'est injectée dans la table SQLite `echo_resources` de la session active afin de préserver l'étanchéité du RAG contextuel.

---

## 2. CRÉATION, ÉDITION & OUVERTURE VISUELLE (`open_codex_ui`)

### 2.1 Création : `create_codex(filename, content, language=None, commit_message=None, workspace="main")`
- Crée un nouveau fichier versionné dans le Codex de la conversation courante.
- **Contrainte d'unicité** : Si `filename` existe déjà dans le `workspace` cible, `create_codex` renvoie une erreur. Le Modèle **DOIT** alors utiliser `edit_codex`.
- Validation préalable recommandée via `query_registry(resource_type="codex")` ou `list_codex(workspace=...)`.

### 2.2 Édition : `edit_codex(filename, new_content=None, instructions=None, commit_message=None, workspace="main")`
- **CONTRAINTE CRITIQUE D'EXCLUSIVITÉ MUTUELLE** : Les paramètres `new_content` et `instructions` sont **strictement mutuellement exclusifs**. Le Modèle DOIT fournir l'un **OU** l'autre, et ne **DOIT JAMAIS** fournir les deux simultanément :
  1. **Mode Remplacement Direct (`new_content="..."`, `instructions=None`)** : Remplace immédiatement l'intégralité du fichier par `new_content` et crée un commit Git (aucun appel LLM intermédiaire, latence nulle). Idéal pour les fichiers courts ou entièrement régénérés.
  2. **Mode Édition Assistée (`instructions="...", new_content=None`)** : Délègue la modification chirurgicale du fichier existant à un sous-agent éditeur (`MODEL_APOLLO`) qui applique les `instructions` sur le document complet et commite le résultat. Idéal pour modifier une section précise d'un long fichier sans réécrire tout le contenu.

### 2.3 Ouverture du HUD Codex : `open_codex_ui(filename, workspace="main")`
- Déclenche l'ouverture asynchrone du HUD Codex dans l'interface de l'Utilisateur sur le fichier `filename`.
- **RÈGLE DE PARCIMONIE** : N'appeler `open_codex_ui` **QUE** si l'Utilisateur en a fait la demande expresse, ou si une relecture visuelle immédiate par l'Utilisateur d'un livrable créé ou modifié est indispensable.

---

## 3. LECTURE, RECHERCHE, MULTI-CHAT & VOYAGE TEMPOREL GIT

### 3.1 Lecture, Synthèse et Inventaire Multi-Chat
- `list_codex(workspace="main", target_chat_id=None, list_all_chats=False)` :
  - **Cartographie globale (`list_all_chats=True`)** : Découvre dynamiquement tous les espaces Codex de l'Utilisateur sur le serveur et dresse un tableau avec l'ID et le titre résolu depuis `webui.db`, en distinguant la conversation courante des autres conversations accessibles.
  - **Listing d'un chat distant (`target_chat_id="..."`)** : Liste les fichiers du workspace d'une autre conversation en lecture seule (badge `🔒 [Lecture Seule]`).
  - **Listing local (Défaut)** : Retourne un tableau Markdown de tous les fichiers du workspace courant (`Fichier`, `Langage`, `Lignes`, `Taille`) ainsi que les statistiques de commits.
- `read_codex(filename, start_line=None, end_line=None, commit_hash=None, workspace="main", target_chat_id=None)` :
  - Lit le contenu d'un fichier du workspace local ou d'un chat distant (`target_chat_id`).
  - Si `commit_hash` est fourni, lit l'état exact du fichier à ce commit sans impacter le pointeur HEAD.
  - Prise en charge de plages partielles de lignes via `start_line` et `end_line`.
- `summarize_codex(filename, workspace="main")` :
  - Génère un résumé technique structuré d'un fichier volumineux via `MODEL_APOLLO` (`thinking="medium"`) pour économiser le contexte de l'Orchestrateur.

### 3.2 Recherche Spatiale / Temporelle & Restauration
- `search_codex(query, target_paths=None, is_regex=False, commit_hash=None, trace_history=False, history_depth=50, workspace="main")` :
  - **Mode Spatial (`trace_history=False`, défaut)** : Recherche `query` (chaîne ou regex si `is_regex=True`) dans les fichiers actuels (ou au commit `commit_hash`), avec filtrage optionnel par chemins `target_paths=["dossier/fichier.py"]`.
  - **Mode Voyage Temporel / Pickaxe (`trace_history=True`)** : Parcourt l'historique Git (jusqu'à `history_depth` commits) et retourne tous les diffs (`+` / `-`) où le motif `query` a été ajouté ou supprimé.
- `history_codex(filename=None, limit=20, workspace="main")` :
  - Liste l'historique des commits Git (`hash`, message, date) globalement ou filtré sur `filename` (conseillé : `limit <= 100`).
- `restore_codex(filename, commit_hash, workspace="main")` :
  - Restaure `filename` à l'état exact du commit `commit_hash` en créant un nouveau commit de restauration.
- `delete_codex(filename, workspace="main")` :
  - Supprime un fichier ou dossier du Codex et purge son entrée du Registre Unifié.
