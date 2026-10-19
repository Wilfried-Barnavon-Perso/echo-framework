# Stratégie de Versioning ECHO 5

Ce document définit la politique officielle de versioning pour l'infrastructure et les composants du framework ECHO 5.

## 1. Version de la Stack (Infrastructure Globale)

La version globale d'ECHO suit le format **SemVer** (`5.Y.Z`) et est pilotée exclusivement par le fichier `VERSION` à la racine du dépôt. Elle représente un état cohérent et validé de l'ensemble de la stack.

* **Format** : `5.Y.Z` (ex: `5.226.18`)
* **Incrément Mineur (`Y`)** : Nouvelles fonctionnalités majeures ou évolutions architecturales structurantes de l'infrastructure.
* **Incrément Patch (`Z`)** : Corrections de bugs, optimisations, ajustements de modules ou mises à jour de sécurité.

## 2. Versioning Unifié des Composants (`GLOBAL.N`)

Tous les composants d'ECHO (Pipes, Filtres, Outils, Actions, Librairies partagées, Services/Workers Docker, Scripts Bash/PowerShell et Manifestes Docker Compose) adoptent un schéma de numérotation à **4 segments** indexé sur la version globale courante :

* **Format** : `<VERSION_GLOBALE>.<N>` (ex: `5.226.18.0`)
* **Passe Initiale (`.0`)** : À l'ouverture d'une passe de version globale `5.Y.Z`, la version de base d'un composant aligné sur cette passe est fixée à `5.Y.Z.0`.
* **Incrément Intra-Passe (`.1`, `.2`, ...)** : Chaque modification ultérieure apportée à un composant au cours de la même passe globale incrémente son 4ème segment (`5.Y.Z.1`, `5.Y.Z.2`, etc.).
* **Réinitialisation sur Bump Global** : Lorsque la version globale `VERSION` est incrémentée (ex: passage à `5.226.19`), le compteur d'itération local `<N>` repart à `.0` (`5.226.19.0`) lors de l'alignement ou de la première modification du composant dans la nouvelle passe.

### Périmètre d'Application et Exclusions
1. **Composants soumis au format `5.Y.Z.N`** :
   * Modules Open WebUI (`10-owui-pipes/`, `11-owui-filters/`, `12-owui-tools/`, `13-owui-actions/`, `14-owui-libs/`) via le champ `version:` du frontmatter docstring.
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

* À chaque incrément de version du composant, une nouvelle entrée `# <version>: <description concise>` est insérée en tête de la liste `# Historique des versions :`.
* Si le bloc dépasse **5 entrées**, l'entrée la plus ancienne est purgée.

## 4. Synchronisation Documentaire & Fractale

Lors de toute évolution de version :
1. **Documentation Globale (`5.Y.Z`)** : Le fichier `VERSION` fait foi et sa valeur doit être reflétée dans `README.md` (titre et badge), le pied de page de `AGENTS.md` racine, et les pieds de page des fichiers `docs/*.html` (sans altérer la structure DOM HTML ni l'historique figé du registre d'audit `docs/20_registre_audit.html`).
2. **Mémoire Sémantique Fractale (`AGENTS.md`)** : Toute modification de version d'un composant (`5.Y.Z.N`) doit être répercutée dans le fichier `AGENTS.md` de son dossier (ainsi que dans `AGENTS.md` racine s'il y est référencé).

## 5. Manifeste de Release (Illustrations)

> [!IMPORTANT]
> Les exemples ci-dessous sont fournis à titre **purement illustratif** pour démontrer le format attendu.
> **L'agent IA ne doit en aucun cas ajouter de nouvelles entrées dans cette section lors des montées de version.**
> La version actuelle de la stack est uniquement pilotée par le fichier `VERSION` à la racine.

**Exemple A (Alignement Initial de Passe) :**
```json
{
  "stack_version": "5.226.18",
  "release_date": "2026-10-19",
  "description": "Harmonisation globale des composants sur le schéma à 4 segments (GLOBAL.N).",
  "components": {
    "echo_orchestrator.py": "5.226.18.0",
    "memory_and_rag_tool.py": "5.226.18.0"
  }
}
```

**Exemple B (Modifications Successives dans la Même Passe) :**
```json
{
  "stack_version": "5.226.18",
  "release_date": "2026-10-19",
  "description": "Ajustements incrémentaux sur l'orchestrateur et les outils généralistes.",
  "components": {
    "echo_orchestrator.py": "5.226.18.2",
    "generalist_tools.py": "5.226.18.1"
  }
}
```

## 6. Workflow de Mise à Jour

1. **Développement (`dev`)** :
   * Tout composant créé ou aligné sur la passe courante démarre à `<GLOBAL>.0` (ex: `5.226.18.0`).
   * Chaque retouche ultérieure du composant au cours de la passe incrémente son 4ème segment (`.1`, `.2`, etc.) avec mise à jour de son changelog d'en-tête (max 5 entrées) et de son `AGENTS.md` local.
2. **Release Stack (`main`)** :
   * Mise à jour du fichier `VERSION` à la racine (`5.Y.Z`) et synchronisation des fichiers documentaires (`README.md`, `AGENTS.md`, `docs/*.html`).

*Document de référence pour l'équipe ECHO Architecture.*
