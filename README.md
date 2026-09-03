<div align="center">
  <img src="docs/logo-echo-full.png" alt="ECHO Framework Logo" width="350">
  
  # 🧠 ECHO Framework v5.200.83
  
  **The Sovereign Intelligence Orchestrator**
  
  [![Version](https://img.shields.io/badge/version-5.200.83-blue.svg)](#)
  [![Open WebUI](https://img.shields.io/badge/Powered%20by-Open%20WebUI-4CAF50.svg)](#)
  [![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-F9AB00.svg)](#)
  [![License](https://img.shields.io/badge/license-Apache%202.0-purple.svg)](#)

  *Parce qu'un LLM nu n'est qu'un miroir. Donnez-lui une véritable colonne vertébrale.*
</div>

---

## 👋 Qu'est-ce qu'ECHO ?

Imaginez **Open WebUI sous stéroïdes**. 
**ECHO** (Espace Cognitif Heuristique Opérationnel) est une infrastructure d'intelligence artificielle souveraine, conçue pour orchestrer les modèles de pointe (Google Gemini) et les modèles locaux au-dessus d'Open WebUI. Ce n'est pas un simple wrapper d'API, mais un véritable système d'exploitation cognitif de niveau entreprise.

ECHO agit comme un **Kernel** : il impose ses règles méthodologiques et son déterminisme au modèle via un écosystème de pipes, de filtres et de conteneurs spécialisés. L'IA devient un collaborateur technique autonome doté d'une structure rigoureuse, d'une persistance contextuelle sans faille et d'une force d'action outillée.

---

## 🏗️ Architecture Globale

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                        BOUCLIER PÉRIMÉTRIQUE                            │
│    WAF BunkerWeb (ModSecurity granulaire) • Résolution DNS Dynamique    │
│    ECHO Auth Manager (IdP natif, SSO, MFA/TOTP, anti-session loop)      │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────┐
│                          CORTEX (OPEN WEBUI)                            │
│   • Pipe Engine : routage cognitif, suture SQLite bit-perfect (shadows) │
│   • SSOT Registry : gouvernance unifiée des modèles (PRO, FLASH, LITE)  │
│   • Filtres & Contexte : Smart Context, injection proprioceptive XML    │
│   • HUD & UI Engine : interfaces dynamiques et réactives sur mobile     │
└──────────────────┬───────────────────────────────────┬──────────────────┘
                   │                                   │
 ┌─────────────────▼─────────────────┐   ┌─────────────▼─────────────────┐
 │       MÉMOIRE & VECTORISATION     │   │      ARSENAL D'EXÉCUTION      │
 │ • Qdrant local                    │   │ • Python Sandbox (Isolée)     │
 │ • Harrier-OSS 0.6B (GGUF Q8_0)    │   │ • Edge Bridge (WebGPU/WASM)   │
 │ • Codex Git avec verrous async    │   │ • Browser Worker (Playwright) │
 │ • Identity Vault (Secrets & MCP)  │   │ • N8N Headless Engine         │
 │ • FastMCP Broker                  │   │ • Delphi Council (Multi-agent)│
 └───────────────────────────────────┘   └───────────────────────────────┘
```

---

## ✨ Fonctionnalités Clés

- 🔒 **Souveraineté & Sécurité Périmétrique** : Vos bases vectorielles (Qdrant), votre historique de shadow-messages (SQLite) et vos fichiers (Codex) restent confinés localement. L'accès est verrouillé par **ECHO Auth** (IdP natif avec MFA/TOTP et flux SSO durci) placé derrière un reverse-proxy WAF **BunkerWeb** avec règles ModSecurity et résolutions DNS dynamiques.
- 🧠 **Mémoire Vectorisée O(1) & Edge Bridge** : Fini le gouffre de la fenêtre glissante. ECHO associe une indexation vectorielle locale instantanée à un **Edge Embedding Bridge filter** exploitant l'accélération matérielle `q4f16` (WebGPU / WASM) directement sur le poste client. En relais serveur, le worker local fait tourner le modèle compact **Harrier-OSS-v1-0.6B** quantifié en GGUF Q8_0 sous `llama.cpp`. Zéro fuite de données et préservation totale du budget d'attention.
- 🪢 **Suture Sémantique (Bit-perfect) & SSOT** : Le Pipe Engine garantit une reprise de session chirurgicale via un Cumulative Hash et un suivi de version rigoureux. Toutes les capacités des LLM sont régies par un registre cognitif unifié (`ECHO_MODELS_REGISTRY`), doté d'un **Circuit Breaker OAuth2** (Fast-Failover Intra-Retry) multiplexé en HTTP/2 et d'un mécanisme d'auto-guérison de modèle orphelin.
- 🔐 **Identity Vault & Sécurité Applicative** : Gestionnaire de secrets intégré permettant d'enregistrer et de manipuler des accès applicatifs ou des serveurs distants Model Context Protocol (MCP) sous contrôle et validation explicite de l'utilisateur.
- 🛠️ **Sovereign Toolbox & Workers Dédiés** :
  - **ECHO N8N Orchestrator** : Déploiement et pilotage headless de workflows automatisés, avec exécution asynchrone et gestion de sous-fils d'agents.
  - **FastMCP Broker** : Passerelle proxy unifiée pour l'interrogation normée des gisements de données internes et académiques.
  - **Python Worker Sandbox** : Conteneur dédié sous Python 3.14 pour l'exécution d'analyses de données et d'algorithmes en environnement hermétique.
  - **Browser Worker** : Navigation web autonome asynchrone propulsée par Playwright.
  - **ECHO Codex & Delphi** : Espace documentaire avec éditeur Monaco, gestionnaire Git embarqué (dulwich) protégé contre les accès concurrents par verrous asynchrones, et tables rondes multi-agents parallélisées (`consult_council`, `consult_supervised_workers`).
  - **Moteur de Rendu UI & HUD** : Composants interactifs riches et cockpit de pilotage cognitif optimisés pour interfaces mobiles et desktop.

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
