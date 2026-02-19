# Guide de Configuration n8n pour RAG Agentique (ECHO v5.20)

**Objectif :** Configurer le pipeline ETL automatisé qui alimente la mémoire de l'IA (Qdrant) à partir de Google Drive.

**Prérequis :**
*   L'infrastructure Docker tourne (`docker-compose up -d`).
*   Accès à n8n via `http://localhost:5678` (ou ton domaine).

---

## 1. Connexion des Services (Credentials)

Dans n8n, allez dans **Settings > Credentials** et ajoutez :

### A. Google Drive OAuth2 API
*   **Type :** OAuth2.
*   **Client ID / Secret :** Utilisez ceux de votre projet Google Cloud (les mêmes que dans `pipe_engine.py` si possible pour simplifier, ou créez-en de nouveaux sur la console Google Cloud).
*   **Scopes :** `https://www.googleapis.com/auth/drive.readonly`
*   **Action :** Authentifiez-vous avec votre compte Google principal (celui qui a accès aux fichiers).

### B. Qdrant API
*   **URL :** `http://echo-qdrant:6333` (URL interne Docker).
*   **API Key :** Laisser vide (Pas d'auth configurée par défaut dans notre stack).

### C. Google Gemini API (Pour les Embeddings)
*   **Type :** Header Auth.
*   **Name :** `Google AI Studio`.
*   **Header Name :** `x-goog-api-key`.
*   **Value :** Votre clé API `AI Studio` (nécessaire ici car n8n ne partage pas le token OAuth utilisateur d'Open-WebUI).
    *   *Note :* Si vous n'avez pas de clé statique, vous pouvez utiliser un nœud "Google OAuth2" générique et appeler l'API Vertex AI, mais la clé API AI Studio est plus simple pour `text-embedding-004`.

---

## 2. Création du Workflow "Ingestion RAG"

Créez un nouveau workflow et ajoutez ces nœuds en chaîne :

### Étape 1 : Trigger (Déclencheur)
*   **Node :** `Google Drive Trigger`.
*   **Event :** `File Created` (Poll time: 1 min).
*   **Folder :** Sélectionnez un dossier spécifique (ex: "ECHO_MEMOIRE").
*   **Download :** `Yes` (Important pour lire le contenu).

### Étape 2 : Extraction du Texte
*   **Node :** `Read PDF` (si vous visez des PDF).
    *   *Alternative Universelle :* Utilisez le nœud **"Text Parser"**.

### Étape 3 : Découpage (Chunking)
*   **Node :** `Code` (Javascript).
*   **Rôle :** Découper le texte en morceaux digérables pour l'embedding (max 2048 tokens).
*   **Code JS :**
    ```javascript
    const text = items[0].json.text; // Adaptez selon la sortie du nœud précédent
    const fileName = items[0].json.fileName || "Sans titre";
    const webLink = items[0].json.webViewLink || "";
    
    const chunkSize = 2000; // Caractères (~500 tokens)
    const overlap = 200;
    const chunks = [];
    
    for (let i = 0; i < text.length; i += chunkSize - overlap) {
      chunks.push({
        json: {
          content: text.substring(i, i + chunkSize),
          source: fileName,
          url: webLink
        }
      });
    }
    
    return chunks;
    ```

### Étape 4 : Vectorisation (Embedding)
*   **Node :** `HTTP Request`.
*   **Method :** `POST`.
*   **URL :** `https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent`
*   **Authentication :** Predefined Credential Type -> `Google AI Studio`.
*   **Body Content :** JSON.
*   **JSON :**
    ```json
    {
      "model": "models/text-embedding-004",
      "content": {
        "parts": [{ "text": "={{ $json.content }}" }]
      }
    }
    ```

### Étape 5 : Stockage (Qdrant)
*   **Node :** `Qdrant`.
*   **Operation :** `Upsert`.
*   **Collection :** `echo_knowledge` (Cochez "Create if missing").
*   **ID :** Auto-generate.
*   **Vector :** Expression `{{ $json.embedding.values }}` (Chemin à adapter selon la réponse Google).
*   **Payload :**
    *   `content` : `{{ $json.content }}`
    *   `source` : `{{ $json.source }}`
    *   `url` : `{{ $json.url }}`

---

## 3. Test de Validation
1.  Activez le workflow.
2.  Déposez un PDF dans le dossier Google Drive cible.
3.  Attendez 1 minute.
4.  Allez dans Open-WebUI et demandez à l'outil `@memory_search` : "De quoi parle le document que je viens d'ajouter ?"
