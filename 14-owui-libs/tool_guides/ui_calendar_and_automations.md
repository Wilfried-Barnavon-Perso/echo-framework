---
domain_id: ui_calendar_and_automations
version: 6.24.1.1
updated_at: 2026-11-26
name: Agenda de l'Interface Utilisateur, Automatisations CRON (RRULE), Saisie Interactive, Tâches UI & Lecture des Sorties Délestées
description: Gestion complète du calendrier de l'Interface Utilisateur (manage_ui_calendar), programmation de tâches de fond CRON (manage_ui_automations, syntaxe RRULE iCalendar), ask_user_input, wait_timer, checklist UI (manage_ui_tasks) et exploitation paginée/purge des sorties d'outils délestées (read_tool_output, delete_tool_output).
tools: [manage_ui_calendar, manage_ui_automations, ask_user_input, wait_timer, manage_ui_tasks, read_tool_output, delete_tool_output]
keywords: [calendrier, agenda, événement, rendez-vous, rappel, automation, cron, rrule, dtstart, persistent_chat, existing_chat, new_chat, temporisation, checklist, ui_task, tools_output, output_id, truncated, spillover, délestage]
---

# MANUEL OPÉRATIONNEL : AGENDA UI, AUTOMATISATIONS CRON & INTERACTIONS (`ui_calendar_and_automations`)

## 1. GESTION DE L'AGENDA DE L'INTERFACE UTILISATEUR (`manage_ui_calendar`)
Toutes les dates DOIVENT être alignées sur la référence temporelle `<artifact id="AEC_temporalite">`.
- **Format attendu pour `start` et `end`** : Chaîne ISO 8601 (ex: `"2026-10-25T14:00:00Z"`) ou `"YYYY-MM-DD HH:MM"`.
- **Contrainte chronologique stricte (`end >= start`)** : Le Système vérifie `end >= start` sur `list`, `create` et `update` (y compris vis-à-vis de la borne existante lors d'une modification partielle d'un événement). Tout format invalide ou incohérence chronologique lève une erreur immédiate.
- **Signature unifiée** : `manage_ui_calendar(action="list"|"read"|"create"|"update"|"delete", event_ids=None, query="", start=None, end=None, limit=30, title=None, description=None, location=None, all_day=None, is_cancelled=None, reminder_minutes=None)` *(défaut `action="list"`)*.
- **Paramètre `event_ids`** : Accepte un identifiant unique (`str`), une chaîne CSV d'identifiants séparés par des virgules ou une liste `list[str]` (`1..N` pour `read` et `delete`, exactement `1` pour `update`).

