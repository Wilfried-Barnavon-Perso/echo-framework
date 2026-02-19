# Plan Stratégique Détaillé : Architecture Agentique & Smart Context (ECHO v5.20+)

**Version :** 11.0 (Spécifications Techniques Complètes)
**Date :** 14 Février 2026
**Cible :** ECHO Framework (VM 8Go RAM / Docker / CPU-Only)

---

## 1. Principes & Architecture

### 1.1 Stratégie "No-Code-Mod"
*   **Intégrité :** On ne modifie **PAS** `pipe_engine.py` pour éviter les régressions.
*   **Auth Partagée :** On crée une librairie `echo_utils.py` capable de lire les tokens OAuth2 existants dans les bases SQLite utilisateurs.
*   **Configuration :** Tout est pilotable via des Valves (UI).

### 1.2 Stack Technique
| Composant | Technologie | Rôle | Configuration |
| :--- | :--- | :--- | :--- |
| **Orchestrateur** | **Gemini 3 Pro** | Cerveau | `thinking_level="high"` |
| **Analyste** | **Gemini 3 Flash** | Smart Context | `thinking_level="minimal"` |
| **Mémoire** | **Qdrant** | Stockage Vectoriel | Image `v1.13.4` (RAM Illimitée) |
| **ETL** | **n8n** | Ingestion | Image `1.77.1` (RAM Illimitée) |

---

## 2. Phase 1 : Infrastructure (Docker)

**Fichier Cible :** `01-config/stack-echo.yml`

### 2.1 Service Qdrant
```yaml
  qdrant:
    image: qdrant/qdrant:v1.13.4
    container_name: echo-qdrant
    restart: always
    networks:
      - echo-network
    ports:
      - "6333:6333"
    volumes:
      - echo-qdrant-data:/qdrant/storage
    environment:
      - TZ=Europe/Paris
```

### 2.2 Service n8n
```yaml
  n8n:
    image: n8nio/n8n:1.77.1
    container_name: echo-n8n
    restart: always
    networks:
      - echo-network
    ports:
      - "5678:5678"
    environment:
      - N8N_HOST=${ECHO_DOMAIN:-localhost}
      - N8N_PORT=5678
      - N8N_PROTOCOL=https
      - NODE_ENV=production
      - WEBHOOK_URL=https://n8n.${ECHO_DOMAIN:-localhost}/
      - GENERIC_TIMEZONE=Europe/Paris
      - TZ=Europe/Paris
    volumes:
      - echo-n8n-data:/home/node/.n8n
```

---

## 3. Phase 2 : Socle Technique (Python)

**Fichier :** `12-owui-tools/echo_utils.py` (Librairie Partagée)

### 3.1 Classe `EchoAuth`
*   **Responsabilité :** Fournir un Token Google valide à partir d'un `user_id`.
*   **Logique :**
    1.  Connexion à `/app/backend/data/user_dbs/user-{user_id}.db`.
    2.  SELECT `value` FROM `auth_data` WHERE `key`='google_token'.
    3.  Parsing JSON (Access Token, Refresh Token, Expiry).
    4.  Si Expiré : Appel POST `https://oauth2.googleapis.com/token` (grant_type=refresh_token).
    5.  Retourne le Token valide.

---

## 4. Phase 3 : Smart Context (Le Filtre)

**Fichier :** `11-owui-filters/new_context_filter.py`

### 4.1 Configuration (Valves)
*   `ENABLE_SMART_CONTEXT`: bool (Default: `False`)
*   `SMART_CONTEXT_THRESHOLD_KB`: int (Default: `2048`)
*   `GEMINI_FLASH_MODEL`: str (Default: `gemini-3-flash-preview`)

### 4.2 Spécifications des Prompts (Gemini Flash)

**A. Cas Texte / PDF (> 2Mo)**
*   **System Prompt :**
    > "Tu es un analyste documentaire expert. Ta tâche est d'extraire les métadonnées essentielles d'un document pour qu'un LLM principal puisse décider de le lire ou non. Sois concis et factuel."
*   **User Prompt :**
    > "Analyse ce début de fichier (Max 10k chars). Retourne EXCLUSIVEMENT un objet JSON valide avec cette structure :
    > ```json
    > {
    >   "titre": "Titre du document ou Inconnu",
    >   "type_document": "Rapport financier, Spécification technique, Roman, etc.",
    >   "resume_executif": ["Point clé 1", "Point clé 2", "Point clé 3"],
    >   "mots_cles": ["Tag1", "Tag2", "Tag3"],
    >   "entites_nommees": ["Personnes", "Lieux", "Organisations importantes"]
    > }
    > ```"

