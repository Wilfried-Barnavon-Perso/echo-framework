---
domain_id: agent_orchestration
version: 5.231.22.1
updated_at: 2026-10-27
name: Orchestration Multi-Agents, Sous-Agents Autonomes, Data Broker & Skills
description: Délégation à un sous-agent outillé (delegate_to_agent), Table Ronde Delphi (consult_council), Ouvriers Supervisés (consult_supervised_workers), Data Broker et gestion des Skills.
tools: [delegate_to_agent, list_agent_sessions, close_agent_session, summarize_agent_session, consult_council, consult_supervised_workers, list_councils, close_council, list_supervised_tasks, close_supervised_task, forge_skill, list_skills, read_skill, search_skill, delete_user_skill, ask_data_broker]
keywords: [agent, sous-agent, délégation, council, table_ronde, delphi, supervised_workers, superviseur, workers, skill, forge_skill, data_broker, sub_sid]
---

# MANUEL OPÉRATIONNEL : ORCHESTRATION MULTI-AGENTS & SKILLS (`agent_orchestration`)

## 1. MATRICE D'ARBITRAGE DES 4 MOTEURS DE DÉLÉGATION
Conformément au principe **PARE** du Kernel, dès qu'une action nécessite potentiellement plus de 3 appels d'outils successifs, un offload cognitif ou une confrontation d'experts, l'Orchestrateur **DOIT** déléguer à l'un des 4 moteurs suivants :

