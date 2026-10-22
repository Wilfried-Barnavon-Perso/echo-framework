# Stratégie de Versioning ECHO 5

Ce document définit la politique officielle de versioning pour l'infrastructure et les composants du framework ECHO 5.

## 1. Version de la Stack (Infrastructure Globale)

La version globale d'ECHO suit le format **SemVer** (`A.B.C`, avec `A = 5`) et est pilotée exclusivement par le fichier `VERSION` à la racine du dépôt. Elle représente l'agrégat cohérent et validé des modifications apportées aux composants de la stack.

* **Format** : `A.B.C` (ex: `5.226.18`)
* **Incrément Mineur (`B + 1`, `C = 0`)** : Nouvelles fonctionnalités majeures ou évolutions architecturales structurantes de l'infrastructure.
* **Incrément Patch (`C + 1`)** : Corrections de bugs, optimisations, ajustements de modules ou mises à jour de sécurité.

## 2. Versioning Unifié des Composants (`A.B.C.X`)

Tous les composants d'ECHO (Pipes, Filtres, Outils, Actions, Librairies partagées, Services/Workers Docker, Scripts Bash/PowerShell et Manifestes Docker Compose) adoptent un schéma de numérotation à **4 segments** indexé sur la version globale courante :

* **Format** : `<VERSION_GLOBALE>.<X>` (`A.B.C.X`, ex: `5.226.18.0`)
* **Héritage Conditionnel Initial (`.0`)** : Lors d'une nouvelle intervention sur un composant, si les 3 premiers champs de la version globale sont strictement supérieurs à ceux du composant (`Global[0..2] > Composant[0..2]`), le composant hérite d'abord de la version globale avec son 4ème champ initialisé à `.0` (`A.B.C.0`), puis applique ses changements vers `A.B.C.X`.
* **Granularité de l'Incrément Intra-Composant (`.1`, `.2`, ... `.X`)** : Le 4ème champ s'incrémente (`+1`) à chaque **passe d'édition effective** sur le composant au cours de la tâche :
  * `+1` lors de la passe initiale de modification (`A.B.C.0` ➔ `A.B.C.1`).
  * `+1` à chaque reprise consécutive à un test non concluant de l'agent nécessitant une retouche du code.
  * `+1` à chaque aller-retour de débogage ou d'ajustement entre l'Utilisateur et l'Agent (ex: 7 passes/retouches au total ➔ `A.B.C.7`).
* **Maintien des Composants & Mise à Jour Globale Différée ("Enfin")** : La version globale (`VERSION`) est l'agrégat de la somme des modifications des composants. À la clôture de la tâche :
  1. Les composants modifiés **conservent leur version atteinte `A.B.C.X`** (ex: `5.226.18.7`) et inscrivent leur synthèse sous ce numéro dans leur historique.
  2. La version globale (`VERSION`) est **ensuite** incrémentée en `A.B.(C+1)` (Patch) ou `A.(B+1).0` (Mineur). Ainsi, tous les composants modifiés durant la passe (`A.B.C.X`) redeviennent automatiquement antérieurs à la nouvelle version globale (`Composant[0..2] < Global[0..2]`), prêts à hériter de `<NOUVELLE_VERSION_GLOBALE>.0` lors d'une future intervention.

### Périmètre d'Application et Exclusions
1. **Composants soumis au format `A.B.C.X`** :
   * Modules Open WebUI (`10-owui-pipes/`, `11-owui-filters/`, `12-owui-tools/`, `13-owui-actions/`, `14-owui-libs/`) via le champ `version:` du frontmatter docstring.
   * Manuels opérationnels Markdown (`14-owui-libs/tool_guides/*.md`) via les champs `version:` et `updated_at:` (format `YYYY-MM-DD`) du frontmatter YAML, devant être mis à jour à chaque modification documentaire ou d'outils rattachés.
   * Services et Workers Docker (`20` à `30`) via la directive `VERSION :` d'en-tête.
   * Scripts d'infrastructure (`00-echo-scripts/*.sh`, `install-hyperv.ps1`, `install-linux.sh`, `install-wsl2.sh`) et manifestes (`01-config/stack-echo.yml`, `01-config/bunkerweb-stack.yml`).
   * Variables runtime internes liées à la version du composant (`SCRIPT_VERSION` dans `edge_embed_bridge_filter.py` et `install-hyperv.ps1`, `FastAPI(..., version=...)` dans `30-docker-embedding-worker/app.py`).
2. **Exclusions strictes** :
   * Les fichiers `Dockerfile` (`LABEL version="..."`) ne doivent pas être modifiés lors des passes de versioning afin de préserver l'intégrité du cache de couches Docker BuildKit.
   * Les constantes protocolaires ou de schémas tiers (`_AGY_IDE_VERSION`, `MODEL_CACHE_VERSION`, `"version"` des fichiers JSON de configuration).

## 3. Standard d'En-tête et Historique des Versions

Chaque composant doit maintenir immédiatement sous son en-tête un bloc d'historique standardisé soumis à une **règle stricte de rétention des 5 dernières versions** :

```python
"""
title: ECHO Memory & RAG Tool
author: Wilfried BARNAVON
version: 5.226.18.0
description: ECHO Toolbox pour gestion RAG (Sessions & Codex).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.0: Alignement sur le versioning global ECHO.
```

