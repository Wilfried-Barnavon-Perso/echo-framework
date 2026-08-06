# 🌌 ECHO Framework - Architecture & Protocoles

> **ATTENTION AGENTS** : Ce document est la source de vérité ABSOLUE de l'architecture ECHO. Le code dicte la loi, ce document la retranscrit. Toute invention, supposition ou hypothèse non vérifiée par le code est strictement proscrite.

- **IDE :** VS Code.
- **Terminal :** PowerShell (Admin requis pour Hyper-V).
- **Infrastructure Cible :** Machine Virtuelle Ubuntu 24.04 sur Hyper-V (Allouée à 8Go RAM et 70Go VHD dynamiques).
- **Orchestration :** Docker Compose (Standardisé version 9.3+ avec WAF ModSecurity granulaire).
- **Note Critique :** L'agent opère depuis l'hôte Windows. L'interaction avec la cible se fait via `install-hyperv.ps1` (injection ZIP via disque Seed, pointant par défaut sur la branche `dev`), `sync-echo.sh` (distribution), et `upgrade-echo.sh` (mise à niveau majeure).

---

## 1. 🏗️ Le Triptyque Fondamental

L'architecture repose sur trois piliers fondamentaux (Auto-Hébergement, Véracité, Autonomie) articulés autour du Kernel ECHO (dont la partie statique est définie dans `01-config/system-prompt.md`) :

### 1.1 Le Kernel Statique
- **Méta-Principes et Identité :** Définit les conditions d'exécution indépassables du Modèle, son persona, ses outils, et les Artéfacts Environnementaux Contextuels (AEC) pour assurer la cohésion globale.
- **Principe de Cognition Interne et Réflexion (PCIR) :** Impose au modèle une réflexion verbale interne exhaustive et critique avant toute interaction ou déclenchement d'outil, pour maximiser le raisonnement.
- **PGCU (Gestion du Contexte Unifié) :** Fixe l'attention du modèle selon une hiérarchie stricte (Kernel > AEC > Méta-Artéfacts et Mémoires Vectorisées > Requêtes > Outils).
- **PRAF (Rigueur Analytique et Factuelle) :** Impose la vérification systématique de chaque fait et hypothèse via recherche web avec un niveau de confiance justifié.

## 2. 🧠 Le Cortex (`/opt/ECHO/owui-pipes/`)

Le système nerveux central d'ECHO repose sur le `pipe_engine.py` et les bibliothèques centrales (`echo_constants.py`, `echo_protocol.py`, `echo_utils.py`).

- **Suture Bit-Perfect des Métadonnées :** Reconstruction de l'historique via SQLite (`message_shadows`) pour une continuité absolue. Garantit une reprise de session identique au bit près via l'ID de message et le timestamp (Verrou de Version). Suivi de la branche active et état de session garanti par un hash cumulatif (Cumulative Hash).
- **Registre Cognitif Unifié (SSOT) :** Toutes les capacités LLM sont désormais gouvernées centralement par `ECHO_MODELS_REGISTRY`. Ce dictionnaire abstrait mappe les identifiants métiers (`MODEL_PRO`, `MODEL_FLASH`, `MODEL_LITE`, `MODEL_DISTILLATION`) vers leurs identifiants API (AI Studio / Code Assist), leur `hierarchy` cognitive stricte (0, 1, 2), et leur `generationConfig` détaillée (température, `maxOutputTokens=65535`, et `thinkingConfig`).
- **Routage Dynamique & Fluctuation Continue :** Les modes (AUTO, AUTO_PRO) interrogent dynamiquement la hiérarchie du Registre Cognitif. Lors de la reprise d'une session ou en cas d'erreur API, le système applique un *Clamping Dynamique* et une cascade descendante (PRO → FLASH → LITE) calculés algorithmiquement sur les valeurs entières de la hiérarchie. Inclut une logique d'**Auto-heal SQLite** : si un modèle orphelin est détecté, le système effectue un reverse-lookup ou force le `MODEL_LITE` pour prévenir tout crash.
- **Orchestration Multi-Agents (`agent_orchestration_tool.py`) :** `consult_council` (Table Ronde Delphi, N experts agentiques avec outils, tours parallélisés exigeant une dialectique structurée : Analyse/Dialectique/Réponse) et `consult_supervised_workers` (boucle critique/correction récursive).
- **HTTP/2 Stealth Headers :** Utilisation de `httpx` (H2 obligatoire) avec en-têtes de navigation haute fidélité pour simuler un navigateur réel.

## 3. 👁️ La Conscience (`/opt/ECHO/owui-filters/`)