| Moteur | Architecture & Modèles | Cas d'Usage Nominal | Gestion des Sessions |
| :--- | :--- | :--- | :--- |
| **`delegate_to_agent`** | 1 Sous-Agent autonome outillé (défaut `MODEL_APOLLO`, budget 25 appels d'outils par invocation). | Tâche spécialisée isolée, exécution technique multi-outils, offload cognitif pour protéger le contexte principal. | `sub_sid` (`list_agent_sessions`, `summarize_agent_session`, `close_agent_session`). |
| **`consult_council`** | **Table Ronde Delphi** : 2 à 5 experts (`participants` = liste de `skill_id`) débattant en parallèle sur $R$ tours + Synthétiseur (`MODEL_PROMETHEUS` `thinking="high"`). | Sujet multidimensionnel, incertain ou stratégique nécessitant un débat contradictoire multi-perspectives. | `council_id` (`list_councils`, `close_council`). |
| **`consult_supervised_workers`** | **Boucle Critique/Correction** : $N$ sous-agents Ouvriers (`Workers`, `MODEL_APOLLO`) + 1 Superviseur Critique (`MODEL_PROMETHEUS` `thinking="high"`) + 1 Consolidateur (`MODEL_APOLLO`). | Livrables complexes exigeant une qualité irréprochable (code critique, architecture, rédaction formelle multi-parties) avec vérification et correction itérative. | `task_id` (`list_supervised_tasks`, `close_supervised_task`). |
| **`ask_data_broker`** | Sous-agent `MODEL_APOLLO` pré-câblé sur les outils de recherche Web, Identity Vault et MCP. | Collecte et extraction rapide de données externes structurées (Web + MCP) sans saturer l'Orchestrateur. | Stateless (pas de session persistée). |

---

## 2. GOUVERNANCE DES COMPÉTENCES (`SKILLS`)
Les **Skills** définissent l'expertise métier et la méthodologie d'un sous-agent (requis notamment pour chaque participant de `consult_council` et pour les `workers` de `consult_supervised_workers`).
- **Séquence Impérative avant Délégation** :
  1. Appeler `search_skill(need_description="...")` (retourne le top 3 des `skill_id` par pertinence sémantique) ou `list_skills()` (catalogue complet).
  2. Pour inspecter les directives d'un Skill existant sans l'exécuter : `read_skill(skill_id)`.
  3. **Création si manquant (`forge_skill`)** : Si aucune expertise existante ne couvre le besoin, appeler `forge_skill(skill_id, name, description, instructions, require_web_grounding=False)` :
     - `skill_id` : Identifiant en `snake_case` (ex: `"expert_sec_cloud"`).
     - `instructions` : Prompt système détaillé rédigé selon le protocole **PAS** (impersonnel, haut SNR, opérateurs `DOIT`/`INTERDIT`).
     - `require_web_grounding=True` : Ajoute une directive obligeant le sous-agent à vérifier systématiquement l'état de l'art récent sur le Web avant de répondre.
- `delete_user_skill(skill_id, timeout_seconds=300)` : Supprime un Skill utilisateur après confirmation via modale interactive (interdit depuis un sous-agent headless).

---

## 3. SPÉCIFICATIONS DES MOTEURS D'AGENTS & GESTION DES SESSIONS

### 3.1 Agent Autonome (`delegate_to_agent`)
`delegate_to_agent(task, system_prompt=None, skill_id=None, sub_sid=None, with_context_distillate=False, target_model_key=None)` :
- `task` : Mission détaillée (protocole PAS) ou réponse de clarification si reprise d'un `sub_sid` en statut `pending_question`.
- `skill_id` & `system_prompt` : Si les deux sont fournis, ils se combinent (`skill_content + system_prompt`). Si les deux sont omis, le sous-agent hérite du `ECHO_FOUNDATION_KERNEL`.
- `sub_sid` : Omettre pour créer une nouvelle session ; fournir le `sub_sid` existant pour reprendre et poursuivre le travail du même agent avec son historique complet.
- `with_context_distillate=True` : Injecte un résumé distillé de la conversation principale dans le prompt initial du sous-agent.
- `target_model_key` : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut) ou `"MODEL_PROMETHEUS"`.
- **Administration des sessions d'agents** :
  - `list_agent_sessions()` : Liste toutes les sessions de sous-agents actives dans la conversation (`sub_sid`, type `agent`/`expert`/`council`/`supervisor`, nombre d'étapes).
  - `summarize_agent_session(sub_sid)` : Génère via `MODEL_APOLLO` un résumé structuré en 4 sections (*Tâche initiale, Outils utilisés, Résultats clés, Conclusion*) de l'historique d'un sous-agent sans le fermer.
  - `close_agent_session(sub_sid)` : Ferme définitivement la session `sub_sid` et purge son historique de session.
    - **Règle de cycle de vie** : Ne DOIT être appelée QUE lorsque la tâche du sous-agent est réputée définitivement close et validée. Tant qu'il est potentiellement utile de reprendre son travail, d'approfondir ou de lui poser une question de clarification, la session DOIT rester ouverte afin de préserver son fil d'outils et de permettre sa reprise via `delegate_to_agent(..., sub_sid=sub_sid)`.

### 3.2 Table Ronde Delphi (`consult_council`, `list_councils`, `close_council`)
`consult_council(question, participants, council_id, target_model="MODEL_PROMETHEUS", synthesis_model="MODEL_PROMETHEUS", rounds=None, close_on_finish=False)` :
- `participants` : Liste de `skill_id` existants (`List[str]`, **minimum 2**, maximum `COUNCIL_MAX_PARTICIPANTS = 5`). Tous les `skill_id` DOIVENT exister (sinon appeler `forge_skill` au préalable).
- `rounds` : Nombre de tours de délibération (défaut `3`, max `5`, avec `len(participants) * rounds <= 30`). Chaque expert dispose d'un budget de 3 appels d'outils par tour.
- `council_id` : Identifiant choisi pour le conseil (ex: `"arch_review"`).
- `close_on_finish` : `False` par défaut (permet de relancer `consult_council` avec le même `council_id` pour approfondir). Une fois la délibération terminée, appeler **`close_council(council_id)`** (et `list_councils()` pour lister les conseils ouverts).
- **Directive de restitution** : Le rapport de synthèse du conseil n'étant pas affiché directement à l'Utilisateur, l'Orchestrateur DOIT en restituer la substance dans sa réponse.

### 3.3 Ouvriers Supervisés (`consult_supervised_workers`, `list_supervised_tasks`, `close_supervised_task`)
`consult_supervised_workers(objective, workers, target_model="MODEL_APOLLO", critic_model="MODEL_PROMETHEUS", max_correction_rounds=None, close_on_finish=False)` :
- **Architecture** :
  1. **Délégation Initiale** : Chaque entrée du dictionnaire `workers` lance un sous-agent Ouvrier (`target_model`, défaut `MODEL_APOLLO`) identifié par `thread_supervisor_{task_id}_{worker_id}`.
  2. **Évaluation Critique** : Un Superviseur indépendant (`critic_model`, défaut `MODEL_PROMETHEUS` en `thinking="high"`) audite tous les livrables face à `objective`.
  3. **Boucle de Correction** : Si le Superviseur prononce `REJECTED` (ou si un Worker a épuisé son budget d'outils en bonne voie), il renvoie ses directives de correction uniquement aux Workers marqués `needs_correction` (jusqu'à `max_correction_rounds`, défaut `3`).
  4. **Consolidation** : Un agent Consolidateur (`MODEL_APOLLO`) assemble les travaux finaux.
- **Format strict du paramètre `workers` (`dict`)** :
  ```json
  {
    "worker_backend": {
      "task": "Implémenter la logique de validation...",
      "skill_id": "python_senior_dev"
    },
    "worker_sec": {
      "task": "Rédiger les règles de filtrage...",
      "skill_id": "sec_auditor"
    }
  }
  ```
- **Gestion des sessions des Ouvriers Supervisés** :
  - `list_supervised_tasks()` : Liste les missions supervisées actives (`task_id` de forme `sup_xxxxxxxx`) et les identifiants des sous-agents Ouvriers (`Workers`) rattachés dont l'historique est encore ouvert.
  - `close_supervised_task(task_id)` : Ferme définitivement la mission supervisée `task_id` et purge toutes les sessions (`thread_supervisor_{task_id}_*`) de ses sous-agents Ouvriers.
