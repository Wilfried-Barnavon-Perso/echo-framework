---
domain_id: ui_calendar_and_automations
version: 6.10.0.1
updated_at: 2026-11-20
name: Agenda de l'Interface Utilisateur, Automatisations CRON (RRULE), Dossiers UI, Étiquettes, Saisie Interactive, Tâches UI, Historique Chats & Lecture des Sorties Délestées
description: Gestion complète du calendrier de l'Interface Utilisateur, programmation de tâches de fond CRON (syntaxe RRULE iCalendar), dossiers UI, exploration des étiquettes (tags), ask_user_input, wait_timer, checklist UI (create_ui_tasks, update_ui_task), recherche/lecture SQLite de conversations (search_ui_chats, view_ui_chat) et exploitation paginée/purge des sorties d'outils délestées (read_tool_output, delete_tool_output).
tools: [search_ui_calendar_events, list_ui_calendar_events, read_ui_calendar_events, create_ui_calendar_event, update_ui_calendar_event, delete_ui_calendar_event, create_ui_automation, update_ui_automation, toggle_ui_automation, delete_ui_automation, read_ui_automations, list_ui_automations, list_ui_folders, list_ui_tags, ask_user_input, wait_timer, create_ui_tasks, update_ui_task, search_ui_chats, view_ui_chat, read_tool_output, delete_tool_output]
keywords: [calendrier, agenda, événement, rendez-vous, calendar, automation, cron, rrule, dtstart, toggle_ui_automation, ask_user_input, wait_timer, create_ui_tasks, update_ui_task, search_ui_chats, view_ui_chat, dossier, folder, tag, etiquette, tools_output, output_id, truncated, read_tool_output, delete_tool_output]
---

# MANUEL OPÉRATIONNEL : AGENDA UI, AUTOMATISATIONS CRON & INTERACTIONS (`ui_calendar_and_automations`)

## 1. GESTION DE L'AGENDA DE L'INTERFACE UTILISATEUR (`*_ui_calendar_event*`)
Toutes les dates doivent être alignées sur la référence temporelle `<artifact id="AEC_temporalite">`.
- **Format attendu pour `start` et `end`** : Chaîne ISO 8601 (ex: `"2026-10-25T14:00:00Z"`) ou `"YYYY-MM-DD HH:MM"`.

### 1.1 Consultation, Recherche & Lecture Détaillée
- `search_ui_calendar_events` : Recherche d'événements par mot-clé (`query` dans titre, description, lieu) et bornes temporelles `start` / `end` (défaut `limit=10`).
- `list_ui_calendar_events` : Liste élargie des événements sur une période (défaut `limit=30`) pour vérifier les disponibilités ou récupérer les `event_id` avant modification/suppression.
- `read_ui_calendar_events` :
  - `event_ids` : Liste d'UUIDs (`list[str]`) ou chaîne d'IDs séparés par des virgules.
  - Retourne l'intégralité des métadonnées de chaque événement (`title`, `description`, `start`, `end`, `all_day`, `rrule`, `location`, `color`, `reminder_minutes`, `is_cancelled`, `attendees`). **Indispensable** pour auditer un événement avant mise à jour.

### 1.2 Création, Mise à Jour & Suppression
- `create_ui_calendar_event` : Crée un nouvel événement dans l'agenda.
- `update_ui_calendar_event` :
  - Seuls les paramètres non-`None` sont modifiés ; les champs omis conservent leur valeur existante.
  - `reminder_minutes` : Délai en minutes avant l'événement (`0` pour l'heure pile, `-1` pour désactiver).
- `delete_ui_calendar_event` : Supprime définitivement l'événement `event_id`.

---

## 2. AUTOMATISATIONS & TÂCHES DE FOND CRON (`*_ui_automation*`)
Les automatisations programment l'exécution autonome d'un `prompt` par l'Orchestrateur sans opérateur interactif (`ask_user_input` et modales bloquées). Trois modes de ciblage (`target_mode`) sont disponibles :
- **`new_chat`** *(défaut)* : Chaque déclenchement crée un nouveau chat dans `target_folder_name` (créé automatiquement s'il n'existe pas).
- **`existing_chat`** : Exécute les déclenchements dans une conversation existante (`target_chat_id`, ou `"current"` pour cibler la conversation active hors chat temporaire `local:`). Aucun dossier n'est créé. Si le chat cible est supprimé, l'automatisation est automatiquement mise en pause (`is_active=False`).
- **`persistent_chat`** : Crée un chat unique lors du premier déclenchement (dans `target_folder_name`) puis réutilise ce même chat pour tous les déclenchements suivants. En cas de suppression ultérieure du chat cible, l'automatisation est mise en pause.

