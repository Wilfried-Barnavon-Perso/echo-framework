# 🌌 ECHO Framework - Connaissance Sémantique : `12-owui-tools`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `12-owui-tools`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier constitue **l'Arsenal** du modèle. Il contient l'ensemble des Outils (Tools) exécutables nativement par les LLM via le framework Open WebUI. Tous ces outils requièrent l'injection stricte du contexte Open WebUI via les arguments `__user__` et `__metadata__` dans leurs signatures de fonction pour fonctionner.

## 2. Cartographie des Fichiers et Algorithmes

### Orchestration Agentique & Automatisation
- **`agent_orchestration_tool.py`** : Moteur multi-agents. Implémente `consult_council` (Table Ronde Delphi avec N experts, tours de parole stricts Analyse/Dialectique/Réponse). Intègre désormais le **Skill Management** (gestion des compétences avec modales de confirmation, et une protection stricte interdisant la suppression `delete_user_skill` en mode Headless sans interface UI `__event_call__`) et le **Web Grounding**. Il gère également le déclenchement asynchrone des **Child Chats** invoqués par N8N.
- **`agent_engine_tool.py`** : Moteur d'exécution pour un agent délégué unique, gérant un budget et interdisant la récursion RAG. Il injecte l'identité du sous-agent via des `contextvars` (`ECHO_SUBAGENT_CONTEXT`) pour contourner l'encapsulation restrictive d'Open WebUI sans altérer la signature des outils.
- **`delegate_to_data_broker.py`** : [NOUVEAU] Permet au modèle de déléguer la récupération de données complexes (API tierces, gros volumes) à un agent spécialisé (Data Broker).
- **`n8n_orchestrator_tool.py`** : Interface de commande vers l'API locale N8N. Gère le déploiement de workflows en Sandbox (éphémères, nécessitant obligatoirement un `Execute Workflow Trigger`) ou en mode Démon (permanent). Intègre une délégation experte `delegate_to_n8n_grapher` pour la conception architecturale, de nouvelles capacités d'interrogation et téléchargement de templates depuis le Hub communautaire (`search_n8n_hub`, `download_n8n_hub_template`), et utilise désormais nativement `wrap_tool_output` pour la restitution (ex: `query_n8n_documentation`).

### Communication Inter-Services (MCP) & Sécurité
- **`remote_mcp_tool.py`** : [NOUVEAU] Exécuteur natif permettant au LLM de requêter des outils exposés par un MCP Server distant via le MCP Broker. L'outil gère la transmission des credentials et du JSON Schema dynamique.
- **`internal_mcp_tool.py`** : [NOUVEAU] Outil permettant d'exécuter des fonctionnalités internes isolées.
- **`identity_vault_tool.py`** : [NOUVEAU] Coffre-fort d'identités. Permet au modèle de consulter, générer ou révoquer ses propres credentials d'accès de manière sécurisée en base SQLite. Intègre une tolérance aux pannes (parsing JSON) pour garantir la résilience de l'affichage global de `list_identities`. La notion d'accès RO/RW a été totalement supprimée pour un accès unique universel.

### Persistance & RAG
- **`memory_and_rag_tool.py`** : Outils de manipulation explicite de la base Qdrant. Implémente `search_sessions_context` (recherche avec le flag `global_search` inter-sessions et injection stricte du `score_threshold` vers Qdrant), `update_meta_artifact` et `search_meta_artifacts` (fusion sémantique avec cartographie d'index et reranking). Intègre désormais une purge idempotente pré-indexation pour empêcher toute fuite de vecteurs fantômes, ainsi qu'un bridage optimisé du chevauchement (overlap) des chunks.
- **`echo_codex_tool.py`** : Éditeur de code intégré. Gère la modification de fichiers, l'intégration Git native, l'enregistrement dans SQLite. Intègre un **Lock asynchrone** (clé `user_id:chat_id`) pour prévenir toute race condition. Intègre désormais une capacité de **Voyage Temporel** (`search_codex` avec `trace_history` pour le Pickaxe/Delta) permettant de tracer l'évolution du code, ainsi que `restore_codex` pour restaurer une version historique, et une purge récursive (`delete_codex`) gérant la suppression intégrale de dossiers.

### Web & Navigation
- **`navigation_engine_tool.py`** : Pilote de navigateur autonome basé sur Playwright. Fonctionne selon une Boucle OODA, implémente une descente cognitive via l'injection de schémas, un mode hybride Lidar/Vision et un streaming sémantique. Il assure la synchronisation de l'URL en temps réel dans le HUD Live via `stream_proxy`.
- **`sovereign_web_search.py`** : Outil de recherche web souveraine (SearXNG / DuckDuckGo) avec capacité de délégation récursive.

### Exploration Locale
- **`file_content_explorer.py`** : Sondage sémantique, lecture brute (RAW) et Base64 des fichiers locaux. Intègre l'accès direct aux espaces Codex (main/sandbox) via la résolution étendue des chemins (`_resolve_extended_file_path`) et une sécurité stricte de taille maximale pour l'injection LLM (`MAX_MULTIMODAL_SIZE_KB`).
- **`query_registry_tool.py`** : Outil obligatoire avant toute modification de fichier. Permet au modèle d'interroger le registre unifié SQLite, incluant le `FILE_INGESTION_STATUS`.

### Utilitaires Spécialisés
- **`api_client.py`** : ECHO Universal API Client. Composant système interne fournissant une abstraction robuste pour l'exécution des requêtes HTTP asynchrones.
- **`code_executor_tool.py`** : Sandbox d'exécution unifiée (remplace l'ancien `python_code_executor.py`). Supporte nativement l'exécution multi-langages (Python 🐍, JavaScript/Node 📦), gère dynamiquement l'installation de dépendances (pip, npm) avec timeout calculé, le tout adossé au protocole HTTPX asynchrone sur Pydantic V2.
- **`strategic_planner.py`** : Gère la planification tactique des sous-agents via un cycle en multi-étapes (multi-stage planning), avec suivi obligatoire des statuts (`update_plan`) et outils d'analyse (`analyze_plan`). La persistance est adossée à un **Codex Git** (Git-backed Codex) pour un versionnement robuste.
- **`universal_visual_generator.py`** : Génération de diagrammes (Mindmaps, Graphes) et cartes (Leaflet) injectés directement sous forme de Data Islands isolés.
- **`gemini_maps_grounding.py`** : Interface avec l'API Google Maps Grounding pour des résultats géospatiaux enrichis.
- **`context_gauge.py`** : Jauge de contexte intelligente. Mesure l'état de saturation de la fenêtre de contexte du modèle et implémente des seuils de monitoring dynamiques (définis dans `echo_constants.py`) pour alerter l'agent avant saturation complète.
- **`generalist_tools.py`** : [NOUVEAU] Boîte à outils unifiée remplaçant les scripts épars (ex: `ask_user_input`). Implémente la saisie utilisateur interactive (via `echoCustomPrompt`) et des **Wait Timers asynchrones** programmables pour l'attente de tâches de fond.

## 3. Dépendances Logiques
- Ces outils exploitent les variables injectées par l'API Open WebUI (ID du chat, informations de l'utilisateur).
- S'ils génèrent des médias (images, graphes), ils doivent encapsuler leur retour dans la directive `wrap_tool_output` (mot-clé `echo_tool_multiparts`) définie dans `echo_constants.py` pour un rendu multimodal natif.
