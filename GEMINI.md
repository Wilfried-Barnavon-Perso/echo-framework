# 🧠 ECHO Framework (GEMINI.md) - Version 5.182.17

ECHO (Espace Cognitif Heuristique Opérationnel) est un framework d'orchestration d'intelligence auto-hébergée de grade industriel, conçu comme un Kernel de contrôle pour Open WebUI. Optimisé pour la famille Gemini (Google AI Studio), il garantit la confidentialité, l'autonomie et la persistance cognitive.

## 💻 Environnement de Développement & Déploiement

- **OS Hôte :** Windows 11 Pro.
- **IDE :** VS Code.
- **Terminal :** PowerShell (Admin requis pour Hyper-V).
- **Infrastructure Cible :** Machine Virtuelle Ubuntu 24.04 sur Hyper-V (Allouée à 8Go RAM et 70Go VHD dynamiques).
- **Orchestration :** Docker Compose (Standardisé version 9.3+ avec WAF ModSecurity granulaire).
- **Note Critique :** L'agent opère depuis l'hôte Windows. L'interaction avec la cible se fait via `install-hyperv.ps1` (injection ZIP via disque Seed), `sync-echo.sh` (distribution), et `upgrade-echo.sh` (mise à niveau majeure de l'infrastructure).

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
- **Orchestration Multi-Agents (`agent_orchestration_tool.py`) :** `consult_council` (Table Ronde Delphi, N experts agentiques avec outils, tours parallélisés) et `consult_supervised_workers` (boucle critique/correction récursive). Gestion des Skills via `forge_skill`/`list_skills`. Conservation des `thoughtSignatures` Gemini 3.x.
- **HTTP/2 Stealth Headers :** Utilisation de `httpx` (H2 obligatoire) avec en-têtes de navigation haute fidélité (`get_stealth_headers`) pour simuler un navigateur réel.

### 2. La Conscience (`/opt/ECHO/owui-filters/`)
- **Base vectorielle des souvenirs :** Système RAG vectoriel (Qdrant) avec Distillation Contextuelle automatique par **fenêtre glissante déterministe** (`WINDOW_SIZE`=5 + `WINDOW_OVERLAP`=2, configurable). Nettoyage des messages (role+content+fichiers) pour optimiser le budget tokens de la distillation Cloud.
- **Gestion de l'importance des souvenirs :** Algorithme de fusion sémantique préservant le score `memory_importance` maximal des souvenirs.
- **Smart Context :** Injection de faits via des balises XML structurelles (`<smart_context>`) et utilisation de `source_id` natifs (au lieu de slugs) pour la Mémoire Vectorisée de Session.
- **Pipeline d'Ingestion Zéro-RAM :** Conversion native des documents Office en Markdown (MarkItDown) et traitement hybride transparent (Mémoire Vectorisée, Codex Git, Fallback SQLite) géré dynamiquement par le filtre.

### 3. Contexte Proprioceptif : `environnement_contexte` & `evenement_systeme`
Le vecteur d'état global (AEC V2) est injecté systématiquement par le filtre `new_context_filter.py`.
- **Contenu Statique (`<environnement_contexte>`) :** Identité (`modèle_actuel`, `modèle_origine`) et grounding géo-temporel.
- **Évènements Système (`<evenement_systeme>`) :** Bloc XML évènementiel notifiant le Modèle des ressources créées au tour courant ou détectées en asynchrone via le Watermark Delta.
- **Règle d'Or :** Le modèle **DOIT** utiliser l'outil `query_registry` pour consulter le Registre Unifié V2 (Codex, Plans, Médias, URLs) et valider l'existence ou l'état d'une ressource avant toute manipulation.

### 4. L'Arsenal (`/opt/ECHO/owui-tools/`)
- **Planification Stratégique :** Construction, modification et gestion de plans d'action via un agent planificateur LLM (`strategic_planner.py`). Cascade cognitive centralisée via `call_cascade()`, persistance Markdown dans le Vault, registre SQLite par chat, injection proprioceptive dans `registre_plan`.
- **Mémoire & RAG (`memory_and_rag_tool.py`) :** Outils explicites de gestion mémoire : `save_memory` (long terme, importance 1→5), `search_memory` (reranking pondéré), `save_session_context` / `search_session_context` (Mémoire Vectorisée de Session), `forget_memory`, `list_memory_topics`.
- **Visual Intelligence :** Génération d'interfaces dynamiques (Mindmaps, Graphes) via `universal_visual_generator.py` et `echo_visuals.py` (Pattern 'Data Island' pour isoler le JS).
- **Web Intelligence :** Navigation autonome Playwright (`navigation_engine_tool.py`) pilotée par **boucle OODA autonome** via une **API à 4 piliers** (A11y, DOM, Inspect, Control). Utilise un mode hybride Lidar/Vision (Vision-On-Demand) et une intégration multimodale (Gemini 3.x).
- **Sovereign Web Search (`sovereign_web_search.py`) :** Outils de recherche souveraine via SearXNG (recherche classique One-Shot) et DuckDuckGo (réponse instantanée factuelle), avec capacité de délégation à un agent de recherche profonde (Deep Research Agent) autonome pour les requêtes complexes multi-tours.
- **Agent Engine (`agent_engine_tool.py`) :** Moteur d'exécution d'un agent unique via `delegate_to_agent` (± Skill via `role_name`). Boucle agentique avec outils, escalade cognitive, budget configurable. Depth=1 (pas de récursion), pas d'écriture RAG.
- **Explorateur de l'Espace Personnel :** Analyse sécurisée et indexation des documents locaux de l'utilisateur.
- **Registre Unifié V2 (`query_registry_tool.py`) :** Outil de consultation de l'état cognitif des ressources centralisées (`FILE_INGESTION_STATUS`) indexées par le système.
- **ECHO Codex (`echo_codex_tool.py`) :** Éditeur multi-langage avec Git intégré (dulwich). 9 fonctions (create, edit, read, search, summarize, list, delete, history). Édition assistée par sub-chat `MODEL_FLASH` via `call_cascade`. Registre `codex_docs` dans SQLite par chat. Distillation Cloud pour résumé technique.

