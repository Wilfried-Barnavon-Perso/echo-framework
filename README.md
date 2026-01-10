# ECHO Framework v5

**Infrastructure Agentique & Constitutionnelle**

> *Transformer l'IA d'un simple interlocuteur en un collaborateur technique auditable et souverain.*

La version 5 d'ECHO est un **Système d'Exploitation Agentique**. Elle combine la puissance d'une infrastructure Dockerisée (Le Corps) avec la rigueur logique d'un système constitutionnel hiérarchique (L'Esprit).

## 🏗️ Architecture Technique

L'intelligence est appuyée par une stack logicielle modulaire, orchestrée par **Open WebUI**.

| Composant | Dossier | Rôle |
| :--- | :--- | :--- |
| **Admin Manager** | `01-docker-admin-manager` | Dashboard d'administration et monitoring système. |
| **Python Worker** | `02-docker-python-worker` | Sandbox Docker pour l'exécution sécurisée de code Python. |
| **ECHO Engine** | `03-OWUI-functions` | Le "Cerveau" (Pipe). Injecte la constitution et gère le contexte. |
| **Agent Tools** | `04-OWUI-tools` | Les "Bras". Outils spécialisés (Recherche Web, Exécution Code). |
| **Filtres** | `05-OWUI-filters` | Sécurité (Token Monitor) et **Infrastructure (Bypass RAG - Requis)**. |
| **Browser Agent** | `06-docker-browser-agent` | Agent de navigation autonome. |

> **Note Critique :** Le filtre `bypass_rag.py` est **obligatoire** pour le bon fonctionnement du `pipe_engine`. Il intercepte les fichiers avant le traitement RAG natif d'Open WebUI, permettant au moteur ECHO de gérer le contexte de manière autonome.

## ⚖️ Système Constitutionnel

ECHO v5 implémente une **Hiérarchie des Normes** stricte via le `pipe_engine.py`. Le modèle est soumis à une "loi" interne supérieure à la requête utilisateur, garantissant sécurité, identité et respect du protocole avant toute exécution.

## 🚀 Déploiement

L'installation et le déploiement sont gérés par des scripts automatisés.

- **Déploiement VM** : `VM-ECHOv5-Deploy.ps1` (Script maître PowerShell).
- **Scripts Shell** : `00-Install/` contient les scripts de provisionning (`install-stack.sh`) et de mise à jour (`update-echo.sh`).

## 📚 Documentation

- **[MANIFEST.md](MANIFEST.md)** : Vision détaillée, philosophie et état du projet.
- **[VERSIONING.md](VERSIONING.md)** : Politique de gestion des versions et workflow de mise à jour.
- **Legacy** : Les concepts de la v4 sont archivés dans `_v4-legacy-concept/`.

---
*Version actuelle : v5.4.x (Stable)*