---
domain_id: strategic_planner
version: 6.17.2.1
updated_at: 2026-11-21
name: Planification Stratégique & Suivi Tactique
description: Conception de plans d'action (MODEL_PROMETHEUS), audit contradictoire, amorçage par validation utilisateur et pointage tactique des tâches (MODEL_APOLLO) adossés au workspace plans du Codex.
tools: [build_plan, read_plan, update_plan, update_tasks, process_plan, close_plan, delete_plan, analyze_plan]
keywords: [plan, planification, stratégie, tâches, tasks, build_plan, process_plan, close_plan, update_tasks, analyze_plan, roadmap, plans]
---

# MANUEL OPÉRATIONNEL : PLANIFICATION STRATÉGIQUE (`strategic_planner`)

## 1. MACHINE À ÉTATS & CYCLE DE VIE DU PLAN
Le Planificateur Stratégique génère, versionne sous Git et maintient deux fichiers couplés dans le workspace dédié `plans` du Codex (indexés dans le Registre Unifié sous `resource_type="plan"`) :
1. **Fichier de Stratégie** : `{plan_id}_{slug}.md` (Objectif, architecture, phases).
2. **Fichier des Tâches** : `tasks_{plan_id}_{slug}.md` (Checklist tactique).

### Séquence Opérationnelle Impérative
1. **Création (`proposed`)** :
   - Le Modèle DOIT appeler `build_plan(goal, context)`.
   - Le plan est créé au statut `'proposed'`. Le Modèle **DOIT** présenter la stratégie à L'Utilisateur et attendre sa validation avant toute exécution.
2. **Audit Contradictoire & Révision Stratégique (Statut `proposed` uniquement)** :
   - `analyze_plan(plan_id, focus="...", context_info="")` : Délègue un audit critique non modificateur pour vérifier la cohérence, les dépendances et les angles morts du plan.
   - `update_plan(plan_id, instructions)` : Modifie la stratégie d'un plan au statut `'proposed'`.
3. **Amorçage Officiel de l'Exécution (`proposed ➔ executing`)** :
   - Dès que L'Utilisateur demande de lancer le plan, Le Modèle **DOIT IMPÉRATIVEMENT** invoquer `process_plan(plan_id, user_already_validated=...)` **AVANT** d'exécuter la première tâche.
   - Si `user_already_validated=False` (défaut), une modale interactive demande confirmation à L'Utilisateur (impossible en contexte headless ou automatisation planifiée). Si L'Utilisateur a déjà explicitement validé dans son message, Le Modèle DOIT passer `user_already_validated=True`.
   - `process_plan` bascule le statut du plan à `'executing'` et verrouille `update_plan`.
4. **Exécution Séquentielle & Pointage Tactique (`executing`)** :
   - Le Modèle DOIT exécuter les tâches dans l'ordre chronologique.
   - Après **CHAQUE** tâche accomplie ou échouée, Le Modèle DOIT appeler `update_tasks(plan_id, instructions)` pour mettre à jour les cases à cocher dans `tasks_{plan_id}_{slug}.md`.
5. **Clôture Officielle (`executing` ➔ `success|partial|failed|abandoned` ou `proposed` ➔ `abandoned`)** :
   - Une fois l'exécution terminée (ou pour abandonner un plan), Le Modèle **DOIT IMPÉRATIVEMENT** appeler `close_plan(plan_id, final_status, execution_summary)` pour basculer le statut du plan vers le statut terminal adapté (`success`, `partial`, `failed`, `abandoned`) et consigner la section `## Synthèse d'exécution`.

---

## 2. SPÉCIFICATIONS DES PARAMÈTRES & CODIFICATION DES TÂCHES

### 2.1 `build_plan`
- `goal` : Objectif final mesurable de la mission.
- `context` : Contraintes techniques, périmètre, ressources existantes et décisions préalables.
- **Retour** : Génère `plan_id` et retourne la stratégie complète ainsi que les tâches initiales.

### 2.2 `update_tasks`
Outil exclusif pour modifier le statut des tâches dans `tasks_{plan_id}_{slug}.md` sans altérer la stratégie globale.
- **Codification Stricte des Statuts dans `instructions`** :
  - `[ ]` : Tâche en attente (Non commencée).
  - `[/]` : Tâche en cours d'exécution.
  - `[x]` : Tâche terminée avec succès.
  - `[!]` : Tâche échouée ou bloquée.
  - `[-]` : Tâche ignorée ou obsolète.
- Exemple d'`instructions` : `"Passe la sous-tâche 1.1 au statut [x] et la sous-tâche 1.2 au statut [/]."`.

### 2.3 `close_plan`
Outil exclusif de clôture formelle d'un plan (`plan_id`).
- `final_status` : Statut terminal obligatoire parmi `success`, `partial`, `failed`, `abandoned`. Un plan au statut `'proposed'` DOIT être clôturé exclusivement avec `final_status='abandoned'`.
- `execution_summary` : Synthèse non vide des actions menées et résultats obtenus, consignée sous `## Synthèse d'exécution`.

### 2.4 `read_plan` & `delete_plan`
- `read_plan(plan_id)` : Restitue simultanément le contenu intégral du fichier de stratégie et du fichier de tâches.
- `delete_plan(plan_id)` : Supprime définitivement le plan du Codex et nettoie son entrée dans le Registre Unifié.

---

## 3. GARDE-FOUS & INTERDICTIONS FORMELLES
- **Gatekeeper 1 (Verrou Stratégique)** : `update_plan` refuse toute modification dès que le statut du plan n'est plus `'proposed'` (en cours d'exécution, utiliser `update_tasks` et `close_plan`).
- **Gatekeeper 2 (Interdiction de Bypass)** : Il est **ABSOLUMENT INTERDIT** d'utiliser `update_plan` ou `edit_codex` pour modifier manuellement `status:` dans le frontmatter YAML. Seul `process_plan` est habilité à passer un plan à `'executing'`, et seul `close_plan` est habilité à le clôturer vers un statut terminal.
- **Verrouillage Post-Clôture** : Une fois le plan clôturé (`success`, `partial`, `failed`, `abandoned`), toute modification via `update_plan`, `update_tasks` ou nouvelle clôture via `close_plan` est **ABSOLUMENT INTERDITE**.
- **Contrainte Headless / Automatisation** : En contexte Sous-Agent ou automatisation planifiée, `process_plan(user_already_validated=False)` est bloqué car la modale interactive est indisponible (`user_already_validated=True` REQUIS uniquement sur validation explicite préalable de L'Utilisateur).
