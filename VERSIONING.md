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

**Exemple pour la 5.107.0 (ECHO WebPlayer v6.1 - Light Visualization) :**

{
  "stack_version": "5.107.0",
  "release_date": "2026-04-09",
  "description": "Optimisation du ECHO WebPlayer v6.1 : Transition vers un mode de visualisation automatique unifié, simplification du moteur JS pour plus de fluidité et retrait du handover complexe IA/Humain.",
  "components": {
    "echo_utils": "2.81",
    "navigation_engine_tool": "7.2",
    "web_navigation_replay_action": "3.6"
  }
}

**Exemple pour la 5.106.0 (ECHO WebPlayer & Co-pilot Edition) :**

{
  "stack_version": "5.106.0",
  "release_date": "2026-04-08",
  "description": "Introduction du ECHO WebPlayer v6.0 : Cockpit de co-pilotage hybride avec Handover IA/Humain, synchronisation mathématique des pixels/hitboxes, Smart Input Proxy et Dead Man's Switch de sécurité.",
  "components": {
    "echo_utils": "2.80",
    "navigation_engine_tool": "7.0",
    "web_navigation_replay_action": "3.6"
  }
}

**Exemple pour la 5.105.2 (Premium Viewer Unification) :**

{
  "stack_version": "5.105.2",
  "release_date": "2026-04-07",
  "description": "Unification UX du Premium Viewer (HUD + Fichiers) avec moteur de Zoom/Pan, Aide interactive intégrée, Fallback CORS et fix de l'extraction multi-canvas.",
  "components": {
    "echo_utils": "2.72",
    "file_content_explorer": "5.105.1",
    "gemini_maps_grounding": "12.51"
  }
}

**Exemple pour la 5.105.1 (Premium Viewer Refactoring & Context Fix) :**

{
  "stack_version": "5.105.1",
  "release_date": "2026-04-07",
  "description": "Fix strict sur le retour du contexte pour le Rich UI (string au lieu de dictionnaire) et introduction complète du Premium Viewer avec zoom interactif et pan.",
  "components": {
    "echo_utils": "2.71",
    "file_content_explorer": "5.105.1",
    "gemini_maps_grounding": "12.51"
  }
}

**Exemple pour la 5.105.0 (Stealth Downloader & Premium Visualizer) :**

{
  "stack_version": "5.105.0",
  "release_date": "2026-04-06",
  "description": "Renforcement massif du Stealth Engine (Anti-403 Wikimedia), unification du viewer d'images (Local/Remote) et outils de précision Rich UI (Loupe native, Sélecteur de zone interactif).",
  "components": {
    "echo_utils": "2.70",
    "file_content_explorer": "5.105.0"
  }
}

**Exemple pour la 5.103.0 (Rich UI Suture & Sovereign Maps) :**

{
  "stack_version": "5.103.0",
  "release_date": "2026-04-06",
  "description": "Introduction du Framework Rich UI : Visualisation d'images interactive (Base64) et intégration de cartes OpenStreetMap via Leaflet.js pour une souveraineté totale.",
  "components": {
    "echo_utils": "2.50",
    "file_content_explorer": "5.103.0",
    "gemini_maps_grounding": "12.50"
  }
}

**Exemple pour la 5.102.0 (Dynamic Cognitive Delegation & Memory Filter Documentation) :**

{
  "stack_version": "5.102.0",
  "release_date": "2026-04-06",
  "description": "Refonte du Cognitive Core : Fusion des outils de raisonnement en 'delegate_reasoning' avec routage dynamique de modèle (LITE, FLASH, PRO) et support des System Instructions. Mise à jour critique de la documentation sur la cascade cognitive et la mémoire organique.",
  "components": {
    "cognitive_core": "3.50"
  }
}

**Exemple pour la 5.101.0 (Memory Governance Update) :**

{
  "stack_version": "5.101.0",
  "release_date": "2026-04-06",
  "description": "Introduction du 'Droit à l'oubli' (Purge Action) et réorganisation des priorités UI des filtres et actions.",
  "components": {
    "new_context_filter": "6.63",
    "web_navigation_replay_action": "3.5",
    "purge_memory_action": "1.0"
  }
}

