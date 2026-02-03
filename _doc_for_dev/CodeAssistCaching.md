# Documentation Technique : Optimisation Cache & Upload (Gemini Code Assist)

**Version cible** : 5.15.x
**Statut** : Documentation de Reverse Engineering (Validée par analyse de code source `gemini-cli` officiel).
**Objectif** : Remplacer l'envoi de fichiers en Base64 "Inline" par l'API d'Upload native de `cloudcode-pa`.

## 1. Contexte & Problématique

Actuellement (`v5.14`), ECHO envoie les fichiers encodés en Base64 directement dans le payload de chaque requête de chat (`streamGenerateContent`).
*   **Problème** : Consommation bande passante énorme à chaque tour de conversation.
*   **Solution** : Utiliser l'API d'Upload dédiée pour obtenir une référence (`fileUri`) et n'envoyer que cette référence.

## 2. Spécifications Techniques Reverse-Engineered

L'API `cloudcode-pa.googleapis.com` expose un endpoint non documenté publiquement mais utilisé par les clients officiels (VS Code extension, CLI).

### A. Endpoint d'Upload

*   **URL** : `https://cloudcode-pa.googleapis.com/v1internal:uploadFile`
*   **Méthode** : `POST`
*   **Authentification** : `Authorization: Bearer <ACCESS_TOKEN>` (Même token OAuth2 que pour le chat).
*   **Headers** : `Content-Type: application/json`

#### Payload de Requête
```json
{
  "project": "cloudaicompanion-xxxxxx",  // ID du projet récupéré lors du loadCodeAssist
  "file": {
    "display_name": "nom_du_fichier.ext",  // Purement informatif
    "content": "<DATA_BASE64>"             // Contenu binaire encodé en Base64 standard (pas URL-safe)
  }
}
```

#### Réponse (Succès 200 OK)
```json
{
  "file": {
    "name": "files/abcdef123456",        // <--- L'identifiant (fileUri) à conserver précieusement
    "display_name": "nom_du_fichier.ext",
    "size_bytes": "1048576"
  }
}
```

### B. Usage dans le Chat

Lors de l'appel à `:streamGenerateContent`, il faut remplacer le champ `inlineData` par `fileData`.

*   **Endpoint** : `https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent`

#### Payload de Requête (Partie `contents`)
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "fileData": {
            "mimeType": "application/pdf",  // Type MIME du fichier original
            "fileUri": "files/abcdef123456" // L'identifiant obtenu à l'étape A
          }
        },
        { "text": "Analyse ce document." }
      ]
    }
  ]
}
```

## 3. Gestion du Cycle de Vie (Cache & Invalidation)

Le serveur ne fournit pas de TTL (Time To Live) explicite. La gestion doit être réactive.

### Détection de l'Expiration (Signature Précise)
Le client officiel détecte l'expiration en analysant le corps de la réponse d'erreur HTTP 400.

*   **Code HTTP** : `400 Bad Request`
*   **Signature JSON** :
    ```json
    {
      "error": {
        "code": 400,
        "message": "[INVALID_ARGUMENT]: The provided file 'files/abcdef123456' was not found. It may have expired or never existed.",
        "status": "INVALID_ARGUMENT"
      }
    }
    ```
*   **Logique de Détection (Regex)** :
    Le message d'erreur doit matcher l'expression régulière : `/file.+not found/i`
    *(Ex: "The provided **file** '...' was **not found**")*

### Algorithme de Robustesse (Implémentation recommandée)

1.  **Cache Local (SQLite)** :
    *   Stocker le mapping : `Hash(Fichier) -> { uri: "files/...", expires_at: timestamp_estime }`.
    *   *Note* : Le timestamp estimé (ex: 24h) sert juste à forcer un rafraîchissement préventif, mais n'est pas une garantie.

2.  **Logique "Optimistic Upload"** :
    *   Vérifier si le fichier est en cache local.
    *   Si OUI -> Utiliser l'URI.
    *   Si NON -> Uploader -> Stocker l'URI -> Utiliser l'URI.

3.  **Gestion d'Erreur (Retry Loop)** :
    *   Si l'appel `streamGenerateContent` échoue avec `400` + "Not Found" :
        1.  Identifier l'URI fautif (ou invalider tous les URIs du message).
        2.  Supprimer l'entrée du cache SQLite.
        3.  Relancer la logique d'upload (qui fera un nouvel upload frais).
        4.  Relancer l'appel de chat.

## 4. Bénéfices Attendus

1.  **Réduction Bande Passante** : Upload unique par fichier (vs upload à chaque message). Gain massif pour les fichiers > 10 Mo.
2.  **Activation Cache Google** : L'utilisation de références stables (`fileUri`) permet au backend Google d'activer son **Prefix Caching** (Context Caching) beaucoup plus efficacement qu'avec des blobs Base64 qui peuvent varier (padding, encodage).
3.  **Latence** : Réduction du temps de parsing côté Google (le fichier est déjà indexé/stocké).

---
*Ce document sert de spécification technique pour l'implémentation future dans `pipe_engine.py`.*
