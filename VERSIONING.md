# Stratégie de Versioning ECHO 5

Ce document définit les règles de versioning pour l'infrastructure ECHO 5.

## 1. Version de la Stack (Infrastructure Globale)

La version globale d'ECHO suit le format **SemVer** (`X.Y.Z`). Elle représente un état stable et testé de l'ensemble des composants.

* **Format** : `5.Y.Z`
* **Incrément Mineur (Y)** : Nouvelles fonctionnalités majeures ou changements structurels de l'infrastructure.
* **Incrément Patch (Z)** : Corrections de bugs, mises à jour de sécurité ou ajustements mineurs des scripts.

## 2. Versioning des Composants (Granulaire)

Chaque module ou outil possède sa propre version interne pour permettre un suivi précis des modifications.

### A. Pipe Engine
* **Format** : `XXX.YY`
* **Exemple** : `190.05`

### B. Admin Manager
* **Format** : `X.YY`
* **Exemple** : `5.52`

### C. Modules, Outils & Filtres
* **Format** : `X.Y`
* Suivent une numérotation simple.

## 3. Manifeste de Release (Illustrations)

> [!IMPORTANT]
> Les exemples ci-dessous sont fournis à titre **purement illustratif** pour démontrer le format attendu. 
> **L'agent IA ne doit en aucun cas ajouter de nouvelles entrées dans cette section lors des montées de version.** 
> La version actuelle de la stack est uniquement pilotée par le fichier `VERSION` à la racine.

**Exemple A (Correctif Technique) :**
{
  "stack_version": "5.141.7",
  "release_date": "2026-05-16",
  "description": "Fiabilisation du Cortex : Implémentation du lecteur Python robuste (ast.literal_eval) et résolution des conflits de noms d'outils.",
  "components": {
    "pipe_engine.py": "190.6",
    "cognitive_agents.py": "5.7"
  }
}

**Exemple B (Optimisation) :**
{
  "stack_version": "5.141.6",
  "release_date": "2026-05-16",
  "description": "Amélioration de la détection de la balise d'arrêt <FINAL_ANSWER>.",
  "components": {
    "cognitive_agents.py": "5.6"
  }
}

**Exemple C (Nouvelle Fonctionnalité) :**
{
  "stack_version": "5.141.4",
  "release_date": "2026-05-16",
  "description": "Ajout de l'outil list_skills et clarification de la profondeur généalogique.",
  "components": {
    "cognitive_agents.py": "5.4"
  }
}

## 4. Workflow de Mise à Jour

1. **Développement (`dev`)** :
   * On travaille sur les composants (ex: Pipe 136.0).
   * On teste sur la VM de dev.

2. **Validation (`tag`)** :
   * Une fois stable, on tague le composant (ex: commit "Pipe 136.0").

3. **Release Stack (`main`)** :
   * On met à jour le fichier `VERSION` à la racine (ex: 5.141.7).
   * On tague le dépôt avec la version globale.

*Document de référence pour l'équipe ECHO Architecture.
