# Stratégie de Versioning ECHO v5

Ce document définit les règles de versioning pour l'infrastructure ECHO v5.

## 1\. Version de la Stack (Infrastructure Globale)

La version globale d'ECHO suit le format **SemVer** (`vX.Y.Z`). Elle représente un état stable et testé de l'ensemble des composants.

-   **Format** : `v5.Y.Z`
-   **Emplacement** : Fichier `VERSION` à la racine du dépôt.

**Type**

**Incrément**

**Description**

**Exemple**

**Majeur**

`v6.0.0`

Refonte architecturale complète (ex: changement d'orchestrateur, abandon de Docker).

v5 -> v6

**Mineur**

`v5.3.0`

Ajout d'un nouveau service (conteneur), changement d'OS de base, nouvelles fonctionnalités clés.

v5.2 -> v5.3

**Patch**

`v5.2.1`

Correctifs de scripts, mise à jour de configuration, update de dépendances mineures.

v5.2.0 -> v5.2.1

## 2\. Versioning des Composants (Modules)

Chaque composant possède son propre cycle de vie et sa propre numérotation, documentée dans son en-tête de fichier.

### A. ECHO Engine (Le Pipe)

-   **Format** : `vXXX.YY` (Entier.Décimale)
-   `XXX` : Version fonctionnelle majeure (ex: 132). Incrémenté lors d'ajouts de logique (nouveau fix, nouvelle gestion de mémoire).
-   `YY` : Correctif ou ajustement mineur (ex: 01).
-   **Fichier** : `03-OWUI-functions/pipe_engine.py`
-   **Exemple** : `v132.01` (Stable PKCE + Hybrid Memory).

### B. Admin Manager

-   **Format** : `vX.Y`
-   **Fichier** : `01-docker-admin-manager/server.py`
-   **Exemple** : `v2.6` (Support UTF-8).

### C. Workers & Tools

-   **Format** : `vX.Y`
-   Suivent une numérotation simple.

## 3\. Le Manifeste de Déploiement

Pour garantir la cohérence, chaque Release de la Stack (`v5.Y.Z`) est définie par un snapshot précis des versions de composants.

**Exemple pour la v5.2.1 :**

{
  "stack\_version": "v5.2.1",
  "release\_date": "2026-01-02",
  "components": {
    "pipe\_engine": "v132.01",
    "admin\_manager": "v2.6",
    "python\_worker": "v1.0",
    "browser\_agent": "v1.0",
    "open\_webui": "main"
  }
}

## 4\. Workflow de Mise à Jour

1.  **Développement (**`**dev**`**)** :

-   On travaille sur les composants (ex: Pipe v133.00).
-   On teste sur la VM de dev.

1.  **Validation (**`**tag**`**)** :

-   Une fois stable, on tague le composant (ex: commit "Pipe v133.00").

1.  **Release Stack (**`**main**`**)** :

-   On met à jour le fichier `VERSION` (ex: v5.2.1 -> v5.3.0).
-   On met à jour le `ECHO_MANIFEST.json`.
-   On fusionne sur `main` et on crée un tag git `v5.3.0`.

_Document de référence pour l'équipe ECHO Architecture._