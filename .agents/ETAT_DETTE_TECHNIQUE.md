# État de la Dette Technique ECHO v6 (Post-Assainissement 6.6.9)

Ce document recense les 5 chantiers architecturaux différés à l'issue de l'audit d'assainissement de la version `6.6.9`, avec leurs ancrages exacts dans le code source, causes racines, risques techniques et architectures cibles.

---

## 1. Éradication finale du pont `eval(JS)` V5 (UCTP & Exécution Dynamique)

### Ancrages vérifiés
- **Façade d'exécution distante** : `14-owui-libs/echo_events.py` (`EchoEvents._build_uctp_frames` L105, `emit_execute` L185, `call_execute` L269).
- **Fragmentation SSE UCTP** : `14-owui-libs/echo_window_manager.py` (`EchoWindowManager._broadcast_execute` L297, L357, L394).
- **Évaluateur JS client** : `16-docker-echo-webui/src/lib/components/chat/Chat.svelte` L1808 (`const asyncFunction = new Function(...)`).
- **Modales JS injectées** : `14-owui-libs/echo_ui.py` (`EchoUI.get_custom_modals_js()` L2539), consommées via `call_execute` dans :
  - `12-owui-tools/generalist_tools.py` L218, L255, L273 (`ask_user`) *(Mis à jour : `ask_user_input` migré vers le composant Svelte natif `AskUserCard.svelte`)* ;
  - `12-owui-tools/agent_orchestration_tool.py` L251, L267 (`forge_skill`) ;
  - `12-owui-tools/identity_vault_tool.py` L184, L189, L212, L217 (`manage_identity`) ;
  - `12-owui-tools/strategic_planner.py` L566, L576 (`process_plan`).
- **Sandbox interactive `echo_monitor`** : *(Migré en 6.6.17)* `12-owui-tools/code_executor_tool.py` (`v6.6.16.1`) et `14-owui-libs/echo_ui.py` (`v6.6.16.1`, suppression de `get_floating_monitor_js()`) ont été raccordés nativement à `EchoCanvasTab.svelte` via le bus SSE/RPC `/echo/v1/wm` et `EchoWindowManager` (`v6.6.16.2`).
- **HUDs transitoires & Actions** :
  - ~~`#echo-migration-hud` : `13-owui-actions/resume_in_new_chat_action.py`~~ *(Supprimé : migré en natif vers `ChatMenu.svelte` + `14-owui-libs/echo_session_resume.py` et `routers/chat_actions.py` ; de même `13-owui-actions/reset_auth_action.py` supprimé et migré vers `Settings/LlmAuth.svelte` + `routers/llm_auth.py`)* ;
  - `#echo-clone-hud` : `15-owui-events/echo_chat_cloned_event.py` L83-114, L159, L173, L213 (`emit_execute`) ;
  - Export PDF : `13-owui-actions/print_pdf_action.py` L80 (`call_execute`) ;
  - ~~Purge mémoire : `13-owui-actions/purge_memory_action.py`~~ *(Supprimé : migré en natif vers `EchoMetaArtifacts.svelte` + `routers/meta_artifacts.py`)*.

### Cause racine & Risques
- Le protocole UCTP V5 transporte du code JavaScript brut depuis les plugins Python vers le navigateur via Socket.IO (`__event_call__`) et SSE (`wm_uctp_exec` / `wm_uctp_chunk`), exécuté via `new Function(...)` côté Svelte (`Chat.svelte:1808`) et le runtime `EchoWM`.
- Surface d'exécution dynamique (`eval`/`new Function`) incompatible avec une politique CSP stricte (`script-src 'self'`), latence de fragmentation/réassemblage, et dépendance à l'état DOM impératif (`#echo-migration-hud`, `#echo-clone-hud`).

### Architecture cible
- *(Mis à jour)* Le bus Window Manager a migré du SSE `/echo/v1/wm/stream` vers le WebSocket bidirectionnel `/echo/v1/wm/ws` (`16-docker-echo-webui/backend/open_webui/echo/routers/wm.py` v6.17.4.1). Remplacer les appels résiduels `emit_execute` / `call_execute` et la fragmentation UCTP par des événements typés déclaratifs sur `/echo/v1/wm/ws` avec retour RPC JSON structuré sur `POST /echo/v1/wm/rpc` :
  - `ui_modal_request` / `ui_modal_response` (Svelte natif) pour `forge_skill`, `manage_identity` et `process_plan` *(déjà migré vers `AskUserCard.svelte` pour `ask_user_input`, et `EchoMetaArtifacts.svelte` pour la purge mémoire)* ;
  - ~~`monitor_frame_open` / `monitor_frame_event` pour `echo_monitor` dans `code_executor_tool.py`~~ *(Réalisé en 6.6.17 via `window_mounted` / `echo_canvas_event` et `canvas_ui_event`)* ;
  - `hud_progress` (`migration` / `clone`) rendu par un composant Svelte global dans `(app)/+layout.svelte` ;
  - Action d'export PDF portée par un handler client natif déclenché sur événement typé.
