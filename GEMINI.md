# 🧠 ECHO Framework (GEMINI.md) - Version 5.171.0

ECHO (Espace Cognitif Heuristique Opérationnel) est un framework d'orchestration d'intelligence auto-hébergée de grade industriel, conçu comme un Kernel de contrôle pour Open WebUI. Optimisé pour la famille Gemini (Google AI Studio), il garantit la confidentialité, l'autonomie et la persistance cognitive.

## 💻 Environnement de Développement & Déploiement

- **OS Hôte :** Windows 11 Pro.
- **IDE :** VS Code.
- **Terminal :** PowerShell (Admin requis pour Hyper-V).
- **Infrastructure Cible :** Machine Virtuelle Ubuntu 24.04 sur Hyper-V.
- **Orchestration :** Docker Compose (Standardisé version 9.3+ avec WAF ModSecurity granulaire).
- **Note Critique :** L'agent opère depuis l'hôte Windows. L'interaction avec la cible se fait via `deploy-hyperv.ps1` (injection ZIP via disque Seed), `sync-echo.sh` (distribution), et `upgrade-echo.sh` (mise à niveau majeure de l'infrastructure).

## 🏗️ Architecture V5 : Le Triptyque Fondamental

L'architecture repose sur trois piliers fondamentaux (Auto-Hébergement, Véracité, Autonomie) articulés autour du Kernel ECHO situé dans `/opt/ECHO` :

### 1. Le Cortex (`/opt/ECHO/owui-pipes/pipe_engine.py`)
- **Suture Bit-Perfect des Métadonnées Gemini :** Reconstruction de l'historique via SQLite (`message_shadows`, table conservée pour compatibilité production) pour une continuité absolue. Garantit une reprise de session identique au bit près via l'ID de message et le timestamp (Verrou de Version). Le suivi de la branche active et de l'état de la session est garanti par un calcul de hash cumulatif (Cumulative Hash) via `EchoStateManager`.
- **Ajustement du niveau cognitif :** Routage dynamique intelligent (LITE -> FLASH -> PRO).
- **Délégation Cognitive :** Utilisation de l'outil `new_cognitive_level` pour déléguer les tâches complexes au modèle PRO lors de la traversée de la "Vallée de la Mort Contextuelle" (saturation contextuelle > 50%).
- **Expertise Conseil (`/opt/ECHO/owui-tools/cognitive_agents.py`) :** Orchestration d'agents spécialisés via une délégation cognitive récursive sans état. Utilise Gemini 3.1 avec `includeThoughts: False` (conservation uniquement des `thoughtSignatures`) pour une boucle itérative efficiente. Inclut `consult_council` : Table Ronde Multi-Experts (protocole Delphi) avec N participants en tours parallélisés et synthèse finale par modèle dédié.
- **HTTP/2 Stealth Headers :** Utilisation de `httpx` (H2 obligatoire) avec en-têtes de navigation haute fidélité (`get_stealth_headers`) pour simuler un navigateur réel.

### 2. La Conscience (`/opt/ECHO/owui-filters/`)
- **Base vectorielle des souvenirs :** Système RAG vectoriel (Qdrant) avec Distillation Contextuelle automatique par **fenêtre glissante déterministe** (`WINDOW_SIZE`=5 + `WINDOW_OVERLAP`=2, configurable). Nettoyage des messages (role+content+fichiers) pour optimiser le budget tokens de la distillation Cloud.
- **Gestion de l'importance des souvenirs :** Algorithme de fusion sémantique préservant le score `memory_importance` maximal des souvenirs.
- **Smart Context :** Injection de faits via des balises XML structurelles (`<smart_context>`) et utilisation de `source_id` natifs (au lieu de slugs) pour le RAG éphémère.

### 3. Contexte Proprioceptif : `environnement_contexte`
Le vecteur d'état global `<environnement_contexte>` est un bloc YAML injecté systématiquement par le filtre `new_context_filter.py`.
- **Contenu :** Identité (`modèle_actuel`, `modèle_origine`), grounding géo-temporel, le **Registre Conversationnel des Fichiers** (`registre_fichiers`), le **Registre des Plans** (`registre_plan`) et le **Registre Codex** (`registre_codex`).
- **Règle d'Or :** Le modèle **DOIT** consulter ces registres pour valider l'existence et l'état d'une ressource, d'un plan ou d'un fichier Codex avant toute manipulation.

