---
domain_id: agent_orchestration
version: 6.13.2.1
updated_at: 2026-11-20
name: Orchestration Multi-Agents, Sous-Agents Autonomes, Data Broker & Skills
description: Délégation par tranches supervisées (delegate_to_agent), pilotage unifié (control_subagents), Table Ronde Delphi (consult_council), Ouvriers Supervisés (consult_supervised_workers), Data Broker et gestion des Skills.
tools: [delegate_to_agent, control_subagents, consult_council, consult_supervised_workers, forge_skill, list_skills, read_skill, search_skill, delete_user_skill, ask_data_broker]
keywords: [agent, sous-agent, délégation, control_subagents, tranche, checkpoint, council, table_ronde, delphi, supervised_workers, superviseur, workers, skill, forge_skill, data_broker, sub_sid]
---

# MANUEL OPÉRATIONNEL : ORCHESTRATION MULTI-AGENTS & SKILLS (`agent_orchestration`)

## 1. MATRICE D'ARBITRAGE DES 4 MOTEURS DE DÉLÉGATION

| Moteur | Architecture & Modèles | Cas d'Usage Nominal | Gestion des Sessions |
| :--- | :--- | :--- | :--- |
| **`delegate_to_agent`** | 1 Sous-Agent autonome outillé (défaut `MODEL_APOLLO`, exécuté par **tranches coopératives de 8 tours d'outils (jusqu'à 5 appels parallèles/tour) / 120s** sans limite préalable de mission, avec disjoncteur PARE à 3 erreurs). Parallélisable (jusqu'à 4 Sous-Agents simultanés par tour). | Tâche spécialisée isolée, exécution technique multi-outils, offload cognitif parallèle. | `sub_sid` piloté via **`control_subagents`**. |
| **`consult_council`** | **Table Ronde Delphi** : 1 à 7 profils (`participants` = liste de `skill_id`) démultipliés par `multiplier` ($1 \dots 5$, min 2 participants effectifs) débattant en parallèle sur $R$ tours ($3 \dots 10$, **3 tours d'outils par défaut (`0..6`) / 120s par round Delphi**) + Synthétiseur (`MODEL_PROMETHEUS`). | Sujet multidimensionnel, incertain ou stratégique nécessitant un débat contradictoire multi-perspectives ou intra-spécialité. | `council_id` piloté via **`control_subagents`**. |
| **`consult_supervised_workers`** | **Boucle Critique/Correction** : $N$ Sous-Agents Ouvriers (`Workers`, `MODEL_APOLLO`, **passe autonome de 15 tours d'outils / 300s**) + 1 Superviseur Critique (`MODEL_PROMETHEUS`) + 1 Consolidateur (`MODEL_APOLLO`). | Livrables complexes exigeant une qualité irréprochable (code critique, architecture, rédaction formelle multi-parties) avec vérification et correction itérative. | `task_id` (`sup_xxxxxxxx`) piloté via **`control_subagents`**. |
| **`ask_data_broker`** | Sous-Agent `MODEL_APOLLO` pré-câblé sur les outils de recherche Web, Identity Vault et MCP. | Collecte et extraction rapide de données externes structurées (Web + MCP) sans saturer Le Modèle principal. | Stateless (pas de session persistée). |

---

## 2. GOUVERNANCE DES COMPÉTENCES (`SKILLS`)
Les **Skills** définissent l'expertise métier et la méthodologie d'un Sous-Agent (REQUIS pour chaque participant de `consult_council` et pour les `workers` de `consult_supervised_workers`).
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
- `target_model_key` : `"MODEL_ZEPHYR"`, `"MODEL_APOLLO"` (défaut), `"MODEL_PROMETHEUS"` ou `"MODEL_ATHENA"`.
- **Gouvernance par Tranches Coopératives (sans limite préalable de mission)** :
  - Chaque invocation ou reprise accorde une tranche fraîche de **8 tours d'outils** (jusqu'à 5 appels parallèles par tour) ou **120 secondes**.
  - Si la mission n'est pas achevée en fin de tranche, Le Sous-Agent retourne `status="suspended_checkpoint"` avec sa synthèse d'étape. S'il subit 3 erreurs d'outils consécutives, il s'arrête en `status="suspended_pare_alert"`.

### 3.2 Contrôleur Unifié des Sous-Agents (`control_subagents`)
Outil unique gouvernant le cycle de vie de tous les Sous-Agents unitaires (`sub_sid`), conseils (`council_id`), missions supervisées (`sup_xxxxxxxx`) et sessions navigateur (`thread_web_*`) :
- `action` :
  - `"status"` (défaut) : État temps réel des sessions (`status`, tranche `slice_turns_used/limit`, cumul `total_turns_used`, `total_calls_used`, `checkpoint_reason`, file FIFO). Filtrable par `filter_type` (`"all" | "agent" | "council" | "supervisor" | "web"`).
  - `"collect"` : Lit la dernière production et les métadonnées des `target_ids` sans consommer d'appel LLM.
  - `"steer"` : Injecte une `directive` (`<REQUETE_ORCHESTRATEUR>`) dans la file FIFO de `target_ids` (dépilée entre deux tours d'outils) et modifie optionnellement `target_model_key`.
  - `"resume"` : Relance `target_ids` pour une nouvelle tranche fraîche de 8 tours d'outils (jusqu'à 5 appels parallèles/tour) / 120s (avec `directive` et/ou `target_model_key` optionnels). **Si `target_ids` contient plusieurs identifiants, les reprises s'exécutent simultanément en parallèle**.
  - `"suspend"` : Met en pause proprement (`status="suspended"`) à la fin du tour d'outil courant.
  - `"kill"` : Interrompt immédiatement l'exécution, scelle proprement l'historique d'outils et libère les ressources (ex: navigateur) sans supprimer l'historique.
  - `"summarize"` : Génère un résumé structuré en 4 sections de l'historique des `target_ids` sans les fermer.
  - `"close"` : Ferme définitivement et purge les sessions `target_ids` (accepte un `sub_sid`, un `council_id`, un `task_id` `sup_...` ou un `thread_web_...`).

### 3.3 Table Ronde Delphi (`consult_council`)
- `participants` : Liste de `skill_id` existants (`List[str]`, maximum `7`, avec `len(participants) * multiplier >= 2`). Tous les `skill_id` DOIVENT exister (sinon Le Modèle DOIT appeler `forge_skill` au préalable).
- `multiplier` : Facteur multiplicateur par `skill_id` (entier `1` à `5`, défaut `1`), permettant d'instancier plusieurs experts de même spécialité pour se contredire entre eux.
- `rounds` : Nombre de tours de délibération (min et défaut `3`, max `10`, sous la contrainte arithmétique stricte `(len(participants) * multiplier) * rounds <= 60`), avec **3 tours d'outils par défaut (`0..6`) / 120s par round Delphi** (`0` = délibération pure sans outil).
- `council_id` : Identifiant choisi pour le conseil (ex: `"arch_review"`).
- `close_on_finish` : `False` par défaut (permet de relancer `consult_council` avec le même `council_id` pour approfondir). Une fois la délibération terminée, Le Modèle DOIT appeler **`control_subagents(action="close", target_ids=[council_id])`**.
- **Directive de restitution** : Le rapport de synthèse du conseil n'étant pas affiché directement à L'Utilisateur, Le Modèle principal DOIT en restituer la substance dans sa réponse.

### 3.4 Ouvriers Supervisés (`consult_supervised_workers`)
- **Paramètres clés** :
  - `objective` : Objectif global servant de référentiel d'audit au Superviseur.
  - `max_correction_rounds` : Nombre maximal de cycles de correction en cas de rejet par le Superviseur (défaut `3`).
  - `close_on_finish` : `False` par défaut.
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
- **Gestion des sessions** :
  - Inspecter ou clore une mission supervisée (`task_id` de forme `sup_xxxxxxxx`) via **`control_subagents(action="status", filter_type="supervisor")`** et **`control_subagents(action="close", target_ids=[task_id])`**.

### 3.5 Collecteur de Données Spécialisé (`ask_data_broker`)
- `query` : Requête textuelle descriptive spécifiant les données externes précises à collecter (recherche Web ciblée, Identity Vault et exécution sur serveurs MCP).
- **Sans État (Stateless)** : N'ouvre aucune session persistée et restitue une synthèse technique directement exploitable.
