# 🌌 ECHO Framework - Architecture & Protocoles

> **ATTENTION AGENTS** : Ce document est la source de vérité ABSOLUE de l'architecture ECHO. Le code dicte la loi, ce document la retranscrit. Toute invention, supposition ou hypothèse non vérifiée par le code est strictement proscrite.

- **IDE :** VS Code.
- **Terminal :** PowerShell (Admin requis pour Hyper-V).
- **Infrastructure Cible :** Machine Virtuelle Ubuntu 24.04 sur Hyper-V (Allouée à 8Go RAM et 70Go VHD dynamiques).
- **Orchestration :** Docker Compose (Standardisé version 9.3+ avec WAF ModSecurity granulaire).
- **Note Critique :** L'agent opère depuis l'hôte Windows. L'interaction avec la cible se fait via `install-hyperv.ps1` (injection ZIP via disque Seed, pointant par défaut sur la branche `dev`), `sync-echo.sh` (distribution), et `upgrade-echo.sh` (mise à niveau majeure).

---

## 0. 🗺️ Cartographie Fractale de la Connaissance

Ce document racine édicte les **Règles Globales** et l'architecture transversale du système.
Cependant, la base de connaissances d'ECHO est **distribuée et fractale**. Chaque sous-dossier majeur du projet contient son propre fichier `AGENTS.md` agissant comme une "Mémoire Sémantique Locale". 

> **Règle d'Investigation Absolue** : Lorsqu'un agent intervient sur un composant spécifique (ex: `10-owui-pipes`, `26-docker-n8n-worker`), il **DOIT obligatoirement** lire le fichier `AGENTS.md` situé dans ce dossier pour acquérir la connaissance chirurgicale locale (algorithmes, rôles, dépendances) *avant* de lire ou modifier le code.

---

## 1. 🏗️ Le Triptyque Fondamental

L'architecture repose sur trois piliers fondamentaux (Auto-Hébergement, Véracité, Autonomie) articulés autour du Kernel ECHO (dont la partie statique est définie dans `01-config/system-prompt.md`) :

### 1.1 Le Kernel Statique
- **Méta-Principes et Identité :** Définit les conditions d'exécution indépassables du Modèle, son persona (interdiction stricte des tics IA, anglicismes, listes excessives), ses outils, et les Artéfacts Environnementaux Contextuels (AEC) géo-temporels pour assurer la cohésion globale.
- **Principe de Cognition Interne et Réflexion (PCIR) :** Impose au modèle une réflexion verbale interne exhaustive et critique avant toute interaction ou déclenchement d'outil, pour maximiser le raisonnement.
- **PGCU (Gestion du Contexte Unifié) :** Fixe l'attention du modèle selon une hiérarchie stricte (Kernel > AEC > Méta-Artéfacts et Mémoires Vectorisées > Requêtes > Outils).
- **PRAF (Rigueur Analytique et Factuelle) :** Stipule que toute hypothèse vérifiable et non vérifiée sur le réel est invalidée. Impose la vérification systématique via recherche web avec un niveau de confiance justifié.

## 2. 🧠 Le Cortex (`/opt/ECHO/owui-pipes/`)

Le système nerveux central d'ECHO repose sur le **composant core `pipe_engine.py`** récemment implémenté, soutenu par les bibliothèques centrales partagées (`echo_constants.py`, `echo_protocol.py`, `echo_utils.py`).