**B. Cas Image (> 2Mo)**
*   **System Prompt :**
    > "Tu es un expert en vision par ordinateur. Décris cette image pour un modèle textuel aveugle."
*   **User Prompt :**
    > "Analyse cette image. Retourne un JSON :
    > ```json
    > {
    >   "description_visuelle": "Description détaillée de la scène...",
    >   "ocr_text": "Tout texte visible dans l'image",
    >   "analyse_artistique": "Style, ambiance, couleurs dominantes",
    >   "objets_detectes": ["Liste", "des", "objets"]
    > }
    > ```"

**C. Cas Audio (> 2Mo)**
*   **System Prompt :**
    > "Tu es un expert en analyse audio et acoustique."
*   **User Prompt :**
    > "Écoute cet extrait. Retourne un JSON :
    > ```json
    > {
    >   "transcription_resume": "Résumé de ce qui est dit",
    >   "analyse_sonore": "Bruit de fond, qualité, musique (genre/tempo)",
    >   "analyse_emotionnelle": "Ton des voix, émotions détectées (Colère, Joie, Neutre)",
    >   "locuteurs": "Estimation du nombre et type (Homme/Femme)"
    > }
    > ```"

### 4.3 Injection du Menu (Markdown)
Si succès de Flash, le fichier est remplacé par :
```markdown
📂 **Smart Context :** [ID: {file_id}]
**Titre :** {json.titre}
**Type :** {json.type_document}
**Résumé :**
- {json.resume_executif[0]}
- {json.resume_executif[1]}
- {json.resume_executif[2]}
> *Fichier non chargé. Utilisez l'outil `read_file_content` pour consulter.*
```

---

## 5. Phase 4 : Les Outils (Tools)

**Fichiers :** `12-owui-tools/`

### 5.1 `file_retriever.py`
*   **Fonction :** `read_file_content(file_id: str, start_chunk: int = 0)`
*   **Logique Chunking :**
    *   Lit le fichier en binaire.
    *   Décode UTF-8 (ignore errors).
    *   Retourne `text[start_chunk*50000 : (start_chunk+1)*50000]`.
    *   Ajoute un footer : *"Chunk {x}. Utilisez start_chunk={x+1} pour lire la suite."*

### 5.2 `cognitive_delegate.py`
*   **Fonction :** `consult_expert(query: str, context_excerpt: str, expert_persona: str, thinking_level: str)`
*   **Payload API Gemini :**
    ```json
    {
      "model": "gemini-3-pro-preview", // ou Flash selon param
      "contents": [...],
      "thinking_config": {
        "include_thoughts": true,
        "thinking_level": "{thinking_level}" // "low" ou "high"
      }
    }
    ```

---

## 6. Phase 5 : RAG Automation (n8n)

**Workflow "Ingestion RAG" (Détail des Nœuds) :**

1.  **Trigger :** `Google Drive Trigger` (Event: File Created, Poll Time: 1min).
2.  **Download :** `Google Drive` (Resource: File, Operation: Download).
3.  **Extraction Texte :**
    *   Utiliser le nœud `Read PDF` (si PDF).
    *   Ou `Text Parser` (si texte).
4.  **Code JS (Chunking) :**
    ```javascript
    const text = items[0].json.text;
    const chunkSize = 2000; // Caractères
    const overlap = 200;
    const chunks = [];
    for (let i = 0; i < text.length; i += chunkSize - overlap) {
      chunks.push({
        json: {
          content: text.substring(i, i + chunkSize),
          source: items[0].json.fileName,
          url: items[0].json.webViewLink
        }
      });
    }
    return chunks;
    ```
5.  **Embed (Loop) :**
    *   `HTTP Request` vers `https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent`.
    *   Auth: Header `x-goog-api-key`.
6.  **Store :**
    *   `Qdrant` (Operation: Upsert).
    *   Collection: `echo_knowledge`.
    *   Vector: `{{ $json.embedding.values }}`.
    *   Payload: `{{ $json.content }}`, `{{ $json.source }}`, `{{ $json.url }}`.

---

## 7. Validation Finale

1.  **Infrastructure :** `docker-compose up -d`.
2.  **Auth :** Test script `echo_utils.py` isolé.
3.  **Smart Context :** Upload PDF > 2Mo -> Vérif Menu.
4.  **Vision :** Upload Image > 2Mo -> Vérif Description.
5.  **Délégation :** Chat "Audite ce code (High Thinking)" -> Vérif Tool Call.
) 