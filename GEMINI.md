- **IDE :** VS Code.
- **Terminal :** PowerShell (Admin requis pour Hyper-V).
- **Infrastructure Cible :** Machine Virtuelle Ubuntu 24.04 sur Hyper-V (Allouée à 8Go RAM et 70Go VHD dynamiques).
- **Orchestration :** Docker Compose (Standardisé version 9.3+ avec WAF ModSecurity granulaire).
- **Note Critique :** L'agent opère depuis l'hôte Windows. L'interaction avec la cible se fait via `install-hyperv.ps1` (injection ZIP via disque Seed, pointant désormais par défaut sur la branche `dev` en version 5.147.43), `sync-echo.sh` (distribution), et `upgrade-echo.sh` (mise à niveau majeure de l'infrastructure).

## 🏗️ Architecture V5 : Le Triptyque Fondamental

L'architecture repose sur trois piliers fondamentaux (Auto-Hébergement, Véracité, Autonomie) articulés autour du Kernel ECHO (dont la partie statique est définie dans `01-config/system-prompt.md`) :

### 0. Le Kernel Statique (`01-config/system-prompt.md`)
- **Méta-Principes et Identité :** Définit les conditions d'exécution indépassables du Modèle, son persona, ses outils, et les Artéfacts Environnementaux Contextuels (AEC) pour assurer la cohésion globale.
- **Principe de Cognition Interne et Réflexion (PCIR) :** Impose au modèle une réflexion verbale interne exhaustive et critique avant toute interaction ou déclenchement d'outil, pour maximiser le raisonnement.
- **PGCU (Gestion du Contexte Unifié) :** Fixe l'attention du modèle selon une hiérarchie stricte (Kernel > AEC > Méta-Artéfacts et Mémoires Vectorisées > Requêtes > Outils).
- **PRAF (Rigueur Analytique et Factuelle) :** Impose la vérification systématique de chaque fait et hypothèse via recherche web avec un niveau de confiance justifié.

### 1. Le Cortex (`/opt/ECHO/owui-pipes/pipe_engine.py`)
- **Suture Bit-Perfect des Métadonnées Gemini :** Reconstruction de l'historique via SQLite (`message_shadows`, table conservée pour compatibilité production) pour une continuité absolue. Garantit une reprise de session identique au bit près via l'ID de message et le timestamp (Verrou de Version). Le suivi de la branche active et de l'état de la session est garanti par un calcul de hash cumulatif (Cumulative Hash) via `EchoStateManager`.
- **Ajustement du niveau cognitif :** Routage dynamique intelligent (LITE -> FLASH -> PRO).
- **Délégation Cognitive :** Utilisation de l'outil `new_cognitive_level` pour déléguer les tâches complexes au modèle PRO lors de la traversée de la "Vallée de la Mort Contextuelle" (saturation contextuelle > 50%).
- **Orchestration Multi-Agents (`agent_orchestration_tool.py`) :** `consult_council` (Table Ronde Delphi, N experts agentiques avec outils, tours parallélisés exigeant une dialectique structurée : Analyse/Dialectique/Réponse) et `consult_supervised_workers` (boucle critique/correction récursive). Gestion des Skills via `forge_skill`/`list_skills`. Conservation des `thoughtSignatures` Gemini 3.x.
- **HTTP/2 Stealth Headers :** Utilisation de `httpx` (H2 obligatoire) avec en-têtes de navigation haute fidélité (`get_stealth_headers`) pour simuler un navigateur réel.