### 4. L'Arsenal (`/opt/ECHO/owui-tools/`)
- **Planification Stratégique :** Construction, modification et gestion de plans d'action via un agent planificateur LLM (`strategic_planner.py`). Cascade cognitive centralisée via `call_cascade()`, persistance Markdown dans le Vault, registre SQLite par chat, injection proprioceptive dans `registre_plan`.
- **Mémoire & RAG (`memory_and_rag_tool.py`) :** Outils explicites de gestion mémoire : `save_memory` (long terme, importance 1→5), `search_memory` (reranking pondéré), `save_session_context` / `search_session_context` (RAG éphémère par session), `forget_memory`, `list_memory_topics`.
- **Visual Intelligence :** Génération d'interfaces dynamiques (Mindmaps, Graphes) via `universal_visual_generator.py` et `echo_visuals.py` (Pattern 'Data Island' pour isoler le JS).
- **Web Intelligence :** Navigation autonome Playwright (`navigation_engine_tool.py`) avec distillation de page (`distill_page`) et indexation RAG éphémère automatique.
- **Delegate Sub-Agent (`delegate_tool.py`) :** Délégation asynchrone sécurisée. Contraintes pour l'agent de codage : pas de récursion (`depth=1`), pas d'écriture RAG (`save_memory` interdit), et appendice système injecté dynamiquement.
- **Explorateur de l'Espace Personnel :** Analyse sécurisée et indexation des documents locaux de l'utilisateur.
- **ECHO Codex (`echo_codex_tool.py`) :** Éditeur multi-langage avec Git intégré (dulwich). 9 fonctions (create, edit, read, search, summarize, list, delete, history). Édition assistée par sub-chat `MODEL_FLASH` via `call_cascade`. Registre `codex_docs` dans SQLite par chat. Distillation Cloud pour résumé technique.

### 5. Gouvernance & Administration (`/opt/ECHO/docker-admin-manager/`)
- **Dashboard Gridstack :** Interface interactive de monitoring du cluster Docker et des ressources système.
- **Régulation & Consolidation :** Optimisation physique SQLite (Vacuum/WAL) et gestion des sauvegardes à chaud.
- **Purge Temporelle des Souvenirs (TTL) :** Centralisation du processus d'élagage de la base vectorielle des souvenirs pour optimiser les performances.

### 6. Actions Interactives (`/opt/ECHO/owui-actions/`)
- **Cockpit de Rejeu :** Interface de contrôle pour la navigation web (`web_navigation_replay_action.py`).
- **Export PDF :** Export de conversations en PDF via `export_pdf_action.py`.
- **Purge Mémoire :** Interface scrollable de suppression sélective de la base vectorielle (`purge_memory_action.py`) avec filtrage par tags, sélection par plage et confirmation.
- **Sub-Agent Monitor :** Action HUD (`subagent_monitor_action.py`) offrant une vue arborescente des threads cognitifs en temps réel via lecture SQLite.
- **Réinitialisation Auth :** Purge des tokens Google OAuth2 de l'Espace Personnel (`reset_auth_action.py`).
- **ECHO Codex (`echo_codex_action.py`) :** HUD Monaco Editor draggable avec file tree, mini-chat AI (quick actions), diff view (accept/reject), import/export PC, navigation historique Git (◀ ▶ avec mode read-only), restauration de version.

### 7. Infrastructure d'Exécution
- **Python Worker (`/opt/ECHO/docker-python-worker/`) :** Exécution isolée de code Python avec support `orjson`/`pybase64`.
- **Browser Agent (`/opt/ECHO/docker-browser-agent/`) :** Instance Playwright pilotée par API pour la navigation autonome.
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

- **Async-First :** Utilisation impérative d'`asyncio` et `httpx`.
- **Persistence :** `EchoStateManager` (SQLite) pour la persistance par utilisateur (`identity.db`) et par chat (`{chat_id}.db`).
- **UI HUD :** Toutes les interactions visuelles (Jauges de contexte, Status) passent par `echo_ui.py`.
- **API Resilience :** `EchoGeminiClient` gère le multi-provider, le basculement sur erreur 429/500 et le backoff exponentiel.
- **Politique Modèle Centralisée :** `call_cascade()` dans `echo_utils.py` gère le clamping (politique Pipe via UserValve `MODEL_SELECTION`), l'injection `thinkingConfig` automatique, la cascade descendante PRO→FLASH→LITE et la signalisation (🔒 clamping, ⚡ cascade, ❌ épuisement). `wrap_cascade_output()` rend le modèle effectif visible au LLM orchestrateur.

## ⚠️ Zones d'Exclusion & Sécurité

- **`.tmp_audit` :** Strictement réservé à l'analyse de code tiers. Ne doit jamais être inclus dans les déploiements ou la logique métier.
- **Auto-Hébergement :** Les données sensibles (clés API dans l'Espace Personnel) ne sortent jamais de l'infrastructure Docker.

---
*Document de référence pour l'agent ECHO - Version de Stack Actuelle : 5.171.0*