- Supprimer `new Function` dans `Chat.svelte:1808`, `EchoUI.get_custom_modals_js()`, `_build_uctp_frames` et `_broadcast_execute`.

---

## 2. Fusion SSE Cognition dans `/echo/v1/wm/stream` & fin du polling SQLite `0.25s`

### Ancrages vérifiés
- **Polling SQLite actif** : `16-docker-echo-webui/backend/open_webui/echo/routers/cognition.py` (`_get_cognition_state_sig` L530-557, boucle `await asyncio.sleep(0.25)` L594 dans `get_cognition_stream` L573-607).
- **Compteur monotone existant** : `14-owui-libs/echo_subagent_hub.py` (`bump_cognition_seq` L57, L135, L156, L230, L247).
- **Émetteurs** : `14-owui-libs/echo_state_manager.py` L218-219 (`save_call_bridge`), L577-578 (`save_thread_step`).
- **Configuration workers** : `16-docker-echo-webui/backend/open_webui/env.py` L475-477 (`UVICORN_WORKERS`), `16-docker-echo-webui/backend/open_webui/__init__.py` L79, L91.

### Cause racine & Risques
- `get_cognition_stream` exécute un polling toutes les `0.25s` (`_get_cognition_state_sig` via `asyncio.to_thread`) ouvrant une connexion SQLite `readonly` sur `session.db` (3 requêtes `COUNT/MAX` par cycle et par client connecté).
- Multiplie les ouvertures de descripteurs et les réveils CPU/I/O à vide hors activité cognitive, et maintient deux connexions persistantes concurrentes côté navigateur (`/echo/v1/wm/ws` + `/echo/v1/cognition/{chat_id}/stream`, *(Mis à jour : le bus Window Manager a migré du SSE `/echo/v1/wm/stream` vers le WebSocket bidirectionnel `/echo/v1/wm/ws` dans `routers/wm.py` v6.17.4.1)*).

