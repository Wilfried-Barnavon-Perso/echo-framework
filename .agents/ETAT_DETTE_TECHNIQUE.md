# État de la Dette Technique ECHO v6 (Audit v6.22.1 — Post-Assainissement 6.6.9)

Ce document recense les 6 chantiers architecturaux suivis depuis l'audit d'assainissement `6.6.9`, mis à jour à partir du code source réel en version `6.22.1` (ancrages exacts, éléments clôturés, reliquats et architectures cibles).

| # | Chantier | Statut |
|---|---|---|
| 1 | Éradication finale du pont `eval(JS)` V5 (UCTP & Exécution Dynamique) | **Partiellement résolu** |
| 2 | Fusion Cognition dans `/echo/v1/wm/ws` & fin du polling SQLite `0.25s` | **Résolu (`v6.21.10`)** |
| 3 | Souveraineté MPSI Offline WebGPU (`src/lib/stores/echo/index.ts`) | **Partiellement résolu (`v6.6.12`)** |
| 4 | Hygiène mémoire longue durée Backend (Verrous, Clients HTTP, Pagination Qdrant) | **Résolu (`v6.21.10`)** |
| 5 | Nettoyage profond du tunnel amont Open WebUI & Modales UX | **Partiellement résolu (`v6.22.0`)** |
| 6 | Vigile Sémantique Pré-Outil Conditionnel (Étage 2 MPSI) | **En réserve** |

---

## 1. Éradication finale du pont `eval(JS)` V5 (UCTP & Exécution Dynamique) — [PARTIELLEMENT RÉSOLU]

### Éléments résolus
- **Suppression des modales JS injectées (`v6.20.3.1`)** : `EchoUI.get_custom_modals_js()` et `get_sanitize_html_js()` ont été supprimés de `14-owui-libs/echo_ui.py` (`v6.21.8.1`). Seuls subsistent les helpers `get_media_export_js()` (L495, vues HTML `image_viewer` L914 et `build_rich_view_html` L1427) et `get_print_isolation_js()` (L1655, utilisé par `print_pdf_action.py`).
- **Migration des 4 outils vers les événements natifs (`0` appel `call_execute`/`emit_execute`)** :
  - `12-owui-tools/generalist_tools.py` (`v6.21.9.1`) : `ask_user_input` (L159) passe par `events.call("request:user_input", ...)` (L270) vers `AskUserCard.svelte` ;
  - `12-owui-tools/agent_orchestration_tool.py` (`v6.20.3.2`) : `forge_skill` (L91) sans modale, `delete_user_skill` (L274) migré vers `events.confirm(..., timeout_seconds=...)` (`v6.20.3.1`) ;
  - `12-owui-tools/identity_vault_tool.py` (`v6.20.3.1`) : `manage_identity` (L132) migré vers `events.confirm(...)` (L179, L198) ;
  - `12-owui-tools/strategic_planner.py` (`v6.20.3.1`) : `process_plan` (L579) migré vers `events.confirm(...)` (L614).
- **Sandbox interactive `echo_monitor` (`v6.6.17`)** : `12-owui-tools/code_executor_tool.py` et `14-owui-libs/echo_ui.py` (suppression de `get_floating_monitor_js()`) raccordés nativement à `EchoCanvasTab.svelte` via `/echo/v1/wm/ws` (`window_mounted` / `echo_canvas_event` / `canvas_ui_event`).
- **Actions migrées en natif et supprimées** : `resume_in_new_chat_action.py` (`ChatMenu.svelte` + `echo_session_resume.py` + `routers/chat_actions.py`), `reset_auth_action.py` (`Settings/LlmAuth.svelte` + `routers/llm_auth.py`), `purge_memory_action.py` (`EchoMetaArtifacts.svelte` + `routers/meta_artifacts.py`).
- **Bus WM frontend purgé de UCTP** : `16-docker-echo-webui/src/lib/stores/echo/index.ts` (handler WS `/echo/v1/wm/ws`, L654-812) ne traite plus `wm_execute`, `wm_uctp_chunk` ni `wm_uctp_exec` ; `window.EchoWM` n'existe plus côté `src/`.

### Reliquats vérifiés
- **Façade d'exécution distante & méthodes orphelines** : `14-owui-libs/echo_events.py` (`v6.20.3.1`) :
  - `_wrap_wm_js` (L100), `_build_uctp_frames` (L125), `emit_execute` (L205), `_call_via_socket_caller` (L233), `_reassemble_uctp_payload` (L248), `call_execute` (L289) ;
  - Méthodes internes orphelines appelant `call_execute` sans aucun appelant dans le dépôt : `wait_hud_action` (L386/396), `resolve_live_session` (L399/455/470), `trigger_action` (L480/523).
