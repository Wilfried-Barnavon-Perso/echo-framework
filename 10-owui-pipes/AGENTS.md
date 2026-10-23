# 🌌 ECHO Framework - Connaissance Sémantique : `10-owui-pipes`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `10-owui-pipes`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le **Système Nerveux Central** (le Cortex) de l'intégration LLM dans ECHO, à savoir le moteur `echo_orchestrator.py`. Ce composant a été formellement implémenté en tant que composant *core* (Pipe Manifold) Open WebUI. Il est conçu pour intercepter, formater, gérer la mémoire et router toutes les requêtes utilisateur vers l'API Gemini (AI Studio / Code Assist), tout en préservant une intégrité bit-perfect des métadonnées (Suture State).

## 2. Cartographie des Fichiers et Algorithmes

### `echo_orchestrator.py` (v5.229.2.1)

#### A. Classe `UserDataManager` (Le Tisserand de Mémoire)
**Rôle** : Gestionnaire de l'état asynchrone SQLite (proxy vers `EchoStateManager`). Il reconstruit l'historique exact de la conversation.
- **Invariant & Hash Cumulatif** (`calculate_invariant`, `calculate_cumulative`) : Crée une empreinte unique (hash) pour chaque tour de parole, assurant le verrouillage de version (Version Lock) de la conversation.
- **Shadow Suture** (`save_shadow`, `get_shadow`) : Persiste les requêtes et réponses structurées (incluant les cascades multi-messages, `tool_calls` et payloads multimodaux) en base SQLite locale pour pallier les limitations de persistance d'Open WebUI.
- **Registre KV Unifié (`echo_settings`)** : Persiste l'état de session (`save_session_setting`, `get_session_setting`, dont `turn_origin_model` et les paliers `aec_tier_*`) ainsi que les statistiques de contexte (`save_context_stats`, `get_last_context_stats`).
- **Signature & Bridge** (`save_signature_by_id`, `get_call_bridge`) : Fait le pont entre un appel d'outil déclenché (`call_id`) et son résultat renvoyé par OWUI au tour suivant. L'injection de la `thoughtSignature` (obligatoire pour Gemini 3+) est traitée dynamiquement et associée exclusivement au premier appel `functionCall` en cas d'appels parallèles, conformément à la spécification de l'API ([API Docs](https://ai.google.dev/gemini-api/docs/thinking#signatures)).

#### B. Classe `Orchestrator` (Le Cerveau Exécutif)
**Rôle** : Restauration du contexte Bit-Perfect, gestion du *Clamping Dynamique* et des mutations AEC.
- **Seuil d'Alerte Contextuel (`CONTEXT_WARNING_THRESHOLD`)** : Dans `prepare_context()`, si la charge dépasse `CONTEXT_WARNING_THRESHOLD` (80%), un toast invite l'utilisateur à migrer la session vers une nouvelle conversation via l'Action `'Poursuivre dans une nouvelle conversation'` (`resume_in_new_chat_action.py`).
- **Traduction & Déballage d'Outils (`echo_core.py`)** : S'appuie sur `convert_owui_tools()` et `unbox_tool_output()` (factorisés dans `echo_core.py`) pour normaliser les schémas d'outils en `camelCase` (`functionDeclarations`, `functionCallingConfig`), appliquer les politiques `MODEL_ENUM_BY_POLICY` via `ECHO_MODELS_REGISTRY`, et extraire les payloads multimodaux (`echo_tool_multiparts`) avec leur `call_id`.
- **Verrou de Tour (`turn_origin_model`) & `_mutate_context_identity()`** : Fige en base SQLite l'origine cognitive du tour utilisateur (`turn_origin_model`) afin que les sous-tours d'outils et les cascades descendantes conservent la trace exacte du modèle initial. `_mutate_context_identity()` patche chirurgicalement la seule dernière occurrence de `<artifact id="AEC_modele">` (`<modele_actuel>` et `<modele_origine>`) pour préserver le cache de préfixes de l'API Gemini sur l'historique.
- **Rappels Cognitifs Multi-Axes (`AEC_REMINDERS`) & Drainage Atomique (`EchoAEC.drain_pending_directives`)** : Évalue `promptTokenCount` dans `prepare_context()` pour déclencher les rappels par paliers (`context_hygiene` 140k, `kernel` 100k, `factuality` 70k, `strategy` 60k, `alignment` 55k, `efficiency` 44k), purge les AEC orphelins postérieurs au `t_pivot`, et draine atomiquement (extraction + suppression en une transaction SQLite) les `aec_directive` et `aec_event` en attente avant de les sceller dans le Shadow.
- **Exclusion de la 400 de la Cascade** : Une `FatalAPIError` (400, payload rejeté) est remontée immédiatement à l'utilisateur sans cascade descendante, le contexte invalide l'étant pour tous les modèles.
- **Règles d'Élasticité Cognitive (`new_cognitive_level`)** : Interdiction formelle, dans la description de l'outil, de l'appeler en parallèle d'un autre outil. Applique une symétrie stricte sur la prohibition de l'inertie cognitive en `MODEL_ZEPHYR` comme en `MODEL_APOLLO`, et exclut automatiquement du menu d'escalade tout modèle ayant échoué durant le tour courant (`failed_models_this_turn`).
- **Routage HTTP/2 (`EchoGeminiClient`)** : S'appuie intégralement sur le client modulaire `EchoGeminiClient` (`echo_gemini_client.py`) pour bénéficier du Circuit Breaker OAuth2 et du multiplexage H2.

#### C. Classe `StreamProcessor`
**Rôle** : Moteur de flux temps-réel asynchrone.
- **Sémantique** : Parse la réponse SSE (Server-Sent Events) du LLM. Capte et compile les appels d'outils, met à jour le HUD d'interface et formate la réponse Markdown.
- **Gestion de l'Auto-Continue (MAX_TOKENS)** : Le pipeline gère nativement la troncature. Si le modèle s'arrête prématurément (MAX_TOKENS) au milieu d'un texte ou d'un appel d'outil massif, le système injecte dynamiquement une instruction de continuation via une balise sémantique `<artifact id="AEC_directive">` stricte et relance automatiquement la génération de façon transparente. Le préfixe UI Status pour ces rappels d'alignement est `🛤️ Alignement du Modèle`.

#### D. Classe `Pipe` (Point d'Entrée OWUI)
**Rôle** : Interface de connexion conforme à la signature Open WebUI. Initialise les Valves, intercepte en **Fast-Track** les tâches système d'Open WebUI (`title_generation`, `tags_generation`, `follow_up_generation`) vers `MODEL_ZEPHYR` en `stateless_bypass` (en excluant le message `role == "system"`, avec `maxOutputTokens: 8192`, `thinkingLevel: "low"`, `responseMimeType: "application/json"`, et filtrage des `parts` de type `thought`), assure lors de `tags_generation` une **réconciliation active agnostique** (injection SQLite du catalogue d'étiquettes existantes de l'Utilisateur et des tags du chat courant pour forcer le réemploi prioritaire et bloquer la prolifération de quasi-synonymes), alimente le cache `_TOOLS_CACHE` (bridé par `ECHO_TOOLS_CACHE_MAX_SIZE = 100`), gère le flux d'authentification PKCE par tunnel SSH éphémère, enregistre de manière idempotente le pont audio in-process (`register_echo_audio_routes()`), gère la boucle de cascade descendante dynamique (`get_descending_cascade`) avec forçage factorisé de `thinkingLevel="high"` sur échec API 429 (`get_generation_config(..., is_fallback_cascade=True)`, réinitialisé à `False` sur transition cognitive volontaire `new_cognitive_level` affichant l'identifiant technique via `_get_ui_display(new_target)`), rafraîchit en tâche de fond non-bloquante les quotas OAuth2 (`echo_auth.refresh_quota_if_needed()`), extrait les quotas **5h et Hebdomadaire** (`get_quota_summary`) pour les projeter dans `EchoUI.deploy_context_gauge()`, et lance directement le pipeline principal sans émettre de statut initial générique (`"⚙️ Traitement en cours..."` supprimé en v192.82).

## 3. Dépendances Logiques & Infrastructure
- Ce composant dépend intimement des nouvelles librairies modulaires de `14-owui-libs` (`echo_constants.py` pour le Registre Cognitif et `echo_state_manager.py` pour le requêtage de base de données).
- Il s'exécute de manière asynchrone dans le Tier 3 (Open WebUI).
- **Mise en cache PIP** : L'environnement d'exécution (Open WebUI) dispose désormais d'un cache local persistant (`PIP_CACHE_DIR=/app/backend/data/.cache/pip`) via le volume de données. Cela accélère considérablement l'installation à froid des dépendances Python requises par les modules Pipes.