- **Suture Bit-Perfect des Métadonnées :** Reconstruction de l'historique via SQLite (`message_shadows`) pour une continuité absolue. Garantit une reprise de session identique au bit près via l'ID de message et le timestamp (Verrou de Version). Suivi de la branche active et état de session garanti par un hash cumulatif (Cumulative Hash).
- **Registre Cognitif Unifié (SSOT) :** Toutes les capacités LLM sont désormais gouvernées centralement par `ECHO_MODELS_REGISTRY`. Ce dictionnaire abstrait mappe les identifiants métiers (`MODEL_PRO`, `MODEL_FLASH`, `MODEL_LITE`, `MODEL_DISTILLATION`) vers leurs identifiants API (AI Studio / Code Assist), leur `hierarchy` cognitive stricte (0, 1, 2), et leur `generationConfig` détaillée (température, `maxOutputTokens=65535`, et `thinkingConfig`).
- **Routage Dynamique & Fluctuation Continue :** Les modes (AUTO, AUTO_PRO) interrogent dynamiquement la hiérarchie du Registre Cognitif. Lors de la reprise d'une session ou en cas d'erreur API, le système applique un *Clamping Dynamique* et une cascade descendante (PRO → FLASH → LITE) calculés algorithmiquement sur les valeurs entières de la hiérarchie. Intègre un **Circuit Breaker OAuth2 (Fast-Failover Intra-Retry)** qui bascule instantanément sur un environnement de secours en cas de 429/503 avant d'évincer le provider. Inclut également une logique d'**Auto-heal SQLite** : si un modèle orphelin est détecté, le système effectue un reverse-lookup ou force le `MODEL_LITE` pour prévenir tout crash.
- **Orchestration Multi-Agents (`agent_orchestration_tool.py`) :** `consult_council` (Table Ronde Delphi, N experts agentiques avec outils, tours parallélisés exigeant une dialectique structurée : Analyse/Dialectique/Réponse) et `consult_supervised_workers` (boucle critique/correction récursive). L'orchestration intègre désormais le **Skill Management** (gestion de compétences avec modales de confirmation interactives) et le **Web Grounding**. Elle gère également les **Child Chats** déclenchés asynchronement par les flux N8N pour garantir une traçabilité totale.
- **HTTP/2 Stealth Headers (`echo_utils.py`) :** Multiplexage HTTP/2 natif via le nouveau client consolidé `EchoGeminiClient`. Utilisation de `httpx` (H2 obligatoire) avec en-têtes de navigation haute fidélité pour simuler un navigateur réel tout en évitant les blocages WAF. Remplacement des exceptions silencieuses par des remontées directes (raise) pour déclencher la cascade cognitive du Pipe Engine lors des erreurs réseau. Renommage des constantes de résilience (ex: `ECHO_API_KEY_RETRIES`).

## 3. 👁️ La Conscience (`/opt/ECHO/owui-filters/`)

- **Base Vectorielle des Souvenirs :** Système RAG vectoriel (Qdrant).
- **Gestion de l'Importance :** Algorithme de fusion sémantique préservant le score `memory_importance` maximal des souvenirs lors de l'ingestion.
- **Smart Context :** Injection de faits via balises XML (`<AEC_smart_context>`) et `source_id` natifs. Le filtre intercepte exhaustivement les fichiers du Workspace.
- **Conversation RAG Filter :** Filtre Outlet asynchrone pour l'injection sans latence de l'historique conversationnel dans le Session RAG (par fenêtre de tour dynamique). Extraction textuelle stricte pour bloquer les payloads Base64 (images). Le filtre applique un mécanisme d'**Upsert Idempotent Zéro-Latence** par tour de parole. Pour empêcher la réindexation redondante, il exploite désormais une **Validation O(1) en amont** en interrogeant directement le flag booléen `is_embedded` de la table SQLite `message_shadows`, garantissant une sollicitation asynchrone ultra-légère.
- **User Native Context Filter :** Filtre Inlet (priorité 10) hébergeant les réglages utilisateur (nom, localisation) pour protéger le moteur système.
- **App Drawer Filter :** Filtre Inlet silencieux (priorité 1000) injectant un composant Data Island UI (ECHO App Drawer) natif. Fournit un HUD flottant qui s'interface avec l'API WebUI pour déclencher directement les Actions sans rechargement.
- **Pipeline d'Ingestion Zéro-RAM :** Architecture asynchrone déportée pour le traitement de masse. Conversion MarkItDown et traitement hybride (Vectoriel, Codex Git, SQLite).
- **Edge Embedding Bridge :** Offload de l'inférence vectorielle (Harrier-OSS) vers le WebGPU/WASM du client. Intègre désormais un **HUD d'initialisation navigateur**, le support natif Mobile WebGPU (quantification q4), l'auto-reload, une gestion multi-onglets (via UUID `client_id` et détection de `visibility`), et un mécanisme de **Fallback CPU Asynchrone Instantané** (annulation des requêtes `asyncio.Future` si le WebSocket est rompu).
- **TCP Keep-Alive Filter :** Filtre Inlet empêchant la déconnexion inopinée des sessions websocket longues.

