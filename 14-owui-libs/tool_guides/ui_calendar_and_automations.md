---
domain_id: ui_calendar_and_automations
version: 5.239.17.1
updated_at: 2026-11-14
name: Agenda de l'Interface Utilisateur, Automatisations CRON (RRULE), Dossiers UI, Étiquettes, Saisie Interactive & Lecture des Sorties Délestées
description: Gestion complète du calendrier de l'Interface Utilisateur, programmation de tâches de fond CRON (syntaxe RRULE iCalendar), dossiers UI, exploration des étiquettes (tags), ask_user_input, wait_timer et exploitation paginée/purge des sorties d'outils délestées (read_tool_output, delete_tool_output).
tools: [search_ui_calendar_events, list_ui_calendar_events, read_ui_calendar_events, create_ui_calendar_event, update_ui_calendar_event, delete_ui_calendar_event, create_ui_automation, update_ui_automation, delete_ui_automation, read_ui_automations, list_ui_automations, list_ui_folders, list_ui_tags, ask_user_input, wait_timer, read_tool_output, delete_tool_output]
keywords: [calendrier, agenda, événement, rendez-vous, calendar, automation, cron, rrule, dtstart, ask_user_input, wait_timer, dossier, folder, tag, etiquette, tools_output, output_id, truncated, read_tool_output, delete_tool_output]
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
Les automatisations programment l'exécution autonome d'un `prompt` par l'Orchestrateur, chaque déclenchement générant un nouveau chat dans le dossier `target_folder_name` (créé automatiquement s'il n'existe pas).

### 2.1 Règles Strictes de Syntaxe `rrule` (iCalendar RFC 5545)
L'argument `rrule` dans `create_ui_automation` et `update_ui_automation` **DOIT IMPÉRATIVEMENT** être une chaîne iCalendar valide (et **NON** une expression cron Unix à 5 étoiles) :
- **RÈGLE CRITIQUE** : Si `COUNT` est spécifié (ex: exécution unique `COUNT=1`), la clause `DTSTART:YYYYMMDDTHHMMSS` (séparée par un saut de ligne `\n`) est **OBLIGATOIRE**.
- **Modèles de syntaxe `rrule` certifiés** :
  - Exécution unique à une date/heure précise : `"DTSTART:20261025T140000\nRRULE:FREQ=DAILY;COUNT=1"`
  - Tous les jours à 09h00 : `"DTSTART:20261022T090000\nRRULE:FREQ=DAILY"`
  - Du lundi au vendredi à 08h30 : `"DTSTART:20261022T083000\nRRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"`
  - Toutes les heures : `"RRULE:FREQ=HOURLY;INTERVAL=1"`

### 2.2 Spécifications des Fonctions d'Automatisation, Dossiers & Étiquettes
- `create_ui_automation` : Crée la tâche planifiée (`name`, `prompt`, `rrule`, `target_folder_name="Automations"`).
- `list_ui_automations` : Liste les automatisations existantes (`id`, `name`, `rrule`, `is_active`, `next_run_at`).
- `read_ui_automations` : Inspecte la configuration intégrale (`prompt` complet, `folder_id`) d'une liste d'`automation_ids` avant modification.
- `update_ui_automation` : Met à jour uniquement les champs fournis.
- `delete_ui_automation` : Supprime la tâche planifiée.
- `list_ui_folders` : Retourne tous les dossiers de l'Interface Utilisateur (`[{"id": "...", "name": "..."}]`).
- `list_ui_tags(global_scope=False, chat_id=None)` :
  - **Format** : Étiquettes normalisées en anglais minuscules (`snake_case` ou `kebab-case` au singulier).
  - `global_scope=False` (défaut) : Retourne les étiquettes de la conversation active (`{"scope": "local", "chat_id": "...", "tags": [{"id": "...", "name": "..."}]}`).
  - `global_scope=True` : Retourne l'ensemble des étiquettes de toutes les conversations de l'Utilisateur (`{"scope": "global", "total_tags": N, "tags": [{"id": "...", "name": "...", "chat_count": X}]}`).
  - **Prérequis RAG** : Avant tout appel à `search_sessions_context(tag=...)`, le Modèle **DOIT impérativement** appeler `list_ui_tags(global_scope=True)` pour utiliser la nomenclature exacte existante.

---

## 3. INTERACTION DIRECTE & TEMPORISATION (`ask_user_input`, `wait_timer`)
- `ask_user_input(question, input_type="text", options=None, timeout_seconds=300)` :
  - Interroge l'Utilisateur via modale interactive (**interdit depuis un sous-agent headless**).
  - `input_type="text"` : Saisie libre (avec suggestions rapides si `options=["Choix A", "Choix B"]` est fourni).
  - `input_type="confirm"` : Approbation binaire (`Oui` / `Non`).
- `wait_timer(seconds)` :
  - Met en pause l'exécution de `1` à `300` secondes. **Strictement limité à 1 seul appel par boucle agentique**.

## 4. LECTURE PAGINÉE & PURGE DES SORTIES D'OUTILS DÉLESTÉES (`read_tool_output`, `delete_tool_output`)
- Tout retour textuel d'outil (hors contenu Base64 protégé) excédant `ECHO_TOOL_OUTPUT_PREVIEW_CHARS` (`32 768` caractères) est automatiquement délesté intégralement dans le domaine de session `tools_output/{output_id}.txt`. La réponse inline retourne alors un aperçu `Head + Tail` (avec marquage exact des lignes omises) accompagné des métadonnées `truncated: true`, `output_id`, `total_chars`, `total_lines`, `omitted_lines_range` et `next_tools: ["read_tool_output", "delete_tool_output"]`.
- `read_tool_output(output_id, start_line=1, end_line=400, search_pattern=None)` :
  - Lit une tranche bornée (au plus `800` lignes et `46 080` octets / ~45 Ko par appel) d'une sortie délestée identifiée par `output_id`.
  - Si `search_pattern` (expression régulière) est fourni, extrait uniquement les lignes correspondantes avec `±2` lignes de contexte et les numéros de lignes exacts avant toute lecture ciblée.
  - Restitue `has_more` et `next_start_line` pour l'enchaînement paginé.
- `delete_tool_output(output_id)` :
  - Supprime un fichier `tools_output/{output_id}.txt` spécifique après exploitation, ou purge l'intégralité du répertoire `tools_output/` de la session courante si `output_id="all"` ou `"*"`.
