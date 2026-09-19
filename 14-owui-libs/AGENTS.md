# 🌌 ECHO Framework - Connaissance Sémantique : `14-owui-libs`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `14-owui-libs`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier constitue le **Cœur Applicatif (Core Libraries)** du framework. Il centralise toutes les fonctions utilitaires, la gestion des accès base de données, la sécurité DOM, l'authentification forte, et sert de référentiel absolu (Single Source of Truth) pour les configurations via `echo_constants.py`.

## 2. Cartographie des Fichiers et Algorithmes

### Fondations & Registre Unifié
- **`echo_constants.py`** : C'est le Registre Unifié et la librairie de fondations (Shared Core Functionality). 
  - **Sémantique** : Contient `ECHO_MODELS_REGISTRY` dictant la hiérarchie cognitive (Pro, Flash avec `gemini-3.8-flash`, Lite, Distillation), `ECHO_SESSION_DOMAINS` pour le Vault, `ECHO_CODEX_WORKSPACES` (main, sandbox) qui remplace les valeurs codées en dur, l'identifiant obligatoire `wrap_tool_output` (`echo_tool_multiparts`) pour le multimodal, et les **seuils de monitoring de la jauge de contexte** (récemment haussés à 40k/30k). Intègre également l'extension `.pdf` au `CODEX_LANG_MAP` pour le rendu natif.
- **Démembrement de l'ancien `echo_utils.py`** : Le framework a subi une refonte modulaire majeure. Le cœur utilitaire massif a été éclaté en composants ultra-spécialisés :
  - **`echo_state_manager.py`** : Gestionnaire universel de l'état asynchrone SQLite (`EchoStateManager`), incluant le verrouillage intra-chat, le suivi RAG O(1), l'ajout récent du support `item_type` (file/directory) pour le Codex Multi-Workspace, et l'accès concurrent sécurisé.
  - **`echo_gemini_client.py`** : Client natif implémentant le multiplexage **HTTP/2**, la Cascade Descendante, et le Circuit Breaker OAuth2 (Fast-Failover Intra-Retry avec verrouillage dynamique). Il applique une normalisation camelCase stricte (`inlineData`) requise par AI Studio pour les payloads multimodaux.
  - **`echo_aec.py`** : Module d'orchestration contextuelle (AEC). Il a subi une refonte majeure : abandon du format YAML (`_dict_to_yaml_aec`) au profit d'un système de templating natif **XML** réparti sur 5 vecteurs de base (modèle, identité, temporalité, localisation, évènements) avec tri chronologique FIFO.
  - **`echo_core.py`** : Fonctions cognitives et utilitaires pures (ex: `build_model_identity`). Intègre le mécanisme de **FIFO Destructif** dans `wrap_tool_output` pour purger la base SQLite exclusivement des `aec_event`, sans altérer les ressources réelles (fichiers, uploads). Ségrege formellement la variable `aec_events` lors de la restitution des outils pour l'isoler dans sa propre balise `<artifact id="AEC_evenement_systeme">`, prévenant ainsi les crashs asynchrones de parsing.
  - **`echo_http.py`**, **`echo_events.py`**, **`echo_logger.py`**, **`echo_paths.py`**, **`echo_prompts.py`** : Briques fondamentales gérant respectivement les requêtes HTTP, évènements OWUI, log, chemins absolus, et gabarits de prompts (`echo_prompts.py` inclut désormais le domaine `SYS_RAG_DISTILL` pour l'extraction vectorielle, et `SYS_ORCHESTRATOR_N8N_GRAPHER` pour imposer les règles architecturales Sandbox et l'interrogation prioritaire du Hub N8N à l'Agent délégataire).
- **`echo_protocol.py`** : Définition des schémas Pydantic natifs et constantes de base pour les protocoles réseau.

### Interfaces Utilisateur (UI & DOM)
- **`echo_ui.py`** : Moteur de rendu UI.
  - **Sémantique** : Responsable de la génération dynamique de code HTML/CSS/JS (Data Islands). Il implémente un système sophistiqué **"OWUI Tools"** avec de fortes optimisations natives mobiles (dvh, touch targets de 44px min, anti-scroll du document, anti-zoom iOS). Il injecte des modales natives asynchrones refactorisées sans effet spaghetti (`window.echoCustomConfirm`, `window.echoCustomPrompt` pour les saisies LLM, doté de boutons 'pills' interactifs, `window.mcpAlert`) qui respectent le mode sombre/clair sans bloquer l'Event Loop de WebUI, tout en sécurisant les rendus via la fonction d'assainissement **`window.echoSanitizeHTML`**. Gère également le support d'impression (`allow-modals`) pour les actions PDF, et intègre la **synchronisation asynchrone de l'URL** en temps réel (`echoWebPlayerUpdate`) pour le HUD Live du navigateur autonome. Le module d'Identity Vault inclut un bouton d'annulation (`vault-btn-cancel`) et un mode "Blind (Modifier)" sécurisé qui écrase les secrets sans altérer l'identifiant du compte. Le HUD Codex intègre désormais un lecteur PDF WYSIWYG natif (reconstruction par Blob) et une fiabilisation de la sauvegarde avec verrou logiciel (anti Race Condition).
- **`echo_visuals.py`** : Traduction des concepts générés par le LLM (arbres, graphes) en composants web interactifs (via d3.js, Leaflet ou vis-network).

### Authentification Antigravity 2.1
- **`echo_auth.py`** : IdP (Identity Provider) Autonome. 
  - **Sémantique** : Gère l'authentification Multi-Provider (OAuth2, TOTP, Master Keys). Intègre l'invalidation proactive de la session Open WebUI (`/api/v1/auths/signout`) lors du SSO logout pour éviter les collisions. Il purge intégralement les bases de données (Chat, Identity, MCP, N8N) lors de la suppression d'un utilisateur.
  - **Fallback API Keys** : Implémente la méthode `validate_and_save_api_key` traitant les clés API Google (AI Studio) comme solution de secours si le flux OAuth2 échoue. Valide le réseau (`/models?key=...`) et sauvegarde en `AUTH_METHOD_KEY_PRIMARY` et `SECONDARY` dans SQLite.
- **`echo_pkce_server.py`** & **`echo_ssh_tunnel.py`** : Implémentent le flow OAuth2 PKCE strict via un serveur callback éphémère et un tunnel SSH (Ports 8020-8024).

### Pipelines Spécialisés
- **`echo_ingestion.py`** : Pipeline d'Ingestion Zéro-RAM asynchrone modulaire pour la base RAG. Gère de façon dynamique les fichiers entrants ("dynamic file handling"), les convertit via MarkItDown et les indexe via traitement hybride.
- **`echo_codex_git.py`** : Surcouche bas niveau des commandes `git` et du registre SQLite utilisé par ECHO Codex.
- **`echo_browser_lib.py`** : Bibliothèque bas niveau de pilotage asynchrone pour le worker Playwright (utilisé par `navigation_engine_tool`).
- **`echo_skills.py`** : Extracteur sémantique de métadonnées pour les Skills Antigravity.

## 3. Dépendances Logiques
- Tous les autres dossiers (`10-owui-pipes`, `11-owui-filters`, `12-owui-tools`, `13-owui-actions`) importent massivement les classes et constantes de `14-owui-libs`. Ce dossier est le socle de l'écosystème Python d'ECHO.