### 2. La Conscience (`/opt/ECHO/owui-filters/`)
- **Base vectorielle des souvenirs :** Système RAG vectoriel (Qdrant) avec Distillation Contextuelle automatique par **fenêtre de tour dynamique**. Nettoyage des messages (role+content+fichiers) pour optimiser le budget tokens de la distillation Cloud.
- **Gestion de l'importance des souvenirs :** Algorithme de fusion sémantique préservant le score `memory_importance` maximal des souvenirs.
- **Smart Context :** Injection de faits via des balises XML structurelles (`<smart_context>`) et utilisation de `source_id` natifs (au lieu de slugs) pour la Mémoire Vectorisée de Session. Le filtre intercepte désormais exhaustivement les fichiers globaux du Workspace pour garantir une ingestion totale.
- **Conversation RAG Filter :** Filtre Outlet asynchrone pour l'injection sans latence de l'historique conversationnel dans le Session RAG par **fenêtre de tour dynamique**. Le filtre assure dorénavant l'extraction textuelle stricte des messages multipart pour bloquer l'ingestion accidentelle de payloads Base64 (images) vers la base vectorielle.
- **User Native Context Filter :** Filtre Inlet d'interface pure (priorité 10) hébergeant les réglages utilisateur (nom, localisation) et les injectant dans les métadonnées de requête pour protéger le `new_context_filter` (moteur système) de toute désactivation.
- **App Drawer Filter :** Filtre Inlet silencieux (priorité 1000) injectant un composant Data Island UI (ECHO App Drawer) natif. Il fournit un HUD flottant capable de s'interfacer directement avec les Actions des Modèles de l'utilisateur sans impacter la fenêtre de discussion.
- **Pipeline d'Ingestion Zéro-RAM :** Architecture asynchrone déportée (`echo_ingestion.py`) pour le traitement mémoire-efficient de masse (batch vector processing). Conversion native des documents Office en Markdown (MarkItDown) et traitement hybride transparent (Mémoire Vectorisée, Codex Git, Fallback SQLite).
- **Edge Embedding Bridge :** Offload de l'inférence vectorielle (microsoft/Harrier-OSS-v1-0.6B) vers le navigateur client via WebGPU/WASM (WebSocket), réduisant drastiquement la charge CPU avec bascule automatique sur le backend Docker en cas d'inactivité.

### 3. Contexte Proprioceptif : `environnement_contexte` & `evenement_systeme`
Le vecteur d'état global (AEC) est injecté systématiquement par le filtre `new_context_filter.py`.
- **Contenu Statique (`<environnement_contexte>`) :** Identité (`modèle_actuel`, `modèle_origine`) et grounding géo-temporel.
- **Évènements Système (`<evenement_systeme>`) :** Bloc XML évènementiel notifiant le Modèle des ressources créées au tour courant ou détectées en asynchrone via le Watermark Delta.
- **Règle d'Or :** Le modèle **DOIT** utiliser l'outil `query_registry` pour consulter le Registre Unifié (Codex, Plans, Médias, URLs) et valider l'existence ou l'état d'une ressource avant toute manipulation.

### 4. L'Arsenal (`/opt/ECHO/owui-tools/`)
- **Planification Stratégique :** Construction, modification et gestion de plans d'action via un agent planificateur LLM (`strategic_planner.py`). Les instructions imposent à l'Orchestrateur le suivi tactique chronologique et la mise à jour obligatoire de l'état d'avancement via `update_plan` (modèle FLASH) après création via `build_plan` (modèle PRO). Cascade cognitive centralisée, persistance Markdown dans le Vault et registre SQLite.
- **Mémoire & RAG (`memory_and_rag_tool.py`) :** Outils explicites basés sur la nomenclature Méta-Artéfacts : `update_meta_artifact`, `search_meta_artifacts` (fusionnant recherche sémantique ciblée et lecture globale d'index avec reranking pondéré), `delete_meta_artifact_item`, `save_session_context`, `delete_session_context_source` (nouveau), et `search_session_context` (fusionnant recherche RAG et cartographie globale des sources inter-sessions).
- **Visual Intelligence :** Génération d'interfaces dynamiques (Mindmaps, Graphes) via `universal_visual_generator.py` et `echo_visuals.py` (Pattern 'Data Island' pour isoler le JS).
- **Web Intelligence :** Navigation autonome Playwright (`navigation_engine_tool.py`) pilotée par **boucle OODA autonome** via une **API à 4 piliers** (A11y, DOM, Inspect, Control). La *Descente Cognitive* (injection dynamique de `action_analyze_page` et `action_archive_page` dans le schéma) rend le Sous-Agent autonome via un Streaming Sémantique natif et un Archivage RAG asynchrone, remplaçant l'ancienne distillation monolithique. Utilise un mode hybride Lidar/Vision (Vision-On-Demand), une intégration multimodale (Gemini 3.x via attribut `parts` pour prévenir l'erreur 400) et le *Parallel Function Calling*. Inclut un mécanisme de *Proactive Context Pruning* (élagage dynamique du DOM via seuil configurable) et proscrit l'usage de moteurs de recherche généralistes.
- **Sovereign Web Search (`sovereign_web_search.py`) :** Outils de recherche souveraine via SearXNG (recherche classique One-Shot) et DuckDuckGo (réponse instantanée factuelle), avec capacité de délégation à un agent de recherche profonde (Deep Research Agent) autonome pour les requêtes complexes multi-tours.
- **Agent Engine (`agent_engine_tool.py`) :** Moteur d'exécution d'un agent unique via `delegate_to_agent` (± Skill via `role_name`). Boucle agentique avec outils, escalade cognitive, budget configurable. Depth=1 (pas de récursion), pas d'écriture RAG. Intègre l'injection universelle du contexte temporel (`<context_temporel>`) et des directives de rigueur (`<directives_globales>`) à la volée.
- **Explorateur de l'Espace Personnel (`file_content_explorer.py`) :** Outil de lecture brute (RAW) et de sondage sémantique des fichiers locaux. Supporte l'extraction textuelle, l'encapsulation Base64 (pour l'injection multimodale) et l'analyse structurelle Hexadécimale. Soumis à validation via le Registre Unifié.
- **Registre Unifié (`query_registry_tool.py`) :** Outil de consultation de l'état cognitif des ressources centralisées (`FILE_INGESTION_STATUS`) indexées par le système.
- **ECHO Codex (`echo_codex_tool.py`) :** Éditeur multi-langage avec Git intégré (dulwich). 9 fonctions (create, edit, read, search, summarize, list, delete, history). Édition assistée par sub-chat `MODEL_FLASH` via `call_cascade`. Registre `codex_docs` dans SQLite par chat. Distillation Cloud pour résumé technique.
- **Python Code Executor (`python_code_executor.py`) :** Exécution de code Python dans une sandbox sécurisée (docker-python-worker) dédiée à la validation analytique complexe et aux calculs Data Science (pandas, numpy). Pilote l'API distante avec isolation des environnements et gestion native des erreurs OS.

