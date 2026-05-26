# 🧠 ECHO Framework (GEMINI.md) - Version 5.160.4

ECHO (Espace Cognitif Heuristique Opérationnel) est un framework d'orchestration d'intelligence auto-hébergée de grade industriel, conçu comme un Kernel de contrôle pour Open WebUI. Optimisé pour la famille Gemini (Google AI Studio), il garantit la confidentialité, l'autonomie et la persistance cognitive.

## 💻 Environnement de Développement & Déploiement

- **OS Hôte :** Windows 11 Pro.
- **IDE :** VS Code.
- **Terminal :** PowerShell (Admin requis pour Hyper-V).
- **Infrastructure Cible :** Machine Virtuelle Ubuntu 24.04 sur Hyper-V.
- **Orchestration :** Docker Compose (Standardisé version 7.7+).
- **Note Critique :** L'agent opère depuis l'hôte Windows. L'interaction avec la cible se fait via `deploy-hyperv.ps1` (injection ZIP via disque Seed) et `/opt/ECHO/echo-scripts/sync-echo.sh`.

## 🏗️ Architecture V5 : Le Triptyque Fondamental

L'architecture repose sur trois piliers fondamentaux (Auto-Hébergement, Véracité, Autonomie) articulés autour du Kernel ECHO situé dans `/opt/ECHO` :

### 1. Le Cortex (`/opt/ECHO/owui-pipes/pipe_engine.py`)
- **Suture Bit-Perfect des Métadonnées Gemini :** Reconstruction de l'historique via SQLite (`message_shadows`, table conservée pour compatibilité production) pour une continuité absolue. Garantit une reprise de session identique au bit près via l'ID de message et le timestamp (Verrou de Version). Le suivi de la branche active et de l'état de la session est garanti par un calcul de hash cumulatif (Cumulative Hash) via `EchoStateManager`.
- **Ajustement du niveau cognitif :** Routage dynamique intelligent (LITE -> FLASH -> PRO).
- **Délégation Cognitive :** Utilisation de l'outil `new_cognitive_level` pour déléguer les tâches complexes au modèle PRO lors de la traversée de la "Vallée de la Mort Contextuelle" (saturation contextuelle > 50%).
- **Expertise Conseil (`/opt/ECHO/owui-tools/cognitive_agents.py`) :** Orchestration d'agents spécialisés via une délégation cognitive récursive sans état. Utilise Gemini 3.1 avec `includeThoughts: False` (conservation uniquement des `thoughtSignatures`) pour une boucle itérative efficiente. Inclut `consult_council` : Table Ronde Multi-Experts (protocole Delphi) avec N participants en tours parallélisés et synthèse finale par modèle dédié.
- **HTTP/2 Stealth Headers :** Utilisation de `httpx` (H2 obligatoire) avec en-têtes de navigation haute fidélité (`get_stealth_headers`) pour simuler un navigateur réel.

### 2. La Conscience (`/opt/ECHO/owui-filters/`)
- **Base vectorielle des souvenirs :** Système RAG vectoriel (Qdrant) avec Distillation Contextuelle automatique.
- **Gestion de l'importance des souvenirs :** Algorithme de fusion sémantique préservant le score `memory_importance` maximal des souvenirs.
- **Smart Context :** Injection de faits via des balises XML structurelles (`<smart_context>`).

### 3. Contexte Proprioceptif : `environnement_contexte`
Le vecteur d'état global `<environnement_contexte>` est un bloc YAML injecté systématiquement par le filtre `new_context_filter.py`.
- **Contenu :** Identité (`modèle_actuel`, `modèle_origine`), grounding géo-temporel, le **Registre Conversationnel des Fichiers** (`registre_fichiers`) et le **Registre des Plans** (`registre_plan`).
- **Règle d'Or :** Le modèle **DOIT** consulter ces registres pour valider l'existence et l'état d'une ressource ou d'un plan avant toute manipulation.

### 4. L'Arsenal (`/opt/ECHO/owui-tools/`)
- **Planification Stratégique :** Construction, modification et gestion de plans d'action via un agent planificateur LLM (`strategic_planner.py`). Cascade cognitive PRO→FLASH→LITE, persistance Markdown dans le Vault, registre SQLite par chat, injection proprioceptive dans `registre_plan`.
- **Visual Intelligence :** Génération d'interfaces dynamiques (Mindmaps, Graphes) via `universal_visual_generator.py` et `echo_visuals.py` (Pattern 'Data Island' pour isoler le JS).
- **Web Intelligence :** Navigation autonome Playwright (`navigation_engine_tool.py`) avec replay interactif.
- **Explorateur de l'Espace Personnel :** Analyse sécurisée et indexation des documents locaux de l'utilisateur.

### 5. Gouvernance & Administration (`/opt/ECHO/docker-admin-manager/`)
- **Régulation :** Monitoring du framework et gestion des sessions.
- **Purge Temporelle des Souvenirs (TTL) :** Centralisation du processus d'élagage de la base vectorielle des souvenirs pour optimiser les performances.

### 6. Actions Interactives (`/opt/ECHO/owui-actions/`)
- **Cockpit de Rejeu :** Interface de contrôle pour la navigation web (`web_navigation_replay_action.py`).
- **Maintenance :** Outils de purge de mémoire et de réinitialisation d'authentification.

### 7. Infrastructure d'Exécution
- **Python Worker (`/opt/ECHO/docker-python-worker/`) :** Exécution isolée de code Python avec support `orjson`/`pybase64`.
- **Browser Agent (`/opt/ECHO/docker-browser-agent/`) :** Instance Playwright pilotée par API pour la navigation autonome.
- **Embedding Worker (`/opt/ECHO/docker-embedding-worker/`) :** Inférence SigLIP-2 locale.
## 🔢 Stratégie de Versioning (`VERSIONING.md`)

### A. Version de la Stack (Globale)
- **Format :** `5.Y.Z` (SemVer) dans le fichier `VERSION`.
- **Incrément :** `Patch (Z)` pour les scripts/configs, `Mineur (Y)` pour les nouveaux services ou fonctionnalités clés.
- **Manifeste :** Les changements de version des composants peuvent être reflétés parfois à titre d'exemple dans le Manifeste de Déploiement lors d'une release de la Stack, en maintenant le nombre d'exemple à 3 au maximum.

### B. Versioning des Composants (Granulaire)
- **Pipe Engine :** Format `XXX.YY` (ex: `190.5`).
- **Admin Manager :** Format `X.Y` (ex: `5.52`).
- **Modules & Tools :** Numérotation simple `X.Y`.

## 🛠️ Standards de Développement

- **Async-First :** Utilisation impérative d'`asyncio` et `httpx`.
- **Persistence :** `EchoStateManager` (SQLite) pour la persistance par utilisateur (`identity.db`) et par chat (`{chat_id}.db`).
- **UI HUD :** Toutes les interactions visuelles (Jauges de contexte, Status) passent par `echo_ui.py`.
- **API Resilience :** `EchoGeminiClient` gère le multi-key, le basculement sur erreur 429/500 et le backoff exponentiel.

## ⚠️ Zones d'Exclusion & Sécurité

- **`.tmp_audit` :** Strictement réservé à l'analyse de code tiers. Ne doit jamais être inclus dans les déploiements ou la logique métier.
- **Auto-Hébergement :** Les données sensibles (clés API dans l'Espace Personnel) ne sortent jamais de l'infrastructure Docker.

---
*Document de référence pour l'agent ECHO - Version de Stack Actuelle : 5.160.4*