- **Base Vectorielle des Souvenirs :** Système RAG vectoriel (Qdrant).
- **Gestion de l'Importance :** Algorithme de fusion sémantique préservant le score `memory_importance` maximal des souvenirs lors de l'ingestion.
- **Smart Context :** Injection de faits via balises XML (`<smart_context>`) et `source_id` natifs. Le filtre intercepte exhaustivement les fichiers du Workspace.
- **Conversation RAG Filter :** Filtre Outlet asynchrone pour l'injection sans latence de l'historique conversationnel dans le Session RAG (par fenêtre de tour dynamique). Extraction textuelle stricte pour bloquer les payloads Base64 (images). Le filtre applique désormais un mécanisme d'**Upsert Idempotent Zéro-Latence** par tour de parole, garantissant l'intégrité de l'indexation asynchrone sans bloquer le flux, en utilisant l'ID du message comme `unique_seed`.
- **User Native Context Filter :** Filtre Inlet (priorité 10) hébergeant les réglages utilisateur (nom, localisation) pour protéger le moteur système.
- **App Drawer Filter :** Filtre Inlet silencieux (priorité 1000) injectant un composant Data Island UI (ECHO App Drawer) natif. Fournit un HUD flottant qui s'interface avec l'API WebUI pour déclencher directement les Actions sans rechargement.
- **Pipeline d'Ingestion Zéro-RAM :** Architecture asynchrone déportée pour le traitement de masse. Conversion MarkItDown et traitement hybride (Vectoriel, Codex Git, SQLite).
- **Edge Embedding Bridge :** Offload de l'inférence vectorielle (Harrier-OSS) vers le WebGPU/WASM du navigateur client via WebSocket.

## 4. 🧭 Contexte Proprioceptif

Le vecteur d'état global (AEC) est injecté systématiquement :
- **Contenu Statique (`<environnement_contexte>`) :** Identité et grounding géo-temporel.
- **Évènements Système (`<evenement_systeme>`) :** Bloc XML évènementiel notifiant le Modèle des ressources asynchrones ou nouvellement créées.
- **Règle d'Or :** Le modèle **DOIT** utiliser l'outil `query_registry` pour consulter le Registre Unifié avant toute manipulation de fichiers ou processus.

## 5. 🛠️ L'Arsenal (`/opt/ECHO/owui-tools/`)