### 5. Gouvernance & Administration (`/opt/ECHO/docker-admin-manager/`)
- **ECHO Auth (SSO & MFA) :** IdP autonome (`/opt/ECHO/24-docker-echo-auth/`) gérant l'authentification forte (TOTP). Couplé à BunkerWeb via Forward Auth (`/api/verify`), l'état des sessions et les bannissements IP sont pilotés depuis l'Admin Manager (Révocations granulaires, Kill-Switch).
- **Dashboard Actif :** Interface interactive (Sidebar asynchrone) de monitoring du cluster Docker, gestion renforcée du SSO (révocation, purge dynamique sécurisée des utilisateurs via garde-fous API) et supervision des ressources système.
- **Sécurité Périmétrique :** Intégration de BunkerWeb (WAF) avec un mécanisme de Kill-Switch de Service Worker (PWA) via surcharge de `version.json` de façon non-destructive (préservation du cookie JWT de session).
- **Régulation & Consolidation :** Optimisation physique SQLite (Vacuum/WAL), gestion des sauvegardes à chaud (incluant les bases IdP) et autosécurité Docker (rotation automatisée des logs pour prévenir la saturation disque).
- **Purge Temporelle des Souvenirs (TTL) :** Centralisation du processus d'élagage de la base vectorielle des souvenirs pour optimiser les performances.
- **Configuration Automatique (Open WebUI) :** Script d'orchestration post-déploiement (`00-echo-scripts/config-owui.sh`) paramétrant dynamiquement l'interface, les modèles et les permissions via API à partir du template statique (`01-config/webui-settings.json`).

### 6. Actions Interactives (`/opt/ECHO/owui-actions/`)
- **Cockpit de Rejeu :** Interface de contrôle pour la navigation web (`web_navigation_replay_action.py`).
- **Print / PDF :** Impression et export PDF de conversations via `print_pdf_action.py`.
- **Purge Mémoire :** Interface scrollable de suppression sélective de la base vectorielle (`purge_memory_action.py`) avec filtrage par tags, sélection par plage et confirmation.
- **Agent Monitor :** Action HUD (`agent_monitor_action.py`) offrant une vue arborescente des agents (agents, experts, conseils, superviseurs, **navigateur web avec analyse DOM**) en temps réel via lecture SQLite.
- **Réinitialisation Auth :** Purge des tokens Google OAuth2 de l'Espace Personnel (`reset_auth_action.py`).
- **ECHO Codex (`echo_codex_action.py`) :** HUD Monaco Editor draggable avec file tree, mini-chat AI (quick actions), diff view (accept/reject), import/export PC, navigation historique Git (◀ ▶ avec mode read-only), restauration de version.

### 7. Infrastructure d'Exécution
- **Python Worker (`/opt/ECHO/docker-python-worker/`) :** Exécution isolée de code Python avec support `orjson`/`pybase64`.
- **Browser Agent (`/opt/ECHO/docker-browser-agent/`) :** Instance Playwright (Python 3.14) pilotée par API (httpx, FastAPI) pour la navigation autonome.
- **Embedding Worker (`/opt/ECHO/docker-embedding-worker/`) :** Inférence BAAI/bge-m3 locale (1024d, multilingue). PyTorch CPU-only. Détection GPU dynamique.

### 8. Orchestration Séquentielle (Docker Compose)
Démarrage ordonné via `healthcheck` + `depends_on: condition: service_healthy` :
- **Tier 1 (Fondations)** : Qdrant, SearXNG, Watchtower — démarrent en parallèle.
- **Tier 2 (Workers)** : Embedding (après Qdrant), Python Worker, Browser Agent.
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
- **UI HUD :** Toutes les interactions visuelles (Jauges de contexte, Status) passent par `echo_ui.py` avec gestion événementielle universelle (`events.emit`) et garde-fous mobiles (`get_mobile_guard_js`).
- **API Resilience :** `EchoGeminiClient` gère le multi-provider, le basculement sur erreur 429/500 et le backoff exponentiel.
- **Politique Modèle Centralisée :** `call_cascade()` dans `echo_utils.py` gère le clamping (politique Pipe via UserValve `MODEL_SELECTION`), l'injection `thinkingConfig` automatique, la cascade descendante PRO→FLASH→LITE et la signalisation (🔒 clamping, ⚡ cascade, ❌ épuisement). `wrap_cascade_output()` rend le modèle effectif visible au LLM orchestrateur.

## 📚 Documentation Technique

- **Documentation Statique (`docs/`) :** Une suite complète de documentation HTML (Fondations, Architecture, Outils, Administration) générée et accessible hors-ligne pour garantir l'autonomie et faciliter la maintenance.

## ⚠️ Zones d'Exclusion & Sécurité

- **`.tmp_audit` :** Strictement réservé à l'analyse de code tiers. Ne doit jamais être inclus dans les déploiements ou la logique métier.
- **Auto-Hébergement :** Les données sensibles (clés API dans l'Espace Personnel) ne sortent jamais de l'infrastructure Docker.

---
*Document de référence pour l'agent ECHO - Version de Stack Actuelle : 5.182.17*


