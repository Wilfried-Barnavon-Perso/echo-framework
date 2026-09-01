# 🌌 ECHO Framework - Connaissance Sémantique : `14-owui-libs`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `14-owui-libs`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier constitue le **Cœur Applicatif (Core Libraries)** du framework. Il centralise toutes les fonctions utilitaires, la gestion des accès base de données, la sécurité DOM, l'authentification forte, et sert de référentiel absolu (Single Source of Truth) pour les configurations via `echo_constants.py`.

## 2. Cartographie des Fichiers et Algorithmes

### Fondations & Registre Unifié
- **`echo_constants.py`** : C'est le Registre Unifié et la librairie de fondations (Shared Core Functionality). 
  - **Sémantique** : Contient `ECHO_MODELS_REGISTRY` dictant la hiérarchie cognitive (Pro, Flash, Lite, Distillation), `ECHO_SESSION_DOMAINS` pour le Vault, ainsi que les modèles de prompts natifs, l'identifiant obligatoire `wrap_tool_output` (`echo_tool_multiparts`) pour le multimodal, et les **seuils de monitoring de la jauge de contexte**.
- **`echo_utils.py`** : Cœur utilitaire massif et librairie partagée du système, gérant la base de données, la gestion des chemins (path management), la résilience réseau et le formatage.
  - **Sémantique** : Instancie `EchoStateManager` gérant l'accès sécurisé et asynchrone à SQLite (incluant le suivi RAG O(1) via `is_message_embedded()`). Contient le client natif `EchoGeminiClient` qui implémente le multiplexage **HTTP/2**, le Fail-fast sur erreur de syntaxe (400), la Cascade Descendante, et un Circuit Breaker OAuth2 robuste (Fast-Failover Intra-Retry avec verrouillage dynamique des URL sur timeout/429 et Auto-heal). Fournit également la primitive `_dict_to_yaml_aec` pour le formatage YAML plat des événements systèmes de l'AEC.
- **`echo_protocol.py`** : Définition des schémas Pydantic natifs et constantes de base pour les protocoles réseau.

### Interfaces Utilisateur (UI & DOM)
- **`echo_ui.py`** : Moteur de rendu UI.
  - **Sémantique** : Responsable de la génération dynamique de code HTML/CSS/JS (Data Islands). Il implémente un système sophistiqué **"OWUI Tools"** avec de fortes optimisations natives mobiles (dvh, touch targets de 44px min, anti-scroll du document, anti-zoom iOS). Il injecte des modales natives asynchrones refactorisées sans effet spaghetti (`window.echoCustomConfirm`, `window.echoCustomPrompt` pour les saisies LLM, doté de boutons 'pills' interactifs, `window.mcpAlert`) qui respectent le mode sombre/clair sans bloquer l'Event Loop de WebUI, tout en sécurisant les rendus via la fonction d'assainissement **`window.echoSanitizeHTML`**. Gère également le support d'impression (`allow-modals`) pour les actions PDF.
- **`echo_visuals.py`** : Traduction des concepts générés par le LLM (arbres, graphes) en composants web interactifs (via d3.js, Leaflet ou vis-network).

### Authentification Antigravity 2.1
- **`echo_auth.py`** : IdP (Identity Provider) Autonome. 
  - **Sémantique** : Gère l'authentification Multi-Provider (OAuth2, TOTP, Master Keys). Il purge intégralement les bases de données (Chat, Identity, MCP, N8N) lors de la suppression d'un utilisateur.
- **`echo_pkce_server.py`** & **`echo_ssh_tunnel.py`** : Implémentent le flow OAuth2 PKCE strict via un serveur callback éphémère et un tunnel SSH (Ports 8020-8024).

### Pipelines Spécialisés
- **`echo_ingestion.py`** : Pipeline d'Ingestion Zéro-RAM asynchrone modulaire pour la base RAG. Gère de façon dynamique les fichiers entrants ("dynamic file handling"), les convertit via MarkItDown et les indexe via traitement hybride.
- **`echo_codex_git.py`** : Surcouche bas niveau des commandes `git` et du registre SQLite utilisé par ECHO Codex.
- **`echo_browser_lib.py`** : Bibliothèque bas niveau de pilotage asynchrone pour le worker Playwright (utilisé par `navigation_engine_tool`).
- **`echo_skills.py`** : Extracteur sémantique de métadonnées pour les Skills Antigravity.

## 3. Dépendances Logiques
- Tous les autres dossiers (`10-owui-pipes`, `11-owui-filters`, `12-owui-tools`, `13-owui-actions`) importent massivement les classes et constantes de `14-owui-libs`. Ce dossier est le socle de l'écosystème Python d'ECHO.
