---
domain_id: n8n_workflows
version: 6.24.1.1
updated_at: 2026-11-26
name: Orchestration & Automatisation N8N
description: Conception déléguée (N8N Grapher Prometheus), recherche Hub N8N, gestion de templates globaux, exécution éphémère Oneshot et déploiement de Daemons persistants.
tools: [manage_n8n_template, manage_n8n_workflow, n8n_hub, query_n8n_documentation, delegate_to_n8n_grapher]
keywords: [n8n, workflow, automatisation, daemon, oneshot, template, grapher, hub, sub_sid, cohort_id, allowed_tools, __ECHO_SECRET_, __ECHO_DOWNLOAD_DIR__]
---

# MANUEL OPÉRATIONNEL : ORCHESTRATION N8N (`n8n_workflows`)

## 1. RÈGLES D'ARCHITECTURE N8N ECHO & DÉLÉGATION AU GRAPHER

### 1.1 Règle Critique de Délégation (`delegate_to_n8n_grapher`)
- **Contrainte d'environnement** : Autorisé pour l'Orchestrateur principal et les supérieurs hiérarchiques `can_delegate=True` (`Stratège` / `Capitaine` avec `depth < ECHO_MAX_PHALANX_DEPTH = 3`, propageant automatiquement `parent_sid`, `group_id` et `depth + 1`). **ABSOLUMENT INTERDIT** depuis un `Hoplite` feuille (`can_delegate=False`).
- Dès qu'un workflow demandé **dépasse 2 nœuds**, nécessite du mocking, une gestion de payloads volumineux ou des tests en Sandbox, Le Modèle principal (ou le supérieur `can_delegate=True`) **DOIT IMPÉRATIVEMENT** déléguer la tâche à `delegate_to_n8n_grapher(instructions, sub_sid=None, cohort_id=None, allowed_tools=None)` (`MODEL_PROMETHEUS`, exécuté par **tranches coopératives de 8 tours d'outils / 120s** sous session `thread_n8n_<hex8>`, reprenable via `control_subagents(..., filter_type="agent")`) :
  - `sub_sid` (`Optional[str]`) : Identifiant explicite optionnel pour nommer ou reprendre la session N8N Grapher.
  - `cohort_id` (`Optional[str]`) : Rattache la session à une Cohorte légère (`phx_<id>`) lorsqu'elle est invoquée hors d'un groupe hiérarchique parent.
  - `allowed_tools` (`Optional[List[str]]`) : Surcharge ou patch (`["*"]`, `["+outil", "-outil"]`, liste explicite) appliqué au pool d'outils par défaut du N8N Grapher.
- **Pool d'outils par défaut (14 outils)** : `manage_n8n_workflow`, `manage_n8n_template`, `n8n_hub`, `query_n8n_documentation`, `list_identities`, `list_identities_services`, `wait_timer`, `query_registry`, `code_executor`, `create_codex`, `read_codex`, `edit_codex`, `list_codex`, `search_codex`. *(Attention : `ask_user_input` et `manage_identity` étant **ABSOLUMENT INTERDITS** en mode headless, la collecte et l'enregistrement d'un secret manquant DOIVENT être effectués par Le Modèle principal avant ou après la délégation).*
- Si Le Modèle principal intervient directement sur un workflow simple, il **DOIT** préalablement appeler `query_n8n_documentation()` pour récupérer la version exacte de l'instance N8N et ses contraintes architecturales.

### 1.2 Barrière de Sécurité des Secrets (`__ECHO_SECRET_<KEY>__`)
- Les blocs `"credentials"` N8N natifs et les en-têtes d'authentification codés en dur (`Authorization`, `Cookie`, `X-API-Key`, `Api-Key`, `Token`) sont **ABSOLUMENT INTERDITS** et bloqués par `manage_n8n_workflow(action="prepare"|"update", ...)`.
- **Procédure obligatoire pour l'authentification dans N8N** :
  1. Vérifier les secrets existants via `list_identities(service="n8n_workflows")`.
  2. Si le secret est manquant, Le Modèle principal DOIT le demander à L'Utilisateur via `ask_user_input` puis l'enregistrer via `manage_identity(action="add", service="n8n_workflows", account_id="NOM_CLE", credentials_json='{"description": "...", "value": "..."}')`.
  3. Dans le JSON du workflow N8N (ex: nœud HTTP Request), injecter la macro **`__ECHO_SECRET_<KEY>__`** où `<KEY>` respecte strictement la regex `^[A-Z0-9_]+$`. Le Système substitue automatiquement la valeur déchiffrée du Vault à l'exécution et bloque tout workflow contenant une macro non résolue ou malformée.

### 1.3 Écriture de Fichiers et Rapports (`__ECHO_DOWNLOAD_DIR__`)
- Les nœuds Webhook entrants sont inutilisables dans cette infrastructure isolée.
- Si un workflow (Oneshot asynchrone ou Daemon) produit un fichier ou un rapport, tout nœud d'écriture sur disque (`Write Binary File` ou `Read/Write Files from Disk` en mode `write`) **DOIT STRICTEMENT** cibler le chemin :
  `__ECHO_DOWNLOAD_DIR__/nom_du_fichier.ext`