- **Planification Stratégique :** Agent planificateur LLM (`strategic_planner.py`). Suivi tactique obligatoire de l'état d'avancement (`update_plan`). Persistance Markdown dans le Codex Git et SQLite.
- **Mémoire & RAG (`memory_and_rag_tool.py`) :** Outils explicites RAG : `update_meta_artifact`, `search_meta_artifacts` (fusionne recherche sémantique ciblée et cartographie d'index avec reranking), `delete_meta_artifact_item`, `save_session_context`, `delete_session_context_source`, et `search_sessions_context` (fusionne recherche RAG et cartographie globale). Le paramètre `global_search` permet d'étendre la recherche à l'intégralité de l'historique inter-sessions, déclenché par des marqueurs temporels (ex: "hier").
- **Visual Intelligence :** Génération d'interfaces dynamiques (Mindmaps, Graphes, Leaflet) via `universal_visual_generator.py` isolé en Data Island.
- **Web Intelligence :** Navigation autonome Playwright (`navigation_engine_tool.py`). Boucle OODA, Descente Cognitive via injection de schémas, mode Lidar/Vision hybride, Proactive Context Pruning et streaming sémantique.
- **Sovereign Web Search :** Recherche souveraine via SearXNG et DuckDuckGo, avec capacité de délégation à un agent de recherche profonde multi-tours.
- **Agent Engine :** Moteur d'exécution d'un agent unique (`delegate_to_agent`). Boucle avec outils, budget, sans récursion RAG.
- **Explorateur de l'Espace Personnel :** Lecture brute (RAW), base64, et sondage sémantique des fichiers locaux.
- **Registre Unifié :** Consultation du `FILE_INGESTION_STATUS`.
- **ECHO Codex :** Éditeur multi-langage avec Git intégré. 9 fonctions. Registre SQLite. Distillation Cloud.
- **Python Code Executor :** Sandbox sécurisée Flask. Validation analytique (pandas, numpy).

## 6. 🛡️ Gouvernance & Administration (`/opt/ECHO/docker-admin-manager/`)

- **ECHO Auth (SSO & MFA) :** IdP autonome gérant l'authentification forte (TOTP) couplé à BunkerWeb.
- **Dashboard Actif :** Interface interactive de monitoring du cluster Docker (Révocations granulaires, Kill-Switch, stats).
- **Sécurité Périmétrique :** BunkerWeb (WAF) protégeant le WebSocket WebGPU et l'API IdP.
- **Régulation & Consolidation :** Optimisation SQLite (Vacuum/WAL) et sauvegardes à chaud. Le script d'installation centralise désormais l'**Autosafety Docker** : configuration d'une politique de logs stricte (max 10 Mo) et mise en place d'un cron hebdomadaire de nettoyage (`docker system prune`) pour éradiquer tout risque de saturation disque.
- **Purge Vectorielle & SQLite :** Élagage temporel (TTL) automatisé des orphelins dans Qdrant et SQLite.
- **Configuration OWUI :** Script de post-déploiement automatisé des modèles et permissions.

## 7. 🖱️ Actions Interactives (`/opt/ECHO/owui-actions/`)

- **Cockpit de Rejeu :** Interface pour la navigation web.
- **Print / PDF :** Export des conversations.
- **Purge Mémoire :** Interface scrollable de suppression vectorielle ciblée.
- **ECHO Identity Vault :** Interface centralisée (`echo_identity_vault_action.py`) se synchronisant dynamiquement avec l'API des schémas du MCP Broker (Port 8000) et les besoins locaux (N8N) pour sauvegarder les credentials dans SQLite (`identity_vault`).
- **Agent Monitor :** HUD offrant une vue arborescente des agents (y compris navigateur web Playwright) en temps réel.
- **Réinitialisation Auth :** Purge des tokens OAuth2.
- **ECHO Codex :** HUD Monaco Editor, file tree, historique Git, restauration de version.
- **Resume in New Chat :** Migration d'état pour lutter contre la saturation contextuelle.

## 8. 🏭 Infrastructure d'Exécution

- **Python Worker :** API Flask pour exécution Python isolée.
- **Browser Worker :** Instance Playwright pilotée par FastAPI asynchrone (bridée à 9 FPS).
- **Embedding Worker :** Offload WebGPU/WASM prioritaire, fallback sur llama.cpp (GGUF CPU) sous Docker.
- **Download Broker :** Service de collecte asynchrone des téléchargements.
- **MCP Broker :** Serveur natif Model Context Protocol (`FastMCP` via Uvicorn). Expose via ASGI StreamableHTTP des outils externes (Omnisearch Jobs, Corporate Sirene/Bodacc, Academic). Intègre un middleware pur ASGI interceptant silencieusement le `x-openwebui-user-id`.
- **UI HUD & Sécurité DOM (`echo_ui.py`) :** Le moteur de rendu de l'interface injecte désormais ses propres modales asynchrones natives (`window.echoCustomConfirm`, `window.mcpAlert`) qui respectent le mode sombre/clair pour ne jamais bloquer l'Event Loop. L'interface ECHO Codex intègre des redimensionnements fluides de la Sidebar (`splitter`), une impression PDF native (`allow-modals`), et une protection contre les états d'affichage corrompus au chargement.

## 9. 🚦 Orchestration Séquentielle (Docker Compose)

Démarrage ordonné par hostnames stricts (`echo-*`) :
- **Tier 1 (Fondations)** : Qdrant, SearXNG, Watchtower.
- **Tier 2 (Workers)** : Embedding, Python Worker, Browser Worker, MCP Broker.
- **Tier 3** : Open WebUI.
- **Tier 4** : Admin Manager.

## 10. 🔢 Stratégie de Versioning (`VERSIONING.md`)

- **Version Globale :** Fichier `VERSION` (SemVer 5.Y.Z).
- **Versioning des Composants :** Granularité définie dans les en-têtes de modules.

## 11. 🔐 Authentification Antigravity 2.1

- **Multi-Provider :** OAuth2 > Clé Primaire > Clé Secondaire.
- **OAuth2 PKCE :** Flow Authorization Code via tunnel SSH éphémère (Ports 8020-8024).

## 12. 📜 Standards de Développement (Rigueur Absolue)

- **OWUI Injection & PEP8 :** L'intégralité des outils de l'Arsenal doit strictement déclarer les arguments `__user__` et `__metadata__` dans leur interface pour garantir l'injection native du contexte par Open WebUI. Le code doit respecter strictement la norme PEP8 (les variables locales inutilisées sont impérativement préfixées par un underscore `_` ou supprimées, et les imports inutiles purgés).
- **OWUI Tool Multiparts :** Les outils générant ou retournant des fichiers médias doivent encapsuler la réponse dans la directive `wrap_tool_output` via le mot-clé standardisé `echo_tool_multiparts` (remplaçant toute ancienne nomenclature) pour assurer le rendu multimodal natif d'Open WebUI.
- **Async-First :** Utilisation impérative d'`asyncio` et `httpx`.
- **Règle d'Énonciation :** Le Kernel statique et les docstrings doivent adopter un ton impersonnel ("Permet au modèle de"). L'utilisation de la 2ème personne ("Tu es...") est réservée aux `system_prompt` des agents.
- **Persistence :** Accès base de données uniquement via `EchoStateManager` (SQLite).
- **Auto-Hébergement :** Les clés API ne sortent jamais du cluster.

---
*Document de référence pour l'agent ECHO - Version de Stack Actuelle : 5.193.0*