### 2.0 File d'attente, Coalescing & Résilience
- **Coalescing par `automation_id`** : Un déclenchement ne s'exécute pas en parallèle de lui-même. Si un run est déjà en cours, les déclenchements suivants sont mis en file d'attente et fusionnés (coalescing) jusqu'à `ECHO_AUTOMATION_QUEUE_MAX_WAIT_SEC` (`10800s` / 3 heures) avant expiration.

### 2.1 Règles Strictes de Syntaxe `rrule` (iCalendar RFC 5545)
L'argument `rrule` dans `create_ui_automation` et `update_ui_automation` **DOIT IMPÉRATIVEMENT** être une chaîne iCalendar valide (et **NON** une expression cron Unix à 5 étoiles) :
- **RÈGLE CRITIQUE** : Si `COUNT` est spécifié (ex: exécution unique `COUNT=1`), la clause `DTSTART:YYYYMMDDTHHMMSS` (séparée par un saut de ligne `\n`) est **OBLIGATOIRE**.
- **Modèles de syntaxe `rrule` certifiés** :
  - Exécution unique à une date/heure précise : `"DTSTART:20261025T140000\nRRULE:FREQ=DAILY;COUNT=1"`
  - Tous les jours à 09h00 : `"DTSTART:20261022T090000\nRRULE:FREQ=DAILY"`
  - Du lundi au vendredi à 08h30 : `"DTSTART:20261022T083000\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"`
  - Toutes les heures : `"RRULE:FREQ=HOURLY;INTERVAL=1"`

### 2.2 Spécifications des Fonctions d'Automatisation, Dossiers & Étiquettes
- `create_ui_automation` : Crée la tâche planifiée (`name`, `prompt`, `rrule`, `target_folder_name="Automations"`, `target_mode="new_chat"`, `target_chat_id=None`).
- `list_ui_automations` : Liste les automatisations existantes (`id`, `name`, `folder_id`, `rrule`, `target`, `is_active`, `next_run_at`).
- `read_ui_automations` : Inspecte la configuration intégrale (`prompt` complet, `folder_id`, `target`) d'une liste d'`automation_ids` avant modification.
- `update_ui_automation` : Met à jour uniquement les champs fournis (`name`, `prompt`, `rrule`, `target_folder_name`, `target_mode`, `target_chat_id`).
- `toggle_ui_automation(automation_id)` : Met en pause ou réactive une automatisation existante (`is_active`) tout en recalculant la prochaine exécution (`next_run_ns`).
- `delete_ui_automation` : Supprime la tâche planifiée.
- `list_ui_folders` : Retourne tous les dossiers de l'Interface Utilisateur (`[{"id": "...", "name": "..."}]`).
- `list_ui_tags(global_scope=False, chat_id=None)` :
  - **Format** : Étiquettes normalisées en anglais minuscules (`snake_case` ou `kebab-case` au singulier).
  - `global_scope=False` (défaut) : Retourne les étiquettes de la conversation active (`{"scope": "local", "chat_id": "...", "tags": [{"id": "...", "name": "..."}]}`).
  - `global_scope=True` : Retourne l'ensemble des étiquettes de toutes les conversations de l'Utilisateur (`{"scope": "global", "total_tags": N, "tags": [{"id": "...", "name": "...", "chat_count": X}]}`).
  - **Prérequis RAG** : Avant tout appel à `search_sessions_context(tag=...)`, le Modèle **DOIT impérativement** appeler `list_ui_tags(global_scope=True)` pour utiliser la nomenclature exacte existante.

---