* **Découplage Compteur / Historique** : Pendant les itérations de travail et de débogage, l'incrément du 4ème champ (`.X`) ne génère aucune ligne dans l'historique. C'est **uniquement lors du scellement final de la passe** (juste avant l'incrément Patch `C+1` ou Mineur `B+1.0` du fichier `VERSION`) qu'une unique entrée `# A.B.C.X: <synthèse concise>` est insérée en tête de la liste `# Historique des versions :` de chaque composant modifié, résumant l'ensemble des changements réalisés sur ce composant durant la passe.
* **Plafond FIFO** : Si le bloc dépasse **5 entrées**, l'entrée la plus ancienne est purgée.

## 4. Synchronisation Documentaire & Fractale

Lors de toute évolution de version :
1. **Documentation Globale (`A.B.C`)** : Le fichier `VERSION` fait foi et sa valeur doit être reflétée dans `README.md` (titre et badge), le pied de page de `AGENTS.md` racine, et les pieds de page des fichiers `docs/*.html` (sans altérer la structure DOM HTML ni l'historique figé du registre d'audit `docs/20_registre_audit.html`).
2. **Mémoire Sémantique Fractale (`AGENTS.md`)** : Toute modification de version d'un composant (`A.B.C.X`) doit être impérativement répercutée dans le fichier `AGENTS.md` de son sous-dossier **ainsi que dans `AGENTS.md` racine** s'il y est mentionné.

## 5. Manifeste de Release (Illustrations du Cycle)

> [!IMPORTANT]
> Les exemples ci-dessous sont fournis à titre **purement illustratif** pour démontrer le cycle d'évolution entre les composants et `VERSION`.
> **L'agent IA ne doit en aucun cas ajouter de nouvelles entrées dans cette section lors des montées de version.**
> La version actuelle de la stack est uniquement pilotée par le fichier `VERSION` à la racine.

**État 1 — Début de passe (Version globale `5.226.18`, composants alignés ou antérieurs) :**
```json
{
  "stack_version": "5.226.18",
  "components": {
    "echo_orchestrator.py": "5.226.18.0",
    "generalist_tools.py": "5.226.17.3"
  }
}
```

**État 2 — Fin de passe & Scellement (Après itérations/debug sur les composants et incrément final de `VERSION`) :**
* `echo_orchestrator.py` (déjà en `5.226.18.0`) a subi 2 passes de modifications/debug ➔ reste en **`5.226.18.2`**.
* `generalist_tools.py` (antérieur en `5.226.17.3`) a hérité de `5.226.18.0` puis a subi 1 passe de modification ➔ reste en **`5.226.18.1`**.
* Chacun consigne sa synthèse sous sa version `5.226.18.X`, puis la version globale passe à **`5.226.19`** :
```json
{
  "stack_version": "5.226.19",
  "release_date": "2026-10-19",
  "description": "Scellement de la passe : les composants modifiés restent en 5.226.18.X et VERSION passe à 5.226.19.",
  "components": {
    "echo_orchestrator.py": "5.226.18.2",
    "generalist_tools.py": "5.226.18.1"
  }
}
```

## 6. Workflow de Mise à Jour (Exécution Séquentielle Stricte)

Lors de toute intervention sur le code, l'agent doit exécuter rigoureusement cette séquence en 3 étapes :

1. **Étape 1 : Contrôle d'Alignement et Héritage (À la 1ère touche d'un composant)**
   * Lire la version globale `A.B.C` dans [`VERSION`] et la comparer aux 3 premiers champs du composant (`Global[0..2]` vs `Composant[0..2]`).
   * Si `Global[0..2] > Composant[0..2]` : le composant hérite immédiatement de la base globale avec son 4ème champ initialisé à zéro (`A.B.C.0`) avant d'appliquer son premier changement.
   * Si `Global[0..2] == Composant[0..2]` : le composant a déjà hérité de la base globale durant la passe courante ; sa valeur `.X` actuelle est conservée comme point de départ.

2. **Étape 2 : Passes d'Édition et Débogage (Pendant le travail)**
   * Incrémenter le 4ème champ (`.X` ➔ `.X+1`) à la première modification effective du composant, puis de nouveau (`+1`) à chaque reprise suite à un test non concluant de l'agent ou à chaque aller-retour de débogage/ajustement avec l'Utilisateur.
   * Mettre à jour l'en-tête (`version:` / `VERSION :`), les éventuelles constantes internes de version du fichier, le fichier `AGENTS.md` local du sous-dossier et `AGENTS.md` racine (si le composant y est cité).
   * **Ne pas modifier** le bloc `# Historique des versions :` ni le fichier `VERSION` durant cette étape.

3. **Étape 3 : Scellement et Montée de Version Globale ("Enfin", à la clôture)**
   * Une fois toutes les modifications validées et stabilisées :
   * **Maintien et Historique des Composants** : Les composants modifiés **restent à leur version `A.B.C.X`**. Insérer en tête du bloc `# Historique des versions :` de chacun une unique ligne `# A.B.C.X: <synthèse concise>` résumant la passe (purger la 6ème entrée si > 5). Vérifier que tous les fichiers `AGENTS.md` (locaux et racine) reflètent bien `A.B.C.X`.
   * **Incrément de la Version Globale (`VERSION`)** : Incrémenter le fichier `VERSION` qui synthétise cette nouvelle mouture de la stack :
     * **Patch (`A.B.C+1`)** : corrections de bugs, débogage, refactorisation, optimisations ou retouches de composants.
     * **Mineur (`A.B+1.0`)** : nouvelles fonctionnalités majeures ou évolutions architecturales structurantes.
   * **Synchronisation Documentaire Globale** : Répercuter la nouvelle version globale (`A.B.C+1` ou `A.B+1.0`) dans `README.md` (titre et badge), le pied de page de `AGENTS.md` racine, et les pieds de page des 21 fichiers `docs/*.html`.

*Document de référence pour l'équipe ECHO Architecture.*