### Architecture cible
- Remplacer la boucle de polling `_get_cognition_state_sig` par un bus push asynchrone en mémoire (`asyncio.Queue` indexées par `chat_id`) déclenché directement par `bump_cognition_seq(chat_id)`.
- Unifier la diffusion des deltas cognitifs dans le flux WebSocket bidirectionnel `/echo/v1/wm/ws` (ex-`/echo/v1/wm/stream`, type d'événement `cognition_update`) pour réduire à 1 le nombre de connexions permanentes par onglet.
- **Contrainte multi-processus** : un bus `asyncio.Queue` en mémoire impose `UVICORN_WORKERS=1`. Si `UVICORN_WORKERS > 1` est activé ultérieurement, le bus devra être adossé à Redis Pub/Sub (déjà référencé via `REDIS_URL` dans `open_webui/main.py:838,2504`).

---

## 3. Souveraineté MPSI Offline WebGPU (`src/lib/stores/echo/index.ts`) — [PARTIELLEMENT RÉSOLU — v6.6.12]

### État actuel
- **Internalisé** : L'import JS de `@huggingface/transformers` et les binaires `.wasm`/`.jsep.*` (`/wasm/`) sont désormais 100 % internalisés dans `16-docker-echo-webui/src/lib/stores/echo/index.ts` (`startEchoEdgeEmbedding`, sans CDN jsDelivr, et `kokoro.worker.ts` supprimé).
- **Reliquat distant** : Seul `allowLocalModels = false` subsiste pour le téléchargement initial du modèle ONNX `onnx-community/harrier-oss-v1-0.6b-ONNX` depuis Hugging Face Hub (mis en cache navigateur ensuite).

### Architecture cible (reliquat)
- Héberger localement le modèle d'embedding quantifié dans `/static/` et basculer `allowRemoteModels = false` / `localModelPath` vers les routes locales servies par `echo-webui` pour un fonctionnement 100 % air-gapped.

---

## 4. Hygiène mémoire longue durée Backend (Verrous, Clients HTTP, Pagination Qdrant)

### Ancrages vérifiés
- **Dictionnaires de verrous non bornés** :
  - `14-owui-libs/echo_paths.py` L51-53 (`_CHAT_CLONE_LOCKS`, `_CODEX_LOCKS`, `_SKILL_LOCKS` ; `dict[str, asyncio.Lock]`) ;
  - `14-owui-libs/echo_subagent_hub.py` L41, L120-123 (`_SUBAGENT_LOCKS: dict[tuple[int, str, str], asyncio.Lock]`).
- **Clients `httpx.AsyncClient` éphémères par requête** :
  - `16-docker-echo-webui/backend/open_webui/echo/routers/meta_artifacts.py` L120, L241, L306, L391, L447 (`async with httpx.AsyncClient(timeout=10.0) as client:`) ;
  - `16-docker-echo-webui/backend/open_webui/echo/routers/vault.py` L237 (`async with httpx.AsyncClient(timeout=3.0) as client:`).
- **Troncature Qdrant sans pagination** :
  - `16-docker-echo-webui/backend/open_webui/echo/routers/meta_artifacts.py` L134 (`"limit": 1000` sur `/collections/.../points/scroll` sans boucle sur `next_page_offset`, contrairement à `14-owui-libs/echo_session_clone.py` L987).

### Cause racine & Risques
- Les dictionnaires `_CHAT_CLONE_LOCKS`, `_CODEX_LOCKS`, `_SKILL_LOCKS` et `_SUBAGENT_LOCKS` croissent indéfiniment avec le nombre de chats/utilisateurs/sous-agents au fil de la durée de vie du processus.
- L'instanciation répétée d'`httpx.AsyncClient` empêche la réutilisation du pool de connexions TCP/TLS.
- Le scroll limité à `1000` points dans `meta_artifacts.py` provoque une perte silencieuse d'artéfacts dès que la collection dépasse 1000 vecteurs.

### Architecture cible
- Migrer les registres de verrous vers `weakref.WeakValueDictionary` (ou cache LRU borné avec éviction uniquement lorsque `not lock.locked()`).
- Mutualiser un `httpx.AsyncClient` partagé au niveau applicatif (lifespan `bootstrap.py` ou `echo_http._get_global_client`) pour `meta_artifacts.py` et `vault.py`.
- Implémenter une boucle de pagination complète exploitant `next_page_offset` dans `meta_artifacts.py` (alignée sur `echo_session_clone.py:987`).

---

## 5. Nettoyage profond du tunnel amont Open WebUI & Modales UX

### Ancrages vérifiés
- **Composants hérités Open WebUI neutralisés ou redondants** :
  - `16-docker-echo-webui/src/lib/components/chat/ChatTabs/SideChat.svelte` (importé dans `ChatTabs.svelte` L98, L180, L1624 ; neutralisé par `canSideChat = false` à L145 sous `[ECHO-V6-HOOK]`) ;
  - `16-docker-echo-webui/src/lib/components/chat/ChatTabs/VoiceNote.svelte` ;
  - `16-docker-echo-webui/src/lib/components/chat/ChatTabs/Browser.svelte` (remplacé côté ECHO par `EchoBrowserTab.svelte`) ;
  - Références `Notes` et `Knowledge` dans `16-docker-echo-webui/src/lib/components/chat/MessageInput/InputMenu.svelte` L6, L27-28, L400, L435, L700-725.
- **Tunnel backend amont sous contrainte de rebase** :
  - `16-docker-echo-webui/backend/open_webui/utils/middleware.py` ;
  - `16-docker-echo-webui/backend/open_webui/utils/payload.py` ;
  - `16-docker-echo-webui/UPSTREAM_LOCK.json`.
- **Modales natives bloquantes du navigateur** :
  - `16-docker-echo-webui/src/lib/components/echo/EchoMetaArtifacts.svelte` L158 (`window.confirm(...)`) ;
  - `16-docker-echo-webui/src/lib/components/echo/EchoSkills.svelte` L105 (`window.confirm(...)`) ;
  - Composant cible disponible : `16-docker-echo-webui/src/lib/components/common/ConfirmDialog.svelte`.

### Cause racine & Risques
- Le maintien de composants amont inutilisés (`SideChat`, `VoiceNote`, `Browser`, `Notes`, `Knowledge`) alourdit le bundle SvelteKit et expose des surfaces UI désynchronisées de la suture SQLite ECHO.
- L'élagage direct de `utils/middleware.py` et `utils/payload.py` engendre des conflits lors des merges/rebases amont trackés par `UPSTREAM_LOCK.json`.
- `window.confirm()` / `window.prompt()` bloquent le thread UI principal, ignorent le thème ECHO et ne s'intègrent pas au cycle de vie des composants Svelte.

### Architecture cible
- Débrancher et supprimer `SideChat.svelte`, `VoiceNote.svelte`, `Browser.svelte` ainsi que les entrées `Notes`/`Knowledge` de `InputMenu.svelte` lors d'une passe dédiée post-rebase.
- Isoler les branches mortes de `utils/middleware.py` et `utils/payload.py` via des guards minimaux documentés compatibles `UPSTREAM_LOCK.json` (préférant les hooks `# [ECHO-V6-HOOK]` aux suppressions massives de code amont).
- Remplacer tous les appels `window.confirm()` / `window.prompt()` dans `EchoMetaArtifacts.svelte:158` et `EchoSkills.svelte:105` par le composant réactif `ConfirmDialog.svelte`.
