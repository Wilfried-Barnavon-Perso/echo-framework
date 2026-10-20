# 🌌 ECHO Framework - Connaissance Sémantique : `13-owui-actions`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `13-owui-actions`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier gère les **Actions Interactives (Boutons sous les messages)**. Dans l'architecture ECHO, ces actions sont souvent couplées avec le filtre `app_drawer_filter.py` pour générer de véritables applications riches injectées dans le DOM du navigateur de l'utilisateur (des Data Islands), offrant un contrôle total sur l'état de l'agent sans interrompre la conversation.

## 2. Cartographie des Fichiers et Algorithmes

### Outils de Monitoring & HUD
- **`agent_monitor_action.py` (v5.226.18.0 - Priorité 50)** : HUD (Heads-Up Display) de supervision en temps réel. Offre une vue arborescente des agents actifs, y compris le flux vidéo ou le statut du navigateur web Playwright.
- **`web_navigation_replay_action.py` (v5.226.18.0 - Priorité 60)** : Cockpit de rejeu permettant à l'utilisateur de visionner la navigation autonome effectuée par le modèle via Playwright. L'UI a subi une refonte complète exploitant des icônes SVG natives et un design épuré (transparence, sans background). Intègre également le support natif du zoom via `Ctrl+Molette`.

### Ingénierie & Edition
- **`echo_codex_action.py` (v5.226.18.0 - Priorité 20)** : Interface avancée intégrant un éditeur Monaco (type VS Code), un explorateur de fichiers (File Tree), une édition assistée par IA (`_codex_ai_edit`, ciblant `MODEL_APOLLO` par défaut) et un historique Git natif permettant la restauration granulaire de versions. Gère nativement la création, le renommage et la suppression de **dossiers entiers**, en recalculant récursivement l'arborescence et en synchronisant l'état (fichier vs répertoire) dans SQLite via `_sync_registry`. Il délègue l'intégralité de sa communication UI à la classe `EchoEvents`, bénéficiant ainsi nativement du protocole **UCTP** pour la transmission de larges arborescences ou fichiers (Chunking bidirectionnel). Intègre également un mécanisme asynchrone optimisé (`asyncio.to_thread`) pour le ping du `heartbeat`, prévenant tout engorgement de l'Event Loop ASGI. Il prend désormais en charge le paramètre optionnel `current_file` lors de l'évènement `switch_workspace` pour permettre le ciblage direct d'un fichier via le trigger LLM `open_codex_ui`.

### Sécurité & Identité
- **`echo_identity_vault_action.py` (v5.226.18.0 - Priorité 40)** : ECHO Identity Vault. Interface centralisée de gestion des secrets pour l'Agent. La notion de permissions d'accès (RO/RW) a été totalement supprimée pour une gestion mono-compte unifiée (`default` pour les services standards, ID serveur pour `remote_mcp`, clé de secret pour `n8n_workflows`).
  - **Sémantique** : Elle se synchronise dynamiquement avec l'API de schémas du MCP Broker (`/schemas`) pour collecter et sauvegarder les identifiants par utilisateur dans la base SQLite locale `identity.db` (table `identity_vault`), et distille automatiquement via LLM une description sémantique lors de l'ajout d'un serveur `remote_mcp`.
  - **Étanchéité** : Les secrets sont stockés de manière isolée par utilisateur dans `identity.db` ; le Modèle découvre les services et identités via `identity_vault_tool.py` (`list_identities_services`, `list_identities`, `manage_identity`) et injecte les secrets N8N par macro `__ECHO_SECRET_<KEY>__` sans jamais exposer les valeurs sensibles en clair dans le contexte.
- **`reset_auth_action.py` (v5.226.18.0 - Priorité 10)** : Action rapide d'urgence pour purger spécifiquement les tokens et clés liés à l'authentification **Google/PKCE** et OAuth2 en cas de désynchronisation.

### Gestion Contextuelle (Migration & Vectoriel)
- **`resume_in_new_chat_action.py` (v5.228.1.1 - Priorité 5)** : Action interactive de migration et de transfert de contexte vers une nouvelle session ("Poursuivre dans une nouvelle conversation"). Extrait l'intégralité de la branche active depuis `Chats.get_chat_by_id` (y compris les sorties d'outils `m["output"]`), construit les transcriptions hors de l'Event Loop (`asyncio.to_thread(_prepare_transcripts, ...)` avec troncature pré-regex), exécute une distillation cognitive en 2 passes via `EchoGeminiClient.call_distillation` (`USR_ACTION_RESUME_GLOBAL` puis `USR_ACTION_RESUME_RECENT`) accompagnée d'un HUD centré non-bloquant (`#echo-resume-hud`, `timeout=15.0` sur chaque `call_execute`), insère la nouvelle conversation via `Chats.insert_new_chat` (adaptatif 2/3 arguments avec rollback en cas d'échec), duplique les ressources physiques, le dépôt Git Codex et `session.db` (en purgeant l'historique conversationnel via `sync_resumed_session` sous verrou `get_chat_clone_lock`), duplique par pagination la mémoire vectorielle `COLLECTION_SESSION_RAG` (`duplicate_session_rag`) et redirige le navigateur vers `/c/{new_chat_id}`.
- **`purge_memory_action.py` (v5.226.18.0 - Priorité 30)** : Interface scrollable permettant la suppression ciblée et granulaire des souvenirs vectoriels (vecteurs orphelins ou erronés) directement dans Qdrant. Intègre une vérification stricte de l'annulation UI (bouton "Annuler" ou modal vide valant `False`).

### Export
- **`print_pdf_action.py` (v5.226.18.0 - Priorité 100)** : Fonctionnalité native d'export des conversations au format PDF pour l'archivage local de la dialectique.

## 3. Dépendances Logiques
- Ces actions nécessitent souvent la déclaration de hooks JS spécifiques natifs à Open WebUI (ex: `window.mcpAlert`, protection `allow-modals`) configurés par `echo_ui.py`.
- L'Identity Vault partage sa logique de stockage SQLite avec l'ensemble du Cortex ECHO via le module `echo_constants.py`.
