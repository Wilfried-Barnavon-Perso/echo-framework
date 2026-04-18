# Stratégie de Versioning ECHO 5

Ce document définit les règles de versioning pour l'infrastructure ECHO 5.

## 1. Version de la Stack (Infrastructure Globale)

La version globale d'ECHO suit le format **SemVer** (`X.Y.Z`). Elle représente un état stable et testé de l'ensemble des composants.

* **Format** : `5.Y.Z`

* **Emplacement** : Fichier `VERSION` à la racine du dépôt.

| Type | Incrément | Description | Exemple | 
 | ----- | ----- | ----- | ----- | 
| **Majeur** | `6.0.0` | Refonte architecturale complète (ex: changement d'orchestrateur, abandon de Docker). | 5 -> 6 | 
| **Mineur** | `5.6.0` | Ajout d'un nouveau service (conteneur), changement d'OS de base, nouvelles fonctionnalités clés (**Turbo JSON**). | 5.5 -> 5.6 | 
| **Patch** | `5.6.1` | Correctifs de scripts, mise à jour de configuration, update de dépendances mineures. | 5.6.0 -> 5.6.1 | 

## 2. Versioning des Composants (Modules)

Chaque composant possède son propre cycle de vie et sa propre numérotation, documentée dans son en-tête de fichier.

### A. ECHO Engine (Le Pipe)

* **Format** : `XXX.YY` (Entier.Décimale)

* `XXX` : Version fonctionnelle majeure (ex: 136). Incrémenté lors d'ajouts de logique (nouveau fix, nouvelle gestion de mémoire).

* `YY` : Correctif ou ajustement mineur (ex: 00).

* **Fichier** : `10-owui-pipes/pipe_engine.py`

* **Exemple** : `136.0` (No Upload / Base64 Mode + Turbo JSON).

### B. Admin Manager

* **Format** : `X.Y`

* **Fichier** : `20-docker-admin-manager/server.py`

* **Exemple** : `2.6` (Support UTF-8).

### C. Workers & Tools

* **Format** : `X.Y`

* Suivent une numérotation simple.

## 3. Le Manifeste de Déploiement

Pour garantir la cohérence, chaque Release de la Stack (`5.Y.Z`) est définie par un snapshot précis des versions de composants.

**Exemple pour la 5.121.0 (Omniscience Visuelle - Full Stack) :**

{
  "stack_version": "5.121.0",
  "release_date": "2026-04-17",
  "description": "Atteinte du jalon Omniscience Visuelle : Déploiement intégral des 7 vagues de moteurs de rendu (13 frameworks). Support natif pour la 3D (A-Frame), les processus métiers (BPMN), le génie civil (SVG Pan-Zoom), la biologie (SMILES), l'électronique (WaveDrom) et la topologie de réseaux (Cytoscape). Orchestration agentique par délégation cognitive asynchrone.",
  "components": {
    "universal_visual_generator.py": "2.4",
    "echo_visuals.py": "1.6"
  }
}

**Exemple pour la 5.120.7 (GBAV NameError & Icon Hotfix) :**

{
  "stack_version": "5.120.7",
  "release_date": "2026-04-17",
  "description": "Hotfix UI & Documentation : Restauration de l'icône SVG dans web_navigation_replay_action.py. Correction de l'erreur NameError (GBAVEngine) dans la documentation (01_hld_architecture.html) et echo_ui.py suite au renommage sémantique vers VisualEngine.",
  "components": {
    "web_navigation_replay_action.py": "3.8",
    "echo_ui.py": "1.6",
    "docs/": "v5.120.7"
  }
}

**Exemple pour la 5.120.6 (Visual Rendering Robustness & Data Island) :**

{
  "stack_version": "5.120.6",
  "release_date": "2026-04-17",
  "description": "Fiabilisation critique du rendu visuel : Implémentation du pattern 'Data Island' (<template>) dans echo_visuals.py pour prévenir les erreurs de syntaxe JavaScript (notamment sur Mermaid.js) causées par l'échappement imparfait des payloads générés par l'IA. Simplification sémantique : abandon du sigle GBAV au profit du terme 'Rendu Visuel'. Renforcement de la regex de sanitization dans l'orchestrateur visuel.",
  "components": {
    "universal_visual_generator.py": "2.3",
    "echo_visuals.py": "1.3"
  }
}

**Exemple pour la 5.120.5 (Universal Visual Suture & Map Comfort) :**

{
  "stack_version": "5.120.5",
  "release_date": "2026-04-17",
  "description": "Amélioration majeure de l'ergonomie visuelle : Centralisation de la Suture Visuelle (Auto-resize dynamique) dans le boilerplate universel. Passage de l'Iframe Google Maps en mode Cinéma (85vh) pour un grounding spatial optimal.",
  "components": {
    "echo_ui.py": "1.5"
  }
}

**Exemple pour la 5.120.4 (Agentic Error Handling & GBAV Logic) :**

{
  "stack_version": "5.120.4",
  "release_date": "2026-04-17",
  "description": "Migration vers une gestion agentique des erreurs : Suppression de la cascade automatique de modèles dans GBAV. Enrichissement des DocStrings (GBAV, Maps, Navigation) pour instruire le modèle sur la résilience sémantique et le choix conscient des ressources.",
  "components": {
    "universal_visual_generator.py": "2.2",
    "gemini_maps_grounding.py": "12.54",
    "navigation_engine_tool.py": "7.5"
  }
}

**Exemple pour la 5.120.3 (Maps & Navigation Stability Hotfix) :**

{
  "stack_version": "5.120.3",
  "release_date": "2026-04-17",
  "description": "Double correctif de stabilité : Fix de l'import MODEL_LITE manquant dans Maps Grounding et résolution des NameError critiques dans les f-strings de EchoUI liées à la variable HUD_ID.",
  "components": {
    "gemini_maps_grounding.py": "12.53",
    "echo_ui.py": "1.4"
  }
}

**Exemple pour la 5.120.2 (UI Stability Hotfix) :**

{
  "stack_version": "5.120.2",
  "release_date": "2026-04-17",
  "description": "Hotfix critique UI : Consolidation de la classe EchoUI pour éviter les erreurs d'attributs lors du déploiement de la jauge de contexte. Correction des méthodes statiques et suppression des redondances structurelles.",
  "components": {
    "echo_ui.py": "1.3"
  }
}

**Exemple pour la 5.120.1 (Refactoring Fixes & Docs Cleanup) :**

{
  "stack_version": "5.120.1",
  "release_date": "2026-04-17",
  "description": "Hotfixes post-refactoring : Correction des imports dans navigation_engine_tool.py et web_navigation_replay_action.py. Nettoyage exhaustif des caractères corrompus (UTF-8) dans la documentation technique et mise à jour des manuels HLD et HUD.",
  "components": {
    "navigation_engine_tool.py": "7.4",
    "web_navigation_replay_action.py": "3.7",
    "docs/": "v5.120.1"
  }
}

**Exemple pour la 5.120.0 (GBAV OS Refactoring & Component Decoupling) :**

{
  "stack_version": "5.120.0",
  "release_date": "2026-04-17",
  "description": "Refactoring architectural majeur : Éclatement de echo_utils.py en trois bibliothèques distinctes (Core, UI, GBAV). Migration de EchoRichUI vers echo_ui.py. Centralisation des configurations visuelles dans echo_gbav.py. Nettoyage de l'encodage de la documentation (UTF-8).",
  "components": {
    "echo_utils.py": "3.0",
    "echo_ui.py": "1.1",
    "echo_gbav.py": "1.1",
    "pipe_engine.py": "180.4"
  }
}

**Exemple pour la 5.119.1 (Architectural Documentation Expansion) :**

{
  "stack_version": "5.119.1",
  "release_date": "2026-04-17",
  "description": "Expansion de la documentation technique : Création du manuel de référence pour la Visual Intelligence (GBAV) et mise à jour des documents HLD et System Prompt pour intégrer le triptyque Kernel/AEC/Requête. Alignement sémantique complet du HUD.",
  "components": {
    "docs/": "v5.119.1"
  }
}

**Exemple pour la 5.119.0 (GBAV - Système d'Exploitation Visuel) :**

{
  "stack_version": "5.119.0",
  "release_date": "2026-04-17",
  "description": "Rupture technologique visuelle : Introduction de la Grande Bibliothèque de l'Arsenal Visuel (GBAV). ECHO déploie désormais des interfaces dynamiques et interactives adaptées à l'intention sémantique. Support initial multi-moteurs (Markmap, Mermaid, ECharts) avec thémage unifié et auto-dimensionnement intelligent.",
  "components": {
    "universal_visual_generator.py": "2.0",
    "echo_utils.py": "2.85"
  }
}

**Exemple pour la 5.118.9 (Visual Intelligence Prototype) :**

{
  "stack_version": "5.118.9",
  "release_date": "2026-04-17",
  "description": "Introduction de l'intelligence visuelle : Nouvel outil de génération de Mindmaps interactives via Markmap.js. Utilise une délégation cognitive stateless pour garantir la syntaxe Markdown et un rendu Rich UI (iframe) auto-dimensionné.",
  "components": {
    "visual_schema_generator.py": "1.0",
    "echo_utils.py": "2.83"
  }
}

**Exemple pour la 5.118.8 (Cognitive Synergy & Python Sandbox Hardening) :**

{
  "stack_version": "5.118.8",
  "release_date": "2026-04-17",
  "description": "Intégration tactique des directives PRAF/PRAC dans les DocStrings de l'Arsenal. Sécurisation de l'environnement Python Worker par le retrait des capacités graphiques (matplotlib, seaborn) non supportées en Headless, et ajout de networkx pour le calcul de graphes mathématiques purs.",
  "components": {
    "memory_organic_tool.py": "2.1",
    "sovereign_web_search.py": "1.1",
    "python_code_executor.py": "6.0",
    "stack-echo.yml": "7.8"
  }
}

**Exemple pour la 5.118.5 (Cognitive Tool Renaming) :**

{
  "stack_version": "5.118.5",
  "release_date": "2026-04-17",
  "description": "Standardisation sémantique : Renommage global de l'outil de transfert 'changement_niveau_cognitif' en 'new_cognitive_level' à travers le Pipe Engine, le Kernel (system-prompt) et la documentation technique.",
  "components": {
    "pipe_engine.py": "180.3"
  }
}

**Exemple pour la 5.118.4 (Semantic XML Wrapping) :**

{
  "stack_version": "5.118.4",
  "release_date": "2026-04-17",
  "description": "Amélioration de la clarté sémantique : Remplacement des blocs de code Markdown par des balises XML structurelles pour l'état ECHO (<etat_echo>) et les résumés Smart Context (<smart_context>). Cette modification renforce la frontière cognitive du modèle et facilite le référencement des métadonnées dans le prompt système.",
  "components": {
    "new_context_filter.py": "6.64"
  }
}

**Exemple pour la 5.118.2 (WebPlayer UX Enhancement) :**

{
  "stack_version": "5.118.2",
  "release_date": "2026-04-16",
  "description": "Amélioration ergonomique du WebPlayer : Autorise le glisser-déposer (pan) de l'image de navigation directement via le clic gauche standard, libérant l'utilisateur des raccourcis complexes (Alt+Clic ou Clic Milieu).",
  "components": {
    "echo_utils.py": "2.82"
  }
}

## 4. Workflow de Mise à Jour

1. **Développement (`dev`)** :

   * On travaille sur les composants (ex: Pipe 136.0).

   * On teste sur la VM de dev.

2. **Validation (`tag`)** :

   * Une fois stable, on tague le composant (ex: commit "Pipe 136.0").

3. **Release Stack (`main`)** :

   * On met à jour le fichier `VERSION` (ex: 5.5.2 -> 5.6.0).

   * On met à jour le `ECHO_MANIFEST.json`.

   * On fusionne sur `main` et on crée un tag git `5.6.0`.

*Document de référence pour l'équipe ECHO Architecture.