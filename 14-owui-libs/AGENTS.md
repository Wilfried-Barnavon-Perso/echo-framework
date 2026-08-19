# 🌌 ECHO Framework - Connaissance Sémantique : `14-owui-libs`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `14-owui-libs`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier constitue le **Cœur Applicatif (Core Libraries)** du framework. Il centralise toutes les fonctions utilitaires, la gestion des accès base de données, la sécurité DOM, l'authentification forte, et sert de référentiel absolu (Single Source of Truth) pour les configurations via `echo_constants.py`.

## 2. Cartographie des Fichiers et Algorithmes

### Fondations & Registre Unifié
- **`echo_constants.py`** : C'est le Registre Unifié. 
  - **Sémantique** : Contient `ECHO_MODELS_REGISTRY` dictant la hiérarchie cognitive (Pro, Flash, Lite, Distillation), `ECHO_SESSION_DOMAINS` pour le Vault, ainsi que les modèles de prompts natifs et l'identifiant obligatoire `wrap_tool_output` (`echo_tool_multiparts`) pour le multimodal.
- **`echo_utils.py`** : Utilitaires de base de données.
  - **Sémantique** : Instancie `EchoStateManager` gérant l'accès sécurisé et asynchrone à SQLite (`message_shadows`, logs, auth). Fournit les primitives de logging et de gestion de fichiers.
- **`echo_protocol.py`** : Définition des schémas Pydantic natifs et gestion de la stack HTTP/2 (`httpx` avec Stealth Headers).

### Interfaces Utilisateur (UI & DOM)
- **`echo_ui.py`** : Moteur de rendu UI.
  - **Sémantique** : Responsable de la génération dynamique de code HTML/CSS/JS (Data Islands). Il injecte les modales natives asynchrones (`window.echoCustomConfirm`, `window.mcpAlert`) qui respectent le mode sombre/clair sans bloquer l'Event Loop de WebUI. Gère également le support d'impression (`allow-modals`) pour les actions PDF.
- **`echo_visuals.py`** : Traduction des concepts générés par le LLM (arbres, graphes) en composants web interactifs (via d3.js, Leaflet ou vis-network).

### Authentification Antigravity 2.1
- **`echo_auth.py`** : IdP (Identity Provider) Autonome. 
  - **Sémantique** : Gère l'authentification Multi-Provider (OAuth2, TOTP, Master Keys). Il purge intégralement les bases de données (Chat, Identity, MCP, N8N) lors de la suppression d'un utilisateur.
- **`echo_pkce_server.py`** & **`echo_ssh_tunnel.py`** : Implémentent le flow OAuth2 PKCE strict via un serveur callback éphémère et un tunnel SSH (Ports 8020-8024).

### Pipelines Spécialisés
- **`echo_ingestion.py`** : Pipeline d'Ingestion Zéro-RAM asynchrone pour la base RAG. Convertit les fichiers massifs via MarkItDown et les indexe via traitement hybride.
- **`echo_codex_git.py`** : Surcouche bas niveau des commandes `git` et du registre SQLite utilisé par ECHO Codex.
- **`echo_browser_lib.py`** : Bibliothèque bas niveau de pilotage asynchrone pour le worker Playwright (utilisé par `navigation_engine_tool`).
- **`echo_skills.py`** : Extracteur sémantique de métadonnées pour les Skills Antigravity.

## 3. Dépendances Logiques
- Tous les autres dossiers (`10-owui-pipes`, `11-owui-filters`, `12-owui-tools`, `13-owui-actions`) importent massivement les classes et constantes de `14-owui-libs`. Ce dossier est le socle de l'écosystème Python d'ECHO.
