---
domain_id: strategic_planner
version: 5.229.0.0
updated_at: 2026-10-22
name: Planification Stratégique & Suivi Tactique
description: Conception de plans d'action (MODEL_PROMETHEUS), audit contradictoire, amorçage par validation utilisateur et pointage tactique des tâches (MODEL_APOLLO) adossés au Codex.
tools: [build_plan, read_plan, update_plan, update_tasks, process_plan, delete_plan, analyze_plan]
keywords: [plan, planification, stratégie, tâches, tasks, build_plan, process_plan, update_tasks, analyze_plan, roadmap]
---

# MANUEL OPÉRATIONNEL : PLANIFICATION STRATÉGIQUE (`strategic_planner`)

## 1. MACHINE À ÉTATS & CYCLE DE VIE DU PLAN
Le Planificateur Stratégique génère et maintient deux fichiers couplés dans le workspace `main` du Codex (indexés dans le Registre Unifié sous `resource_type="plan"`) :
1. **Le Fichier de Stratégie** : `{plan_id}_{slug}.md` (Objectif, architecture, phases).
2. **Le Fichier des Tâches** : `tasks_{plan_id}_{slug}.md` (Checklist tactique).

### Séquence Opérationnelle Impérative
1. **Création (`proposed`)** :
   - Appeler `build_plan(goal, context)` (`MODEL_PROMETHEUS`, `thinking="high"`).
   - Le plan est créé au statut `'proposed'`. Le Modèle **DOIT** présenter la stratégie à l'Utilisateur et attendre sa validation (ou ses demandes d'ajustement) avant toute exécution.
2. **Audit Contradictoire & Révision Stratégique (Statut `proposed` uniquement)** :
   - `analyze_plan(plan_id, focus="...", context_info="")` : Délègue un audit critique non modificateur (`MODEL_PROMETHEUS`, `thinking="high"`) pour vérifier la cohérence, les dépendances et les angles morts du plan.
   - `update_plan(plan_id, instructions)` : Modifie la stratégie d'un plan au statut `'proposed'`.
3. **Amorçage Officiel de l'Exécution (`proposed ➔ executing`)** :
   - Dès que l'Utilisateur demande de lancer le plan, le Modèle **A L'OBLIGATION ABSOLUE** d'invoquer `process_plan(plan_id, user_already_validated=...)` **AVANT** d'exécuter la première tâche.
   - Si `user_already_validated=False` (défaut), une modale interactive demande confirmation à l'Utilisateur. Si l'Utilisateur a déjà explicitement donné son feu vert dans son message de chat (ex: *"OK, lance le plan"*), passer `user_already_validated=True`.
   - `process_plan` bascule le statut du plan à `'executing'` et verrouille `update_plan`.
4. **Exécution Séquentielle & Pointage Tactique (`executing`)** :
   - Le Modèle exécute les tâches dans l'ordre chronologique.
   - Après **CHAQUE** tâche accomplie ou échouée, le Modèle **DOIT** appeler `update_tasks(plan_id, instructions)` pour mettre à jour les cases à cocher dans `tasks_{plan_id}_{slug}.md`.

---

## 2. SPÉCIFICATIONS DES PARAMÈTRES & CODIFICATION DES TÂCHES

### 2.1 `build_plan(goal: str, context: str)`
- `goal` : Objectif final mesurable de la mission.
- `context` : Contraintes techniques, périmètre, ressources existantes et décisions préalables.
- **Retour** : Génère `plan_id` (ex: `plan-1761132000`) et retourne la stratégie complète ainsi que les tâches initiales.

### 2.2 `update_tasks(plan_id: str, instructions: str)`
Outil tactique exclusif (`MODEL_APOLLO`) pour modifier le statut des tâches dans `tasks_{plan_id}_{slug}.md` sans altérer la stratégie globale.
- **Codification Stricte des Statuts dans `instructions`** :
  - `[ ]` : Tâche en attente (Non commencée).
  - `[/]` : Tâche en cours d'exécution.
  - `[x]` : Tâche terminée avec succès.
  - `[!]` : Tâche échouée ou bloquée (nécessite attention).
  - `[-]` : Tâche ignorée ou obsolète.
- Exemple d'`instructions` : `"Passe la sous-tâche 1.1 au statut [x] et la sous-tâche 1.2 au statut [/]."`.

### 2.3 `read_plan(plan_id: str)` & `delete_plan(plan_id: str)`
- `read_plan(plan_id)` : Restitue simultanément le contenu intégral du fichier de stratégie (`=== STRATÉGIE ===`) et du fichier de tâches (`=== TÂCHES ===`).
- `delete_plan(plan_id)` : Supprime définitivement le plan du Codex et nettoie son entrée dans le Registre Unifié.

---

## 3. GARDE-FOUS & INTERDICTIONS FORMELLES
- **Gatekeeper 1 (Verrou Stratégique)** : `update_plan` refuse toute modification si le statut du plan n'est plus `'proposed'`.
- **Gatekeeper 2 (Interdiction de Bypass)** : Il est **ABSOLUMENT INTERDIT** d'utiliser `update_plan` ou `edit_codex` pour forcer manuellement `status: executing` dans le frontmatter YAML. Seul `process_plan` est habilité à amorcer l'exécution.
