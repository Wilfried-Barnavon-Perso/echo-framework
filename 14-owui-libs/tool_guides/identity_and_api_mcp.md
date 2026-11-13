---
domain_id: identity_and_api_mcp
version: 5.239.10.1
updated_at: 2026-11-13
name: Coffre-Fort d'Identités (Identity Vault), Client API HTTP & Protocole MCP
description: Découverte et gestion des secrets/comptes chiffrés et sécurisés (Identity Vault), requêtes HTTP universelles (call_api), instanciation/exécution sur les serveurs MCP (echo_mcp_tool) et stratégies par service.
tools: [list_identities_services, list_identities, manage_identity, call_api, list_mcp_functions, call_mcp_function]
keywords: [vault, secret, clé_api, token, identité, credentials, api, http, rest, mcp, serveur_mcp, broker, m365, workspace, contacts, google_contacts, carnet_adresses, jobs, academic, uvx, npx, sse]
---

# MANUEL OPÉRATIONNEL : IDENTITY VAULT, CLIENT API & MCP (`identity_and_api_mcp`)

## 1. COFFRE-FORT D'IDENTITÉS (`identity_vault_tool.py`)
L'Identity Vault centralise les identifiants d'API, secrets d'orchestration N8N et configurations de serveurs MCP de l'Utilisateur.

### 1.1 Séquence Impérative de Découverte en 2 Étapes
1. **Étape 1 — Découvrir les Services (`list_identities_services`)** :
   - Le Modèle **DOIT IMPÉRATIVEMENT** appeler `list_identities_services(category_filter="all")` avant d'interroger `list_identities`.
   - `category_filter` : `"all"`, `"mcp_native"`, `"mcp_resident"`, `"mcp_ephemeral"`, `"mcp_remote"`, `"orchestration"`, `"uncategorized"`.
   - **Retour** : Restitue les `services_disponibles_via_mcp_broker` (incluant `n8n_workflows` sous `"orchestration"` et les éventuelles `user_required_instructions`) ainsi que la liste des `services_avec_identites_configurees`.
2. **Étape 2 — Lister les Comptes d'un Service (`list_identities`)** :
   - `list_identities(service="...")` : Le paramètre `service` est **strictement obligatoire** (ex: `"n8n_workflows"`, `"microsoft_365"`, `"google_workspace"`, `"remote_mcp"`, `"stdio_mcp"`). Retourne les `account_id` associés à ce service ainsi que leur `"description"`.

