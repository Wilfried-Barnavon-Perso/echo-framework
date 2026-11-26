---
domain_id: agent_orchestration
version: 6.24.0.1
updated_at: 2026-11-26
name: Orchestration Multi-Agents, Sous-Agents Autonomes, Data Broker & Skills
description: Délégation par tranches supervisées (delegate_to_agent), pilotage unifié (control_subagents), Table Ronde Delphi (consult_council), Phalange Multi-Niveaux (consult_phalanx), Data Broker, gestion des Skills, routage cognitif (clamping Pipe et cascade descendante automatique sur erreurs API ou block_reason).
tools: [delegate_to_agent, control_subagents, consult_council, consult_phalanx, forge_skill, list_skills, read_skill, search_skill, delete_user_skill, ask_data_broker]
keywords: [agent, sous-agent, délégation, tranche, checkpoint, council, table_ronde, delphi, phalanx, phalange, cohort_id, cohorte, allowed_tools, stratège, stratege, capitaine, hoplite, handoff, skill, data_broker, sub_sid, parallèle, parallel, steer, resume, suspend, kill, summarize, cascade, repli, fallback, clamping, block_reason, filtre, model_zephyr, model_apollo, model_prometheus, model_athena, target_model_key, new_cognitive_level]
---

# MANUEL OPÉRATIONNEL : ORCHESTRATION MULTI-AGENTS & SKILLS (`agent_orchestration`)

## 1. MATRICE D'ARBITRAGE DES 4 MOTEURS DE DÉLÉGATION

