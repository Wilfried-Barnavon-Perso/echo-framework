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

**Exemple pour la 5.115.1 (UI TTL Integration) :**

{
  "stack_version": "5.115.1",
  "release_date": "2026-04-12",
  "description": "Amélioration UX et Souveraineté : Ajout de 5 champs de configuration dans le Dashboard de l'Admin Manager permettant à l'administrateur de définir librement les durées de rétention (en jours) pour chaque niveau d'importance (Trivial à Axiome) de la mémoire organique.",
  "components": {
    "server.py": "5.51"
  }
}

**Exemple pour la 5.115.0 (Centralized Organic Decay) :**

{
  "stack_version": "5.115.0",
  "release_date": "2026-04-12",
  "description": "Optimisation des performances : Centralisation de la logique de vieillissement (TTL Decay) de la mémoire organique dans la tâche de fond de l'Admin Manager. Allège significativement le temps de réponse du filtre lors des conversations.",
  "components": {
    "server.py": "5.50",
    "conversation_memory_filter": "2.2"
  }
}

**Exemple pour la 5.116.3 (Advanced Memory Sorting) :**

{
  "stack_version": "5.116.3",
  "release_date": "2026-04-12",
  "description": "Amélioration majeure de l'ergonomie de purge : Les catégories (tags) sont désormais agrégées avec leur niveau d'importance et leur fréquence. Le tri hiérarchique (Importance ASC, Liaisons DESC, Alpha ASC) permet d'identifier et de purger prioritairement les souvenirs les plus futiles ou les plus encombrants.",
  "components": {
    "purge_memory_action": "2.7"
  }
}

**Exemple pour la 5.116.2 (Smart Range Selection) :**

{
  "stack_version": "5.116.2",
  "release_date": "2026-04-12",
  "description": "Amélioration de l'ergonomie de l'action de purge : Le parseur de sélection supporte désormais les plages de numéros (ex: 1-5, 8, 17-20), facilitant grandement la gestion de gros volumes de catégories mémorielles.",
  "components": {
    "purge_memory_action": "2.6"
  }
}

**Exemple pour la 5.116.1 (Semantic TTL Labels) :**

{
  "stack_version": "5.116.1",
  "release_date": "2026-04-12",
  "description": "Amélioration UX du Dashboard Admin : Ajout de libellés sémantiques explicites (Trivial, Mineur, Utile, Majeur, Axiome) au-dessus des champs de configuration TTL pour faciliter la gestion des durées de rétention mémorielle.",
  "components": {
    "server.py": "5.52"
  }
}

**Exemple pour la 5.116.0 (Discriminant Memory Tags) :**

{
  "stack_version": "5.116.0",
  "release_date": "2026-04-12",
  "description": "Amélioration de la fiabilité mémorielle : Refonte du prompt de distillation pour forcer la génération de Tags discriminants et interdire les termes génériques. Cette modification garantit l'efficacité du 'Droit à l'oubli' en évitant les collisions sémantiques lors des purges par catégorie.",
  "components": {
    "conversation_memory_filter": "2.3"
  }
}

**Exemple pour la 5.115.2 (Memory Purge Dry Run) :**

{
  "stack_version": "5.115.2",
  "release_date": "2026-04-12",
  "description": "Amélioration de la purge mémoire : Retour à la sélection par Tags (plus ergonomique que les Slugs) couplée à un mécanisme de 'Dry Run' (prévisualisation). L'utilisateur voit désormais la liste exacte des sujets (slugs) qui seront impactés *avant* de valider la suppression définitive, évitant ainsi les dommages collatéraux.",
  "components": {
    "purge_memory_action": "2.5"
  }
}

**Exemple pour la 5.114.2 (Surgical Memory Purge) :**

{
  "stack_version": "5.114.2",
  "release_date": "2026-04-12",
  "description": "Sécurisation critique de la purge mémoire : Bascule du ciblage par 'Tags' (trop génériques) vers les 'Slugs' (sujets précis) pour éviter les suppressions accidentelles massives. Amélioration de la sélection de périmètre via un choix numérique explicite.",
  "components": {
    "purge_memory_action": "2.4"
  }
}

**Exemple pour la 5.114.1 (Maintenance Logs & Action UX Fix) :**

{
  "stack_version": "5.114.1",
  "release_date": "2026-04-12",
  "description": "Correctifs critiques et améliorations UX : Restauration des constantes Qdrant dans l'Admin Manager, ajout d'un bouton 'Logs' explicite pour l'historique de maintenance, correction de l'affichage multiligne de la liste des tags dans l'action de purge et mise à jour de l'icône de l'action.",
  "components": {
    "server.py": "5.41",
    "purge_memory_action": "2.3"
  }
}

**Exemple pour la 5.113.1 (Memory Purge UX Enhancement) :**

{
  "stack_version": "5.113.1",
  "release_date": "2026-04-12",
  "description": "Optimisation UX de la purge mémoire : Sélection simplifiée par numéros (simulant des cases à cocher) avec affichage direct des tags identifiés dans la boîte de dialogue pour une sélection facilitée.",
  "components": {
    "purge_memory_action": "2.2"
  }
}

**Exemple pour la 5.112.0 (Granular Memory Governance) :**

{
  "stack_version": "5.112.0",
  "release_date": "2026-04-12",
  "description": "Amélioration majeure du 'Droit à l'oubli' : L'action de purge mémoire permet désormais une sélection granulaire par Tags (sujets) et par périmètre (conversation actuelle vs mémoire globale).",
  "components": {
    "purge_memory_action": "2.0"
  }
}

**Exemple pour la 5.111.0 (Organic Memory Sync & Audit History) :**

{
  "stack_version": "5.111.0",
  "release_date": "2026-04-12",
  "description": "Amélioration de la gouvernance des données : L'Admin Manager synchronise désormais automatiquement la base vectorielle Qdrant lors de l'élagage sémantique pour supprimer les souvenirs orphelins (utilisateurs ou chats supprimés). Ajout d'un historique d'audit persistant (rétention 1 an) consultable depuis le Dashboard.",
  "components": {
    "server.py": "5.40"
  }
}

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