### 1.2 Ajout, Mise à Jour et Suppression (`manage_identity`)
- **Contrainte d'environnement** : Interdit depuis un sous-agent headless (doit être appelé par l'Orchestrateur principal car une modale de confirmation interactive est présentée à l'Utilisateur).
- `action` : `"add"`, `"update"` ou `"delete"`.
- `service` : Nom du service cible (ex: `"n8n_workflows"`, identifiant d'un serveur MCP résident/natif, `"remote_mcp"`, ou `"stdio_mcp"`).
- `account_id` : Identifiant du compte, nom de domaine ou nom de la macro secret (pour N8N, correspond à `XXX` dans `__ECHO_SECRET_XXX__`).
- **Règles pour `credentials_json` (chaîne JSON valide)** :
  - **Règle générale** : Le JSON **DOIT impérativement** contenir une clé `"description"` explicitant la finalité de l'identité.
  - **Serveurs Distants (`mcp_remote`, service `"remote_mcp"` ou alias `"mcp"`)** :
    - Clés requises : `"url"`, `"transport"` (`"sse"` ou `"streamable_http"`), `"description"`.
    - Clé optionnelle : `"headers"` (chaîne JSON d'en-têtes HTTP, ex: `'{"Authorization": "Bearer XXX"}'`).
  - **Serveurs Locaux Éphémères Stdio (`mcp_ephemeral`, service `"stdio_mcp"`)** :
    - Clés requises : `"command"` (`"uvx"` ou `"npx"`), `"args"` (tableau JSON de paramètres, ex: `'["-y", "@modelcontextprotocol/server-postgres"]'`), `"description"`.
  - **Instructions Spécifiques** : Si `list_identities_services` retourne des `user_required_instructions` pour le service visé, le Modèle **DOIT** suivre scrupuleusement ces instructions et demander les valeurs requises via `ask_user_input`.

---

## 2. COMMUNICATION INTER-SERVICES MCP (`echo_mcp_tool.py`)
L'outil MCP route dynamiquement les requêtes vers les serveurs (`mcp_native`, `mcp_resident`, `mcp_ephemeral`, `mcp_remote`) en injectant leurs credentials depuis l'Identity Vault.

### Séquence d'Exécution MCP
1. **Découverte des Schémas (`list_mcp_functions`)** :
   - `list_mcp_functions(service="...", account_id=None)` : `service` DOIT correspondre à un identifiant valide issu de `list_identities_services()`. `account_id` (optionnel) cible un compte spécifique configuré dans le Vault (défaut : premier compte disponible). Retourne la liste des fonctions exposées et le schéma exact de leurs paramètres.
   - Si le statut retourné indique qu'une configuration est requise, configurer d'abord les identifiants du service via `manage_identity`.
2. **Appel d'une Fonction (`call_mcp_function`)** :
   - `call_mcp_function(service="...", function_name="...", arguments={...}, account_id=None)` : Exécute la méthode `function_name` sur le serveur `service` avec le dictionnaire `arguments`.

---

## 3. STRATÉGIES OPÉRATIONNELLES PAR MODULE MCP

### 3.1 Microsoft 365 (`microsoft_365`)
- **Protocole d'Authentification Device Code Flow** :
  1. Si `list_mcp_functions` signale une absence d'authentification ou si un appel renvoie une erreur de token, appeler `call_mcp_function(service="microsoft_365", function_name="authenticate_account", arguments={})`.
  2. Restituer immédiatement à l'Utilisateur l'URL de vérification (`verification_uri`) et le code alphanumérique (`user_code`).
  3. Dès que l'Utilisateur confirme avoir validé le code, appeler `complete_authentication` avec arguments vides `{}`.
- **Exploration des Dossiers de Messagerie (`list_mail_folders`)** :
  - `call_mcp_function(service="microsoft_365", function_name="list_mail_folders", arguments={})` : Restitue l'arborescence exhaustive des dossiers et sous-dossiers (`displayName`, `path`, `id`, `unreadItemCount`, `totalItemCount`, `childFolderCount`).
- **Recherche Multi-Axes (`search_emails`)** :
  - Arguments :
    - `query` : Mots-clés textuels (optionnel). Si omis, filtre uniquement par dates ou dossier avec tri strict.
    - `folder` : Dossier spécifique ou chemin hiérarchique (ex: `"Boîte de réception/LinkedIn"`, `"inbox"`). Si omis, recherche globale.
    - `received_after` / `received_before` : Bornes temporelles (`"YYYY-MM-DD"` ou ISO).
    - `sender` : Filtre expéditeur (nom partiel ou adresse email).
    - `unread_only` : `True` pour restreindre aux non-lus.
    - `has_attachments` : `True` pour exiger la présence de pièces jointes.
    - `order_by` : `"receivedDateTime desc"` (défaut) ou `"receivedDateTime asc"` (sans `query`).
    - `limit` : Nombre maximal de résultats (1 à 100, défaut `50`).
    - `include_body` : `False` par défaut (le champ `bodyPreview` de 255 caractères est toujours fourni pour qualification rapide).
- **Lecture Batch vs Unitaire (`batch_get_emails` vs `get_email`)** :
  - **Lecteur Batch (`batch_get_emails`) - À PRIVILÉGIER** : `batch_get_emails(email_ids=["id1", "id2", ...], include_body=True, body_max_length=5000, include_attachments=False)` rapatrie le contenu textuel nettoyé de plusieurs e-mails en un seul appel et restitue `failed_ids` et `failed_details` (code HTTP et motif Graph) en cas d'échec partiel.
  - **Lecture unitaire (`get_email`)** : Réservée à l'inspection isolée d'un e-mail unique (`email_id="...", include_body=True`).
- **Lecture et Pagination de Dossier (`list_emails`)** :
  - Arguments : `folder` (nom, chemin, `"inbox"` ou `FolderId`), `limit` (1 à 100, défaut `10`), `skip` (décalage pagination, défaut `0`), `order_by` (`"receivedDateTime desc"` ou `"receivedDateTime asc"`), `include_body` (`False` par défaut), `body_max_length` (défaut `5000`).
- **Suppression, Déplacement et Mutation MAPI des Identifiants (`email_id`)** :
  - **RÈGLE CRITIQUE D'ARCHITECTURE MAPI** : Déplacer un message vers un autre dossier (ou la corbeille) modifie obligatoirement son `email_id` (l'ancien ID renvoie HTTP 404).
  - `trash_email` et `move_email` restituent `{"old_id": "...", "new_id": "..."}` ; `batch_trash_emails` restitue `id_mapping: {"old_id": "new_id"}`. Toute action ultérieure sur un message déplacé **DOIT** utiliser `new_id`.
  - **Mise à la corbeille réversible (Soft Delete - À PRIVILÉGIER)** : `batch_trash_emails(email_ids=[...])` (jusqu'à 100 IDs par lot) ou `trash_email(email_id="...")`.
  - **Purge définitive (`delete_email`)** : Irréversible. **INTERDIT** sauf demande explicite de purge définitive de l'Utilisateur.
  - **Classement métier (`move_email`)** : `move_email(email_id="...", destination_folder="...")` pour archiver/classer (ne pas utiliser pour supprimer).
- **Workflow Optimal pour le Tri/Ménage Massif** :
  1. `list_mail_folders()` pour identifier le chemin exact du dossier.
  2. `search_emails(folder="...", received_before="<date>", order_by="receivedDateTime asc")` (ou `list_emails(..., include_body=False)`).
  3. Optionnel : `batch_get_emails(email_ids=[...])` si vérification du corps complet requise.
  4. `batch_trash_emails(email_ids=[...])`.
- **Téléchargement OneDrive & Pièces Jointes (`get_file`, `get_attachment`)** :
  - Les fichiers téléchargés via `get_file(file_id="...")` et `get_attachment(email_id="...", attachment_id="...")` sont automatiquement ingérés dans le Registre Unifié (`query_registry`) et retournent directement leur `file_id` ECHO (8 caractères hexadécimaux). `download_path` et `save_path` sont optionnels (seul un nom de fichier peut être suggéré).
- **Autres Capacités** : Agenda (`list_events`, `create_event`, `respond_event`) et OneDrive (`list_files`, `create_file`, `update_file`, `delete_file`).

### 3.2 Google Workspace (`google_workspace`)
- **Configuration Vault** : Clés requises : `USER_GOOGLE_EMAIL`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN` (écran de consentement GCP sur *"In production"*).
- **Syntaxe de Recherche Gmail & Drive** :
  - Gmail (`q`) : Syntaxe native Google (ex: `q="from:recrutement@societe.com has:attachment after:2026/01/01"`).
  - Drive : Opérateurs standard Google Drive (`name contains 'CV' and mimeType = 'application/pdf'`).
- **Téléchargements Drive & Pièces Jointes (`get_drive_file_download_url`, `get_gmail_attachment_content`, `download_chat_attachment`)** :
  - Tout fichier téléchargé est automatiquement ingéré dans le Registre Unifié (`query_registry`) et retourne son `file_id` ECHO (8 caractères hexadécimaux).
- **Carnet d'Adresses & Contacts Google (`list_contacts`, `list_contact_groups`, `get_contact`, `get_contact_group`, `manage_contact`, `manage_contact_group`, `manage_contacts_batch`)** :
  - **Consultation & Sauvegarde (Lecture Seule — À PRIVILÉGIER pour tout export ou inventaire)** :
    - `list_contacts` : Rapatrie la liste des contacts (`max_results`: int optionnel, défaut `100` ; `name_filter`: str optionnel ; `user_google_email`: str optionnel). Outil indispensable et prioritaire pour toute sauvegarde, export ou exploration des contacts.
    - `list_contact_groups` : Rapatrie les libellés et groupes de contacts (`user_google_email`: str optionnel).
    - `get_contact` : Détails d'un contact via son `contact_id` ou `resource_name` (`"people/c..."`).
    - `get_contact_group` : Détails d'un groupe via `group_id` ou `resource_name` (`"contactGroups/..."`).
  - **Mutation & Écriture (Création, Modification, Suppression)** :
    - **RÈGLE CRITIQUE D'APPEL** : Les outils `manage_*` exigent **STRICTEMENT** le paramètre racine `action` (`"create"`, `"update"`, `"delete"`). Ne **JAMAIS** appeler un outil `manage_*` pour lire ou exporter des contacts (utiliser impérativement `list_contacts`).
    - `manage_contact` : `action="create"` (avec `given_name`, `family_name`, `email`, `phone`), `action="update"` (avec `contact_id` et champs modifiés), ou `action="delete"` (`contact_id`).
    - `manage_contact_group` : `action="create"` (avec `name`), `action="update"` (avec `group_id`, `name`), `action="delete"` (`group_id`), ou `action="modify_members"` (avec `group_id`, `resource_names_to_add`/`resource_names_to_remove`).
    - `manage_contacts_batch` : Opérations par lots avec `action="create"` (`contacts=[...]`), `action="update"` (`updates=[...]`), ou `action="delete"` (`contact_ids=[...]`).
- **Outils Disponibles** : Gmail (`list_messages`, `get_message`, `send_message`), Drive (`search_files`, `download_file`), Calendar (`list_events`, `create_event`), Contacts (`list_contacts`, `list_contact_groups`, `get_contact`, `get_contact_group`, `manage_contact`, `manage_contact_group`, `manage_contacts_batch`), Docs, Sheets, Tasks.

### 3.3 Agrégateur d'Emploi (`jobs_omnisearch`)
- **Arguments Clés** :
  - `sources` : `["all"]` (LinkedIn, APEC, France Travail, WTTJ en parallèle) ou sous-ensemble (ex: `["linkedin", "wttj"]`).
  - `keywords` : Intitulé de poste, technologies.
  - `location` : Localisation (ex: `"Paris"`, `"Lyon"`, `"Télétravail"`).
  - `seniority` : `"junior"`, `"mid"`, `"senior"`, `"lead"`.
  - `limit` : Résultats max par source (défaut `10`).
- **Identifiants Optionnels (Identity Vault)** : `LINKEDIN_SESSION_COOKIE` (cookie `li_at`), `APEC_DATADOME_COOKIE` (cookie `datadome`), `FRANCE_TRAVAIL_CLIENT_ID` / `FRANCE_TRAVAIL_CLIENT_SECRET`.

### 3.4 Recherche Scientifique (`academic`)
- Recherche multi-sources (arXiv, Semantic Scholar, CrossRef).
- Formuler des requêtes conceptuelles en anglais (ex: `"retrieval augmented generation memory consolidation"`).

---

## 4. CLIENT HTTP UNIVERSEL (`call_api`)
- Exécute une requête HTTP/REST directe (`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`, `"PATCH"`, `"HEAD"`, `"OPTIONS"`) vers une URL externe publique (SSRF bloqué sur IP privées/locales RFC 1918).
- `headers` / `body` : Dictionnaires JSON optionnels.
- **INTERDICTION STRICTE** : Ne **JAMAIS** utiliser `call_api` pour interroger un serveur MCP (utiliser exclusivement `list_mcp_functions` et `call_mcp_function`).
- **Troncature** : Réponse limitée à **131 Ko**. Pour traiter ou filtrer un flux volumineux, déléguer l'appel à `code_executor`.