- Tout chemin relatif ou ne commençant pas par `__ECHO_DOWNLOAD_DIR__/` est rejeté par `manage_n8n_workflow(action="prepare"|"update", ...)`. Tout fichier produit dans ce répertoire est ingéré automatiquement dans le Registre Unifié de la session (plafond d'ingestion asynchrone fixé à **64 Ko**, avec filtrage intra-graphe).

---

## 2. CYCLE DE VIE DES WORKFLOWS DE SESSION

### Étape 1 : Recherche Hub ou Templates Globaux
- **Hub Officiel N8N (`n8n_hub`)** :
  - `n8n_hub(action="search"|"download", query=None, limit=3, hub_id=None, template_id=None)` *(défaut `action="search"`)* :
    - `action="search"` (`query`, `limit=3`) : Recherche des workflows sur l'API publique N8N et retourne leurs `hub_id`.
    - `action="download"` (`hub_id: str`, `template_id`) : Télécharge le workflow `hub_id` et l'enregistre comme template global sous `template_id` (purger les `credentials` natifs éventuels avant instanciation).
- **Templates Globaux Utilisateur (`manage_n8n_template`, `resource_type="n8n_template"`)** :
  - `manage_n8n_template(action="create"|"update"|"delete", template_id, content=None)` : Crée, met à jour (`content` requis pour `"create"` et `"update"`) ou supprime un JSON N8N réutilisable entre sessions.

### Étape 2 : Instanciation et Mise à Jour dans la Session (`manage_n8n_workflow`)
- `manage_n8n_workflow(action="prepare"|"update"|"stop"|"delete"|"run_oneshot"|"deploy_daemon", n8n_workflow_id=None, content=None, from_template_id=None, sync=False, parameters_override=None, execution_id=None)` :
  - `action="prepare"` (`content=None`, `from_template_id=None`) : Instancie un workflow depuis le JSON complet (`content`) ou un template local (`from_template_id`), valide la barrière de sécurité Vault et retourne l'UUID `n8n_workflow_id` (statut `'ready'` dans le Registre).
  - `action="update"` (`n8n_workflow_id`, `content`) : Surcharge le JSON d'un workflow de session existant.

### Étape 3 : Exécution Éphémère (`run_oneshot`), Déploiement Persistant (`deploy_daemon`) & Arrêt (`stop` / `delete`)
1. **Exécution Éphémère CLI (`action="run_oneshot"`)** :
  - **Exigence de Déclencheur** : Tout workflow exécuté en Oneshot **DOIT IMPÉRATIVEMENT** démarrer par un nœud **`Execute Workflow Trigger`**.
  - `sync=False` (**défaut, REQUIS pour les tâches longues ou volumineuses**) : Lance l'exécution en tâche de fond ; les fichiers générés sont ingérés automatiquement (plafond d'ingestion asynchrone `64 Ko` + filtrage intra-graphe).
  - `sync=True` : Mode bloquant qui attend la fin d'exécution et retourne `stdout/stderr` (limité à **8 Ko** max ; au-delà, Le Système bloque le retour et exige `sync=False` ou écriture dans `__ECHO_DOWNLOAD_DIR__`).
  - `parameters_override` : Dictionnaire optionnel `{"Nom du Noeud": {"param": "valeur"}}` pour surcharger à la volée les paramètres d'un nœud sans modifier le JSON.
2. **Déploiement Démon Persistant (`action="deploy_daemon"`)** :
  - **RÈGLE D'OR** : Le Modèle **DOIT** tester et valider son workflow via `manage_n8n_workflow(action="run_oneshot", ...)` avant d'appeler `manage_n8n_workflow(action="deploy_daemon", n8n_workflow_id=...)`.
  - **Substitution obligatoire avant déploiement** : Avant d'appeler `deploy_daemon`, Le Modèle **DOIT IMPÉRATIVEMENT** remplacer le nœud `Execute Workflow Trigger` et les éventuels nœuds de mock par le trigger de production (ex: `Schedule Trigger`) via `manage_n8n_workflow(action="update", n8n_workflow_id=..., content=...)`.
  - Active le workflow en continu en arrière-plan (statut `deployed_as_<real_n8n_id>`). En mode Démon, `stdout/stderr` n'étant pas restitués dans le chat, toute sortie utile DOIT passer par `__ECHO_DOWNLOAD_DIR__/nom_fichier.ext`.
3. **Arrêt Non-Destructif (`action="stop"`)** :
  - `manage_n8n_workflow(action="stop", n8n_workflow_id=..., execution_id=...)` : Accepte `n8n_workflow_id` et/ou `execution_id` (`execution_id` ciblé pour interrompre un run Oneshot asynchrone précis, ou `n8n_workflow_id` seul pour tuer tous les runs Oneshot actifs et/ou dépublier le démon actif).
  - Conserve le fichier JSON du workflow et les fichiers générés, et réinitialise le statut dans le Registre à `'ready'`.
  - **Règle de séparation des moteurs** : `control_sandbox_execution` est exclusif aux processus `code_executor` (`run_*`) ; il est **ABSOLUMENT INTERDIT** de l'invoquer pour inspecter ou tuer un workflow ou une exécution N8N.
4. **Arrêt et Nettoyage Complet (`action="delete"`)** :
  - `manage_n8n_workflow(action="delete", n8n_workflow_id=...)` : Supprime le workflow de la session, arrête les démons actifs et purge les fichiers générés.