### 5. Gouvernance & Administration (`/opt/ECHO/docker-admin-manager/`)
- **ECHO Auth (SSO & MFA) :** IdP autonome (`/opt/ECHO/24-docker-echo-auth-manager/`) gérant l'authentification forte (TOTP). Couplé à BunkerWeb via Forward Auth (`/api/verify`), l'état des sessions et les bannissements IP sont pilotés depuis l'Admin Manager (Révocations granulaires, Kill-Switch).
- **Dashboard Actif :** Interface interactive (Sidebar asynchrone) de monitoring du cluster Docker, gestion renforcée du SSO (révocation, purge dynamique sécurisée des utilisateurs via garde-fous API) et supervision des ressources système.
- **Sécurité Périmétrique :** Intégration de BunkerWeb (WAF) avec Kill-Switch PWA, routage natif (`location = /ws/edge-embed`) pour le pont WebGPU, et routage sécurisé du service `echo-auth-manager` via proxy inverse.
- **Régulation & Consolidation :** Optimisation physique SQLite (Vacuum/WAL), gestion des sauvegardes à chaud (incluant les bases IdP) et autosécurité Docker (rotation automatisée des logs pour prévenir la saturation disque).
- **Purge Vectorielle & SQLite :** Centralisation du processus d'élagage temporel (TTL) et de la purge des orphelins dans les collections Qdrant (`echo_meta_artifacts`, `echo_session_rag`), incluant la purge dynamique utilisateur par introspection SQLite.
- **Configuration Automatique (Open WebUI) :** Script d'orchestration post-déploiement (`00-echo-scripts/config-owui.sh`) paramétrant dynamiquement l'interface, les modèles et les permissions via API à partir du template statique (`01-config/webui-settings.json`).

