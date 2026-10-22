---
domain_id: identity_and_api_mcp
version: 5.229.1.0
updated_at: 2026-10-22
name: Coffre-Fort d'Identités (Identity Vault), Client API HTTP & Protocole MCP
description: Découverte et gestion des secrets/comptes (Identity Vault), requêtes HTTP universelles (call_api), instanciation/exécution sur les serveurs MCP (echo_mcp_tool) et stratégies par service.
tools: [list_identities_services, list_identities, manage_identity, call_api, list_mcp_functions, call_mcp_function]
keywords: [vault, secret, clé_api, token, identité, credentials, api, http, rest, mcp, serveur_mcp, broker, m365, workspace, jobs, academic, uvx, npx, sse]
---

# MANUEL OPÉRATIONNEL : IDENTITY VAULT, CLIENT API & MCP (`identity_and_api_mcp`)

## 1. COFFRE-FORT D'IDENTITÉS (`identity_vault_tool.py`)
L'Identity Vault centralise les identifiants d'API, secrets d'orchestration N8N et configurations de serveurs MCP de l'Utilisateur.

### 1.1 Séquence Impérative de Découverte en 2 Étapes
1. **Étape 1 — Découvrir les Services (`list_identities_services`)** :
   - Le Modèle **DOIT IMPÉRATIVEMENT** appeler `list_identities_services(category_filter="all")` avant d'interroger `list_identities`.
   - `category_filter` : `"all"` (recommandé pour une découverte exhaustive), `"mcp_native"`, `"mcp_resident"`, `"mcp_ephemeral"`, `"mcp_remote"`, `"orchestration"`, `"uncategorized"`.
   - **Retour** : Restitue les `services_disponibles_via_mcp_broker` (avec leurs éventuelles `user_required_instructions`) ainsi que la liste des `services_avec_identites_configurees`.
2. **Étape 2 — Lister les Comptes d'un Service (`list_identities`)** :
   - `list_identities(service="...")` : Le paramètre `service` est **strictement obligatoire** (ex: `"n8n_workflows"`, `"microsoft_365"`, `"google_workspace"`, `"remote_mcp"`, `"stdio_mcp"`). Retourne les `account_id` et descriptions associés à ce service.

### 1.2 Ajout, Mise à Jour et Suppression (`manage_identity`)
`manage_identity(action, service, account_id, credentials_json="")` :
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
  - **Instructions Spécifiques** : Si `list_identities_services` retourne des `user_required_instructions` pour le service visé, le Modèle **DOIT** suivre scrupuleusement ces instructions et demander les valeurs requises via `ask_user`.

---

## 2. COMMUNICATION INTER-SERVICES MCP (`echo_mcp_tool.py`)
L'outil MCP route dynamiquement les requêtes JSON-RPC vers toutes les catégories de serveurs (`mcp_native`, `mcp_resident`, `mcp_ephemeral`, `mcp_remote`) en injectant automatiquement leurs credentials depuis l'Identity Vault.

### Séquence d'Exécution MCP
1. **Découverte des Schémas (`list_mcp_functions`)** :
   - `list_mcp_functions(service="...")` : `service` DOIT correspondre à un identifiant valide issu de `list_identities_services()`. Retourne la liste des fonctions exposées (`tools/list`) et le schéma exact de leurs paramètres.
   - Si le statut retourné indique qu'une configuration est requise, configurer d'abord les identifiants du service via `manage_identity`.
2. **Appel d'une Fonction (`call_mcp_function`)** :
   - `call_mcp_function(service="...", function_name="...", arguments={...})` : Exécute la méthode `function_name` sur le serveur `service` avec le dictionnaire `arguments`.

---

## 3. STRATÉGIES OPÉRATIONNELLES PAR MODULE MCP

### 3.1 Microsoft 365 (`microsoft_365` - Résident `microsoft-mcp`)
- **Protocole d'Authentification Device Code Flow** :
  1. Si `list_mcp_functions` signale une absence d'authentification ou si un appel renvoie une erreur de token, appeler `call_mcp_function(service="microsoft_365", function_name="authenticate_account", arguments={})`.
  2. Restituer immédiatement à l'Utilisateur l'URL de vérification (`verification_uri`) et le code alphanumérique (`user_code`).
  3. Dès que l'Utilisateur confirme avoir validé le code, appeler `complete_authentication` en fournissant le `flow_cache` retourné à l'étape 1.
