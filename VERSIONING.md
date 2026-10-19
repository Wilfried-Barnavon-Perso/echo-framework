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
* **Alignement Conditionnel (`.0`)** : Lors d'une modification, si les 3 premiers champs de la version globale sont supérieurs à ceux du composant (`Global[0..2] > Composant[0..2]`), le composant est d'abord mis à jour en s'alignant sur la version globale avec son 4ème champ initialisé à `.0` (`<VERSION_GLOBALE>.0`).
* **Incrément Intra-Composant (`.1`, `.2`, ...)** : Chaque modification ultérieure apportée au composant incrémente son 4ème champ (`+1` par changement. Ex : 7 modifications successives ➔ incrément de 7 sur le 4ème champ).
* **Mise à Jour Globale Différée ("Enfin")** : La version globale (`VERSION`) n'est jamais modifiée en cours de retouche ; elle est mise à jour à la fin du cycle de modifications selon les règles SemVer.

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

* **Découplage Compteur / Historique** : Le 4ème champ (`.N`) est un compteur d'itérations qui n'insère pas de ligne dans l'historique. C'est **uniquement à chaque incrément du 3ème champ (`Z`)** qu'une nouvelle entrée `# <version>: <synthèse concise>` est insérée en tête de la liste `# Historique des versions :`, résumant l'ensemble des modifications de la passe.
* **Plafond FIFO** : Si le bloc dépasse **5 entrées**, l'entrée la plus ancienne est purgée.

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

Lors de toute intervention ou modification sur le code, la séquence chronologique suivante doit être strictement respectée :

1. **Étape 1 : Contrôle d'Alignement (Avant modification)**
   * Comparer les 3 premiers champs de la version globale ([`VERSION`]) avec ceux du composant (`Global[0..2]` vs `Composant[0..2]`).
   * Si `Global[0..2] > Composant[0..2]` : le composant est immédiatement aligné sur la version globale avec son 4ème champ initialisé à zéro (`<VERSION_GLOBALE>.0`).
   * Si `Global[0..2] == Composant[0..2]` : le composant est déjà aligné sur la passe courante, sa version courante est conservée sans réinitialisation.

2. **Étape 2 : Itérations et Modifications (Pendant le travail)**
   * Chaque modification effective apportée au composant incrémente son 4ème champ (`+1` par changement).
     * *Exemple : si 7 changements ont lieu, le 4ème champ est incrémenté de 7.*
   * Le 4ème champ n'ajoute aucune ligne dans le bloc d'historique. Seul le champ `version:` de l'en-tête et la référence dans le fichier `AGENTS.md` local du sous-dossier sont actualisés.

3. **Étape 3 : Scellement et Mise à Jour Globale ("Enfin", à la fin)**
   * Une fois l'ensemble des modifications de la demande ou de la session terminé et validé :
   * **Consignation de Synthèse** : Pour chaque composant modifié, consigner en une courte ligne la synthèse des modifications dans son bloc `# Historique des versions :` (incrément du 3ème champ).
   * **Montée de Version Globale** : La version globale dans le fichier `VERSION` (`5.Y.Z`) est mise à jour selon la politique de versioning :
     * **Patch (`Z + 1`)** : corrections de bugs, refactorisation, optimisations et retouches de composants.
     * **Mineur (`Y + 1`, `Z = 0`)** : nouvelles fonctionnalités majeures ou refonte architecturale structurante.
   * **Synchronisation Documentaire** : Répercussion synchrone dans `README.md` (titre et badge), pied de page de `AGENTS.md` racine, et pieds de page des 21 fichiers `docs/*.html`.

*Document de référence pour l'équipe ECHO Architecture.*