### 6. Actions Interactives (`/opt/ECHO/owui-actions/`)
- **Cockpit de Rejeu :** Interface de contrôle pour la navigation web (`web_navigation_replay_action.py`).
- **Print / PDF :** Impression et export PDF de conversations via `print_pdf_action.py`.
- **Purge Mémoire :** Interface scrollable de suppression sélective de la base vectorielle (`purge_memory_action.py`) avec filtrage par tags, sélection par plage et confirmation.
- **MCP Identity Vault :** Interface d'authentification centralisée (`echo_mcp_identity_action.py`) se synchronisant dynamiquement avec l'API des schémas du MCP Broker pour sauvegarder de manière isolée les credentials métier dans `mcp_vault` (SQLite).
- **Agent Monitor :** Action HUD (`agent_monitor_action.py`) offrant une vue arborescente des agents (agents, experts, conseils, superviseurs, **navigateur web avec analyse DOM**) en temps réel via lecture SQLite.
- **Réinitialisation Auth :** Purge des tokens Google OAuth2 de l'Espace Personnel (`reset_auth_action.py`).
- **ECHO Codex (`echo_codex_action.py`) :** HUD Monaco Editor draggable avec file tree, mini-chat AI (quick actions), diff view (accept/reject), import/export PC, navigation historique Git (◀ ▶ avec mode read-only), restauration de version.
- **Resume in New Chat (`resume_in_new_chat_action.py`) :** Migration complète du contexte saturé vers une nouvelle session distillée avec duplication du FS Vault, mutation SQLite (`session.db`) et clonage Qdrant.

### 7. Infrastructure d'Exécution
- **Python Worker (`/opt/ECHO/docker-python-worker/`) :** API Flask isolée exécutant du code Python en mémoire protégée via isolation système (`multiprocessing` / dossier temporaire). Conçue pour la validation analytique, elle supporte la restitution graphique en Base64 (`pybase64`) et la sérialisation asynchrone ultra-rapide (`orjson`).
- **Browser Worker (`/opt/ECHO/docker-browser-worker/`) :** Instance Playwright (Python 3.14) pilotée par API **FastAPI asynchrone**. Intègre un Watchdog d'Auto-Stop pour libération CDP, un streaming Live Long-Polling pour le screencast, et délègue les tâches CPU-bound (WebP/html2text) aux threads OS natifs. Moteur bridé à 9 FPS avec sérialisation asynchrone (`ORJSONResponse`) pour des performances optimales.
- **Embedding Worker (`/opt/ECHO/docker-embedding-worker/`) :** Architecture hybride. Offload prioritaire de l'inférence microsoft/Harrier-OSS-v1-0.6B vers le navigateur client via **Edge Computing (WebGPU)**. Fallback transparent sur le conteneur Docker via **llama.cpp (GGUF)** optimisé CPU pour réduire massivement l'empreinte RAM (suppression de PyTorch).
- **Download Broker (`/opt/ECHO/docker-download-broker/`) :** Service autonome gérant l'ingestion asynchrone des téléchargements Web. Assure le Garbage Collection et le déplacement atomique vers le Vault utilisateur avec sérialisation SQLite (`PENDING_INGESTION`).
- **MCP Broker (`/opt/ECHO/docker-mcp-broker/`) :** Serveur natif Model Context Protocol (`FastMCP` via Uvicorn). Expose via ASGI StreamableHTTP des outils dynamiques externes (Omnisearch Jobs, Corporate Sirene/Bodacc, Academic). Intègre un middleware pur ASGI interceptant silencieusement le `x-openwebui-user-id` pour sécuriser l'exécution contextuelle.

### 8. Orchestration Séquentielle (Docker Compose)
Démarrage ordonné via `healthcheck` + `depends_on: condition: service_healthy`. Standardisation stricte des hostnames internes avec le préfixe `echo-` (ex: `echo-python-worker`, `echo-browser-worker`, `echo-searxng`) :
- **Tier 1 (Fondations)** : Qdrant, SearXNG, Watchtower — démarrent en parallèle.
- **Tier 2 (Workers)** : Embedding (après Qdrant), Python Worker, Browser Worker, MCP Broker.
- **Tier 3** : Open WebUI — attend Qdrant + Embedding + SearXNG.
- **Tier 4** : Admin Manager — attend Open WebUI (dernier).
- **Ports internes** : Qdrant (6333), Embedding (7997) ne sont **pas** exposés sur la VM. Accès uniquement via le réseau Docker `echo-network`.
- **Ports exposés** : Open WebUI (3000), Admin Manager (3001), SSH Tunnel (8020-8024).

## 🔢 Stratégie de Versioning (`VERSIONING.md`)

