# 🌌 ECHO Framework - Connaissance Sémantique : `15-owui-events`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `15-owui-events`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier regroupe les **Fonctions Événementielles (Event Functions, `type: event`)** d'Open WebUI. Contrairement aux Filtres (`inlet`/`outlet`) ou aux Actions interactives, une fonction `Event` est invoquée de manière asynchrone par le bus d'événements interne d'Open WebUI (`publish_event` ➔ `dispatch_event_functions` dans `open_webui/utils/events.py`) lors du déclenchement d'événements du cycle de vie applicatif (`chat.cloned`, `chat.created`, etc.).

Caractéristiques architecturales :
- **Activation Unitaire (`is_active`)** : Les fonctions `type: event` ne possèdent ni le flag `is_global` (`/toggle/global` est sans objet), ni attribut `priority` dans leurs `Valves`. Elles sont sélectionnées globalement dès que `is_active == True` (`Functions.get_functions_by_type("event", active_only=True)`).
- **Isolation Utilisateur par Payload** : Bien qu'enregistrée au niveau système, chaque exécution reçoit l'identité de l'utilisateur déclencheur dans `event["actor"]["id"]` et l'identifiant de la ressource cible dans `event["subject"]["id"]`.

## 2. Cartographie des Fichiers et Algorithmes

### `echo_chat_cloned_event.py`
**Rôle** : Synchronisation Événementielle Atomique du Clonage et du Fork de Session ECHO (v5.231.27.2).
- **Sémantique** : Intercepte l'événement `chat.cloned` émis par Open WebUI lors d'une action "Dupliquer la conversation" (Fork au niveau d'un message via `POST /api/v1/chats/{id}/fork`) ou "Cloner" (Sidebar via `POST /api/v1/chats/{id}/clone`), affiche un HUD de progression bloquant (`#echo-clone-hud` avec `data-echo-scope="global"` calqué sur `#echo-migration-hud`), et délègue la duplication atomique du conteneur souverain d'ECHO (`/app/backend/data/users/{user_id}/chats/{chat_id}/`) ainsi que des vecteurs Qdrant (`COLLECTION_SESSION_RAG`) au moteur partagé `echo_session_clone` (`sync_cloned_session` + `duplicate_session_rag`), sans aucune dépendance ni lecture dans la base `webui.db`.
- **Algorithme Clé** :
  1. **Verrouillage Concurrentiel (`get_chat_clone_lock`) & HUD Bloquant (`#echo-clone-hud`)** : Acquiert un verrou `asyncio.Lock` indexé sur `new_chat_id` dans `echo_paths._CHAT_CLONE_LOCKS` (attendu par `wait_for_chat_clone(chat_id)` dans `new_context_filter.py`) et monte via `_resolve_user_events` un overlay plein écran bloquant les interactions (`click`, `keydown`, `submit`) avec barre de progression (`#echo-clone-progress`), étapes temps-réel et retrait garanti en `finally:` accompagné d'une ré-hydratation `window.EchoWM.hydrateChatState(new_chat_id)`.
  2. **Copie Atomique SQLite (`backup_session_db` & `_is_session_db_populated`)** : Duplique `session.db` du parent vers l'enfant via l'API native `src.backup(dst)` exécutée dans `asyncio.to_thread(sync_cloned_session, ...)`, en écrasant toute base `session.db` vide pré-initialisée par `EchoWindowManager` lors de la navigation SPA vers `/c/{new_chat_id}`.
  3. **Résolution Causale & Élagage à l'État du Message Cloné (`prune_forked_history`)** :
     - Lorsque `forked_from_message_id` est fourni (ID d'un message `assistant` ou `user`), résout l'ensemble des sous-tours et messages ancêtres par jointure sur `cumulative_hash` entre `suture_index`, `cognitive_signatures`, `message_shadows`, `tool_journal` et `call_bridge` (via `_extract_call_ids_from_json`), en absorbant le décalage `+1s` de fin de tour (`save_call_bridge` / `save_message_shadow`).
     - Distingue un **Fork sur le dernier message** (`is_partial_fork == False`, traité comme un clone complet préservant l'intégralité de l'état) d'un **Fork intermédiaire** (`is_partial_fork == True`).
     - Sur un Fork intermédiaire, élague les enregistrements postérieurs au point de fork dans `suture_index`, `message_shadows`, `cognitive_signatures`, `tool_journal`, `rich_payloads`, `cognitive_threads`, `processed_files`, `call_bridge`, `echo_resources` et `auth_data` (`cascade_%`), et réaligne `last_model_id` sur la dernière signature conservée (`ORDER BY updated_at DESC, rowid DESC LIMIT 1`).
  4. **Duplication de l'Espace Utilisateur Limitée au Point de Fork (`duplicate_session_domains` & `align_codex_git_to_cutoff`)** :
     - Copie récursivement `files/`, `codex/`, `n8n_workflows/` et `dependencies/`.
     - Sur un Fork intermédiaire, restaure d'abord le dépôt Git `codex/main` au dernier commit $\le$ `cutoff_ts` (`CodexRepo.restore_repo_to_timestamp(cutoff_ts)` avec synchronisation de `echo_resources`), et élague dans `files/`, `n8n_workflows/` et `codex/sandbox/` tout fichier créé après `cutoff_ts` (`_prune_domain_files_to_cutoff`).
     - Réécrit ensuite les symlinks (`rewrite_symlinks`), renomme les fichiers physiques `U_{uid}_C_{orig_cid}_T_*` dans `files/` et `n8n_workflows/` (`rename_cloned_files`), et met à jour `chat_id: {new_cid}` dans le frontmatter YAML des plans (`rewrite_codex_plan_frontmatter`).
  5. **Réajustement Intégral du `chat_id` (`reassign_session_ids`) & Duplication RAG Qdrant (`duplicate_session_rag`)** :
     - Met à jour `chat_id = new_cid` et remplace `_C_{orig_cid}_T_` $\rightarrow$ `_C_{new_cid}_T_` ainsi que `/chats/{orig_cid}/` $\rightarrow$ `/chats/{new_cid}/` dans `suture_index`, `message_shadows`, `cognitive_threads`, `processed_files`, `echo_resources` (`id`, `name`, `storage_path`, `summary`), `tool_journal`, `call_bridge` et `echo_settings` (`wm_state`), puis invalide le cache RAM `EchoWindowManager._chat_states`.
     - Duplique les vecteurs Qdrant de `COLLECTION_SESSION_RAG` (filtrés par `timestamp <= cutoff_ts` si `is_partial_fork`) avec UUID5 déterministe et réajustement de `chat_id` et `source_id`.

## 3. Dépendances Logiques
- S'appuie sur `echo_session_clone.py` (`sync_cloned_session`, `duplicate_session_rag`), `echo_events.py` (`EchoEvents`), `echo_paths.py` (`get_echo_session_path`, `get_chat_clone_lock`) et `echo_codex_git.py` (`CodexRepo.restore_repo_to_timestamp`) dans `14-owui-libs`.
- Déployé et activé automatiquement par `00-echo-scripts/config-owui.sh` via la boucle `functions:functions:Event`.
