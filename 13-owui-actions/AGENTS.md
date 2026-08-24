# 🌌 ECHO Framework - Connaissance Sémantique : `13-owui-actions`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `13-owui-actions`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier gère les **Actions Interactives (Boutons sous les messages)**. Dans l'architecture ECHO, ces actions sont souvent couplées avec le filtre `app_drawer_filter.py` pour générer de véritables applications riches injectées dans le DOM du navigateur de l'utilisateur (des Data Islands), offrant un contrôle total sur l'état de l'agent sans interrompre la conversation.

## 2. Cartographie des Fichiers et Algorithmes

### Outils de Monitoring & HUD
- **`agent_monitor_action.py`** : HUD (Heads-Up Display) de supervision en temps réel. Offre une vue arborescente des agents actifs, y compris le flux vidéo ou le statut du navigateur web Playwright.
- **`web_navigation_replay_action.py`** : Cockpit de rejeu permettant à l'utilisateur de visionner la navigation autonome effectuée par le modèle via Playwright.

### Ingénierie & Edition
- **`echo_codex_action.py`** : Interface avancée intégrant un éditeur Monaco (type VS Code), un explorateur de fichiers (File Tree), et un historique Git natif permettant la restauration granulaire de versions.

### Sécurité & Identité
- **`echo_identity_vault_action.py`** : ECHO Identity Vault. C'est l'interface centralisée de gestion des secrets. 
  - **Sémantique** : Elle se synchronise dynamiquement avec l'API du MCP Broker et de N8N (via les schémas) pour collecter et sauvegarder les identifiants (Credentials) directement dans la base SQLite locale.
  - **Étanchéité** : Elle orchestre la distribution des secrets via des *namespaces* (domaines de session) sans jamais exposer les clés en clair.
- **`reset_auth_action.py`** : Action rapide d'urgence pour purger spécifiquement les tokens et clés liés à l'authentification **Google/PKCE** et OAuth2 en cas de désynchronisation.

### Gestion Contextuelle (Saturation & Vectoriel)
- **`resume_in_new_chat_action.py`** : Mécanisme de migration d'état ("Resume in New Chat"). Il transfère de manière propre l'historique pertinent vers une nouvelle conversation pour lutter contre la saturation contextuelle et alléger le LLM.
- **`purge_memory_action.py`** : Interface scrollable permettant la suppression ciblée et granulaire des souvenirs vectoriels (vecteurs orphelins ou erronés) directement dans Qdrant.

### Export
- **`print_pdf_action.py`** : Fonctionnalité native d'export des conversations au format PDF pour l'archivage local de la dialectique.

## 3. Dépendances Logiques
- Ces actions nécessitent souvent la déclaration de hooks JS spécifiques natifs à Open WebUI (ex: `window.mcpAlert`, protection `allow-modals`) configurés par `echo_ui.py`.
- L'Identity Vault partage sa logique de stockage SQLite avec l'ensemble du Cortex ECHO via le module `echo_constants.py`.
