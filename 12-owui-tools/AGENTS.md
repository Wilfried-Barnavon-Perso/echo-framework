# 🌌 ECHO Framework - Connaissance Sémantique : `12-owui-tools`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `12-owui-tools`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier constitue **l'Arsenal** du modèle. Il contient l'ensemble des Outils (Tools) exécutables nativement par les LLM via le framework Open WebUI. Tous ces outils requièrent l'injection stricte du contexte Open WebUI via les arguments `__user__` et `__metadata__` dans leurs signatures de fonction pour fonctionner.

## 2. Cartographie des Fichiers et Algorithmes

### Orchestration Agentique & Automatisation
- **`agent_orchestration_tool.py`** : Moteur multi-agents. Implémente `consult_council` (Table Ronde Delphi avec N experts, tours de parole stricts Analyse/Dialectique/Réponse) et gère le déclenchement asynchrone des **Child Chats** invoqués par N8N.
- **`agent_engine_tool.py`** : Moteur d'exécution pour un agent délégué unique, gérant un budget et interdisant la récursion RAG.
- **`delegate_to_data_broker.py`** : [NOUVEAU] Permet au modèle de déléguer la récupération de données complexes (API tierces, gros volumes) à un agent spécialisé (Data Broker).
- **`n8n_orchestrator_tool.py`** : Interface de commande vers l'API locale N8N. Gère le déploiement de workflows en Sandbox (éphémères, nécessitant obligatoirement un `Execute Workflow Trigger`) ou en mode Démon (permanent).

### Communication Inter-Services (MCP) & Sécurité
- **`remote_mcp_tool.py`** : [NOUVEAU] Exécuteur natif permettant au LLM de requêter des outils exposés par un MCP Server distant via le MCP Broker. L'outil gère la transmission des credentials et du JSON Schema dynamique.
- **`internal_mcp_tool.py`** : [NOUVEAU] Outil permettant d'exécuter des fonctionnalités internes isolées.
- **`identity_vault_tool.py`** : [NOUVEAU] Coffre-fort d'identités. Permet au modèle de consulter, de générer ou de révoquer ses propres credentials d'accès de manière sécurisée en base SQLite.

### Persistance & RAG
- **`memory_and_rag_tool.py`** : Outils de manipulation explicite de la base Qdrant. Implémente `search_sessions_context` (recherche avec le flag `global_search` inter-sessions), `update_meta_artifact` et `search_meta_artifacts` (fusion sémantique avec cartographie d'index et reranking).
- **`echo_codex_tool.py`** : Éditeur de code intégré avec 9 fonctions. Gère la modification de fichiers, l'intégration Git native, l'enregistrement dans SQLite et un processus de Distillation Cloud pour les revues de code.

### Web & Navigation
- **`navigation_engine_tool.py`** : Pilote de navigateur autonome basé sur Playwright. Fonctionne selon une Boucle OODA, implémente une descente cognitive via l'injection de schémas, un mode hybride Lidar/Vision et un streaming sémantique.
- **`sovereign_web_search.py`** : Outil de recherche web souveraine (SearXNG / DuckDuckGo) avec capacité de délégation récursive.

### Exploration Locale
- **`file_content_explorer.py`** : Sondage sémantique, lecture brute (RAW) et Base64 des fichiers locaux.
- **`query_registry_tool.py`** : Outil obligatoire avant toute modification de fichier. Permet au modèle d'interroger le registre unifié SQLite, incluant le `FILE_INGESTION_STATUS`.

### Utilitaires Spécialisés
- **`python_code_executor.py`** : Exécute de manière sécurisée du code Python (incluant numpy/pandas) via le conteneur `python-worker` Flask.
- **`strategic_planner.py`** : Gère la planification tactique des sous-agents avec un suivi obligatoire (`update_plan`) et une persistance dans le Codex.
- **`universal_visual_generator.py`** : Génération de diagrammes (Mindmaps, Graphes) et cartes (Leaflet) injectés directement sous forme de Data Islands isolés.
- **`gemini_maps_grounding.py`** : Interface avec l'API Google Maps Grounding pour des résultats géospatiaux enrichis.
- **`context_gauge.py`** : Jauge de contexte intelligente. Mesure l'état de saturation de la fenêtre de contexte du modèle et implémente des seuils de monitoring dynamiques (définis dans `echo_constants.py`) pour alerter l'agent avant saturation complète.
- **`generalist_tools.py`** : [NOUVEAU] Boîte à outils unifiée remplaçant les scripts épars (ex: `ask_user_input`). Implémente la saisie utilisateur interactive (via `echoCustomPrompt`) et des **Wait Timers asynchrones** programmables pour l'attente de tâches de fond.

## 3. Dépendances Logiques
- Ces outils exploitent les variables injectées par l'API Open WebUI (ID du chat, informations de l'utilisateur).
- S'ils génèrent des médias (images, graphes), ils doivent encapsuler leur retour dans la directive `wrap_tool_output` (mot-clé `echo_tool_multiparts`) définie dans `echo_constants.py` pour un rendu multimodal natif.
