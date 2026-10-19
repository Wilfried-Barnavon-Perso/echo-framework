# 🌌 ECHO Framework - Connaissance Sémantique : `15-owui-events`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `15-owui-events`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier regroupe les **Fonctions Événementielles (Event Functions, `type: event`)** d'Open WebUI. Contrairement aux Filtres (`inlet`/`outlet`) ou aux Actions interactives, une fonction `Event` est invoquée de manière asynchrone par le bus d'événements interne d'Open WebUI (`publish_event` ➔ `dispatch_event_functions` dans `open_webui/utils/events.py`) lors du déclenchement d'événements du cycle de vie applicatif (`chat.cloned`, `chat.created`, etc.).

Caractéristiques architecturales :
- **Activation Unitaire (`is_active`)** : Les fonctions `type: event` ne possèdent ni le flag `is_global` (`/toggle/global` est sans objet), ni attribut `priority` dans leurs `Valves`. Elles sont sélectionnées globalement dès que `is_active == True` (`Functions.get_functions_by_type("event", active_only=True)`).
- **Isolation Utilisateur par Payload** : Bien qu'enregistrée au niveau système, chaque exécution reçoit l'identité de l'utilisateur déclencheur dans `event["actor"]["id"]` et l'identifiant de la ressource cible dans `event["subject"]["id"]`.

## 2. Cartographie des Fichiers et Algorithmes

### `echo_chat_cloned_event.py`
**Rôle** : Synchronisation Événementielle Atomique du Clonage et du Fork de Session ECHO (v5.227.1.1).
- **Sémantique** : Intercepte l'événement `chat.cloned` émis par Open WebUI lors d'une action "Dupliquer la conversation" (Fork au niveau d'un message via `POST /api/v1/chats/{id}/fork`) ou "Cloner" (Sidebar via `POST /api/v1/chats/{id}/clone`), et délègue la duplication atomique du conteneur souverain d'ECHO (`/app/backend/data/users/{user_id}/chats/{chat_id}/`) au moteur partagé `echo_session_clone.sync_cloned_session`, sans aucune dépendance ni lecture dans la base `webui.db`.
- **Algorithme Clé** :
  1. **Verrouillage Concurrentiel (`get_chat_clone_lock`)** : Acquiert un verrou `asyncio.Lock` indexé sur `new_chat_id` dans `echo_paths._CHAT_CLONE_LOCKS`. Si l'utilisateur soumet immédiatement un message dans la conversation clonée, le filtre Inlet `new_context_filter.py` appelle `await wait_for_chat_clone(chat_id)` et attend la fin de la duplication avant d'instancier `EchoStateManager`.
  2. **Copie Atomique SQLite (`backup_session_db`)** : Duplique `session.db` du parent vers l'enfant via l'API native `src.backup(dst)` exécutée dans `asyncio.to_thread(sync_cloned_session, ...)`, garantissant une copie cohérente même lorsque la base parente est en mode `WAL`.
  3. **Réassignation & Réécriture des Identifiants Composites (`reassign_session_ids`)** :
     - Met à jour `chat_id = new_cid` dans `suture_index`, `message_shadows`, `cognitive_threads` et `processed_files`.
     - Remplace le marqueur composite `_C_{orig_cid}_T_` par `_C_{new_cid}_T_` dans `echo_resources.id`, `echo_resources.storage_path` et `processed_files.file_id` (assurant la compatibilité avec `web_navigation_replay_action.py` et `resolve_upload_file_path`).
     - Réécrit les segments `/chats/{orig_cid}/` en `/chats/{new_cid}/` dans `echo_resources.storage_path`.
  4. **Remontée Généalogique & Élagage Causal en cas de Fork (`prune_forked_history`)** :
     - Lorsque `forked_from_message_id` est fourni (ID d'un message `assistant` ou `user`), résout le nœud de suture correspondant via `suture_index`, `cognitive_signatures` (`message_id = asst_msg_id` ➔ `cumulative_hash`) et `message_shadows`, puis remonte la chaîne `parent_hash` jusqu'à la racine.
     - Distingue un **Fork sur le dernier message** (`is_partial_fork == False`, traité comme un clone complet préservant l'intégralité de l'état, des directives AEC et de `context_stats`) d'un **Fork intermédiaire** (`is_partial_fork == True`).
     - Sur un Fork intermédiaire, purge tous les enregistrements postérieurs à `cutoff_ts` dans `suture_index`, `message_shadows`, `cognitive_signatures`, `tool_journal`, `rich_payloads`, `cognitive_threads`, `processed_files`, `call_bridge`, `echo_resources`, `plans` et `codex_docs`.
     - Purge uniquement les `aec_event_%` et `aec_directive_%` dont `updated_at > cutoff_ts`, ainsi que `context_stats` sur un fork partiel.
  5. **Réinitialisation Vectorielle Passive (`is_embedded = 0`)** : Passe `is_embedded = 0` sur `message_shadows` dans la nouvelle base afin que `conversation_rag_filter.py` indexe de manière asynchrone l'historique sous le filtre `chat_id = new_cid` dans Qdrant.
  6. **Duplication Physique & Alignement Git Codex (`duplicate_session_domains` & `align_codex_git_to_cutoff`)** :
     - Copie récursivement `files/`, `codex/`, `n8n_workflows/` et `dependencies/` avec `shutil.copytree(..., symlinks=True)`, réécrit la cible des symlinks (`_rewrite_symlinks`) et renomme les fichiers physiques `U_{uid}_C_{orig_cid}_T_*` dans `files/` (`_rename_cloned_files`).
     - Sur un Fork intermédiaire, invoque `CodexRepo.restore_repo_to_timestamp(cutoff_ts)` (`dulwich.index.build_index_from_tree`) pour restaurer l'arbre de travail, l'index et `HEAD` du dépôt Git `codex/main` au dernier commit $\le$ `cutoff_ts`, et synchronise `echo_resources` (`resource_type = 'codex'`) ainsi que `codex_docs`.

## 3. Dépendances Logiques
- S'appuie sur `echo_session_clone.py` (`sync_cloned_session`), `echo_paths.py` (`get_echo_session_path`, `get_chat_clone_lock`) et `echo_codex_git.py` (`CodexRepo.restore_repo_to_timestamp`) dans `14-owui-libs`.
- Déployé et activé automatiquement par `00-echo-scripts/config-owui.sh` via la boucle `functions:functions:Event`.
