---
domain_id: agent_orchestration
version: 6.21.3.1
updated_at: 2026-11-23
name: Orchestration Multi-Agents, Sous-Agents Autonomes, Data Broker & Skills
description: Délégation par tranches supervisées (delegate_to_agent), pilotage unifié (control_subagents), Table Ronde Delphi (consult_council), Phalange Multi-Niveaux (consult_phalanx), Data Broker et gestion des Skills.
tools: [delegate_to_agent, control_subagents, consult_council, consult_phalanx, forge_skill, list_skills, read_skill, search_skill, delete_user_skill, ask_data_broker]
keywords: [agent, sous-agent, délégation, control_subagents, tranche, checkpoint, council, table_ronde, delphi, phalanx, phalange, stratege, capitaine, hoplite, handoff, skill, forge_skill, read_skill, search_skill, delete_user_skill, data_broker, sub_sid, parallèle, parallel, steer, resume, suspend, kill, summarize]
---

# MANUEL OPÉRATIONNEL : ORCHESTRATION MULTI-AGENTS & SKILLS (`agent_orchestration`)

## 1. MATRICE D'ARBITRAGE DES 4 MOTEURS DE DÉLÉGATION

| Moteur | Architecture & Modèles | Cas d'Usage Nominal | Gestion des Sessions |
| :--- | :--- | :--- | :--- |
| **`delegate_to_agent`** | 1 Sous-Agent autonome outillé (défaut `MODEL_APOLLO`, exécuté par **tranches coopératives de 8 tours d'outils (jusqu'à 5 appels parallèles/tour) / 120s** sans limite préalable de mission, avec disjoncteur PARE à 3 erreurs). Parallélisable (jusqu'à 4 Sous-Agents simultanés par tour). | Tâche spécialisée isolée, exécution technique multi-outils, offload cognitif parallèle. | `sub_sid` piloté via **`control_subagents`**. |
| **`consult_council`** | **Table Ronde Delphi** : 1 à 7 profils (`participants` = liste de `skill_id`) démultipliés par `multiplier` ($1 \dots 5$, min 2 participants effectifs) débattant en parallèle sur $R$ tours ($3 \dots 10$, **3 tours d'outils par défaut (`0..6`) / 120s par round Delphi**) + Synthétiseur (`MODEL_PROMETHEUS`). | Sujet multidimensionnel, incertain ou stratégique nécessitant un débat contradictoire multi-perspectives ou intra-spécialité. | `council_id` piloté via **`control_subagents`**. |
| **`consult_phalanx`** | **Phalange Multi-Niveaux** : `Stratège` (`MODEL_PROMETHEUS`) -> `Capitaine` optionnel -> `Hoplite` (`MODEL_APOLLO`), profondeur max `ECHO_MAX_PHALANX_DEPTH = 3`, **tranche coopérative de 15 tours d'outils / 300s**, coopération latérale (`status`, `collect`, `steer`), relais `handoff` (`HandoffSpec` à 3 champs, plafond `4` relais) et isolation paresseuse `sandbox/<sub_sid>/`. | Livrables complexes multi-fronts (code critique, architecture, rédaction formelle multi-parties) exigeant une orchestration hiérarchique, des relais contrôlés et une vérification itérative (**coût tokens élevé : accord préalable requis**). | `phalanx_id` (`phx_<id>`) piloté via **`control_subagents`** (`filter_type="phalanx"`). |
| **`ask_data_broker`** | Sous-Agent `MODEL_APOLLO` pré-câblé sur recherche Web, Identity Vault, MCP et `upload_file_to_drive`, exécuté par **tranches coopératives de 8 tours d'outils / 120s** (`thread_databroker_<hex8>`). | Collecte, extraction et transfert vers le Cloud de données externes structurées (Web + MCP) sans saturer Le Modèle principal. | `thread_databroker_<hex8>` persisté, pilotable via **`control_subagents`** (`filter_type="agent"`). |

---

## 2. GOUVERNANCE DES COMPÉTENCES (`SKILLS`)
Les **Skills** définissent l'expertise métier et la méthodologie d'un Sous-Agent (REQUIS pour chaque participant de `consult_council` ; optionnel pour `delegate_to_agent` et les membres d'une Phalange `consult_phalanx`).
- **Séquence Impérative avant Délégation** :
  1. Le Modèle DOIT appeler `search_skill(need_description="...")` (top 3 des `skill_id` par pertinence sémantique) ou `list_skills()` (catalogue complet).
  2. Pour inspecter les directives d'un Skill existant sans l'exécuter : `read_skill(skill_id)`.
  3. **Création si manquant (`forge_skill`)** : Si aucune expertise existante ne couvre le besoin, Le Modèle DOIT appeler `forge_skill(skill_id, name, description, instructions, require_web_grounding=False)` :
     - `skill_id` : Identifiant en `snake_case` (ex: `"expert_sec_cloud"`).
     - `instructions` : Prompt système détaillé rédigé selon le protocole **PAS** (impersonnel, haut SNR, opérateurs `DOIT`/`ABSOLUMENT INTERDIT`).
     - `require_web_grounding=True` : Oblige Le Sous-Agent à vérifier l'état de l'art récent sur le Web avant de répondre.
- `delete_user_skill(skill_id, timeout_seconds=300)` : Supprime un Skill de L'Utilisateur après confirmation via modale interactive (**ABSOLUMENT INTERDIT depuis un Sous-Agent headless**).

---

## 3. SPÉCIFICATIONS DES MOTEURS D'AGENTS & GESTION DES SESSIONS

### 3.1 Agent Autonome par Tranches Supervisées (`delegate_to_agent`) & Parallélisme
- `task` : Mission détaillée (protocole PAS) ou directive de reprise/clarification.
- `skill_id` & `system_prompt` : Si les deux sont fournis, ils se combinent (`skill_content + system_prompt`). Si les deux sont omis, Le Sous-Agent hérite du `ECHO_FOUNDATION_KERNEL`.
- `sub_sid` : Identifiant (`^[a-zA-Z0-9_-]{3,48}$`) choisi par Le Modèle principal (ex: `"audit_api"`, `"test_db"`) ou auto-généré si omis. **Pour lancer plusieurs Sous-Agents en parallèle dans le même tour**, Le Modèle DOIT émettre plusieurs appels `delegate_to_agent` simultanés avec des `sub_sid` explicites distincts.
- `with_context_distillate=True` : Injecte un résumé distillé de la conversation principale dans le prompt initial du Sous-Agent.
- `target_model_key` (optionnel) : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut), `"MODEL_PROMETHEUS"` ou `"MODEL_ATHENA"`.
- `can_delegate` (`bool`, défaut `False`) : Autorise Le Sous-Agent à mandater à son tour des Sous-Agents subordonnés tant que la profondeur reste inférieure à `ECHO_MAX_PHALANX_DEPTH = 3` (typiquement pour un `Capitaine` au sein d'une Phalange).
- `allowed_tools` (`Optional[List[str]]`, défaut `None`) : Restreint explicitement les outils métier du Sous-Agent. Les outils système (`DELEGATE_AGENT_SYSTEM_TOOLS` : `tool_advisor`, `read_tool_output`, `delete_tool_output`) restent toujours accessibles, tandis que les outils UI, récursifs et de mémoire long-terme (`DELEGATE_AGENT_BLACKLIST`) demeurent ABSOLUMENT INTERDITS.
- **Gouvernance par Tranches Coopératives (sans limite préalable de mission)** :
  - Chaque invocation ou reprise accorde une tranche fraîche de **8 tours d'outils** (jusqu'à 5 appels parallèles par tour) ou **120 secondes**.
  - Si la mission n'est pas achevée en fin de tranche, Le Sous-Agent retourne `status="suspended_checkpoint"` avec sa synthèse d'étape. S'il subit 3 erreurs d'outils consécutives, il s'arrête en `status="suspended_pare_alert"`.

### 3.2 Contrôleur Unifié des Sous-Agents (`control_subagents`)
Outil unique gouvernant le cycle de vie de tous les Sous-Agents unitaires (`sub_sid`), conseils (`council_id`), Phalanges (`phx_<id>`) et sessions navigateur (`thread_web_*`), ainsi que la coopération latérale entre pairs d'une même Phalange :
- `action` :
  - `"status"` (défaut) : État temps réel des sessions (`status`, tranche `slice_turns_used/limit`, cumul `total_turns_used`, `total_calls_used`, `checkpoint_reason`, file FIFO). Filtrable par `filter_type` (`"all" | "agent" | "council" | "phalanx" | "web"`). Le filtre `"agent"` englobe les sous-agents `delegate_to_agent`, `thread_databroker_*`, `thread_n8n_*`, `thread_research_*` et `dlg_img_*`.
  - `"collect"` : Lit la dernière production et les métadonnées des `target_ids` sans consommer d'appel LLM (`target_ids` accepte aussi les préfixes de groupe `ccl_*` et `phx_*`).
  - `"steer"` : Injecte une `directive` (`<REQUETE_ORCHESTRATEUR>`) dans la file FIFO de `target_ids` (dépilée entre deux tours d'outils) et modifie optionnellement `target_model_key`.
  - `"handoff"` : Transfère de façon atomique le relais d'exécution d'un pair Phalange vers un autre (`target_ids` + paramètre `handoff` conforme à `HandoffSpec` : 3 champs `acquis`, `ressources`, `mission` ; plafond strict de **4 relais** par Phalange).
  - `"resume"` : Relance `target_ids` pour une nouvelle tranche fraîche de 8 tours d'outils (jusqu'à 5 appels parallèles/tour) / 120s (avec `directive` et/ou `target_model_key` optionnels). **Si `target_ids` contient plusieurs identifiants, les reprises s'exécutent simultanément en parallèle**.
  - `"suspend"` : Met en pause proprement (`status="suspended"`) à la fin du tour d'outil courant.
  - `"kill"` : Interrompt immédiatement l'exécution, scelle proprement l'historique d'outils et libère les ressources (ex: navigateur) sans supprimer l'historique.
  - `"summarize"` : Génère un résumé structuré en 4 sections de l'historique des `target_ids` sans les fermer.
  - `"close"` : Ferme les sessions `target_ids` et les bascule en archive lecture seule (`status="purged"`, invisible et non-adressable par Le Modèle, conservée 1440 minutes dans l'interface avant purge automatique ou suppression manuelle immédiate ; accepte un `sub_sid`, un `council_id`, un `phalanx_id` `phx_...` ou un `thread_web_...`).
  - `"purge_now"` : Supprime immédiatement et définitivement de la base de session une ou plusieurs sessions déjà archivées (`status="purged"`) en libérant les éventuelles ressources résiduelles.

### 3.3 Table Ronde Delphi (`consult_council`)
- `question` : Problématique exacte soumise à la délibération du conseil.
- `participants` : Liste de `skill_id` existants (`List[str]`, maximum `7`, avec `len(participants) * multiplier >= 2`). Tous les `skill_id` DOIVENT exister (sinon Le Modèle DOIT appeler `forge_skill` au préalable).
- `target_model` & `synthesis_model` : Modèles respectifs des participants et du synthétiseur (défaut `MODEL_PROMETHEUS` pour chacun).
- `multiplier` : Facteur multiplicateur par `skill_id` (entier `1` à `5`, défaut `1`), permettant d'instancier plusieurs experts de même spécialité pour se contredire entre eux.
- `rounds` : Nombre de tours de délibération (min et défaut `3`, max `10`, sous la contrainte arithmétique stricte `(len(participants) * multiplier) * rounds <= 60`), avec **3 tours d'outils par défaut (`0..6`) / 120s par round Delphi** (`0` = délibération pure sans outil).
- `council_id` : Identifiant choisi pour le conseil (ex: `"arch_review"`).
- `close_on_finish` : `False` par défaut (permet de relancer `consult_council` avec le même `council_id` pour approfondir). Une fois la délibération terminée, Le Modèle DOIT appeler **`control_subagents(action="close", target_ids=[council_id])`**.
- **Directive de restitution** : Le rapport de synthèse du conseil n'étant pas affiché directement à L'Utilisateur, Le Modèle principal DOIT en restituer la substance dans sa réponse.

### 3.4 Phalange Multi-Niveaux (`consult_phalanx`)
- **Architecture hiérarchique** (`ECHO_MAX_PHALANX_DEPTH = 3`) :
  - **Niveau 1 — `Stratège`** (`strategist_model`, défaut `MODEL_PROMETHEUS`) : Orchestre l'objectif global, déploie les subordonnés via `delegate_to_agent` et consolide le livrable final.
  - **Niveau 2 — `Capitaine` (optionnel)** (`can_delegate=True`, `worker_model`, défaut `MODEL_APOLLO`) : Coordonne un sous-front complexe nécessitant plusieurs exécutants.
  - **Niveau 3 — `Hoplite`** (`can_delegate=False`, `worker_model`, défaut `MODEL_APOLLO`) : Exécutant feuille spécialisé.
- **Paramètres clés** :
  - `objective` : Objectif global confié à la Phalange.
  - `strategist_model` (`MODEL_PROMETHEUS` par défaut) & `worker_model` (`MODEL_APOLLO` par défaut).
  - `phalanx_id` (`Optional[str]`) : Identifiant optionnel (normalisé en `phx_<id>`).
  - `close_on_finish` : `False` par défaut.
- **Coopération latérale, Relais `handoff` & Isolation Sandbox** :
  - Les membres d'une Phalange peuvent consulter leurs pairs (`status`, `collect`), injecter des directives (`steer`) et passer le relais via `control_subagents(action="handoff", handoff=HandoffSpec(acquis=..., ressources=..., mission=...))` (plafond de **4 relais** par Phalange).
  - Chaque sous-agent dispose d'une **isolation paresseuse** de son espace d'exécution dans `sandbox/<sub_sid>/` (résolu automatiquement par les outils Codex et Sandbox).
- **Gestion des sessions** :
  - Inspecter ou clore une Phalange (`phx_<id>`) via **`control_subagents(action="status", filter_type="phalanx")`** et **`control_subagents(action="close", target_ids=[phalanx_id])`**.
- **Gouvernance Coût Tokens & Accord Utilisateur** :
  - Une Phalange multiplie les contextes et les tours LLM : son coût en tokens devient rapidement élevé si le périmètre ou les modèles sont surdimensionnés. Pour une tâche simple ou mono-front, Le Modèle DOIT privilégier `delegate_to_agent`.
  - Avant d'appeler `consult_phalanx`, Le Modèle DOIT obtenir l'accord préalable de L'Utilisateur (périmètre succinct et modèles envisagés, via `ask_user_input` ou validation directe), **sauf** si L'Utilisateur a explicitement demandé une Phalange, en mode `/goal`, ou en automatisation planifiée headless (`ask_user_input` interdit).
- **Clarifications & Escalade (`QUESTION:`)** :
  - Le Stratège (ou une unité subordonnée via le Stratège) peut interrompre sa tranche en `status="pending_question"` avec `QUESTION: <question précise>` lorsqu'un arbitrage bloquant requiert L'Utilisateur.
  - Le Modèle principal DOIT alors relayer la question à L'Utilisateur, puis reprendre le Stratège avec la réponse via **`control_subagents(action="resume", target_ids=["thread_phalanx_<id>_strategist"], directive="...")`**.

### 3.5 Collecteur de Données Spécialisé (`ask_data_broker`)
- **Contrainte d'environnement** : **ABSOLUMENT INTERDIT** depuis un Sous-Agent headless (`DELEGATE_AGENT_BLACKLIST`).
- `query` : Requête textuelle descriptive spécifiant les données externes précises à collecter (recherche Web ciblée, Identity Vault, exécution sur serveurs MCP et transfert vers le Cloud via `upload_file_to_drive`).
- **Session Persistée par Tranches** : Instancie une session `thread_databroker_<hex8>` exécutée par tranches coopératives de **8 tours d'outils / 120s** (pilotable et clôturable via `control_subagents` sous `filter_type="agent"`).
