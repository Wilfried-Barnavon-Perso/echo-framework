# ECHO Architecture - V5 (Infrastructure Agentique)

## 🏗️ Architecture V116 (Modulaire)
L'installation est pilotée par un script PowerShell "Assembleur" qui injecte les composants suivants via Cloud-Init :

| Composant | Dossier | Rôle |
|-----------|---------|------|
| **Install Stack** | `00-Install` | Script Bash principal (Provisioning Docker) |
| **Admin Console** | `01-docker-admin-manager` | Dashboard de Backup & Monitoring (Port 5001) |
| **Python Worker** | `02-docker-python-worker` | Sandbox d'exécution de code sécurisée (Port 5000) |
| **Pipe Engine** | `03-OWUI-functions` | Cerveau : Connecteur Gemini OAuth2 + Gestion de Contexte |
| **Agent Tools** | `04-OWUI-tools` | Bras : Recherche Web & Exécution Python |

## 📦 Legacy V4
L'ancienne architecture basée sur le System Prompt est archivée dans `v4-legacy-concept`.