- **Navigation et Ciblage des Dossiers de Messagerie (`list_emails`)** :
  - **Dossiers standards réservés** : `"inbox"`, `"sent"`, `"drafts"`, `"deleted"`, `"junk"`, `"archive"`.
  - **Ciblage de sous-dossiers arbitraires (ex: "Emploi", "LinkedIn")** :
    - L'API Microsoft Graph n'accepte **aucun** libellé textuel arbitraire dans l'endpoint `/me/mailFolders/{folder}/messages` et renvoie une erreur **HTTP 404 (`ErrorItemNotFound`)**.
    - Pour interroger un sous-dossier, le Modèle **DOIT IMPÉRATIVEMENT fournir le `FolderId` immuable Graph** (ex: `AQMkADAw...`) dans l'argument `folder`.
    - **Découverte du `FolderId` sans outil `list_folders`** : Lorsqu'un e-mail est consulté via `get_email(email_id="...", account_id="...")`, Microsoft Graph retourne la ressource intégrale du message incluant la propriété **`parentFolderId`**. Le Modèle DOIT extraire cet identifiant pour interroger ensuite directement le dossier cible : `list_emails(folder="<parentFolderId>")`.
- **Comptes Personnels (`consumers` vs organisationnels)** :
  - L'argument `search_query` sur `list_emails` peut déclencher des erreurs HTTP 400 sur les tenants personnels si combiné à certains filtres.
  - Privilégier le ciblage précis par dossier (`folder="<FolderId>"`) avec pagination (`limit`, `skip`), puis filtrer localement les résultats.
- **Autres Capacités** : Gestion d'agenda (`list_events`, `create_event`) et stockage OneDrive (`list_files`, `upload_file`).

### 3.2 Google Workspace (`google_workspace` - Résident `workspace-mcp`)
- **Pérennité du Refresh Token** :
  - Le projet Google Cloud Console DOIT avoir son écran de consentement configuré sur **"In production"** (pour bloquer l'invalidation automatique au bout de 7 jours).
  - Clés requises dans le Vault : `USER_GOOGLE_EMAIL`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`.
- **Syntaxe de Recherche Gmail & Drive** :
  - L'argument `q` exploite la syntaxe native des moteurs Google (ex: `q="from:recrutement@societe.com has:attachment after:2026/01/01"`).
  - Les requêtes Drive supportent les opérateurs de recherche standard Google Drive (`name contains 'CV' and mimeType = 'application/pdf'`).
- **Outils Disponibles** : Messagerie Gmail (`list_messages`, `get_message`, `send_message`), Drive (`search_files`, `download_file`), Calendar (`list_events`, `create_event`), Contacts, Docs, Sheets, Tasks.

### 3.3 Agrégateur d'Emploi (`jobs_omnisearch` - Natif `m2_jobs_omnisearch.py`)
- **Sélection des Sources** :
  - `sources: ["all"]` : Déclenche en parallèle l'interrogation de **LinkedIn, APEC, France Travail et Welcome to the Jungle (WTTJ)**.
  - Sélection ciblée : Passer un sous-ensemble (ex: `sources: ["linkedin", "wttj"]`).
- **Arguments Clés** :
  - `keywords` : Mots-clés de recherche (intitulé de poste, technologies).
  - `location` : Localisation géographique (ex: `"Paris"`, `"Lyon"`, `"Télétravail"`).
  - `seniority` : Filtre de séniorité (`"junior"`, `"mid"`, `"senior"`, `"lead"`).
  - `limit` : Nombre maximal de résultats par source (défaut `10`).
- **Optimisation Anti-Bot (Identity Vault)** :
  - Le module fonctionne nativement sans authentification obligatoire, mais des identifiants optionnels améliorent la résilience :
    - `LINKEDIN_SESSION_COOKIE` (cookie `li_at` pour requêtes LinkedIn fiables).
    - `APEC_DATADOME_COOKIE` (cookie `datadome` pour contourner les protections APEC).
    - `FRANCE_TRAVAIL_CLIENT_ID` / `FRANCE_TRAVAIL_CLIENT_SECRET` (API officielle francetravail.io).

### 3.4 Recherche Scientifique (`academic` - Natif `m4_academic.py`)
- **Périmètre & Moteurs** :
  - Recherche multi-sources sur les dépôts de prépublications et littérature académique (arXiv, Semantic Scholar, CrossRef).
- **Stratégie de Requête** :
  - Formuler des requêtes conceptuelles en anglais pour maximiser le rappel (ex: `"retrieval augmented generation memory consolidation"`).
  - Exploiter les métadonnées extraites (auteurs, date, DOI, abstract, citations) pour les synthèses de l'état de l'art.

---

## 4. CLIENT HTTP UNIVERSEL (`call_api`)
`call_api(url, method="GET", headers=None, body=None)` :
- Exécute une requête HTTP/REST directe (`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`, `"PATCH"`, `"HEAD"`, `"OPTIONS"`) vers une URL externe publique (protection anti-SSRF active contre les IP privées/locales RFC 1918).
- `headers` / `body` : Dictionnaires JSON optionnels.
- **INTERDICTION STRICTE** : Ne **JAMAIS** utiliser `call_api` pour interroger un serveur MCP (utiliser exclusivement `list_mcp_functions` et `call_mcp_function`).
- **Règle de Troncature Anti-OOM** : La réponse de `call_api` est tronquée à **131 Ko** (~32k tokens). Si l'API cible renvoie un flux volumineux nécessitant un filtrage ou une agrégation, déléguer l'appel et le parsing à `code_executor`.
