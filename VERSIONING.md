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

* **Fichier** : `03-OWUI-functions/pipe_engine.py`

* **Exemple** : `136.0` (No Upload / Base64 Mode + Turbo JSON).

### B. Admin Manager

* **Format** : `X.Y`

* **Fichier** : `01-docker-admin-manager/server.py`

* **Exemple** : `2.6` (Support UTF-8).

### C. Workers & Tools

* **Format** : `X.Y`

* Suivent une numérotation simple.

## 3. Le Manifeste de Déploiement

Pour garantir la cohérence, chaque Release de la Stack (`5.Y.Z`) est définie par un snapshot précis des versions de composants.

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