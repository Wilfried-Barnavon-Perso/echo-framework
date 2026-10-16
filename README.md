<div align="center">
  <img src="docs/logo-echo-full.png" alt="ECHO Framework Logo" width="350">
  
  # 🧠 ECHO Framework v5.223.20
  
  **The Sovereign Intelligence Orchestrator**
  
  [![Version](https://img.shields.io/badge/version-5.223.20-blue.svg)](#)
  [![Open WebUI](https://img.shields.io/badge/Powered%20by-Open%20WebUI-4CAF50.svg)](#)
  [![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-F9AB00.svg)](#)
  [![License](https://img.shields.io/badge/license-Apache%202.0-purple.svg)](#)

  *Parce qu'un LLM nu n'est qu'un miroir. Donnez-lui une véritable colonne vertébrale.*
</div>

---

## 👋 Qu'est-ce qu'ECHO ?

ECHO transforme Open WebUI en un **véritable agent sécurisé "à la Google AntiGravity"**. 
**ECHO** (Espace Cognitif Heuristique Opérationnel) est une infrastructure d'intelligence artificielle souveraine, conçue pour orchestrer les modèles de pointe (Google Gemini / Code Assist) et les modèles locaux. Ce n'est pas un simple wrapper d'API, mais un véritable système d'exploitation cognitif de niveau entreprise qui dote vos LLMs d'une autonomie totale, d'une persistance mémoire infaillible et d'une force d'action outillée sur votre infrastructure.

ECHO agit comme un **Kernel** : il impose ses règles méthodologiques et son déterminisme au modèle via un écosystème de pipes, de filtres et de conteneurs spécialisés. L'IA devient un collaborateur technique autonome doté d'une structure rigoureuse, d'une persistance contextuelle sans faille et d'une exécution isolée.

---

## 🏗️ Architecture Globale

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                  BOUCLIER PÉRIMÉTRIQUE & GOUVERNANCE                    │
│    WAF BunkerWeb (x-bw-config, Rate Limit 300r/s, DNS dyn.)             │
│    ECHO Auth Manager (IdP natif, SSO Forward Auth, MFA/TOTP, Argon2)    │
│    Admin Manager Modulaire (SSoT Fail-Closed, Backups 5 vol. SFTP/Cloud)│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────┐
│                          CORTEX (OPEN WEBUI)                            │
│   • Pipe Engine : routage cognitif, Verrou de Tour, suture SQLite       │
│   • SSOT Registry : gouvernance dual-backend (PROMETHEUS, APOLLO, ZEPHYR)         │
│   • Filtres & AEC : Smart Context, directives XML, drainage atomique    │
│   • HUD & UI Engine : protocole UCTP (256 Ko), interfaces mobile-first  │
└──────────────────┬───────────────────────────────────┬──────────────────┘
                   │                                   │
 ┌─────────────────▼─────────────────┐   ┌─────────────▼─────────────────┐
 │       MÉMOIRE & PERSISTANCE       │   │      ARSENAL D'EXÉCUTION      │
 │ • Qdrant (Graduation 1..5 & L1→L2)│   │ • Coding Worker (Python/Node) │
 │ • Validation RAG O(1) (SQLite)    │   │ • Edge Bridge (WebGPU/WASM)   │
 │ • Harrier-OSS 0.6B (GGUF F16)     │   │ • Browser Worker (Playwright) │
 │ • Codex Git multi-workspaces      │   │ • N8N Headless & Download Brk │
 │ • Identity Vault (Secrets & MCP)  │   │ • MCP Broker & Cache MSAL     │
 │ • Registre KV (echo_settings)     │   │ • Pont Audio In-Process       │
 └───────────────────────────────────┘   └───────────────────────────────┘
```

---

## ✨ Fonctionnalités Clés

- 🔒 **Souveraineté, Sécurité Périmétrique & Gouvernance SSoT Fail-Closed** : Vos bases vectorielles (Qdrant), votre historique de shadow-messages (SQLite) et vos fichiers (Codex) restent confinés localement sur deux réseaux IPAM isolés (`echo-network` et `echo-sandbox`). L'accès est verrouillé par **ECHO Auth Manager** (IdP natif avec hachage Argon2, MFA/TOTP et flux SSO Forward Auth `/api/verify` intégrant **l'invalidation proactive des sessions** via `/sso-logout`) placé derrière un reverse-proxy WAF **BunkerWeb**. L'**Admin Manager modulaire** applique une **politique SSoT Fail-Closed stricte** (`webui.db::user`, 3 verrous de sécurité cumulatifs empêchant toute purge en cas d'indisponibilité, réconciliation atomique des 5 volumes de données à la suppression d'un compte, et sauvegardes `tar.xz` intégrales avec télémétrie temps réel et externalisation NAS SFTP / Cloud Rclone).
- 🧠 **Mémoire Biomimétique Graduée (PACP/PRAC), Consolidation Nocturne & Edge Bridge** : Fini l'amnésie des LLMs et le gouffre de la fenêtre glissante. Calquée sur la psychologie cognitive humaine, la mémoire d'ECHO capte les signaux dès la première interaction et les fait mûrir sur **5 paliers d'importance** (`1 - Trivial` dès la 1ère observation isolée, `2 - Mineur` à la 2e récurrence, `3 - Utile` ou `4 - Majeur` dès 3 occurrences concordantes, et `5 - Axiome` sur injonction explicite ou invariant stratégique). Chaque nuit, l'Admin Manager opère une **consolidation sémantique (`Lvl1 → Lvl2`) par centroïde L2-normalisé** qui fusionne les observations triviales récurrentes en souvenirs durables sans solliciter de LLM. Cette mémoire s'appuie sur une **validation O(1) en amont** (`is_embedded` dans SQLite) et un **Edge Embedding Bridge** (`onnx-community/harrier-oss-v1-0.6b-ONNX` en `q4f16`, WebGPU / WASM sur Desktop avec bypass serveur-side sur mobile, relayé par **Harrier-OSS-v1-0.6B** GGUF F16 sous `llama.cpp`).
- 🪢 **Suture Sémantique (Bit-perfect), Verrou de Tour & SSOT** : Le Pipe Engine garantit une reprise de session chirurgicale via un Cumulative Hash, un tri chronologique déterministe des `thoughtSignature` et un **Verrou de Tour (`turn_origin_model`)** préservant le cache de préfixes Gemini. Toutes les capacités LLM sont régies par le registre unifié `ECHO_MODELS_REGISTRY` (`MODEL_PROMETHEUS`, `MODEL_APOLLO`, `MODEL_ZEPHYR`, `MODEL_DISTILLATION`), doté d'un **Circuit Breaker OAuth2** (Fast-Failover Intra-Retry en HTTP/2), d'un **Drainage Transactionnel Atomique** des directives XML (`<artifact id="AEC_directive">`) et de **Rappels Cognitifs Multi-Axes (`AEC_REMINDERS`)**.
- 🔐 **Identity Vault & Sécurité Applicative** : Gestionnaire de secrets intégré permettant d'enregistrer et de manipuler des accès applicatifs (N8N, serveurs MCP) dans SQLite sans jamais exposer les clés en clair au LLM (injection par macros `__ECHO_SECRET_...` à l'exécution).
- 🛠️ **Sovereign Toolbox & Workers Dédiés** :
  - **ECHO N8N Orchestrator & Download Broker** : Pilotage direct de workflows d'automatisation avec distinction stricte entre Sandbox éphémère (`Execute Workflow Trigger`, sortie synchrone bornée à 8 Ko anti-OOM) et Déploiement Démon permanent (Webhooks/Crons), conception assistée (`delegate_to_n8n_grapher`), recherche sur le Hub N8N et collecte asynchrone des fichiers produits via le **Download Broker** (`__ECHO_DOWNLOAD_DIR__`).
  - **MCP Broker (Proxy HTTP sans BDD & Cache MSAL Persistant)** : Serveur Model Context Protocol natif agissant comme un **Proxy HTTP complet sans accès à la base de données** (`m5_proxy_mcp.py`, sessions `sse` et `streamable_http` mises en cache en mémoire par couple utilisateur/service), couplé à l'outil universel `echo_mcp_tool.py` qui injecte les identifiants depuis SQLite en amont. Intègre un volume persistant dédié (`echo-mcp-cache`) préservant les jetons OAuth2 MSAL Microsoft 365 (`.microsoft_mcp_token_cache.json`) avec invalidation automatique par empreinte MD5 (`.cred_hash`) en cas de changement d'identifiants.
  - **Pont Audio In-Process (STT & TTS)** : Passerelle vocale native intégrée au Cortex (`/echo/v1/audio`) combinant la transcription multimodale haute précision via Gemini `MODEL_ZEPHYR` (sniffing automatique des magic bytes audio WebM, WAV, OGG, FLAC, MP3, MP4/M4A jusqu'à 14 Mo) et la synthèse vocale neuronale en streaming via Microsoft Edge Read-Aloud (`edge-tts`, voix `fr-FR-VivienneMultilingualNeural`, vitesse nominale `1.0`, pitch feutré `-2Hz`).
  - **Coding Worker Sandbox** : Conteneur isolé sous Python 3.14 et Node.js 22 avec confinement `bubblewrap` (`bwrap`), linting pré-exécution, Bidi Polling temps réel, multiplexage JSONL via UCTP (`echo_monitor`) et Supervisor Pattern (JS Heartbeat `/kill`).
  - **Browser Worker** : Navigation web autonome asynchrone propulsée par Playwright (bridée à 9 FPS), dotée d'un contournement WAF/Anti-Bot avancé, d'un Lidar Check anti-Layout Shift et d'un ciblage géométrique de précision (Viseur Rouge).
  - **ECHO Codex, Delphi & Skills** : Éditeur Monaco multi-workspaces (`main` / `sandbox`) avec lecteur PDF WYSIWYG, gestionnaire Git embarqué (`dulwich`), tables rondes multi-agents parallélisées (`consult_council`, `consult_supervised_workers`) et gestionnaire de compétences (`Skill Management`).
  - **Moteur de Rendu UI & Protocole UCTP** : Composants HUD flottants, tiroir d'actions natif (`ECHO App Drawer`) et protocole de transfert fragmenté **UCTP** (chunks de 256 Ko) optimisés pour interfaces mobiles (`100dvh`) et desktop.

---

## 🚀 Déploiement Rapide

L'infrastructure s'installe en quelques minutes via des scripts idempotents orchestrant la stack Docker complète par paliers de dépendances.

**Sur Linux Natif (Ubuntu / Debian) :**
```bash
curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-linux.sh | sudo bash
```

**Sur WSL2 (Windows) :**
```bash
curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-wsl2.sh | sudo bash
```

**Sur Hyper-V (Windows) :**
*(Provisionne et configure automatiquement une VM Linux dédiée sous Ubuntu 24.04 avec Cloud-Init)*
```powershell
.\install-hyperv.ps1
```

---

## 📚 Documentation Technique

Pour explorer en profondeur la cartographie du système, la gouvernance des agents ou les modules du framework :

- 🗺️ **[Cartographie & Gouvernance des Agents](AGENTS.md)**
- 🔢 **[Politique de Versioning](VERSIONING.md)**
- 🌐 **[Documentation Technique Interactive](docs/index.html)**

---

*Built with 🧠 & ❤️ by Wilfried BARNAVON. Ready to resonate.*