### A. Version de la Stack (Globale)
- **Format :** `5.Y.Z` (SemVer) dans le fichier `VERSION`.
- **Incrément :** `Patch (Z)` pour les scripts/configs, `Mineur (Y)` pour les nouveaux services ou fonctionnalités clés.
- **Manifeste :** Les changements de version des composants peuvent être reflétés parfois à titre d'exemple dans le Manifeste de Déploiement lors d'une release de la Stack, en maintenant le nombre d'exemple à 3 au maximum.

### B. Versioning des Composants (Granulaire)
- **Pipe Engine :** Format `XXX.YY` (ex: `190.5`).
- **Admin Manager :** Format `X.Y` (ex: `5.52`).
- **Modules & Tools :** Numérotation simple `X.Y`.

## 🔐 Authentification Antigravity 2.1

- **Multi-Provider :** Résolution par priorité OAuth2 > Clé Primaire > Clé Secondaire via `EchoAuth.get_ordered_auth_providers()`.
- **OAuth2 PKCE :** Flow Authorization Code + PKCE (RFC 7636) via tunnel SSH éphémère (`echo_ssh_tunnel.py` + `echo_pkce_server.py`). Ports 8020-8024. Refresh token automatique (TTL 55min).
- **Protocole Symétrique :** `echo_protocol.py` traduit les modèles AI Studio ↔ Code Assist. Le payload est encapsulé selon le backend (AI Studio : API Key / Antigravity : Bearer + project).

## 🛠️ Standards de Développement

- **Éthique Open Source :** Tout composant ou bibliothèque tiers intégré à ECHO DOIT obligatoirement être crédité avec sa licence dans le fichier `CREDITS.md` situé à la racine.
- **Async-First :** Utilisation impérative d'`asyncio` et `httpx`.
- **Persistence :** `EchoStateManager` (SQLite) pour la persistance par utilisateur (`identity.db`) et par chat (`{chat_id}.db`).
- **UI HUD (EchoRichUI) :** Moteur de rendu modulaire centralisé dans `echo_ui.py`. Standardise la génération des composants (Drag/Drop, Resizable HUD), la gestion événementielle universelle (`events.emit`), et inclut des mécanismes robustes (heartbeat, synchronisation automatique de thème, logique de rendu HUD mobile, état persistant et fallback automatique).
- **API Resilience :** `EchoGeminiClient` gère le multi-provider, le basculement sur erreur 429/500 et le backoff exponentiel.
- **Politique Modèle Centralisée :** `call_cascade()` dans `echo_utils.py` gère le clamping (politique Pipe via UserValve `MODEL_SELECTION`), l'injection `thinkingConfig` automatique, la cascade descendante PRO→FLASH→LITE et la signalisation (🔒 clamping, ⚡ cascade, ❌ épuisement). `wrap_cascade_output()` rend le modèle effectif visible au LLM orchestrateur.
- **Règle d'Énonciation :** Le Kernel statique et les docstrings des outils doivent impérativement adopter un ton impersonnel (ex: "Le Modèle doit", "Permet au Modèle de"). L'utilisation de la 2ème personne ("Tu es...", "Tu DOIS") est STRICTEMENT réservée aux prompts internes (`system_prompt`) destinés aux sous-agents pour définir leur persona.
- **OWUI Injection & PEP8 :** L'intégralité des outils de l'Arsenal doit strictement déclarer les arguments `__user__` et `__metadata__` dans leur interface pour garantir l'injection native du contexte par Open WebUI, tout en observant un code exempt de variables/imports inutilisés (norme PEP8).
- **OWUI Tool Multiparts :** Les outils générant ou retournant des médias doivent encapsuler les fichiers dans la réponse via le mot-clé standardisé `echo_tool_multiparts` (remplaçant `nouveaux_fichiers`) pour assurer le rendu multimodal natif.

## 📚 Documentation Technique

- **Documentation Statique (`docs/`) :** Une suite complète de documentation HTML (Fondations, Architecture, Outils, Administration) générée et accessible hors-ligne pour garantir l'autonomie et faciliter la maintenance.

## ⚠️ Zones d'Exclusion & Sécurité

- **`.tmp_audit` :** Strictement réservé à l'analyse de code tiers. Ne doit jamais être inclus dans les déploiements ou la logique métier.
- **Auto-Hébergement :** Les données sensibles (clés API dans l'Espace Personnel) ne sortent jamais de l'infrastructure Docker.

---
*Document de référence pour l'agent ECHO - Version de Stack Actuelle : 5.192.28*
