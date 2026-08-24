# 🌌 ECHO Framework - Connaissance Sémantique : `10-owui-pipes`

> **ATTENTION AGENTS** : Ce document est la base de connaissances sémantique exclusive du dossier `10-owui-pipes`. Il complète les règles globales définies dans le `AGENTS.md` à la racine.

## 1. Rôle du Dossier

Ce dossier contient le **Système Nerveux Central** (le Cortex) de l'intégration LLM dans ECHO, à savoir le moteur `pipe_engine.py`. Ce composant est un Pipe (Manifold) Open WebUI conçu pour intercepter, formater, gérer la mémoire et router toutes les requêtes utilisateur vers l'API Gemini ou l'ECHO N8N Orchestrator, tout en préservant une intégrité bit-perfect des métadonnées (Suture State).

## 2. Cartographie des Fichiers et Algorithmes

### `pipe_engine.py`

#### A. Classe `UserDataManager` (Le Tisserand de Mémoire)
**Rôle** : Gestionnaire de l'état asynchrone SQLite. Il reconstruit l'historique exact de la conversation.
- **Invariant & Hash Cumulatif** (`calculate_invariant`, `calculate_cumulative`) : Crée une empreinte unique (hash) pour chaque tour de parole, assurant le verrouillage de version (Version Lock) de la conversation.
- **Shadow Suture** (`save_shadow`, `get_shadow`) : Persiste les requêtes et réponses structurées (incluant les tool_calls et payloads Base64 complexes) en base SQLite locale pour pallier les limitations de persistance d'Open WebUI.
- **Signature & Bridge** (`save_signature_by_id`, `get_call_bridge`) : Fait le pont entre un appel d'outil déclenché (call_id) et son résultat renvoyé par OWUI au tour suivant.

#### B. Classe `Orchestrator` (Le Cerveau Exécutif)
**Rôle** : Traduction des schémas OWUI vers l'API cible, gestion du *Clamping Dynamique*.
- **`convert_owui_tools()`** : Parse les schémas d'outils OWUI et les traduit dans le format strict Gemini (OpenAPI), en appliquant les politiques de sécurité (Tool Forcing, Model Policy) via `ECHO_MODELS_REGISTRY`.
- **`_mutate_context_identity()`** : Modifie à la volée le System Prompt ou l'identité si un *reverse-lookup* (Auto-heal) impose un changement de modèle (ex: Fallback vers MODEL_LITE en cas de surcharge).
- **`_unbox_tool_output()`** : Extrait et normalise les réponses asynchrones des outils (comme la réception multimédia issue de `echo_tool_multiparts`).
- **Routage HTTP/2 (`EchoGeminiClient`)** : S'appuie désormais intégralement sur le client consolidé `EchoGeminiClient` (provenant de `echo_utils.py`) pour bénéficier du Circuit Breaker OAuth2 et du multiplexage H2, déportant ainsi la logique réseau hors du Pipe.

#### C. Classe `StreamProcessor`
**Rôle** : Moteur de flux temps-réel asynchrone.
- **Sémantique** : Parse la réponse SSE (Server-Sent Events) du LLM. Capte et compile les appels d'outils (Tool Calls) en cours de frappe, met à jour le HUD d'interface et formate la réponse Markdown avant de l'envoyer au client OWUI.

#### D. Classe `Pipe` (Point d'Entrée OWUI)
**Rôle** : Interface de connexion conforme à la signature Open WebUI. Initialise les Valves (paramètres réglables par l'Admin) et lance le pipeline via `pipe()`.

## 3. Dépendances Logiques
- Ce composant est extrêmement dépendant des librairies partagées `14-owui-libs` (`echo_constants.py` pour le Registre Cognitif et `echo_utils.py` pour le requêtage de base de données).
- Il s'exécute de manière asynchrone dans le Tier 3 (Open WebUI).
