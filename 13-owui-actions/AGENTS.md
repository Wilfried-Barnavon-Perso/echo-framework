# 🌌 ECHO Framework - Connaissance Sémantique : `13-owui-actions`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `13-owui-actions`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier gère les **Actions Interactives (Boutons sous les messages)** conservées dans Open WebUI. Les anciens HUDs flottants injectés par Data Islands (`agent_monitor_action.py`, `web_navigation_replay_action.py`, `echo_codex_action.py`, `echo_identity_vault_action.py`) ont été supprimés et remplacés par les onglets natifs SvelteKit de `ChatTabs` (`EchoCognitionTab.svelte`, `EchoBrowserTab.svelte`, `EchoCodexTab.svelte`, `EchoVaultTab.svelte`).

## 2. Cartographie des Fichiers et Algorithmes

### Export
- **`print_pdf_action.py` (v6.18.23.1 - Priorité 100)** : Fonctionnalité native d'impression et d'export PDF ciblée exclusivement sur le message courant (`#message-{safe_msg_id}` extrait depuis `__metadata__["message_id"]` ou `body["id"]`), acheminant l'exécution JS via `EchoEvents.call_execute` (avec support UCTP et double transport Socket.IO + SSE/RPC) et masquant les barres de boutons d'actions en `@media print`.

### Actions Dépréciées & Supprimées
- `purge_memory_action.py` : Supprimé. Remplacé par la vue native `Espace de travail > Mémoire` (`EchoMetaArtifacts.svelte` et `routers/meta_artifacts.py`).
- `reset_auth_action.py` : Supprimé. Remplacé par l'onglet natif `LlmAuth.svelte` (`Paramètres > Profil > Authentification LLM`) et le routeur souverain `routers/llm_auth.py` (`/echo/v1/llm-auth`).
- `resume_in_new_chat_action.py` : Supprimé. Remplacé par l'entrée `Poursuivre dans un nouveau chat` de `ChatMenu.svelte`, le module `14-owui-libs/echo_session_resume.py` et le routeur souverain `routers/chat_actions.py` (`POST /echo/v1/chats/{chat_id}/resume`).
- `echo_identity_vault_action.py` : Supprimé. Remplacé par l'onglet natif `EchoVaultTab.svelte` et `routers/vault.py`.
- `agent_monitor_action.py` : Supprimé. Remplacé par l'onglet natif `EchoCognitionTab.svelte` et `routers/cognition.py`.
- `web_navigation_replay_action.py` : Supprimé. Remplacé par l'onglet natif `EchoBrowserTab.svelte` et `routers/cognition.py` (`/browser-frames`).
- `echo_codex_action.py` : Supprimé. Remplacé par l'onglet natif `EchoCodexTab.svelte`, `EchoCodexUI.get_codex_bridge_js()` et `routers/codex.py`.

## 3. Dépendances Logiques
- Ces actions nécessitent souvent la déclaration de hooks JS spécifiques natifs à Open WebUI (ex: `window.mcpAlert`, protection `allow-modals`) configurés par `echo_ui.py`.
- L'Identity Vault partage sa logique de stockage SQLite avec l'ensemble du Cortex ECHO via le module `echo_constants.py`.