- **Fragmentation WM backend (code mort côté client)** : `14-owui-libs/echo_window_manager.py` (`v6.21.9.1`) :
  - `_broadcast_execute` (L920) émet `wm_execute` (L934), `wm_uctp_chunk` (L950) et `wm_uctp_exec` (L960), invoqué par `emit_js` (L969/980) et `call_js` (L990/1017) ; `handle_rpc` conserve encore la branche `action == "rpc_result"` (L1049-1060).
- **Évaluateur JS client** : `16-docker-echo-webui/src/lib/components/chat/Chat.svelte` L1757-1768 (branche `type === 'execute'` avec `const asyncFunction = new Function(...)` à L1760).
- **4 appelants résiduels de `emit_execute` / `call_execute` dans le dépôt** :
  - `#echo-clone-hud` : `15-owui-events/echo_chat_cloned_event.py` (`v5.235.5.1`) L85-116 (`_build_clone_hud_mount_js`), appels `emit_execute` à L161, L175, L215 *(L217-220 référence encore `window.EchoWM.hydrateChatState`, désormais inexistant côté frontend)* ;
  - Export PDF : `13-owui-actions/print_pdf_action.py` (`v6.18.23.1`, seul `.py` restant dans `13-owui-actions/`) : `EchoUI.get_print_isolation_js(...)` (L88) + `await events.call_execute(js_logic)` (L90) ;
  - Double transport redondant `echo:open-tab` (en doublon de `await wm.open_tab(...)`, déjà acheminé nativement par `echo_open_tab` sur `/echo/v1/wm/ws` à `index.ts:706`) :
    - `12-owui-tools/echo_codex_tool.py` (`v6.21.1.1`) L1235 (`emit_execute`, doublonnant `wm.open_tab` L1230) ;
    - `12-owui-tools/navigation_engine_tool.py` (`v6.15.0.1`) L470 (`emit_execute`, doublonnant `wm.open_tab` L464).

### Cause racine & Risques
- Le protocole UCTP V5 transporte encore du code JavaScript brut via Socket.IO (`__event_call__`) vers `new Function(...)` dans `Chat.svelte:1760`, tandis que sa branche WM (`wm_execute` / `wm_uctp_*`) est déjà du code mort côté frontend.
- Cette surface d'exécution dynamique reste incompatible avec une politique CSP stricte (`script-src 'self'`) et maintient un couplage DOM impératif (`#echo-clone-hud`, `print_pdf_action.py`).

