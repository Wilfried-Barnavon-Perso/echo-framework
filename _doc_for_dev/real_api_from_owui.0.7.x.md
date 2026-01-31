# Open WebUI API Documentation (v0.7.x)

Cette documentation est générée à partir d'une analyse exhaustive du code source (`open_webui/routers/*` et `open_webui/main.py`). Elle couvre l'ensemble des endpoints disponibles.

**Base URL API** : `/api/v1`
**Base URL Ollama** : `/ollama`
**Base URL OpenAI** : `/openai`

**Authentification** :
*   Header : `Authorization: Bearer <JWT_TOKEN>`
*   Certains endpoints (Ollama/OpenAI proxy) acceptent aussi des clés API spécifiques.

---

## 1. Authentification & Session (`/api/v1/auths`)

Gestion des comptes, connexions et clés API.

| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `POST` | `/auths/signin` | Connexion utilisateur. | `SigninForm` {email, password} |
| `POST` | `/auths/signup` | Inscription nouvel utilisateur. | `SignupForm` {email, password, name, profile_image_url} |
| `GET` | `/auths/` | Récupère les infos de la session courante (User + Token). | - |
| `GET` | `/auths/signout` | Déconnexion (invalide le token/cookies). | - |
| `POST` | `/auths/update/profile` | Met à jour le profil (nom, image). | `UpdateProfileForm` |
| `POST` | `/auths/update/password` | Change le mot de passe. | `UpdatePasswordForm` |
| `POST` | `/auths/update/timezone` | Met à jour le fuseau horaire. | `UpdateTimezoneForm` |
| `POST` | `/auths/ldap` | Connexion via LDAP. | `LdapForm` |
| `GET` | `/auths/api_key` | Récupère la clé API de l'utilisateur. | - |
| `POST` | `/auths/api_key` | Génère/Régénère la clé API. | - |
| `DELETE` | `/auths/api_key` | Supprime la clé API. | - |
| `POST` | `/auths/add` | (Admin) Ajout manuel d'utilisateur. | `AddUserForm` |
| `GET` | `/auths/admin/config` | (Admin) Récupère la config auth. | - |
| `POST` | `/auths/admin/config` | (Admin) Modifie la config auth. | `AdminConfig` |
| `GET` | `/auths/admin/details` | (Admin) Détails administrateur. | - |

---

## 2. Utilisateurs (`/api/v1/users`)

Gestion administrative et recherche d'utilisateurs.

| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `GET` | `/users/` | (Admin) Liste les utilisateurs (paginée). | `?page=`, `?query=`, `?order_by=` |
| `GET` | `/users/all` | (Admin) Liste tous les utilisateurs (complet). | - |
| `GET` | `/users/search` | Recherche d'utilisateurs. | `?query=` |
| `GET` | `/users/{user_id}` | Détails d'un utilisateur par ID. | - |
| `POST` | `/users/{user_id}/update` | (Admin) Met à jour un utilisateur. | `UserUpdateForm` |
| `DELETE` | `/users/{user_id}` | (Admin) Supprime un utilisateur. | - |
| `GET` | `/users/groups` | Groupes de l'utilisateur courant. | - |
| `GET` | `/users/permissions` | Permissions de l'utilisateur. | - |
| `GET` | `/users/user/settings` | Paramètres UI de l'utilisateur. | - |
| `POST` | `/users/user/settings/update` | Met à jour les paramètres UI. | `UserSettings` |
| `GET` | `/users/user/info` | Infos additionnelles utilisateur. | - |
| `POST` | `/users/user/info/update` | Met à jour les infos additionnelles. | JSON Dict |

---

## 3. Chats (`/api/v1/chats`)

Historique et gestion des conversations.

| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `GET` | `/chats/` | Liste des conversations (titres/IDs). | `?page=`, `?include_pinned=` |
| `POST` | `/chats/new` | Crée une nouvelle conversation. | `ChatForm` |
| `GET` | `/chats/{id}` | Récupère une conversation complète. | - |
| `POST` | `/chats/{id}` | Met à jour une conversation (ajout messages). | `ChatForm` |
| `DELETE` | `/chats/{id}` | Supprime une conversation. | - |
| `DELETE` | `/chats/` | Supprime TOUTES les conversations de l'utilisateur. | - |
| `POST` | `/chats/{id}/clone` | Clone une conversation. | - |
| `POST` | `/chats/{id}/archive` | Archive/Désarchive une conversation. | - |
| `POST` | `/chats/{id}/share` | Partage une conversation. | - |
| `POST` | `/chats/{id}/tags` | Ajoute un tag. | `TagForm` |
| `GET` | `/chats/tags/all` | Liste tous les tags de l'utilisateur. | - |
| `GET` | `/chats/search` | Recherche dans les conversations. | `?text=` |

---

## 4. Modèles (`/api/v1/models` & `/api/models`)

Gestion des modèles (OpenAI, Ollama, Pipes).

| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/models` | Liste tous les modèles actifs (OpenAI/Ollama/Custom). | - |
| `GET` | `/models/list` | Liste les modèles (format DB interne). | - |
| `GET` | `/models/model` | Récupère un modèle par ID. | `?id=model_id` |
| `POST` | `/models/create` | Crée un modèle personnalisé (Pipe/Filter). | `ModelForm` |
| `POST` | `/models/model/update` | Met à jour un modèle. | `ModelForm` |
| `POST` | `/models/model/delete` | Supprime un modèle. | `ModelIdForm` |
| `POST` | `/models/model/toggle` | Active/Désactive un modèle. | `?id=model_id` |
| `GET` | `/models/base` | Liste les modèles de base disponibles. | - |

---

## 5. Fichiers & RAG (`/api/v1/files` & `/api/v1/retrieval`)

Gestion des fichiers (RAG) et recherche vectorielle.

### Fichiers (`/files`)
| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `POST` | `/files/` | Upload de fichier. | Multipart (`file`), `metadata`, `process=true` |
| `GET` | `/files/` | Liste les fichiers. | - |
| `DELETE` | `/files/{id}` | Supprime un fichier. | - |
| `GET` | `/files/{id}/content` | Télécharge le contenu brut. | - |
| `GET` | `/files/{id}/data/content` | Récupère le texte extrait. | - |

### Retrieval (`/retrieval`)
| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `GET` | `/retrieval/config` | Configuration RAG actuelle. | - |
| `POST` | `/retrieval/config/update` | (Admin) Mise à jour config RAG. | `ConfigForm` |
| `POST` | `/retrieval/process/file` | Lance le processing (embedding) d'un fichier. | `ProcessFileForm` |
| `POST` | `/retrieval/process/web` | Crawl et indexe une URL. | `ProcessUrlForm` |
| `POST` | `/retrieval/embedding/update` | (Admin) Change le modèle d'embedding. | `EmbeddingModelUpdateForm` |

---

## 6. Outils (`/api/v1/tools`)

Outils appelables par les modèles (Function Calling).

| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `GET` | `/tools/` | Liste les outils installés. | - |
| `POST` | `/tools/create` | Installe un outil. | `ToolForm` |
| `GET` | `/tools/id/{id}` | Récupère un outil par ID. | - |
| `POST` | `/tools/id/{id}/update` | Met à jour un outil. | `ToolForm` |
| `DELETE` | `/tools/id/{id}/delete` | Supprime un outil. | - |
| `GET` | `/tools/id/{id}/valves` | Récupère les "Valves" (paramètres secrets). | - |
| `POST` | `/tools/id/{id}/valves/update` | Met à jour les Valves. | JSON Dict |

---

## 7. Fonctions (`/api/v1/functions`)

Pipes et Filtres globaux.

| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `GET` | `/functions/` | Liste les fonctions. | - |
| `POST` | `/functions/create` | Crée une fonction. | `FunctionForm` |
| `GET` | `/functions/id/{id}` | Récupère une fonction. | - |
| `POST` | `/functions/id/{id}/update` | Met à jour une fonction. | `FunctionForm` |
| `GET` | `/functions/id/{id}/valves` | Valves de la fonction. | - |
| `POST` | `/functions/id/{id}/toggle/global` | Active globalement (pour tous les chats). | - |

---

## 8. Knowledge (`/api/v1/knowledge`)

Bases de connaissances (Collections de fichiers).

| Méthode | Endpoint | Description | Body / Paramètres |
| :--- | :--- | :--- | :--- |
| `GET` | `/knowledge/` | Liste les bases de connaissances. | - |
| `POST` | `/knowledge/create` | Crée une base de connaissances. | `KnowledgeForm` |
| `GET` | `/knowledge/{id}` | Détails d'une base. | - |
| `POST` | `/knowledge/{id}/file/add` | Ajoute un fichier à la base. | `KnowledgeFileIdForm` |
| `POST` | `/knowledge/{id}/file/remove` | Retire un fichier. | `KnowledgeFileIdForm` |
| `DELETE` | `/knowledge/{id}/delete` | Supprime la base. | - |
| `POST` | `/knowledge/{id}/update` | Met à jour la base (nom, description). | `KnowledgeForm` |

---

## 9. Groupes & Channels

### Groupes (`/api/v1/groups`)
| Méthode | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/groups/` | Liste les groupes utilisateurs. |
| `POST` | `/groups/create` | Crée un groupe. |
| `POST` | `/groups/id/{id}/users/add` | Ajoute des utilisateurs au groupe. |

### Channels (`/api/v1/channels`)
| Méthode | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/channels/` | Liste les canaux de discussion. |
| `POST` | `/channels/create` | Crée un canal. |
| `GET` | `/channels/{id}/messages` | Récupère les messages d'un canal. |
| `POST` | `/channels/{id}/messages/post` | Poste un message dans un canal. |

---

## 10. Ollama Proxy (`/ollama`)

Proxy direct vers l'instance Ollama intégrée (si activée).
Structure identique à l'API Ollama officielle.

*   `GET /ollama/api/tags` : Liste les modèles.
*   `POST /ollama/api/generate` : Génération texte.
*   `POST /ollama/api/chat` : Chat.
*   `POST /ollama/api/pull` : Télécharge un modèle.

---

## 11. OpenAI Proxy (`/openai`)

Proxy compatible OpenAI.

*   `GET /openai/v1/models` : Liste les modèles.
*   `POST /openai/v1/chat/completions` : Chat completions.
*   `POST /openai/v1/completions` : Text completions.

---

## 12. Autres Endpoints Utiles

*   **Config Globale** : `/api/v1/configs` (Admin - gestion des connexions modèles, bannières, etc.)
*   **Audio** : `/api/v1/audio` (TTS/STT endpoints)
    *   `POST /audio/speech` : Text-to-Speech.
    *   `POST /audio/transcriptions` : Speech-to-Text.
*   **Images** : `/api/v1/images`
    *   `POST /images/generations` : Génération d'images (DALL-E / ComfyUI / A1111).
    *   `GET /images/config` : Configuration image.
*   **Prompts** : `/api/v1/prompts` (Gestion des "Saved Prompts" / Commandes `/`)
*   **Memories** : `/api/v1/memories` (Gestion de la mémoire long terme utilisateur)
*   **Utils** : `/api/v1/utils`
    *   `GET /utils/gravatar?email=...`
    *   `GET /utils/db/download` : Télécharge la DB SQLite (Admin).
*   **Health** :
    *   `GET /health` : Statut du service.
    *   `GET /api/version` : Version de l'application.