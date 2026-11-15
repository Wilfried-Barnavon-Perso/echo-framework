# 🌌 ECHO Framework - Connaissance Sémantique : `13-owui-actions`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `13-owui-actions`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier gère les **Actions Interactives (Boutons sous les messages)** conservées dans Open WebUI. Les anciens HUDs flottants injectés par Data Islands (`agent_monitor_action.py`, `web_navigation_replay_action.py`, `echo_codex_action.py`, `echo_identity_vault_action.py`) ont été supprimés et remplacés par les onglets natifs SvelteKit de `ChatTabs` (`EchoCognitionTab.svelte`, `EchoBrowserTab.svelte`, `EchoCodexTab.svelte`, `EchoVaultTab.svelte`).

## 2. Cartographie des Fichiers et Algorithmes

### Sécurité & Identité
- **`reset_auth_action.py` (v5.235.5.1 - Priorité 10)** : Action rapide d'urgence pour purger spécifiquement les tokens et clés liés à l'authentification **Google/PKCE** et OAuth2 en cas de désynchronisation via la méthode factorisée `EchoAuth.delete_auth_data_by_prefixes` sur `identity.db`.

### Gestion Contextuelle (Migration & Vectoriel)
- **`resume_in_new_chat_action.py` (v5.239.13.1 - Priorité 20)** : Action interactive de migration et de transfert de contexte vers une nouvelle session ("Résume et Transfert vers un nouveau chat"). Extrait l'intégralité de la branche active depuis `Chats.get_chat_by_id` via le wrapper asynchrone `_call_owui` (y compris les sorties d'outils `m["output"]`), propage automatiquement les étiquettes (`tags`) du chat parent vers la nouvelle conversation via `Chats.add_chat_tag_by_id`, construit les transcriptions hors de l'Event Loop (`asyncio.to_thread(_prepare_transcripts, ...)` après purge des blocs `<think>`/`<thought>` et `<details type="reasoning">` avant pré-troncature), exécute une distillation cognitive en 2 passes via `EchoGeminiClient.call_distillation` (`USR_ACTION_RESUME_GLOBAL` puis `USR_ACTION_RESUME_RECENT`) accompagnée d'un HUD centré non-bloquant (`#echo-migration-hud` avec `data-echo-scope="global"`, `timeout=15.0` sur chaque `call_execute`), insère la nouvelle conversation via `Chats.insert_new_chat` avec une paire de messages initiale (`user` racine + `assistant` de synthèse) et rollback asynchrone `Chats.delete_chat_by_id_and_user_id` en cas d'échec, duplique sous le même verrou `get_chat_clone_lock` les ressources physiques, le dépôt Git Codex, `session.db` (`sync_resumed_session` avec `live_msg_ids` et `anchor_msg_id=assistant_msg_id`) et la mémoire vectorielle `COLLECTION_SESSION_RAG` (`duplicate_session_rag` filtrée sur `live_msg_ids` et réancrée sur `anchor_msg_id`, retournant un bilan `RagCloneReport`). Le bilan est traité après libération du verrou, sans bloquer la migration : copie complète ⇒ HUD « fragments transférés » ; copie incomplète ou exception ⇒ journalisation, retrait de `#echo-migration-hud` (z-index 99999) puis modale native `EchoEvents.confirm` (cause et ratio `cloned/eligible`) avant toute redirection ; sur refus, annulation ou expiration de l'`__event_call__`, aucune redirection (toast d'information, la nouvelle conversation reste accessible dans la barre latérale). Rien n'est injecté dans le contexte du Modèle. La redirection s'effectue ensuite via `window.location.replace('/c/{new_chat_id}')`.
- **`purge_memory_action.py` (v5.226.18.0 - Priorité 30)** : Interface scrollable permettant la suppression ciblée et granulaire des souvenirs vectoriels (vecteurs orphelins ou erronés) directement dans Qdrant. Intègre une vérification stricte de l'annulation UI (bouton "Annuler" ou modal vide valant `False`).

### Export
- **`print_pdf_action.py` (v5.231.46.1 - Priorité 100)** : Fonctionnalité native d'export des conversations au format PDF pour l'archivage local de la dialectique, acheminant l'exécution JS via `EchoEvents.call_execute` (avec support UCTP et double transport Socket.IO + SSE/RPC).

### Actions Dépréciées & Supprimées
- `echo_identity_vault_action.py` : Supprimé. Remplacé par l'onglet natif `EchoVaultTab.svelte` et `routers/vault.py`.
- `agent_monitor_action.py` : Supprimé. Remplacé par l'onglet natif `EchoCognitionTab.svelte` et `routers/cognition.py`.
- `web_navigation_replay_action.py` : Supprimé. Remplacé par l'onglet natif `EchoBrowserTab.svelte` et `routers/cognition.py` (`/browser-frames`).
- `echo_codex_action.py` : Supprimé. Remplacé par l'onglet natif `EchoCodexTab.svelte`, `EchoCodexUI.get_codex_bridge_js()` et `routers/codex.py`.

## 3. Dépendances Logiques
- Ces actions nécessitent souvent la déclaration de hooks JS spécifiques natifs à Open WebUI (ex: `window.mcpAlert`, protection `allow-modals`) configurés par `echo_ui.py`.
- L'Identity Vault partage sa logique de stockage SQLite avec l'ensemble du Cortex ECHO via le module `echo_constants.py`.
