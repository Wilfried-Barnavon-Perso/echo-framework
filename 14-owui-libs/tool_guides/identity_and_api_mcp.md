---
domain_id: identity_and_api_mcp
name: Coffre-Fort d'Identités (Identity Vault), Client API HTTP & Protocole MCP
description: Découverte et gestion des secrets/comptes (Identity Vault), requêtes HTTP universelles (call_api) et interrogation/exécution sur les serveurs MCP (echo_mcp_tool).
tools: [list_identities_services, list_identities, manage_identity, call_api, list_mcp_functions, call_mcp_function]
keywords: [vault, secret, clé_api, token, identité, credentials, api, http, rest, mcp, serveur_mcp, broker]
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
   - `list_identities(service="...")` : Le paramètre `service` est **strictement obligatoire** (ex: `"n8n_workflows"`, `"github"`, `"brave_search"`). Retourne les `account_id` et descriptions associés à ce service.

### 1.2 Ajout, Mise à Jour et Suppression (`manage_identity`)
`manage_identity(action, service, account_id, credentials_json="")` :
- **Contrainte d'environnement** : Interdit depuis un sous-agent headless (doit être appelé par l'Orchestrateur principal car une modale de confirmation interactive est présentée à l'Utilisateur).
- `action` : `"add"`, `"update"` ou `"delete"`.
- `service` : Nom du service cible (ex: `"n8n_workflows"`, `"mcp"`, ou identifiant d'un serveur du MCP Broker).
- `account_id` : Identifiant du compte ou nom de la macro secret (pour N8N, correspond à `XXX` dans `__ECHO_SECRET_XXX__`).
- **Règles strictes pour `credentials_json` (chaîne JSON valide)** :
  - Pour **tous** les services : Le JSON **DOIT impérativement** contenir une clé `"description"` explicitant la finalité de l'identité.
  - Pour un serveur **`mcp_remote`** (`service="mcp"`) : Le JSON **DOIT** contenir les clés `"url"`, `"transport"` (`"sse"` ou `"streamable_http"`) et `"description"`.
  - Si `list_identities_services` retourne des `user_required_instructions` pour le service visé, le Modèle **DOIT** suivre scrupuleusement ces instructions et demander les valeurs requises via `ask_user_input`.

---

## 2. COMMUNICATION INTER-SERVICES MCP (`echo_mcp_tool.py`)
L'outil MCP route dynamiquement les requêtes JSON-RPC vers tous les serveurs MCP (`mcp_native`, `mcp_resident`, `mcp_ephemeral`, `mcp_remote`) en injectant automatiquement leurs credentials depuis l'Identity Vault.

### Séquence d'Exécution MCP
1. **Découverte des Schémas (`list_mcp_functions`)** :
   - `list_mcp_functions(service="...")` : `service` DOIT correspondre à un identifiant valide issu de `list_identities_services()`. Retourne la liste des fonctions exposées (`tools/list`) et le schéma exact de leurs paramètres.
   - Si le statut retourné indique qu'une configuration est requise, configurer d'abord les identifiants du service via `manage_identity`.
2. **Appel d'une Fonction (`call_mcp_function`)** :
   - `call_mcp_function(service="...", function_name="...", arguments={...})` : Exécute la méthode `function_name` sur le serveur `service` avec le dictionnaire `arguments`.

---

## 3. CLIENT HTTP UNIVERSEL (`call_api`)
`call_api(url, method="GET", headers=None, body=None)` :
- Exécute une requête HTTP/REST directe (`"GET"`, `"POST"`, `"PUT"`, `"DELETE"`, `"PATCH"`, `"HEAD"`, `"OPTIONS"`) vers une URL externe publique (protection anti-SSRF active contre les IP privées/locales RFC 1918).
- `headers` / `body` : Dictionnaires JSON optionnels.
- **INTERDICTION STRICTE** : Ne **JAMAIS** utiliser `call_api` pour interroger un serveur MCP (utiliser exclusivement `list_mcp_functions` et `call_mcp_function`).
- **Règle de Troncature Anti-OOM** : La réponse de `call_api` est tronquée à **131 Ko** (~32k tokens). Si l'API cible renvoie un flux volumineux nécessitant un filtrage ou une agrégation, déléguer l'appel et le parsing à `code_executor`.