## 4. 🧭 Contexte Proprioceptif

Le vecteur d'état global (AEC) est injecté systématiquement :
- **Contenu Statique (`<AEC_environnement_contexte>`) :** Balise XML dont le contenu est au format YAML contenant l'identité et le grounding géo-temporel.
- **Évènements Système (`<AEC_evenement_systeme>`) :** Balise XML évènementielle dont le contenu est au format YAML notifiant le Modèle des ressources asynchrones ou nouvellement créées.
- **Règle d'Or :** Le modèle **DOIT** utiliser l'outil `query_registry` pour consulter le Registre Unifié avant toute manipulation de fichiers ou processus.

## 5. 🛠️ L'Arsenal (`/opt/ECHO/owui-tools/`)

- **ECHO N8N Orchestrator (`n8n_orchestrator_tool.py`) :** [NOUVEAU] Moteur d'interaction direct avec l'API locale N8N d'ECHO permettant de déployer, tester, modifier et supprimer des workflows d'automatisation. Il implémente les directives strictes de `n8n_architecture.md` (distinction radicale entre Sandbox Éphémère imposant un `Execute Workflow Trigger` et Déploiement Permanent Démon pour les webhooks/crons).
- **Planification Stratégique :** Agent planificateur LLM (`strategic_planner.py`). Suivi tactique obligatoire de l'état d'avancement (`update_plan`). Persistance Markdown dans le Codex Git et SQLite.
- **Mémoire & RAG (`memory_and_rag_tool.py`) :** Outils explicites RAG : `update_meta_artifact`, `search_meta_artifacts` (fusionne recherche sémantique ciblée et cartographie d'index avec reranking), `delete_meta_artifact_item`, `save_session_context`, `delete_session_context_source`, et `search_sessions_context` (fusionne recherche RAG et cartographie globale). Le paramètre `global_search` permet d'étendre la recherche à l'intégralité de l'historique inter-sessions, déclenché par des marqueurs temporels (ex: "hier").
- **Visual Intelligence :** Génération d'interfaces dynamiques (Mindmaps, Graphes, Leaflet) via `universal_visual_generator.py` isolé en Data Island.
- **Web Intelligence :** Navigation autonome Playwright (`navigation_engine_tool.py`). Boucle OODA, Descente Cognitive via injection de schémas, mode Lidar/Vision hybride, Proactive Context Pruning et streaming sémantique.
- **Sovereign Web Search :** Recherche souveraine via SearXNG et DuckDuckGo, avec capacité de délégation à un agent de recherche profonde multi-tours.
- **Agent Engine & Délégation :** Moteur d'exécution d'un agent unique (`delegate_to_agent`) et **Data Broker** (`delegate_to_data_broker.py`) pour déléguer la récupération complexe de données à un agent spécialisé.
- **Outils Généralistes :** `generalist_tools.py` intègre un `async_wait_timer` programmable et des capacités de saisie utilisateur interactive (remplaçant les scripts épars).
- **Communication Inter-Services (MCP Natif) :** Outils d'orchestration proxy `remote_mcp_tool.py` (exécution de tâches sur un MCP distant avec schéma dynamique) et `internal_mcp_tool.py` (tâches internes isolées).
- **Identity Vault (`identity_vault_tool.py`) :** Outil permettant au modèle de manipuler directement ses propres secrets et de découvrir dynamiquement les schémas d'authentification requis par le MCP distant.
- **Explorateur de l'Espace Personnel :** Lecture brute (RAW), base64, et sondage sémantique des fichiers locaux.
- **Registre Unifié :** Consultation du `FILE_INGESTION_STATUS`.
- **ECHO Codex :** Éditeur multi-langage avec Git intégré. 9 fonctions. Registre SQLite. Distillation Cloud.
- **Python Code Executor :** Sandbox sécurisée Flask. Validation analytique (pandas, numpy).

## 6. 🛡️ Gouvernance & Administration (`/opt/ECHO/docker-admin-manager/`)

- **ECHO Auth (SSO & MFA) :** IdP autonome gérant l'authentification forte (TOTP) couplé à BunkerWeb. Prise en charge des comptes locaux et OAuth2. Intègre désormais l'invalidation proactive de la session interne d'Open WebUI lors de la déconnexion globale du SSO pour éviter les collisions. La suppression d'une identité entraîne la **purge atomique totale** sur toutes les bases SQLite associées (chat, identity, MCP, N8N).
- **Dashboard Actif :** Interface interactive de monitoring du cluster Docker (Révocations granulaires, Kill-Switch, stats). Intègre désormais le monitoring de l'élagage vectoriel asynchrone (Background Task) via long-polling API (`/api/task_status`).
- **Sécurité Périmétrique :** BunkerWeb (WAF) protégeant le WebSocket WebGPU et l'API IdP.
- **Régulation & Consolidation :** Optimisation SQLite (Vacuum/WAL) et sauvegardes à chaud. Introduction du script automatisé `clean-echo.sh`. Le script d'installation centralise désormais l'**Autosafety Docker** : politique de logs stricte (max 10 Mo) et cron de nettoyage. Le dashboard `server.py` permet d'invoquer manuellement une purge profonde (Cache APT, build cache, images orphelines) pour éradiquer tout risque de saturation disque.
- **Purge Vectorielle & SQLite (Asynchrone) :** Élagage temporel (TTL) automatisé des orphelins dans Qdrant et SQLite. L'élagage se fait dorénavant via un thread dédié en arrière-plan (`run_semantic_pruning`) pour ne jamais bloquer l'interface d'administration.
- **Configuration OWUI :** Script de post-déploiement automatisé des modèles et permissions.

## 7. 🖱️ Actions Interactives (`/opt/ECHO/owui-actions/`)

- **Cockpit de Rejeu :** Interface pour la navigation web.
- **Print / PDF :** Export des conversations.
- **Purge Mémoire :** Interface scrollable de suppression vectorielle ciblée.
- **ECHO Identity Vault :** Interface centralisée (`echo_identity_vault_action.py` fusionnant MCP et N8N) se synchronisant dynamiquement avec l'API des schémas du MCP Broker et de N8N pour sauvegarder les credentials dans SQLite. Elle orchestre la distribution des secrets via `ECHO_N8N_VAULT_KEY` et maintient une étanchéité stricte.
- **Agent Monitor :** HUD offrant une vue arborescente des agents (y compris navigateur web Playwright) en temps réel.
- **Réinitialisation Auth :** Purge des tokens OAuth2.
- **ECHO Codex :** HUD Monaco Editor, file tree, historique Git, restauration de version.
- **Resume in New Chat :** Migration d'état pour lutter contre la saturation contextuelle.

## 8. 🏭 Infrastructure d'Exécution

L'infrastructure s'est enrichie pour supporter les flux asynchrones Headless N8N pilotés par l'LLM :
- **Python Worker :** API Flask pour exécution Python isolée.
- **Browser Worker :** Instance Playwright pilotée par FastAPI asynchrone (bridée à 9 FPS).
- **Embedding Worker :** Offload WebGPU/WASM prioritaire, fallback sur llama.cpp (GGUF CPU) sous Docker.
- **Download Broker :** Service de collecte asynchrone des téléchargements.
- **MCP Broker :** Serveur natif Model Context Protocol agissant désormais comme un **Proxy HTTP complet** (`m5_proxy_mcp.py`). Il implémente un système de **Forwarding d'erreurs** (remontée transparente vers le LLM) et un middleware pur ASGI interceptant silencieusement le `x-openwebui-user-id` pour l'authentification.
- **[NOUVEAU] N8N Worker :** Sous-système Headless (`26-docker-n8n-worker`) encapsulant le moteur N8N (utilisant nativement SQLite via le volume `echo-n8n-data`). API Python (Port 5003) interfaçant l'LLM avec l'orchestrateur de workflow. Les workflows N8N (Mode Démon) peuvent agir comme clients MCP (pour requêter des services d'ECHO) tout en déclenchant de nouveaux "Child Chats" dans le système ECHO pour exécuter des tâches LLM complexes avec traçabilité.
- **Workers & Logs Centralisés :** Mise en place d'un système de log unifié JSON (`logging.json`) et implémentation d'un **System-wide Health Check Rate-Limiting** sur tous les workers (STT, TTS, Embedding, Browser, Python) pour éviter la saturation par les sondes Docker.
- **UI HUD & Sécurité DOM (`echo_ui.py`) :** Le moteur de rendu implémente un système natif **"OWUI Tools"** avec des optimisations 100% Mobile Natives (unités dvh, anti-zoom iOS, touch targets 44px, anti-scroll du body). Il injecte des modales asynchrones interactives (ex: `window.echoCustomPrompt` enrichies par des boutons 'pills' pour les options) respectant le mode sombre/clair pour ne jamais bloquer l'Event Loop, et sécurise les rendus HTML via `window.echoSanitizeHTML`.

## 9. 🚦 Orchestration Séquentielle (Docker Compose)

L'infrastructure est désormais pilotée via la configuration standardisée `stack-echo.yml`. Démarrage ordonné par hostnames stricts (`echo-*`) via `healthcheck` + `depends_on: condition: service_healthy` :
- **Tier 1 (Fondations)** : Qdrant, SearXNG, Watchtower.
- **Tier 2 (Workers)** : Embedding, Python Worker, Browser Worker, MCP Broker, N8N Worker, **STT Worker**, **TTS Worker**.
- **Tier 3** : Open WebUI.
- **Tier 4** : Admin Manager.

## 10. 🔢 Stratégie de Versioning (`VERSIONING.md`)

- **Version Globale :** Fichier `VERSION` (SemVer 5.Y.Z). 
- **Encodage Strict :** Les fichiers `VERSION`, `.py`, `.xml`, `.sh`, et `.json` doivent **obligatoirement être encodés en UTF-8 sans BOM**. L'introduction de BOM ou de mojibake est strictement interdite.
- **Versioning des Composants :** Granularité définie dans les en-têtes de modules.

## 11. 🔐 Authentification Antigravity 2.1

- **Multi-Provider :** OAuth2 > Clé Primaire > Clé Secondaire.
- **OAuth2 PKCE :** Flow Authorization Code via tunnel SSH éphémère (Ports 8020-8024).

## 12. 📜 Standards de Développement (Rigueur Absolue)

- **Architecture N8N (Règles strictes) :** Tout workflow éphémère de Sandbox testé via le CLI doit **obligatoirement** démarrer par le nœud `Execute Workflow Trigger`. Le Mocking de payload asynchrone via des nœuds "Code" ou "Set" est impératif pour simuler les Webhooks/Emails lors de tests LLM. L'usage en dur de tokens d'API dans les nœuds est proscrit.
- **OWUI Injection & PEP8 :** L'intégralité des outils de l'Arsenal doit strictement déclarer les arguments `__user__` et `__metadata__` dans leur interface pour garantir l'injection native du contexte par Open WebUI. Le code doit respecter strictement la norme PEP8 (les variables locales inutilisées sont impérativement préfixées par un underscore `_` ou supprimées, et les imports inutiles purgés).
- **OWUI Tool Multiparts :** Les outils générant ou retournant des fichiers médias doivent encapsuler la réponse dans la directive `wrap_tool_output` via le mot-clé standardisé `echo_tool_multiparts` (remplaçant toute ancienne nomenclature) pour assurer le rendu multimodal natif d'Open WebUI.
- **Async-First :** Utilisation impérative d'`asyncio` et `httpx`. L'API Admin utilise désormais des tâches en arrière-plan (`threading.Thread` + polling API) pour les opérations longues (élagage Qdrant).
- **Règle d'Énonciation :** Le Kernel statique et les docstrings doivent adopter un ton impersonnel ("Permet au modèle de"). L'utilisation de la 2ème personne ("Tu es...") est réservée aux `system_prompt` des agents.
- **Persistence :** Accès base de données uniquement via `EchoStateManager` (SQLite) ou la base native SQLite de N8N.
- **Auto-Hébergement :** Les clés API ne sortent jamais du cluster.
- **Vérification du Code :** Lors du codage, il est impératif de vérifier systématiquement l'algorithme, les imports et la syntaxe du code produit.

---
---
---
*Document de référence pour l'agent ECHO - Version de Stack Actuelle : 5.202.13*
