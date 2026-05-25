# 🧠 ECHO Framework v5.153.0

<div align="center">
  <img src="docs/logo-echo-full.png" alt="ECHO Framework Logo" width="350">
</div>

**Espace Cognitif Heuristique Opérationnel**

> *"Transformer l'IA d'un simple interlocuteur en un collaborateur technique souverain, auditable et doté d'une mémoire absolue."*

---

## 📜 Manifeste : De la Réverbération à la Résonance

Le projet **ECHO** tire son nom de la nymphe mythologique condamnée à ne jamais pouvoir parler en premier, perdant ainsi toute intention propre. Sans intervention, un LLM "nu" est le miroir servile de cette tragédie : il est condamné à la complaisance et à l'inertie ontologique.

**ECHO brise cette malédiction.** En injectant un **Kernel** (colonne vertébrale d'instructions et de règles), nous donnons au modèle une existence qui précède l'interaction. L'IA cesse d'être une *réverbération* passive pour devenir une véritable **résonance** ferme. Elle ne se contente pas de prédire des mots : elle choisit la meilleure méthode de résolution, structure sa pensée et impose sa propre rigueur méthodologique.

### Les 4 Méta-Principes Fondateurs
1.  **MPDI (Identité)** : L'IA reconnaît son cadre technique et sa nature d'orchestrateur souverain.
2.  **MPAH (Arbitrage Hiérarchique)** : Les lois du framework sont supérieures aux caprices ou aux biais de l'utilisateur.
3.  **MPCE (Conditions d'Exécution)** : Calibrage dynamique des hyperparamètres (Thinking Level, Température) selon la criticité.
4.  **MPSI (Sécurité et Intégrité)** : Protection active contre le contournement des protocoles et la dérive sémantique.

---

## 🏗️ Architecture du Système : "Sovereign Intelligence"

ECHO transforme l'accès aux modèles cloud en une infrastructure agentique privée et hautement sécurisée.

### 🧩 Les Composants Cœurs
*   **Le Cortex (Pipe Engine - `10-owui-pipes`)** : L'unité centrale de traitement. Il gère la **Suture Sémantique** (reconstruction bit-perfect via Shadows SQLite), la **Cascade Cognitive** (routage dynamique inter-modèles) et la **Thought Hygiene** (gestion chirurgicale de la Chain-of-Thought via `thoughtSignature`).
*   **La Conscience (Context Filter - `11-owui-filters`)** : Gateway cognitive incluant la **Mémoire Organique V2.5** (distillation probabiliste avec recouvrement adaptatif et fusion sémantique vectorielle) et le **Smart Context** (analyse multimodale > 256 Ko via Gemini Flash).
*   **L'Admin Manager (`20-docker-admin-manager`)** : Dashboard de monitoring, backup et gestion de la stack Docker.

### 🛠️ L'Arsenal des Outils (Sovereign Toolbox)
Chaque outil interagit directement avec le **Vault** (coffre-fort) utilisateur :
*   🧠 **Proactive Memory** : Commandes chirurgicales `memorize_that`, `prepare_forget_memory`, `execute_forget_memory` et `list_memory_topics` sous contrôle utilisateur.
*   🌐 **Navigation Engine** : Pilotage de Chromium via Playwright avec retour visuel HUD en temps réel.
*   🐍 **Python Code Executor** : Sandbox isolée pour l'analyse de données et la génération de graphiques.
*   🔍 **Sovereign Search** : Recherche web multi-sources via SearXNG préservant la confidentialité.
*   🧠 **Cognitive Agents** : Délégation récursive et sous-réflexions spécialisées (Conseil des Experts).
*   📂 **Vault Explorer** : Exploration brute et sondage sémantique des documents.
*   📊 **Context Gauge** : Monitoring visuel de la consommation de tokens et de l'état du cache.

---

## 🚀 Fonctions Clés & Innovations

| Fonction | Description |
| :--- | :--- |
| **Suture Sémantique** | Restauration parfaite de l'historique Gemini incluant les médias binaires et les états de raisonnement. |
| **Smart Context** | Distillation automatique des documents massifs pour maximiser l'efficience du contexte. |
| **Mémoire Organique** | Système de mémorisation asynchrone avec fusion sémantique et fenêtre de recouvrement adaptative. |
| **Arsenal Proactif** | Contrôle granulaire de la mémoire vectorielle par l'IA via function-calling avec confirmation humaine. |
| **Shadow Shadows** | Registre d'ombres persistant garantissant l'immunité contre l'amnésie des interfaces volatiles. |
| **Bypass PKCE** | Authentification Google AI Pro/One totalement intégrée et transparente. |

---

## 🚀 Installation Rapide

### Linux Natif (Ubuntu / Debian)
```bash
curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-linux.sh | sudo bash
```

### WSL2 (Windows Subsystem for Linux 2)
```bash
curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-wsl2.sh | sudo bash
```

> **Option `--branch`** : ajoutez `-- --branch dev` après `sudo bash` pour cibler une branche spécifique.  
> **Idémpotence** : les scripts sont relancables. Si ECHO est déjà installé, ils effectuent une mise à jour vers la branche cible.

---

## 🔧 Déploiement & Infrastructure

ECHO est conçu pour un déploiement autonome sur infrastructure souveraine.

| Méthode | Script | Usage recommandé |
| :--- | :--- | :--- |
| **Windows / Hyper-V** | `deploy-hyperv.ps1` | Production souveraine (VM dédiée) |
| **Linux Natif** | `install-linux.sh` | Serveur Ubuntu/Debian dédié |
| **WSL2** | `install-wsl2.sh` | Développement & test sous Windows |

*   **Provisioning Docker** : `00-echo-scripts/install-stack.sh` orchestre la stack complète (Open WebUI, Qdrant, Redis, Workers).
*   **Sécurisation Edge** : Intégration native de **BunkerWeb** (WAF) pour une exposition sécurisée avec SSL automatique.

---

## 📚 Documentation

Pour approfondir, consultez le corpus documentaire dans le dossier `/docs` :
- **[Introduction](docs/index.html)** : Vue d'ensemble du framework.
- **[Fondations](docs/00_fondations.html)** : Philosophie détaillée et mythologie d'ECHO.
- **[High-Level Design (HLD)](docs/01_hld_architecture.html)** : L'architecture "Ombre Riche" et OWUI Middleware.
- **[Le Cortex](docs/06_pipe.html)** : Analyse technique de l'algorithme de suture.
- **[L'Arsenal](docs/07_arsenal_outils.html)** : Catalogue complet des protocoles d'outils.

---
*Version actuelle : v5.153.0 "Sovereign Intelligence" | Copyright © 2026 Wilfried BARNAVON*