**Exemple pour la 5.100.0 (Organic Memory V2 & Cognitive Suture) :**

{
  "stack_version": "5.100.0",
  "release_date": "2026-04-06",
  "description": "Suture Cognitive V2 : Distillation Flash 2.5, Fusion Sémantique et Auto-Pruning via Qdrant (gemini-embedding-2-preview).",
  "components": {
    "echo_constants": "1.22",
    "conversation_memory_filter": "2.0",
    "memory_organic_tool": "2.0"
  }
}

**Exemple pour la 5.99.7 (Pydantic Strict Fix - Multimodal Suture) :**

{
  "stack_version": "5.99.7",
  "release_date": "2026-04-06",
  "description": "Fix strict Pydantic binding pour les valves multimodales (Clean architecture).",
  "components": {
    "file_content_explorer": "5.99.1"
  }
}

**Exemple pour la 5.99.6 (Native Multimodal Suture) :**

{
  "stack_version": "5.99.6",
  "release_date": "2026-04-06",
  "description": "Native Multimodal Suture (Injection Video/Audio/Image via file_content_explorer)",
  "components": {
    "file_content_explorer": "5.99",
    "pipe_engine": "180.2"
  }
}

**Exemple pour la 5.99.2 (Unification UI & Registre Cognitif Statique) :**

{
  "stack_version": "5.99.2",
  "release_date": "2026-04-05",
  "description": "Unification Totale (MODEL_PRO, AUTO) et stabilisation statique du Registre Cognitif",
  "components": {
    "pipe_engine": "180.2",
    "echo_constants": "1.21"
  }
}

**Exemple pour la 5.94.0 (Dual-Key Resilience & Factorized API Client) :**

{
  "stack_version": "5.94.0",
  "release_date": "2026-03-31",
  "description": "Dual-Key Resilience & Factorized API Client (Multi-Key Fallback + Centralized Gemini Engine)",
  "components": {
    "pipe_engine": "170.0",
    "echo_constants": "1.10",
    "echo_utils": "2.32",
    "echo_auth": "1.4",
    "new_context_filter": "6.44",
    "cognitive_core": "3.15",
    "file_content_explorer": "5.84",
    "gemini_maps_grounding": "12.31",
    "memory_organic_tool": "1.3",
    "memory_search": "2.0",
    "web_navigation_replay_action": "3.4"
  }
}

**Exemple pour la 5.93.0 (Dynamic Model Routing) :**

{
  "stack_version": "5.93.0",
  "release_date": "2026-03-30",
  "description": "Dynamic Model Routing (Juge IA Gemma 3 & Heuristiques Cognitives)",
  "components": {
    "pipe_engine": "169.0",
    "echo_utils": "2.31",
    "echo_constants": "1.9"
  }
}

**Exemple pour la 5.78.0 (Bit-Perfect Suture Update) :**

{
  "stack_version": "5.78.0",
  "release_date": "2026-03-23",
  "description": "Bit-Perfect Suture Update (ID-Anchored Shadowing + Strict Gemini Signatures)",
  "components": {
    "pipe_engine": "168.0",
    "new_context_filter": "6.30",
    "echo_utils": "2.20"
  }
}

**Exemple pour la 5.75.0 (Smart Memory & Organic RAG Update) :**

{
  "stack_version": "5.75.0",
  "release_date": "2026-03-20",
  "description": "Smart Memory & Organic RAG Update (Gemini Flash Distillation + Qdrant Integration)",
  "components": {
    "pipe_engine": "136.0",
    "conversation_memory_filter": "1.1",
    "memory_organic_tool": "1.0",
    "open_webui": "main"
  }
}

**Exemple pour la 5.6.0 (Turbo Performance Update) :**

{ "stack_version": "5.6.0", "release_date": "2026-01-15", "description": "Turbo Performance Update (orjson injection + Streaming infra)", "components": { "pipe_engine": "136.0", "admin_manager": "2.6", "python_worker": "1.0", "browser_agent": "1.0", "open_webui": "main" } }


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