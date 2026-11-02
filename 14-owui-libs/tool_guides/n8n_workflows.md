---
domain_id: n8n_workflows
version: 5.231.52.1
updated_at: 2026-11-02
name: Orchestration & Automatisation N8N
description: Conception déléguée (N8N Grapher Prometheus), recherche Hub N8N, gestion de templates globaux, exécution éphémère Oneshot et déploiement de Daemons persistants.
tools: [create_n8n_template, modify_n8n_template, delete_n8n_template, prepare_n8n_workflow, modify_n8n_workflow, delete_n8n_workflow, run_n8n_oneshot_workflow, deploy_n8n_daemon, search_n8n_hub, download_n8n_hub_template, query_n8n_documentation, delegate_to_n8n_grapher]
keywords: [n8n, workflow, automatisation, daemon, oneshot, template, grapher, hub, __ECHO_SECRET_, __ECHO_DOWNLOAD_DIR__]
---

# MANUEL OPÉRATIONNEL : ORCHESTRATION N8N (`n8n_workflows`)

## 1. RÈGLES D'ARCHITECTURE N8N ECHO & DÉLÉGATION AU GRAPHER

### 1.1 Règle Critique de Délégation (`delegate_to_n8n_grapher`)
- Dès qu'un workflow demandé **dépasse 2 nœuds**, nécessite du mocking, une gestion de payloads volumineux ou des tests en Sandbox, l'Orchestrateur **DOIT IMPÉRATIVEMENT** déléguer la tâche à `delegate_to_n8n_grapher(instructions="...")` (`MODEL_PROMETHEUS`).
- `delegate_to_n8n_grapher` dispose de tous les outils N8N, du Codex, du Code Executor et de la consultation de l'Identity Vault (`list_identities_services`, `list_identities`) pour concevoir, tester en Oneshot et déployer le graphe de bout en bout. *(Attention : `ask_user_input` et `manage_identity` étant bloqués en mode Headless, la collecte et l'enregistrement d'un secret manquant doivent être effectués par l'Orchestrateur principal avant ou après la délégation).*
- Si l'Orchestrateur intervient directement sur un workflow simple, il **DOIT** préalablement appeler `query_n8n_documentation()` pour récupérer la version exacte de l'instance N8N et ses contraintes architecturales.

### 1.2 Barrière de Sécurité des Secrets (`__ECHO_SECRET_XXX__`)
- Les blocs `"credentials"` N8N natifs et les en-têtes d'authentification codés en dur (`Authorization`, `Cookie`, `X-API-Key`, `Token`) sont **STRICTEMENT INTERDITS** et bloqués par `prepare_n8n_workflow` / `modify_n8n_workflow`.
- **Procédure obligatoire pour l'authentification dans N8N** :
  1. Vérifier les secrets existants via `list_identities(service="n8n_workflows")`.
  2. Si le secret est manquant, l'Orchestrateur principal DOIT le demander à l'Utilisateur via `ask_user_input` puis l'enregistrer via `manage_identity(action="add", service="n8n_workflows", account_id="NOM_CLE", credentials_json='{"description": "...", "value": "..."}')`.
  3. Dans le JSON du workflow N8N (ex: nœud HTTP Request), injecter la macro **`__ECHO_SECRET_NOM_CLE__`**. Le moteur substitue automatiquement la valeur déchiffrée du Vault à l'exécution et bloque tout workflow contenant une macro non résolue.

### 1.3 Écriture de Fichiers et Rapports (`__ECHO_DOWNLOAD_DIR__`)
- Les nœuds Webhook entrants sont inutilisables dans cette infrastructure isolée.
- Si un workflow (Oneshot asynchrone ou Daemon) produit un fichier ou un rapport, tout nœud d'écriture sur disque (`Write Binary File` ou `Read/Write Files from Disk` en mode `write`) **DOIT STRICTEMENT** cibler le chemin :
  `__ECHO_DOWNLOAD_DIR__/nom_du_fichier.ext`
- Tout chemin relatif ou ne commençant pas par `__ECHO_DOWNLOAD_DIR__/` est rejeté par `prepare_n8n_workflow` / `modify_n8n_workflow`. Tout fichier produit dans ce répertoire est ingéré automatiquement dans le Registre Unifié de la session.

---

## 2. CYCLE DE VIE DES WORKFLOWS DE SESSION

### Étape 1 : Recherche Hub ou Templates Globaux
- **Hub Officiel N8N** :
  - `search_n8n_hub(query, limit=3)` : Recherche des workflows sur l'API publique N8N et retourne leurs `hub_id`.
  - `download_n8n_hub_template(hub_id, template_id)` : Télécharge le workflow `hub_id` et l'enregistre comme template global sous `template_id` (purger les `credentials` natifs éventuels avant instanciation).
- **Templates Globaux Utilisateur (`resource_type="n8n_template"`)** :
  - `create_n8n_template(template_id, content)` : Enregistre un JSON N8N réutilisable entre sessions.
  - `modify_n8n_template(template_id, new_content)` / `delete_n8n_template(template_id)`.

### Étape 2 : Instanciation dans la Session (`prepare_n8n_workflow`)
- `prepare_n8n_workflow(content=None, from_template_id=None)` :
  - Fournir soit le JSON complet (`content`), soit l'identifiant d'un template local (`from_template_id`).
  - Valide la barrière de sécurité Vault et retourne l'UUID `n8n_workflow_id` (statut `'ready'` dans le Registre).
- `modify_n8n_workflow(n8n_workflow_id, new_content)` : Surcharge le JSON d'un workflow de session existant.

### Étape 3 : Exécution Éphémère (`run_n8n_oneshot_workflow`) vs Déploiement Persistant (`deploy_n8n_daemon`)
1. **Exécution Éphémère CLI (`run_n8n_oneshot_workflow`)** :
  - `sync=True` : Mode bloquant qui attend la fin d'exécution et retourne `stdout/stderr` (limité à **8 Ko** max ; au-delà, le système bloque le retour et exige `sync=False` ou écriture dans `__ECHO_DOWNLOAD_DIR__`).
  - `sync=False` (Défaut recommandé pour scraping/tâches longues) : Lance l'exécution en tâche de fond ; les fichiers générés sont ingérés automatiquement.
  - `parameters_override` : Dictionnaire optionnel `{"Nom du Noeud": {"param": "valeur"}}` pour surcharger à la volée les paramètres d'un nœud sans modifier le JSON.
2. **Déploiement Démon Persistant (`deploy_n8n_daemon`)** :
  - **RÈGLE D'OR** : Le Modèle **DOIT** tester et valider son workflow via `run_n8n_oneshot_workflow` avant d'appeler `deploy_n8n_daemon(n8n_workflow_id)`.
  - Active le workflow en continu en arrière-plan (statut `deployed_as_<real_n8n_id>`). En mode Démon, `stdout/stderr` n'étant pas restitués dans le chat, toute sortie utile DOIT passer par `__ECHO_DOWNLOAD_DIR__/nom_fichier.ext`.
3. **Arrêt et Nettoyage Complet (`delete_n8n_workflow`)** :
  - `delete_n8n_workflow(n8n_workflow_id)` : Supprime le workflow de la session, arrête les démons actifs et purge les fichiers générés.
