# 🧠 ECHO Framework (GEMINI.md) - Version 5.140.29

ECHO (Espace Cognitif Heuristique Opérationnel) est un framework d'orchestration d'intelligence souveraine de grade industriel, conçu comme un noyau de contrôle (Kernel) pour Open WebUI. Optimisé pour la famille Gemini (Google AI Studio), il implémente une architecture de "Citadelle" garantissant la confidentialité, l'autonomie et la persistance cognitive.

## 💻 Environnement de Développement & Déploiement

- **OS Hôte :** Windows 11 Pro.
- **IDE :** VS Code.
- **Terminal :** PowerShell (Admin requis pour Hyper-V).
- **Infrastructure Cible :** Machine Virtuelle Ubuntu 24.04 sur Hyper-V.
- **Orchestration :** Docker Compose (Standardisé version 7.7+).
- **Note Critique :** L'agent opère depuis l'hôte Windows. L'interaction avec la cible se fait via `deploy-hyperv.ps1` (injection ZIP Base64 via Cloud-Init) et `00-echo-scripts/sync-echo.sh`.

## 🏗️ Architecture V5 : Le Triptyque Souverain

L'architecture repose sur trois piliers fondamentaux (Souveraineté, Véracité, Autonomie) articulés autour du Kernel ECHO :

### 1. Le Cortex (`10-owui-pipes/pipe_engine.py`)
- **Suture Bit-Perfect :** Restauration d'historique via SQLite (`message_shadows`) pour une continuité sémantique absolue. Garantit une reprise de session identique au bit près via l'ID de message et le timestamp (Verrou de Version).
- **Cascade Cognitive :** Routage dynamique intelligent (LITE -> FLASH -> PRO).
- **Relais Dynamiques :** Utilisation de l'outil `new_cognitive_level` pour déléguer les tâches complexes au modèle PRO lors de la traversée de la "Vallée de la Mort" (saturation contextuelle > 50%).
- **HTTP/2 Stealth :** Utilisation de `httpx` (H2 obligatoire) avec en-têtes de navigation haute fidélité (`get_stealth_headers`) pour simuler un navigateur réel.

### 2. La Conscience (`11-owui-filters/`)
- **Mémoire Organique :** Système RAG vectoriel (Qdrant) avec distillation automatique.
- **Anti-Atrophie :** Algorithme de fusion sémantique préservant le score d'importance maximal des souvenirs.
- **Smart Context :** Injection de faits via des balises XML structurelles (`<smart_context>`).

### 3. Proprioception & Frontière : `environnement_contexte`
Le vecteur d'état global `<environnement_contexte>` est un bloc YAML injecté systématiquement par le filtre `new_context_filter.py`.
- **Contenu :** Identité (`modèle_actuel`, `modèle_origine`), grounding géo-temporel, et le **Registre Souverain des Fichiers** (`registre_fichiers`).
- **Règle d'Or :** Le modèle **DOIT** consulter ce registre pour valider l'existence et l'état d'une ressource avant toute manipulation.

### 4. L'Arsenal (`12-owui-tools/`)
- **Visual Intelligence :** Génération d'interfaces dynamiques (Mindmaps, Graphes) via `universal_visual_generator.py` et `echo_visuals.py` (Pattern 'Data Island' pour isoler le JS).
- **Web Intelligence :** Navigation autonome Playwright (`navigation_engine_tool.py`) avec replay interactif.
- **Vault Explorer :** Analyse sécurisée et indexation des documents locaux.

## 🔢 Stratégie de Versioning (`VERSIONING.md`)

### A. Version de la Stack (Globale)
- **Format :** `5.Y.Z` (SemVer) dans le fichier `VERSION`.
- **Incrément :** `Patch (Z)` pour les scripts/configs, `Mineur (Y)` pour les nouveaux services ou fonctionnalités clés.
- **Manifeste :** Les changements de version des composants peuvent être reflétés parfois à titre d'exemple dans le Manifeste de Déploiement lors d'une release de la Stack, en maintenant le nombre d'exemple à 3 au maximum.

### B. Versioning des Composants (Granulaire)
- **Pipe Engine :** Format `XXX.YY` (ex: `180.4`).
- **Modules :** Numérotation simple `X.Y` dans l'en-tête du fichier.

## 🛠️ Standards de Développement

- **Async-First :** Utilisation impérative d'`asyncio` et `httpx`.
- **Persistence :** `EchoStateManager` (SQLite) pour la persistance par utilisateur (`identity.db`) et par chat (`{chat_id}.db`).
- **UI HUD :** Toutes les interactions visuelles (Gauges de contexte, Status) passent par `echo_ui.py`.
- **API Resilience :** `EchoGeminiClient` gère le multi-key, le basculement sur erreur 429/500 et le backoff exponentiel.

## ⚠️ Zones d'Exclusion & Sécurité

- **`.tmp_audit` :** Strictement réservé à l'analyse de code tiers. Ne doit jamais être inclus dans les déploiements ou la logique métier.
- **Souveraineté :** Les données sensibles (clés API dans le Vault) ne sortent jamais de l'infrastructure Docker.

---
*Document de référence pour l'agent ECHO - Version de Stack Actuelle : 5.140.29*