### Architecture cible (reliquat)
- Supprimer les 2 appels `emit_execute` redondants dans `echo_codex_tool.py:1235` et `navigation_engine_tool.py:470` (`wm.open_tab` assure déjà l'ouverture via `/echo/v1/wm/ws`).
- Migrer `#echo-clone-hud` (`echo_chat_cloned_event.py`) vers un événement typé `hud_progress` sur `/echo/v1/wm/ws`, rendu par un composant Svelte global dans `(app)/+layout.svelte`, et supprimer l'appel mort `window.EchoWM.hydrateChatState`.
- Porter l'export PDF (`print_pdf_action.py` + `EchoUI.get_print_isolation_js`) sur un handler client natif déclenché par événement typé.
- Supprimer `new Function` dans `Chat.svelte:1760`, les méthodes UCTP et orphelines de `echo_events.py` (`_wrap_wm_js`, `_build_uctp_frames`, `emit_execute`, `_call_via_socket_caller`, `_reassemble_uctp_payload`, `call_execute`, `wait_hud_action`, `resolve_live_session`, `trigger_action`), ainsi que le code mort `_broadcast_execute`, `emit_js`, `call_js` et `rpc_result` dans `echo_window_manager.py`.

---

## 2. Fusion Cognition dans `/echo/v1/wm/ws` & fin du polling SQLite `0.25s` — [RÉSOLU — v6.21.10]

### État actuel (Vérifié intact en v6.22.1)
- **Suppression du flux SSE et du polling** : La route `/echo/v1/cognition/{chat_id}/stream`, `_get_cognition_state_sig` et la boucle `await asyncio.sleep(0.25)` sont absentes de `16-docker-echo-webui/backend/open_webui/echo/routers/cognition.py` (`v6.21.9.1` ; snapshot actif sur `GET /{chat_id}/snapshot` L756).
- **Bus push unifié** : `bump_cognition_seq` (`14-owui-libs/echo_subagent_hub.py` `v6.21.9.1`, L192/202) et `EchoStateManager` (`14-owui-libs/echo_state_manager.py` `v6.21.9.1`, L339, L737 et `delete_thread` L825) déclenchent `EchoWindowManager.notify_cognition_update` (`14-owui-libs/echo_window_manager.py` `v6.21.9.1`, L354), qui pousse `echo_cognition_update` de manière thread-safe sur `/echo/v1/wm/ws`.
- **Réception Frontend coalescente** : `16-docker-echo-webui/src/lib/stores/echo/index.ts` consomme `wm_connected` (L660-666) et `echo_cognition_update` (L803-807) via `runCognitionRefresh` (L444) / `scheduleCognitionRefresh` (L462) sans `EventSource` ni polling.
- **Note multi-processus** : Le bus en mémoire suppose `UVICORN_WORKERS=1`. Si `UVICORN_WORKERS > 1` est activé ultérieurement, le bus devra être adossé à Redis Pub/Sub.

---

## 3. Souveraineté MPSI Offline WebGPU (`src/lib/stores/echo/index.ts`) — [PARTIELLEMENT RÉSOLU — v6.6.12]

### État actuel (Vérifié inchangé en v6.22.1)
- **Internalisé** : L'import JS `@huggingface/transformers` (L1264) et les binaires `.wasm`/`.jsep.*` (`wasmPaths = '/wasm/'`, L1270, copiés via `vite.config.ts` L40-46) sont 100 % locaux dans `16-docker-echo-webui/src/lib/stores/echo/index.ts` (sans CDN jsDelivr, `kokoro.worker.ts` supprimé).
- **Reliquat distant** : `transformers.env.allowLocalModels = false` subsiste (L1268 ; aucune occurrence de `allowRemoteModels` ni `localModelPath`) pour le téléchargement initial de `ECHO_EDGE_TARGET_REPO = 'onnx-community/harrier-oss-v1-0.6b-ONNX'` (L413) depuis Hugging Face Hub (mis en cache navigateur ensuite). Aucun fichier `.onnx` n'est embarqué dans `16-docker-echo-webui/static/`.

### Architecture cible (reliquat)
- Héberger localement le modèle d'embedding quantifié sous `/static/` et basculer `allowRemoteModels = false` / `localModelPath` vers les routes locales servies par `echo-webui` pour un fonctionnement 100 % air-gapped.

---

## 4. Hygiène mémoire longue durée Backend (Verrous, Clients HTTP, Pagination Qdrant) — [RÉSOLU — v6.21.10]

### État actuel (Vérifié intact en v6.22.1)
- **Verrous faibles bornés automatiquement** : `_CHAT_CLONE_LOCKS`, `_CODEX_LOCKS`, `_SKILL_LOCKS` en `weakref.WeakValueDictionary` protégés par `_PATHS_LOCK` dans `14-owui-libs/echo_paths.py` (`v6.21.9.1`, L62-65), et `_SUBAGENT_LOCKS` en `weakref.WeakValueDictionary` dans `14-owui-libs/echo_subagent_hub.py` (`v6.21.9.1`, L129).
- **Mutualisation HTTP** : `echo_http._get_global_client()` utilisé sur les 5 routes + `_ensure_meta_collection` (L86, L129, L261, L328, L416, L474) de `16-docker-echo-webui/backend/open_webui/echo/routers/meta_artifacts.py` (`v6.21.9.1`) et dans `get_vault_schemas` (L242) de `16-docker-echo-webui/backend/open_webui/echo/routers/vault.py` (`v6.21.9.1`).
- **Pagination Qdrant complète** : Boucle de scroll paginée via `next_page_offset` (L165) par lots de `500` points dans `list_meta_artifacts` (`meta_artifacts.py`).
- **Note mineure hors périmètre initial** : `14-owui-libs/echo_window_manager.py` (`v6.21.9.1`) conserve 5 instanciations ponctuelles `async with httpx.AsyncClient(...)` (L479, L492, L681, L837, L1109) vers `echo-coding-worker`.

---

## 5. Nettoyage profond du tunnel amont Open WebUI & Modales UX — [PARTIELLEMENT RÉSOLU — v6.22.0]

### Éléments résolus
- **Aplatissement de `InputMenu.svelte`** : `16-docker-echo-webui/src/lib/components/chat/MessageInput/InputMenu.svelte` (359 lignes) ne contient plus aucune référence active à `Notes`, `Knowledge`, `Chats` ou `Webpage` (`[ECHO-V6-HOOK]` L41 : maintien exclusif de `files`).
- **Purge de la compaction contextuelle amont (`v6.22.0`)** : Suppression de `16-docker-echo-webui/backend/open_webui/utils/context_compaction.py` (612 lignes), retrait de ses appels dans `backend/open_webui/utils/middleware.py` (-159 lignes) et suppression des composants frontend associés (`ContextCompactionLabel.svelte`, `SlashCommands.svelte`, `contextUsage.ts`) au profit de `EchoCompactionEngine` / `chat_windowing.py` et `EchoCompactionBoundary.svelte`.

### Reliquats vérifiés
- **Composants hérités Open WebUI neutralisés ou orphelins** :
  - `16-docker-echo-webui/src/lib/components/chat/ChatTabs/SideChat.svelte` (importé dans `ChatTabs.svelte` L95, typé L181, instancié L1676 ; neutralisé par `canSideChat = false` L145-146 sous `[ECHO-V6-HOOK]` et `Chat.svelte` L357 `sideChatAllowed = false`) ;
  - `16-docker-echo-webui/src/lib/components/chat/ChatTabs/VoiceNote.svelte` (0 import dans `src/` ; seul le raccourci `VOICE_NOTE` subsiste dans `src/lib/shortcuts.ts` L22/267) ;
  - `16-docker-echo-webui/src/lib/components/chat/ChatTabs/Browser.svelte` (0 import dans `src/`, remplacé côté ECHO par `EchoBrowserTab.svelte` dans `ChatTabs.svelte` L98/1670) ;
  - Sous-composants orphelins sur disque (0 import) dans `16-docker-echo-webui/src/lib/components/chat/MessageInput/InputMenu/` : `Chats.svelte`, `Knowledge.svelte`, `Notes.svelte`.
- **Tunnel backend amont sous contrainte de rebase** :
  - `16-docker-echo-webui/backend/open_webui/utils/middleware.py` (9 hooks `# [ECHO-V6-HOOK]` : L177, L1989, L2007, L2148, L2418, L2445, L2904, L5707, L5896) ;
  - `16-docker-echo-webui/backend/open_webui/utils/payload.py` (0 hook ECHO, intact) ;
  - `16-docker-echo-webui/UPSTREAM_LOCK.json` (`base_tag: "v0.13.2"`, `extracted_at: "2026-11-08"`, 11 backports).
- **Modales natives bloquantes du navigateur** :
  - `16-docker-echo-webui/src/lib/components/echo/EchoMetaArtifacts.svelte` L159 (`window.confirm(...)`) ;
  - `16-docker-echo-webui/src/lib/components/echo/EchoSkills.svelte` L106 (`window.confirm(...)`) ;
  - Composant cible disponible (non importé dans `components/echo/`) : `16-docker-echo-webui/src/lib/components/common/ConfirmDialog.svelte`.

### Cause racine & Risques
- Le maintien des composants amont inutilisés (`SideChat`, `VoiceNote`, `Browser`, `InputMenu/{Chats,Knowledge,Notes}`) alourdit le dépôt/bundle et expose des surfaces UI désynchronisées de la suture SQLite ECHO.
- L'élagage direct de `utils/middleware.py` et `utils/payload.py` engendre des conflits lors des merges/rebases amont trackés par `UPSTREAM_LOCK.json`.
- `window.confirm()` bloque le thread UI principal, ignore le thème ECHO et ne s'intègre pas au cycle de vie des composants Svelte.

### Architecture cible (reliquat)
- Débrancher et supprimer `SideChat.svelte`, `VoiceNote.svelte`, `Browser.svelte` ainsi que les fichiers orphelins `InputMenu/{Chats,Knowledge,Notes}.svelte` lors d'une passe dédiée post-rebase.
- Isoler les branches mortes restantes de `utils/middleware.py` et `utils/payload.py` via des guards minimaux documentés compatibles `UPSTREAM_LOCK.json` (`# [ECHO-V6-HOOK]`).
- Remplacer `window.confirm()` dans `EchoMetaArtifacts.svelte:159` et `EchoSkills.svelte:106` par le composant réactif `ConfirmDialog.svelte`.

---

## 6. Vigile Sémantique Pré-Outil Conditionnel (Étage 2 MPSI — En réserve)

### État actuel (Vérifié inchangé en v6.22.1)
- **Mis en réserve** : Aucune implémentation `pre_tool` conditionnelle dans le code source ; le chantier reste en réserve architecturale.
- **Protections actives** : L'isolation `<REQUETE_UTILISATEUR>`, les invariants `MPAH`/`MPSI`, le Registre Unifié des outils et la coupure d'outils sensibles des sous-agents Web protègent déjà ECHO.
- **Déclencheur futur** : À réévaluer uniquement si un vecteur d'injection indirecte avéré contourne l'isolation actuelle sur un outil critique.