## 3. INTERACTION DIRECTE & TEMPORISATION (`ask_user_input`, `wait_timer`)
- `ask_user_input(question="", input_type="text", options=None, timeout_seconds=300, questions=None)` :
  - Affiche le composant Svelte natif `AskUserCard` dans l'interface du chat principal (**strictement interdit depuis un sous-agent headless**).
  - Mode `input_type="text"` : Saisie libre pleine largeur (avec suggestions rapides si `options=["Choix A", "Choix B"]` ou `[{"label": "...", "description": "...", "recommended": true}]` est fourni).
  - Mode `input_type="confirm"` : Approbation binaire (`Oui` / `Non`, sans saisie libre).
  - Mode `questions=[...]` : Questionnaire multi-étapes (1 à 5 questions structurées avec `id`, `header`, `question`, `options`, `allow_other`, `show_recommended`). Retourne `{"status": "success", "answers": {id: valeur}}`.
- `wait_timer(seconds)` :
  - Met en pause l'exécution de `1` à `300` secondes. **Strictement limité à 1 seul appel par boucle agentique**.

## 4. LECTURE PAGINÉE & PURGE DES SORTIES D'OUTILS DÉLESTÉES (`read_tool_output`, `delete_tool_output`)
- Gouvernance SSOT à 3 régimes (`get_tool_truncation_limit`) : `32 768` caractères (`ECHO_TOOL_OUTPUT_PREVIEW_CHARS`) pour les outils I/O bruts par défaut, `524 288` caractères (`ECHO_COGNITIVE_OUTPUT_PREVIEW_CHARS`, 512 Ko) pour les outils de distillation cognitive et sous-agents (`ECHO_COGNITIVE_DISTILLATION_TOOLS`), et exemption totale pour les lecteurs déjà fenêtrés (`ECHO_PAGINATED_READER_TOOLS`). Lorsqu'un seuil est franchi (hors Base64 protégé), l'intégralité de la sortie est délestée dans `tools_output/{output_id}.txt` et la réponse inline retourne un aperçu `Head + Tail` accompagné de `truncated: true`, `output_id`, `total_chars`, `total_lines`, `omitted_lines_range` et `next_tools`. Si la requête de l'Utilisateur exige une restitution exhaustive ou un verbatim, il est ABSOLUMENT INTERDIT de synthétiser l'aperçu partiel sans paginer toutes les lignes omises.
- `read_tool_output(output_id, start_line=1, end_line=None, search_pattern=None)` :
  - Lit une fenêtre bornée (par défaut et au plus `800` lignes `ECHO_CODEX_MAX_READ_LINES` et `46 080` octets `ECHO_CODEX_MAX_READ_BYTES` par appel, parcours total illimité) d'une sortie délestée identifiée par `output_id`. Les fenêtres peuvent être lues séquentiellement via `has_more` / `next_start_line` ou en parallèle (jusqu'à 5 appels `read_tool_output` par tour lorsque `omitted_lines_range` est connu).
  - Si `search_pattern` (expression régulière) est fourni, extrait uniquement les lignes correspondantes avec `±2` lignes de contexte et les numéros de lignes exacts avant toute lecture ciblée.
  - Restitue `has_more` et `next_start_line` pour l'enchaînement paginé.
- `delete_tool_output(output_id)` :
  - Supprime un fichier `tools_output/{output_id}.txt` spécifique après exploitation, ou purge l'intégralité du répertoire `tools_output/` de la session courante si `output_id="all"` ou `"*"`.

---

## 5. CHECKLIST UI TEMPS RÉEL & HISTORIQUE DES CONVERSATIONS (`*_ui_task*`, `*_ui_chat*`)
- **Checklist UI (`create_ui_tasks`, `update_ui_task`)** :
  - Réservée à l'Orchestrateur du chat principal (**interdite aux sous-agents headless**).
  - Usage : suivi visuel léger des étapes d'une tâche courante dans l'interface (`pending`, `in_progress`, `completed`, `cancelled`), distinct de `strategic_planner` (planification/exécution persistée complexe).
- **Recherche & Lecture SQLite des Chats (`search_ui_chats`, `view_ui_chat`)** :
  - `search_ui_chats(query, count=5, start_timestamp=None, end_timestamp=None)` : Recherche exacte par titre et contenu en base SQLite (exclut le chat courant). Complémentaire à `search_sessions_context` (recherche sémantique Qdrant).
  - `view_ui_chat(chat_id)` : Restitue l'historique chronologique complet d'une conversation précédente identifiée par `search_ui_chats`.