| Moteur | Architecture & Modèles | Cas d'Usage Nominal | Gestion des Sessions |
| :--- | :--- | :--- | :--- |
| **`delegate_to_agent`** | 1 Sous-Agent autonome outillé (défaut `MODEL_APOLLO`, exécuté par **tranches coopératives de 8 tours d'outils (jusqu'à 5 appels parallèles/tour) / 120s** sans limite préalable de mission, avec disjoncteur PARE à 3 erreurs). Parallélisable (jusqu'à 4 Sous-Agents simultanés par tour). | Tâche spécialisée isolée, exécution technique multi-outils, offload cognitif parallèle. | `sub_sid` piloté via **`control_subagents`**. |
| **`consult_council`** | **Table Ronde Delphi** (réservée exclusivement à l'Orchestrateur principal) : 1 à 7 profils (`participants` = liste de `skill_id`) démultipliés par `multiplier` ($1 \dots 5$, min 2 participants effectifs) débattant en parallèle sur $R$ tours ($3 \dots 10$, **3 tours d'outils par défaut (`0..6` via UserValve) / 120s par round Delphi**) + Synthétiseur (`MODEL_PROMETHEUS`). | Sujet multidimensionnel, incertain ou stratégique nécessitant un débat contradictoire multi-perspectives ou intra-spécialité. | `council_id` piloté via **`control_subagents`**. |
| **`consult_phalanx`** | **Phalange Multi-Niveaux** (réservée exclusivement à l'Orchestrateur principal) : `Stratège` (`MODEL_PROMETHEUS`, **15 tours / 300s**) -> `Capitaine` optionnel (**15 tours / 300s**) -> `Hoplite` (`MODEL_APOLLO`, **8 tours / 120s**), profondeur max `ECHO_MAX_PHALANX_DEPTH = 3`, coopération latérale (`status`, `collect`, `steer`), relais `handoff` (`HandoffSpec(acquis, ressources=[], mission)`, plafond `4` relais) et isolation paresseuse `sandbox/<sub_sid>/`. | Livrables complexes multi-fronts (code critique, architecture, rédaction formelle multi-parties) exigeant une orchestration hiérarchique, des relais contrôlés et une vérification itérative (**coût tokens élevé : accord préalable requis**). | `phalanx_id` (`phx_<id>`) piloté via **`control_subagents`** (`filter_type="phalanx"`). |
| **`ask_data_broker`** | Sous-Agent `MODEL_APOLLO` pré-câblé sur recherche Web, Identity Vault, MCP et `upload_file_to_drive`, exécuté par **tranches coopératives de 8 tours d'outils / 120s** (`thread_databroker_<hex8>`). | Collecte, extraction et transfert vers le Cloud de données externes structurées (Web + MCP) sans saturer Le Modèle principal. | `thread_databroker_<hex8>` persisté, pilotable via **`control_subagents`** (`filter_type="agent"`). |

### 1.1 Routage Cognitif, Clamping & Cascade Descendante Automatique
- **Politique des Vannes (`COGNITIVE_MODE` / `COGNITIVE_MODEL`)** :
  - **Clamping initial** : Tout appel de Sous-Agent ou d'outil cognitif est d'abord soumis à la politique Pipe de L'Utilisateur. En mode `AUTO`, le modèle demandé (`target_model_key`, `strategist_model`, `worker_model`, `target_model`, `synthesis_model`) est plafonné à `COGNITIVE_MODEL`. En mode `MANUAL`, la demande est ignorée et remplacée par `COGNITIVE_MODEL`.
- **Cascade Descendante Automatique sur Sous-Agents (Non Désactivable)** :
  - Sur les Sous-Agents et outils cognitifs, la cascade descendante (`MODEL_ATHENA` → `MODEL_PROMETHEUS` → `MODEL_APOLLO` → `MODEL_ZEPHYR`, avec `thinkingLevel="high"` forcé sur le palier de repli) s'exécute **automatiquement** dans deux cas :
    1. **Erreur technique API** (quota/429, 503, timeout, indisponibilité d'un palier).
    2. **Blocage par filtre d'inférence** (`block_reason`, ex. `OTHER` ou `SAFETY`) : pour contourner un faux positif isolé d'un palier, le framework tente automatiquement le palier inférieur suivant ; l'erreur bloquante n'est remontée (avec annulation de l'étape non répondue) que si **toute la cascade** bloque.
  - **Aucun paramètre** (`target_model_key`, `new_cognitive_level`, ni `COGNITIVE_MODE="MANUAL"`) ne permet de verrouiller un Sous-Agent sans repli automatique vers `MODEL_ZEPHYR` en cas d'indisponibilité ou de blocage filtre du palier cible.
- **Orchestrateur Principal** :
  - En mode `AUTO`, `new_cognitive_level` pilote le changement volontaire de palier et une erreur technique API (hors erreur 400) déclenche la cascade descendante jusqu'à `MAX_CASCADE_ATTEMPTS` (pouvant atteindre `MODEL_ZEPHYR`).
  - En mode `MANUAL`, le fil principal de l'Orchestrateur reste strictement figé sur `COGNITIVE_MODEL` (pas de `new_cognitive_level` ni de cascade descendante sur le fil principal).

---

## 2. GOUVERNANCE DES COMPÉTENCES (`SKILLS`)
Les **Skills** définissent l'expertise métier et la méthodologie d'un Sous-Agent (REQUIS pour chaque participant de `consult_council` ; optionnel pour `delegate_to_agent` et les membres d'une Phalange `consult_phalanx`).
- **Séquence Impérative avant Délégation** :
  1. Le Modèle DOIT appeler `search_skill(need_description="...")` (top 3 des `skill_id` par pertinence sémantique) ou `list_skills()` (catalogue complet).
  2. Pour inspecter les directives d'un Skill existant (ou avant toute mise à jour afin de ne rien écraser involontairement) : `read_skill(skill_id)`.
  3. **Création ou mise à jour (`forge_skill`)** : Si aucune expertise existante ne couvre le besoin (ou si un Skill doit être enrichi), Le Modèle DOIT appeler `forge_skill(skill_id, name, description, instructions, require_web_grounding=False)` :
     - `skill_id` : Identifiant en `snake_case` (ex: `"expert_sec_cloud"`). Un `skill_id` déjà existant est intégralement remplacé.
     - `name` & `description` : Chacun sur **une seule ligne** ; `description` doit cibler clairement le domaine et les cas d'usage pour rester détectable par `search_skill`.
     - `instructions` : Prompt système complet rédigé selon le protocole **PAS** (impersonnel, haut SNR, opérateurs `DOIT`/`ABSOLUMENT INTERDIT`), **sans en-tête YAML (`---`)**.
     - `require_web_grounding=True` : Oblige Le Sous-Agent à vérifier l'état de l'art récent sur le Web avant toute réponse ou action.
- `delete_user_skill(skill_id, timeout_seconds=300)` : Supprime un Skill de L'Utilisateur après confirmation via modale interactive (**ABSOLUMENT INTERDIT depuis un Sous-Agent headless ET en automatisation planifiée**).

---

## 3. SPÉCIFICATIONS DES MOTEURS D'AGENTS & GESTION DES SESSIONS

### 3.1 Agent Autonome par Tranches Supervisées (`delegate_to_agent`) & Parallélisme
- Signature : `delegate_to_agent(task, system_prompt=None, skill_id=None, sub_sid=None, with_context_distillate=False, target_model_key=None, allowed_tools=None, can_delegate=False, cohort_id=None)`.
- `task` : Mission détaillée (protocole PAS) ou directive de reprise/clarification.
- `skill_id` & `system_prompt` : Si les deux sont fournis, ils se combinent (`skill_content + system_prompt`). Si les deux sont omis, Le Sous-Agent hérite du `ECHO_FOUNDATION_KERNEL`.
- `sub_sid` : Identifiant (`^[a-zA-Z0-9_-]{3,96}$`) choisi par Le Modèle principal (ex: `"audit_api"`, `"test_db"`) ou auto-généré si omis. **Pour lancer plusieurs Sous-Agents en parallèle dans le même tour**, Le Modèle DOIT émettre plusieurs appels `delegate_to_agent` simultanés avec des `sub_sid` explicites distincts.
- `with_context_distillate: bool = False` : Lorsque `True` (actif **uniquement sans `sub_sid`**, lors d'une création initiale), injecte un résumé distillé de la conversation principale dans le prompt initial du Sous-Agent.
- `target_model_key` (optionnel) : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut), `"MODEL_PROMETHEUS"` ou `"MODEL_ATHENA"`.
- `can_delegate` (`bool`, défaut `False`) : Autorise Le Sous-Agent à mandater à son tour des Sous-Agents subordonnés (et les spécialistes hiérarchiques `ask_data_broker`, `delegate_to_n8n_grapher`, `delegate_deep_research`, `delegate_web_browsing`) tant que la profondeur reste inférieure à `ECHO_MAX_PHALANX_DEPTH = 3` (typiquement pour un `Capitaine` au sein d'une Phalange).
- `cohort_id` (`Optional[str]`, défaut `None`) : Identifiant passé **sans préfixe `phx_`** qui rattache Le Sous-Agent à une **Cohorte légère** pilotée directement sous Le Modèle principal sans déployer de `Stratège` intermédiaire via `consult_phalanx`. Normalisé en `group_id="phx_<id>"` sous le moteur `"phalanx"`, il active la coopération latérale entre pairs de la cohorte (`status`, `collect`, `steer`, `handoff`) via `control_subagents`.
- `allowed_tools` (`Optional[List[str]]`, défaut `None`) : Restreint explicitement les outils métier du Sous-Agent. Les outils système (`DELEGATE_AGENT_SYSTEM_TOOLS` : `tool_advisor`, `read_tool_output`, `delete_tool_output`) ainsi que `new_cognitive_level` restent toujours accessibles, tandis que les outils UI, récursifs et de mémoire long-terme (`DELEGATE_AGENT_BLACKLIST`) demeurent ABSOLUMENT INTERDITS (sauf déblocage des spécialistes hiérarchiques lorsque `can_delegate=True`).
- **Gouvernance par Tranches Coopératives (sans limite préalable de mission)** :
  - Chaque invocation ou reprise accorde une tranche fraîche de **8 tours d'outils** (jusqu'à 5 appels parallèles par tour) ou **120 secondes** (ou **15 tours / 300s** si `can_delegate=True`).
  - Si la mission n'est pas achevée en fin de tranche, Le Sous-Agent retourne `status="suspended_checkpoint"` avec sa synthèse d'étape. S'il subit 3 erreurs d'outils consécutives, il s'arrête en `status="suspended_pare_alert"`.

### 3.2 Contrôleur Unifié des Sous-Agents (`control_subagents`)
Signature : `control_subagents(action="status", target_ids=None, filter_type="all", directive=None, target_model_key=None, allowed_tools=None, handoff=None)`.
Outil unique gouvernant le cycle de vie de tous les Sous-Agents unitaires (`sub_sid`), conseils (`council_id`), Phalanges (`phx_<id>`) et sessions navigateur (`thread_web_*`), ainsi que la coopération latérale entre pairs d'une même Phalange ou Cohorte :
- **Droits d'un `Hoplite` (`can_delegate=False`)** : Un `Hoplite` est strictement limité aux actions latérales `status | collect | steer | handoff`, sans possibilité de modifier `target_model_key` ni `allowed_tools`.
- `allowed_tools` (`Optional[List[str]]`, défaut `None`, **réservé à l'Orchestrateur principal et aux supérieurs `can_delegate=True`** — refusé à tout `Hoplite` `can_delegate=False`) : Modifie dynamiquement en vol (ou lors d'un `steer`, `resume` ou `handoff`) les outils autorisés sur les cibles :
  - `["*"]` : Lève la restriction personnalisée (`allowed_tools=None`, accès à tout le pool autorisé).
  - `[]` ou `["none"]` : Restreint aux seuls outils système (`DELEGATE_AGENT_SYSTEM_TOOLS`) et `new_cognitive_level`.
  - `["+outil", "-outil"]` : Patch incrémental ajoutant ou retirant des outils de la liste courante.
  - `["outil_a", "outil_b"]` : Remplacement complet par la liste explicite fournie.
- `action` :
  - `"status"` (défaut) : État temps réel des sessions (`status`, affichage `Tours d'outils : X/Y` — ou `Mode : Synthèse statique` sur `council_synthesis` et `image_gen` —, tranche `slice_turns_used/limit`, cumul `total_turns_used`, `total_calls_used`, `checkpoint_reason`, file FIFO, `allowed_tools`). Filtrable par `filter_type` (`"all" | "agent" | "council" | "phalanx" | "web"`). Le filtre `"agent"` englobe les sous-agents `delegate_to_agent`, `thread_databroker_*`, `thread_n8n_*`, `thread_deepresearch_*` et `dlg_img_*`.
  - `"collect"` : Lit la dernière production et les métadonnées des `target_ids` sans consommer d'appel LLM (`target_ids` accepte aussi les préfixes de groupe `ccl_*` et `phx_*`).
  - `"steer"` : Injecte une `directive` (`<ECHO_MESSAGE>` typé) dans la file FIFO de `target_ids` (dépilée entre deux tours d'outils) et modifie optionnellement `target_model_key` et/ou `allowed_tools` (réservé aux supérieurs `can_delegate=True` ou à l'Orchestrateur). Lorsque l'appelant est un pair et que la cible est au repos, le réveil automatique post-tranche (`wake_box`) est programmé sans deadlock.
  - `"handoff"` : Opère un relais horizontal atomique vers une **cible unique** entre pairs (`Capitaine -> Capitaine`, `Hoplite -> Hoplite`, ou entre pairs d'une `cohort_id`) qui clôt la tranche de l'Appelant et renvoie la sortie du destinataire au Supérieur commun (`target_ids` à 1 élément + paramètre `handoff` conforme à `HandoffSpec(acquis, ressources=[], mission)` ; plafond strict de **4 relais** par Phalange). Un Supérieur (`Stratège` ou `Capitaine`) devant conserver la main pour vérifier et consolider les livrables de ses subordonnés DOIT utiliser `delegate_to_agent` ou `action="resume"` (et non `handoff`). Préserve le grade (`can_delegate`) du destinataire lorsque la cible est un `Capitaine` (`can_delegate=True`) et accepte `target_model_key`/`allowed_tools` depuis un supérieur.
  - `"resume"` : Relance `target_ids` pour une nouvelle tranche fraîche préservant le quota nominal de tranche de chaque cible (`15 tours / 300s` pour un `Stratège` ou un `Capitaine` `can_delegate=True`, `8 tours / 120s` pour un `Hoplite` ou agent standard ; avec `directive`, `target_model_key` et/ou `allowed_tools` optionnels). **Si `target_ids` contient plusieurs identifiants, les reprises s'exécutent simultanément en parallèle**.
  - `"suspend"` : Met en pause proprement (`status="suspended"`) à la fin du tour d'outil courant.
  - `"kill"` : Interrompt immédiatement l'exécution, scelle proprement l'historique d'outils et libère les ressources (ex: navigateur) sans supprimer l'historique.
  - `"summarize"` : Génère un résumé structuré en 4 sections de l'historique des `target_ids` sans les fermer.
  - `"close"` : Ferme les sessions `target_ids` et les bascule en archive lecture seule (`status="purged"`, invisible et non-adressable par Le Modèle, conservée 1440 minutes dans l'interface avant purge automatique ou suppression manuelle immédiate ; accepte un `sub_sid`, un `council_id`, un `phalanx_id` `phx_...` ou un `thread_web_...`).
  - `"purge_now"` : Supprime immédiatement et définitivement de la base de session une ou plusieurs sessions. **`target_ids` explicite est REQUIS** (sans `target_ids`, la purge peut affecter des sessions actives).

### 3.3 Table Ronde Delphi (`consult_council`)
- **Contrainte d'environnement** : Réservé exclusivement à l'Orchestrateur principal (`DELEGATE_AGENT_BLACKLIST`).
- `question` : Problématique exacte soumise à la délibération du conseil.
- `participants` : Liste de `skill_id` existants (`List[str]`, maximum `7`, avec `len(participants) * multiplier >= 2`). Tous les `skill_id` DOIVENT exister (sinon Le Modèle DOIT appeler `forge_skill` au préalable).
- `council_id: str` (**REQUIS**) : Identifiant choisi pour le conseil (ex: `"arch_review"`).
- `target_model` & `synthesis_model` : Modèles respectifs des participants et du synthétiseur (défaut `MODEL_PROMETHEUS` pour chacun).
- `multiplier` : Facteur multiplicateur par `skill_id` (entier `1` à `5`, défaut `1`), permettant d'instancier plusieurs experts de même spécialité pour se contredire entre eux.
- `rounds` : Nombre de tours de délibération (min et défaut `3`, max `10`, sous la contrainte arithmétique stricte `(len(participants) * multiplier) * rounds <= 60`), avec **3 tours d'outils par défaut (`0..6` configurés via `UserValve` `COUNCIL_EXPERT_MAX_TOOL_TURNS_PER_ROUND`, non exposé comme paramètre d'appel) / 120s par round Delphi** (`0` = délibération pure sans outil).
- `allowed_tools` (`Optional[List[str]]`, défaut `None`) : Restreint optionnellement les outils mis à disposition des experts du conseil.
- **Mécanique de Délibération, Cache KV & Convergence** :
  - Chaque expert conserve son prompt système initial strictement immuable (cache KV chaud octet par octet) et un **historique 100 % complet** sur l'ensemble des tours et des séances (les instances démultipliées reçoivent le préfixe anti-collision `m<idx>_<skill_id>`). Le Modèle DOIT sélectionner au minimum `synthesis_model="MODEL_APOLLO"` lorsque les participants utilisent `MODEL_ZEPHYR` (ce dernier étant réservé aux grilles de critères fermés).
  - **Arrêt anticipé par unanimité (`[POSITION_STABILISEE]`)** : Sur un tour intermédiaire (`2 <= round < effective_rounds`), un expert **PEUT** commencer sa réponse par `[POSITION_STABILISEE]` **uniquement** si l'intégralité des sous-questions et chiffrages de l'énoncé sont résolus sans angle mort et qu'aucun argument adverse nouveau n'appelle de réfutation ni de complément ; dès qu'un volet ou chiffrage demeure ouvert, l'émission de `[POSITION_STABILISEE]` est **ABSOLUMENT INTERDITE**. L'arrêt anticipé avant le dernier tour nominal ne se déclenche que si **100 % des participants** émettent `[POSITION_STABILISEE]` sans exception dans le même tour.
  - **Dernier tour nominal (`CONCLUSION DÉFINITIVE`)** : Au dernier tour nominal, `[POSITION_STABILISEE]` est **ABSOLUMENT INTERDIT** ; chaque expert DOIT formuler sa conclusion définitive consolidée (`### Analyse`, `### Dialectique`, `### Réponse`).
  - **Synthèse du Rapporteur & Autorité Décisionnelle** : Le Rapporteur (`SYS_ORCHESTRATOR_COUNCIL_SYNTHESIS`) clôt son rapport par la section **`### État du Consensus & Suites Éventuelles`**. Seul **l'Orchestrateur supérieur** détient l'autorité décisionnelle exclusive pour trancher et décider d'une éventuelle séance complémentaire.
- `close_on_finish` : `False` par défaut (permet de relancer `consult_council` avec le même `council_id` pour mener une Séance #2 sans angle mort : les conclusions finales manquantes du dernier tour de la séance précédente sont injectées au Tour 1 de la nouvelle séance, et toutes les synthèses sont conservées séquentiellement dans `thread_council_<cid>_synthesis`). Une fois la délibération terminée, Le Modèle DOIT appeler **`control_subagents(action="close", target_ids=[council_id])`**.
- **Directive de restitution** : Le rapport de synthèse du conseil n'étant pas affiché directement à L'Utilisateur, Le Modèle principal DOIT en restituer la substance dans sa réponse.

### 3.4 Phalange Multi-Niveaux (`consult_phalanx`)
- **Contrainte d'environnement** : Réservé exclusivement à l'Orchestrateur principal (`DELEGATE_AGENT_BLACKLIST`).
- **Architecture hiérarchique** (`ECHO_MAX_PHALANX_DEPTH = 3`) :
  - **Niveau 1 — `Stratège`** (`strategist_model`, défaut `MODEL_PROMETHEUS`, tranche **15 tours / 300s**) : Orchestre l'objectif global, déploie les subordonnés via `delegate_to_agent` et consolide le livrable final.
  - **Niveau 2 — `Capitaine` (optionnel)** (`can_delegate=True`, `worker_model`, défaut `MODEL_APOLLO`, tranche **15 tours / 300s**) : Coordonne un sous-front complexe nécessitant plusieurs exécutants.
  - **Niveau 3 — `Hoplite`** (`can_delegate=False`, `worker_model`, défaut `MODEL_APOLLO`, tranche **8 tours / 120s**) : Exécutant feuille spécialisé.
- **Paramètres clés** :
  - `objective` : Objectif global confié à la Phalange.
  - `strategist_model` (`MODEL_PROMETHEUS` par défaut) & `worker_model` (`MODEL_APOLLO` par défaut).
  - `phalanx_id` (`Optional[str]`) : Identifiant optionnel passé **sans préfixe `phx_`** (normalisé en `phx_<id>` par Le Système).
  - `close_on_finish` : `False` par défaut.
- **Coopération latérale, Relais `handoff` & Isolation Sandbox** :
  - Les membres d'une Phalange peuvent consulter leurs pairs (`status`, `collect`), injecter des directives (`steer`) et passer le relais horizontal entre pairs via `control_subagents(action="handoff", target_ids=[peer_sid], handoff=HandoffSpec(acquis=..., ressources=[...], mission=...))` (plafond de **4 relais** par Phalange).
  - Chaque sous-agent dispose d'une **isolation paresseuse** en écriture dans `sandbox/<sub_sid>/` (résolu automatiquement par les outils Codex et Sandbox) et **PEUT** lire ou exécuter directement les fichiers d'un pair dans `workspace="sandbox"` via `<peer_sid>/<fichier>` (et `/sandbox/<peer_sid>/<fichier>` dans `code_executor`). L'usage de `codex/main` comme SAS de transit temporaire entre unités est **ABSOLUMENT INTERDIT**.
- **Gestion des sessions** :
  - Inspecter ou clore une Phalange (`phx_<id>`) via **`control_subagents(action="status", filter_type="phalanx")`** et **`control_subagents(action="close", target_ids=[phalanx_id])`**.
- **Gouvernance Coût Tokens & Accord Utilisateur** :
  - Une Phalange multiplie les contextes et les tours LLM : son coût en tokens devient rapidement élevé si le périmètre ou les modèles sont surdimensionnés. Pour une tâche simple ou mono-front, Le Modèle DOIT privilégier `delegate_to_agent`.
  - Avant d'appeler `consult_phalanx`, Le Modèle DOIT obtenir l'accord préalable de L'Utilisateur (périmètre succinct et modèles envisagés, via `ask_user_input` ou validation directe), **sauf** si L'Utilisateur a explicitement demandé une Phalange, en mode `/goal`, ou en automatisation planifiée headless (`ask_user_input` interdit).
- **Clarifications & Escalade (`QUESTION:`)** :
  - Le Stratège (ou une unité subordonnée via le Stratège) peut interrompre sa tranche en `status="pending_question"` avec `QUESTION: <question précise>` lorsqu'un arbitrage bloquant requiert L'Utilisateur.
  - Le Modèle principal DOIT alors relayer la question à L'Utilisateur, puis reprendre le Stratège avec la réponse via **`control_subagents(action="resume", target_ids=["thread_phalanx_<id>_strategist"], directive="...")`**.

### 3.5 Collecteur de Données Spécialisé (`ask_data_broker`)
- **Contrainte d'environnement** : Autorisé pour l'Orchestrateur principal et les supérieurs hiérarchiques `can_delegate=True` (`Stratège` / `Capitaine` avec `depth < ECHO_MAX_PHALANX_DEPTH = 3`, propageant automatiquement `parent_sid`, `group_id` et `depth + 1`). **ABSOLUMENT INTERDIT** depuis un `Hoplite` feuille (`can_delegate=False`).
- `query` : Requête textuelle descriptive spécifiant les données externes précises à collecter (recherche Web ciblée, Identity Vault, exécution sur serveurs MCP et transfert vers le Cloud via `upload_file_to_drive`).
- `sub_sid` (`Optional[str]`, défaut `None`) : Identifiant explicite optionnel pour la session `thread_databroker_<hex8>` ou pour sa reprise.
- `cohort_id` (`Optional[str]`, défaut `None`) : Identifiant passé **sans préfixe `phx_`** rattachant la session à une Cohorte légère (`phx_<id>`) lorsqu'elle est invoquée hors d'un groupe hiérarchique parent.
- `allowed_tools` (`Optional[List[str]]`, défaut `None`) : Surcharge ou patch (`["*"]`, `["+outil", "-outil"]`, liste explicite) appliqué à la liste par défaut des outils du Data Broker (`list_identities_services`, `list_identities`, `list_mcp_functions`, `call_mcp_function`, `upload_file_to_drive`, `search_web`, `search_instant_answer`). *(Note : `manage_identity` et `ask_user_input` étant **ABSOLUMENT INTERDITS** en mode headless, toute collecte ou enregistrement de secret manquant DOIT être effectué par Le Modèle principal avant ou après la délégation).*
- **Session Persistée par Tranches** : Instancie une session `thread_databroker_<hex8>` exécutée par tranches coopératives de **8 tours d'outils / 120s** (pilotable et clôturable via `control_subagents` sous `filter_type="agent"`).