### 1.1 Actions disponibles
- **`action="list"`** : Liste ou recherche d'événements par mot-clé (`query` dans titre, description, lieu) et bornes temporelles `start` / `end` (défaut `limit=30`) pour vérifier les disponibilités ou récupérer les `event_id` avant lecture/modification/suppression.
- **`action="read"`** : Retourne l'intégralité des métadonnées de chaque événement ciblé par `event_ids` (`title`, `description`, `start`, `end`, `all_day`, `rrule`, `location`, `color`, `reminder_minutes`, `is_cancelled`, `attendees`). **REQUIS** pour auditer un événement avant mise à jour.
- **`action="create"`** : Crée un nouvel événement dans l'agenda (`title`, `description`, `start`, `end`, `location`) en supportant directement `all_day` et `reminder_minutes` en un seul appel.
- **`action="update"`** : Met à jour l'événement unique désigné par `event_ids`. Seuls les paramètres non-`None` sont modifiés ; les champs omis conservent leur valeur existante. `reminder_minutes` fixe le délai en minutes avant l'événement (`0` pour l'heure pile, `-1` pour désactiver).
- **`action="delete"`** : Supprime définitivement le ou les événements désignés par `event_ids`.

---

## 2. AUTOMATISATIONS & TÂCHES DE FOND CRON (`manage_ui_automations`)
Les automatisations programment l'exécution autonome d'un `prompt` par Le Modèle principal sans opérateur interactif (`ask_user_input` et modales **ABSOLUMENT INTERDITS**).
- **Signature unifiée** : `manage_ui_automations(action="list"|"read"|"create"|"update"|"toggle"|"delete", automation_ids=None, name=None, prompt=None, rrule=None, target_folder_name=None, target_mode=None, target_chat_id=None, query="", limit=30)` *(défaut `action="list"`)*.
- **Paramètre `automation_ids`** : Accepte `str` (ou CSV) ou `list[str]` (`1..N` pour `read`, `toggle` et `delete`, exactement `1` pour `update`).

### 2.0 Modes de ciblage, File d'attente & Coalescing
Trois modes de ciblage (`target_mode`) mutuellement exclusifs sont disponibles :
- **`new_chat`** *(défaut)* : Chaque déclenchement crée un nouveau chat dans `target_folder_name` (créé automatiquement s'il n'existe pas). `target_chat_id` DOIT être omis.
- **`existing_chat`** : Exécute les déclenchements dans une conversation existante précise (`target_chat_id` **REQUIS** : UUID d'une conversation existante, ou `"current"` pour cibler la conversation active hors chat temporaire `local:`). Aucun dossier n'est utilisé (`target_folder_name` ignoré). Si le chat cible est supprimé, l'automatisation est automatiquement mise en pause (`is_active=False`).
- **`persistent_chat`** : Crée automatiquement un chat dédié unique lors du premier déclenchement (dans `target_folder_name`) puis réutilise ce même chat pour tous les déclenchements suivants (**INTERDIT** de lui passer un `target_chat_id` existant : utiliser `existing_chat` pour cela). En cas de suppression ultérieure du chat dédié, l'automatisation est mise en pause.
- **Coalescing par `automation_id`** : Un déclenchement ne s'exécute pas en parallèle de lui-même. Si un run est déjà en cours, les déclenchements suivants sont mis en file d'attente et fusionnés (coalescing) jusqu'à 3 heures (`10 800 s`) avant expiration.

### 2.1 Règles Strictes de Syntaxe `rrule` (iCalendar RFC 5545)
L'argument `rrule` dans `action="create"` et `action="update"` **DOIT IMPÉRATIVEMENT** être une chaîne iCalendar valide (et **NON** une expression cron Unix à 5 étoiles) :
- **RÈGLE CRITIQUE** : Si `COUNT` est spécifié (ex: exécution unique `COUNT=1`), la clause `DTSTART:YYYYMMDDTHHMMSS` (séparée par un saut de ligne `\n`) est **OBLIGATOIRE**.
- **Modèles de syntaxe `rrule` certifiés** :
  - Exécution unique à une date/heure précise : `"DTSTART:20261025T140000\nRRULE:FREQ=DAILY;COUNT=1"`
  - Tous les jours à 09h00 : `"DTSTART:20261022T090000\nRRULE:FREQ=DAILY"`
  - Du lundi au vendredi à 08h30 : `"DTSTART:20261022T083000\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"`
  - Toutes les heures : `"RRULE:FREQ=HOURLY;INTERVAL=1"`

### 2.2 Spécifications des Actions d'Automatisation
- **`action="list"`** : Liste les automatisations existantes (`id`, `name`, `folder_id`, `rrule`, `target`, `is_active`, `next_run_at`), avec filtrage optionnel par `query` et pagination `limit=30`.
- **`action="read"`** : Inspecte la configuration intégrale (`prompt` complet, `folder_id`, `target`) des `automation_ids` avant modification.
- **`action="create"`** : Crée la tâche planifiée (`name`, `prompt`, `rrule`, `target_folder_name="Automations"`, `target_mode="new_chat"`, `target_chat_id=None`). Si `target_chat_id` (`"current"` ou UUID) est fourni sans `target_mode`, bascule automatiquement en `existing_chat`.
- **`action="update"`** : Met à jour uniquement les champs fournis (`name`, `prompt`, `rrule`, `target_folder_name`, `target_mode`, `target_chat_id`) sur l'automatisation unique ciblée par `automation_ids` :
  - Omettre `target_mode` et `target_chat_id` conserve la destination existante intacte.
  - Fournir `target_chat_id` (`"current"` ou UUID) bascule sur `existing_chat` vers cette conversation (même si `target_mode` est omis).
  - Fournir `target_mode="persistent_chat"` conserve le chat dédié existant s'il est déjà actif, ou initialise un nouveau chat dédié vierge si l'automatisation change de mode ou si l'ancien chat dédié a été supprimé.
  - Fournir `target_chat_id="reset"` (avec `target_mode="persistent_chat"` ou omis) détache explicitement le chat dédié actuel pour repartir sur une nouvelle conversation dédiée au prochain déclenchement.
- **`action="toggle"`** : Met en pause ou réactive une ou plusieurs automatisations (`is_active`) via `automation_ids` tout en recalculant la prochaine exécution (`next_run_ns`).
- **`action="delete"`** : Supprime définitivement la ou les tâches planifiées ciblées par `automation_ids`.
- **Résolution des conversations, dossiers et étiquettes** : Pour identifier un `target_chat_id` existant (ou inspecter les dossiers et étiquettes), Le Modèle DOIT utiliser `list_ui_chats`, `list_ui_folders` et `list_ui_tags` (domaine `ui_conversations`).

---

## 3. INTERACTION DIRECTE & TEMPORISATION (`ask_user_input`, `wait_timer`)
- `ask_user_input(question="", input_type="text", options=None, multi_select=False, timeout_seconds=300, questions=None)` :
  - Affiche une carte interactive de saisie dans le flux du chat principal (**ABSOLUMENT INTERDIT depuis un Sous-Agent headless ou une automatisation**). `timeout_seconds` est borné à `[10, 600]`.
  - Mode `input_type="text"` : Saisie libre pleine largeur (avec suggestions rapides si `options=["Choix A", "Choix B"]` ou `[{"label": "...", "description": "...", "recommended": true}]` est fourni). Avec `multi_select=True`, autorise la sélection multiple (cases à cocher) et retourne une `list[str]` des choix sélectionnés (incluant la saisie libre éventuelle) dans `{"status": "success", "user_input": ...}`.
  - Mode `input_type="confirm"` : Approbation binaire (`Oui` / `Non`, sans saisie libre), retournant `{"status": "success", "user_input": True}` ou `{"status": "cancelled", "user_input": False}`.
  - Mode `questions=[...]` : Questionnaire multi-étapes (1 à 5 questions structurées avec `id`, `header`, `question`, `options`, `multi_select`, `allow_other`, `show_recommended`). Chaque question avec `multi_select=True` retourne une `list[str]` des choix sélectionnés (plus la saisie libre éventuelle). Retourne `{"status": "success", "answers": {id: valeur}}`.
- `wait_timer(seconds)` :
  - Met en pause l'exécution de **`1` à `180` secondes** (`ECHO_MAX_WAIT_TIMER`) tout en diffusant l'état du compte à rebours vers l'interface native via le bus Window Manager (`echo_wait_timer`). **Autorisé aux Sous-Agents** (contrairement à `ask_user_input`). **Strictement limité à 1 seul appel par boucle agentique**.

## 4. LECTURE PAGINÉE & PURGE DES SORTIES D'OUTILS DÉLESTÉES (`read_tool_output`, `delete_tool_output`)
- Gouvernance à 3 régimes : `32 768` caractères pour les outils I/O bruts par défaut, `524 288` caractères (512 Ko) pour les outils de distillation cognitive et Sous-Agents, et exemption totale pour les lecteurs déjà paginés (`read_codex`, `read_tool_output`, `delete_tool_output`). Lorsqu'un seuil est franchi (hors Base64 protégé), l'intégralité de la sortie est délestée dans `tools_output/{output_id}.txt` et la réponse inline retourne un aperçu `Head + Tail` accompagné de `truncated: true`, `output_id`, `total_chars`, `total_lines`, `omitted_lines_range` et `next_tools`. Si la requête de L'Utilisateur exige une restitution exhaustive ou un verbatim, il est ABSOLUMENT INTERDIT de synthétiser l'aperçu partiel sans paginer toutes les lignes omises.
- **Accessibilité Sous-Agent** : `read_tool_output` et `delete_tool_output` appartiennent à `DELEGATE_AGENT_SYSTEM_TOOLS` et restent toujours disponibles aux Sous-Agents même lorsque `allowed_tools` est restreint.
- `read_tool_output(output_id, start_line=1, end_line=None, search_pattern=None)` :
  - Lit une fenêtre bornée (par défaut et au plus `800` lignes et `46 080` octets par appel, parcours total illimité) d'une sortie délestée identifiée par `output_id`. Les fenêtres PEUVENT être lues séquentiellement via `has_more` / `next_start_line` ou en parallèle (jusqu'à 5 appels `read_tool_output` par tour lorsque `omitted_lines_range` est connu).
  - Si `search_pattern` (expression régulière) est fourni, extrait uniquement les lignes correspondantes avec `±2` lignes de contexte et les numéros de lignes exacts avant toute lecture ciblée.
  - Restitue `has_more` et `next_start_line` pour l'enchaînement paginé.
- `delete_tool_output(output_id)` :
  - Supprime un fichier `tools_output/{output_id}.txt` spécifique après exploitation, ou purge l'intégralité du répertoire `tools_output/` de la session courante si `output_id="all"` ou `"*"`.

---

## 5. CHECKLIST UI TEMPS RÉEL (`manage_ui_tasks`)
- **Checklist UI (`manage_ui_tasks(action="create"|"update", tasks=None, task_id=None, status=None)`)** :
  - Réservée au Modèle principal (**ABSOLUMENT INTERDITE aux Sous-Agents headless**).
  - Usage : suivi visuel léger des étapes d'une tâche courante dans l'interface (`pending`, `in_progress`, `completed`, `cancelled`), distinct de `strategic_planner` (planification/exécution persistée complexe).
  - `action="create"` : requiert `tasks=[{"content": "...", "status": "pending", "id": "..."}]` (`content` requis non vide, `status` optionnel parmi `pending`, `in_progress`, `completed`, `cancelled` — défaut `pending` —, `id` optionnel auto-généré si omis).
  - `action="update"` : Met à jour le statut d'une tâche existante (`pending`, `in_progress`, `completed`, `cancelled`). Les paramètres `task_id` et `status` sont **tous deux obligatoires** (aucun statut implicite par défaut n'est appliqué).
