# 🌌 ECHO Framework - Connaissance Sémantique : `10-owui-pipes`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `10-owui-pipes`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le **Système Nerveux Central** (le Cortex) de l'intégration LLM dans ECHO, à savoir le moteur `pipe_engine.py`. Ce composant a été formellement implémenté en tant que composant *core* (Pipe Manifold) Open WebUI. Il est conçu pour intercepter, formater, gérer la mémoire et router toutes les requêtes utilisateur vers l'API Gemini ou l'ECHO N8N Orchestrator, tout en préservant une intégrité bit-perfect des métadonnées (Suture State).

## 2. Cartographie des Fichiers et Algorithmes

### `pipe_engine.py` (v192.82)

#### A. Classe `UserDataManager` (Le Tisserand de Mémoire)
**Rôle** : Gestionnaire de l'état asynchrone SQLite (proxy vers `EchoStateManager`). Il reconstruit l'historique exact de la conversation.
- **Invariant & Hash Cumulatif** (`calculate_invariant`, `calculate_cumulative`) : Crée une empreinte unique (hash) pour chaque tour de parole, assurant le verrouillage de version (Version Lock) de la conversation.
- **Shadow Suture** (`save_shadow`, `get_shadow`) : Persiste les requêtes et réponses structurées (incluant les cascades multi-messages, `tool_calls` et payloads multimodaux) en base SQLite locale pour pallier les limitations de persistance d'Open WebUI.
- **Registre KV Unifié (`echo_settings`)** : Persiste l'état de session (`save_session_setting`, `get_session_setting`, dont `turn_origin_model` et les paliers `aec_tier_*`) ainsi que les statistiques de contexte (`save_context_stats`, `get_last_context_stats`).
- **Signature & Bridge** (`save_signature_by_id`, `get_call_bridge`) : Fait le pont entre un appel d'outil déclenché (`call_id`) et son résultat renvoyé par OWUI au tour suivant. L'injection de la `thoughtSignature` (obligatoire pour Gemini 3+) est traitée dynamiquement et associée exclusivement au premier appel `functionCall` en cas d'appels parallèles, conformément à la spécification de l'API ([API Docs](https://ai.google.dev/gemini-api/docs/thinking#signatures)).

#### B. Classe `Orchestrator` (Le Cerveau Exécutif)
**Rôle** : Restauration du contexte Bit-Perfect, gestion du *Clamping Dynamique* et des mutations AEC.
- **Traduction & Déballage d'Outils (`echo_core.py`)** : S'appuie sur `convert_owui_tools()` et `unbox_tool_output()` (factorisés dans `echo_core.py`) pour normaliser les schémas d'outils en `camelCase` (`functionDeclarations`, `functionCallingConfig`), appliquer les politiques `MODEL_ENUM_BY_POLICY` via `ECHO_MODELS_REGISTRY`, et extraire les payloads multimodaux (`echo_tool_multiparts`) avec leur `call_id`.
- **Verrou de Tour (`turn_origin_model`) & `_mutate_context_identity()`** : Fige en base SQLite l'origine cognitive du tour utilisateur (`turn_origin_model`) afin que les sous-tours d'outils et les cascades descendantes conservent la trace exacte du modèle initial. `_mutate_context_identity()` patche chirurgicalement la seule dernière occurrence de `<artifact id="AEC_modele">` (`<modele_actuel>` et `<modele_origine>`) pour préserver le cache de préfixes de l'API Gemini sur l'historique.
- **Rappels Cognitifs Multi-Axes (`AEC_REMINDERS`) & Drainage Atomique (`EchoAEC.drain_pending_directives`)** : Évalue `promptTokenCount` dans `prepare_context()` pour déclencher les rappels par paliers (`context_hygiene` 120k, `kernel` 88k, `factuality` 55k, `alignment` 34k, `strategy` 34k, `efficiency` 22k), purge les AEC orphelins postérieurs au `t_pivot`, et draine atomiquement (extraction + suppression en une transaction SQLite) les `aec_directive` et `aec_event` en attente avant de les sceller dans le Shadow.
- **Règles d'Élasticité Cognitive (`new_cognitive_level`)** : Applique une symétrie stricte sur la prohibition de l'inertie cognitive en `MODEL_ZEPHYR` comme en `MODEL_APOLLO`, et exclut automatiquement du menu d'escalade tout modèle ayant échoué durant le tour courant (`failed_models_this_turn`).
- **Routage HTTP/2 (`EchoGeminiClient`)** : S'appuie intégralement sur le client modulaire `EchoGeminiClient` (`echo_gemini_client.py`) pour bénéficier du Circuit Breaker OAuth2 et du multiplexage H2.

#### C. Classe `StreamProcessor`
**Rôle** : Moteur de flux temps-réel asynchrone.
- **Sémantique** : Parse la réponse SSE (Server-Sent Events) du LLM. Capte et compile les appels d'outils, met à jour le HUD d'interface et formate la réponse Markdown.
- **Gestion de l'Auto-Continue (MAX_TOKENS)** : Le pipeline gère nativement la troncature. Si le modèle s'arrête prématurément (MAX_TOKENS) au milieu d'un texte ou d'un appel d'outil massif, le système injecte dynamiquement une instruction de continuation via une balise sémantique `<artifact id="AEC_directive">` stricte et relance automatiquement la génération de façon transparente. Le préfixe UI Status pour ces rappels d'alignement est `🛤️ Alignement du Modèle`.

#### D. Classe `Pipe` (Point d'Entrée OWUI)
**Rôle** : Interface de connexion conforme à la signature Open WebUI. Initialise les Valves, intercepte en **Fast-Track** les tâches système d'Open WebUI (`title_generation`, `tags_generation`, `follow_up_generation`) vers `MODEL_ZEPHYR` en `stateless_bypass` (en excluant le message `role == "system"`, avec `maxOutputTokens: 8192`, `thinkingLevel: "low"`, `responseMimeType: "application/json"`, et filtrage des `parts` de type `thought`), alimente le cache `_TOOLS_CACHE` (bridé par `ECHO_TOOLS_CACHE_MAX_SIZE = 100`), gère le flux d'authentification PKCE par tunnel SSH éphémère, enregistre de manière idempotente le pont audio in-process (`register_echo_audio_routes()`), et lance directement le pipeline principal sans émettre de statut initial générique (`"⚙️ Traitement en cours..."` supprimé en v192.82).

## 3. Dépendances Logiques & Infrastructure
- Ce composant dépend intimement des nouvelles librairies modulaires de `14-owui-libs` (`echo_constants.py` pour le Registre Cognitif et `echo_state_manager.py` pour le requêtage de base de données).
- Il s'exécute de manière asynchrone dans le Tier 3 (Open WebUI).
- **Mise en cache PIP** : L'environnement d'exécution (Open WebUI) dispose désormais d'un cache local persistant (`PIP_CACHE_DIR=/app/backend/data/.cache/pip`) via le volume de données. Cela accélère considérablement l'installation à froid des dépendances Python requises par les modules Pipes.
