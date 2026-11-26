---
domain_id: ui_conversations
version: 6.24.0.1
updated_at: 2026-11-26
name: Conversations de l'Interface Utilisateur, État d'Exécution, Transcripts, Dossiers & Étiquettes
description: Découverte et filtrage SQL des conversations existantes, inspection de leur état d'exécution temps réel (running, pending_approval, queued, error, idle), lecture paginée et assainie des transcripts (list_ui_chats, read_ui_chat_transcript), et exploration des dossiers et étiquettes (list_ui_folders, list_ui_tags).
tools: [list_ui_chats, read_ui_chat_transcript, list_ui_folders, list_ui_tags]
keywords: [conversation, conversations, chat, chats, chat_id, transcript, transcripts, historique, running, en_cours, idle, queued, pending_approval, dossier, dossiers, folder, folder_id, tag, tags, etiquette, etiquettes]
---

# MANUEL OPÉRATIONNEL : CONVERSATIONS UI, ÉTAT RUNTIME, TRANSCRIPTS, DOSSIERS & ÉTIQUETTES (`ui_conversations`)

## 1. MATRICE D'ARBITRAGE (CONVERSATIONS SQL vs RAG VECTORIEL vs CODEX)
- **`list_ui_chats` + `read_ui_chat_transcript` (`ui_conversations`)** : **REQUIS** pour lister ou rechercher par mots-clés exacts les conversations existantes, vérifier leur état d'exécution temps réel (`running`, `pending_approval`, `queued`, `error`, `idle`), identifier un `chat_id` cible (notamment pour `manage_ui_automations(action="create", ..., target_mode="existing_chat")` ou `list_codex(target_chat_id=...)`), ou lire de façon paginée le transcript exact de la branche active (y compris les messages récents non encore vectorisés et le flux en cours de génération).
- **`search_sessions_context` (`memory_and_rag`)** : **REQUIS** pour la recherche sémantique inter-sessions par proximité vectorielle (`global_search=True`) et l'exploitation des documents ingérés (`AEC_smart_context`).
- **`list_other_chats_ids` (`codex_management`)** : Réservé à l'inventaire des conversations disposant spécifiquement de fichiers dans un espace Codex (`main`, `plans`, `generated`, `sandbox`).
- **Accessibilité** : Ces 4 outils sont strictement en lecture seule et PEUVENT être invoqués par Le Modèle principal comme par les Sous-Agents headless.

---

## 2. DÉCOUVERTE & ÉTAT D'EXÉCUTION DES CONVERSATIONS (`list_ui_chats`)
- `list_ui_chats(query=None, status="all", folder_id=None, tag=None, include_archived=False, include_current=True, start_date=None, end_date=None, limit=20, offset=0)` :
  - `query` (`Optional[str]`) : Recherche plein texte SQL sur les titres et contenus de messages (retourne un `snippet` par résultat). Si omis, retourne la liste triée par `updated_at` décroissant.
  - `status` (`Literal["all", "running", "pending_approval", "queued", "error", "idle"] = "all"`) :
    - `"running"` : Génération LLM en cours (`runtime.llm_running=True`), Sous-Agent actif (`runtime.running_subagents > 0`) ou automatisation CRON en cours (`runtime.automation_status="running"`).
    - `"pending_approval"` : Exécution suspendue en attente d'approbation d'outil.
    - `"queued"` : Automatisation en file d'attente (`runtime.automation_status="queued"`) ou temporisateur programmé (`runtime.timer_at`).
    - `"error"` : Dernier tour assistant en erreur ou interrompu sans tâche active.
    - `"idle"` : Conversation au repos.
  - `folder_id` / `tag` : Filtrent respectivement sur un identifiant de dossier (issu de `list_ui_folders`) ou une étiquette (issue de `list_ui_tags(global_scope=True)`).
  - `include_archived` (`bool = False`) / `include_current` (`bool = True`) : Incluent ou excluent les conversations archivées et la conversation courante (`is_current`).
  - `start_date` / `end_date` (`Optional[str]`) : Bornes inclusives sur `updated_at` au format ISO `"YYYY-MM-DD"`.
  - `limit` (`1..50`, défaut `20`) / `offset` (`>= 0`) : Pagination avec restitution de `total_matching`, `has_more` et `next_offset`.
  - **Champs retournés par conversation** : `id`, `title`, `folder_id`, `folder_name`, `unread`, `pinned`, `archived`, `is_current`, `tags`, `created_at`, `updated_at`, `runtime` et `snippet` (si `query` est fourni).

---

## 3. LECTURE PAGINÉE & ASSAINIE DES TRANSCRIPTS (`read_ui_chat_transcript`)
- `read_ui_chat_transcript(chat_id, offset=0, limit=20, order="newest_first", search_pattern=None, include_reasoning=False, include_tool_calls=False)` :
  - `chat_id` (`str`, **REQUIS**) : UUID de la conversation cible (ou `"current"`). Les conversations temporaires non sauvegardées (`local:*`) sont **ABSOLUMENT INTERDITES**.
  - **Branche active & Flux Live** : Extrait exclusivement la lignée causale active (`index` chronologique `1..total_branch_messages`). Si la conversation est `running` (`llm_running=True`), injecte automatiquement le flux partiel en cours sur le dernier message avec `"in_progress": true`.
  - `order` (`Literal["newest_first", "oldest_first"] = "newest_first"`) : Par défaut, retourne les échanges les plus récents en premier (`offset=0` cible la fin de la conversation). Utiliser `"oldest_first"` pour lire séquentiellement depuis le début.
  - `search_pattern` (`Optional[str]`) : Expression régulière insensible à la casse filtrant les messages pertinents avant pagination tout en conservant leur `index` chronologique d'origine.
  - **Hygiène du contexte** : Par défaut, `include_reasoning=False` omet les blocs de pensée (`<details type="reasoning">`, `<think>`) et `include_tool_calls=False` condense les appels d'outils en marqueurs compacts `[Outil: <nom>]` (les URI binaires Base64 sont systématiquement purgées et les pièces jointes résumées en `{id, name}`). Le Modèle NE DOIT activer `include_reasoning=True` ou `include_tool_calls=True` que sur besoin d'audit explicite.
  - **Pagination & Délestage** : Plafonne chaque page à `limit` (`1..50`) et `46 080` octets UTF-8 (`has_more`, `next_offset`). Dès que le payload JSON de la page dépasse `32 768` caractères (message unitaire volumineux ou cumul de messages sur la page), le délestage standard vers `tools_output/` (`read_tool_output`) s'applique automatiquement.

---

## 4. DOSSIERS & ÉTIQUETTES DE CONVERSATIONS (`list_ui_folders`, `list_ui_tags`)
- `list_ui_folders()` : Retourne l'arborescence des dossiers de conversations de L'Utilisateur (`[{"id": "...", "name": "..."}]`).
- `list_ui_tags(global_scope=False, chat_id=None)` :
  - **Format** : Étiquettes normalisées en anglais minuscules (`snake_case` ou `kebab-case` au singulier).
  - `global_scope=False` *(défaut)* : Retourne les étiquettes de la conversation courante ou de `chat_id` (`{"scope": "local", "chat_id": "...", "tags": [{"id": "...", "name": "..."}]}`).
  - `global_scope=True` : Retourne le catalogue complet des étiquettes de L'Utilisateur avec le compteur de conversations associées (`{"scope": "global", "total_tags": N, "tags": [{"id": "...", "name": "...", "chat_count": X}]}`). **Prérequis obligatoire** avant tout filtrage par `tag` dans `list_ui_chats` ou `search_sessions_context`.
