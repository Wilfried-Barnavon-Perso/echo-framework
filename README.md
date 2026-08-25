<div align="center">
  <img src="docs/logo-echo-full.png" alt="ECHO Framework Logo" width="350">
  
  # 🧠 ECHO Framework v5.199.39
  
  **The Sovereign Intelligence Orchestrator**
  
  [![Version](https://img.shields.io/badge/version-5.199.39-blue.svg)](#)
  [![Open WebUI](https://img.shields.io/badge/Powered%20by-Open%20WebUI-4CAF50.svg)](#)
  [![Gemini](https://img.shields.io/badge/AI-Google%20Gemini-F9AB00.svg)](#)
  [![License](https://img.shields.io/badge/license-Apache%202.0-purple.svg)](#)

  *Parce qu'un LLM nu n'est qu'un miroir. Donnez-lui une véritable colonne vertébrale.*
</div>

---

## 👋 Qu'est-ce qu'ECHO ?

Imaginez **Open WebUI sous stéroïdes**. 
**ECHO** (Espace Cognitif Heuristique Opérationnel) est une infrastructure d'intelligence artificielle souveraine, conçue pour orchestrer les modèles Gemini de Google au-dessus d'Open WebUI. Ce n'est pas un simple wrapper d'API, mais un framework de contrôle autonome de niveau entreprise.

ECHO agit comme un **Kernel** : il impose ses règles méthodologiques au modèle via un écosystème de "Pipes", "Filtres" et de "Workers" spécialisés. L'IA devient un collaborateur technique capable de structure, de persistance et d'action.

## ✨ Fonctionnalités Clés

- 🔒 **Souveraineté et Sécurité** : Vos bases vectorielles (Qdrant), votre historique (SQLite) et vos fichiers (Codex) restent intégralement confinés localement. Le système intègre **ECHO Auth** (IdP natif avec MFA/TOTP) couplé à un WAF **BunkerWeb** pour une protection périmétrique absolue.
- 🧠 **Mémoire Vectorisée & RAG O(1)** : Abandon de la fenêtre glissante obsolète au profit d'une indexation stateless Zéro-Latence et d'un instantané contextuel plat (YAML). Les documents lourds sont synthétisés via un système de RAG (Retrieval-Augmented Generation) avancé préservant le budget de contexte.
- ⚡ **Suture Sémantique (Bit-perfect)** : Le Pipe Engine garantit une reprise de session identique au bit près, en restaurant dynamiquement les états via un routage cognitif et un **OAuth2 Circuit Breaker** assurant la résilience de l'API (Fast-Failover Intra-Retry) via multiplexage HTTP/2.
- 🛠️ **Sovereign Toolbox & Workers** :
  - **ECHO N8N Orchestrator** : Pont asynchrone natif pour le déploiement et le pilotage de workflows d'automatisation headless (Daemon/Webhook).
  - **MCP Broker** : Serveur FastMCP pour une intégration fluide et normée des données Corporate et Academic.
  - **Python & Browser Workers** : Sandbox d'exécution de code isolée et navigation web autonome (Playwright).
  - **ECHO Codex & Delphi** : Éditeur multi-langage (Monaco) avec gestion Git intégrée (dulwich) et consultation multi-agents parallélisée.
  - **Edge Inference** : Déchargement de l'inférence vectorielle directement sur le GPU client (WebGPU/WASM) ou le worker local `bge-m3`.

## 🚀 Déploiement Rapide

L'infrastructure s'installe via des scripts idempotents orchestrant la stack Docker complète (plus de 10 conteneurs spécialisés).

**Sur Linux Natif (Ubuntu/Debian) :**
```bash
curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-linux.sh | sudo bash
```

**Sur WSL2 (Windows) :**
```bash
curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-wsl2.sh | sudo bash
```

**Sur Hyper-V (Windows) :**
*(Crée et configure automatiquement une VM Linux dédiée avec Cloud-Init)*
```powershell
.\install-hyperv.ps1
```

## 📚 Documentation Technique

Pour comprendre en profondeur l'architecture, la cascade cognitive (PRO → FLASH → LITE) ou le double système RAG :
👉 **[Consultez la documentation officielle](docs/index.html)**

---
*Built with 🧠 & ❤️ by Wilfried BARNAVON. Ready to resonate.*
