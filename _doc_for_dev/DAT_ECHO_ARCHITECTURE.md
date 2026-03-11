# Dossier d'Architecture Technique (DAT) : ECHO Framework v5.70.0

## 1. Introduction et Vision
Le Framework ECHO (Espace Cognitif Heuristique Opérationnel) est un orchestrateur cognitif conçu pour augmenter les capacités des modèles Gemini (v1.5, v2.0, v3.1) au sein d'une infrastructure Open WebUI. Il repose sur une architecture de "Pipe" et de "Filtre" permettant une manipulation granulaire du contexte, du raisonnement (pensées) et des interactions multi-modales.

---

## 2. Algorithme Central et Flux de Données

### 2.1 Le Pipe Engine (`pipe_engine.py`)
Le Pipe est le coeur de l'exécution. Son algorithme repose sur la **Suture Contextuelle**.
1.  **Réception** : Intercepte le payload OpenAI-compatible d'Open WebUI.
2.  **Restauration (Suture)** : Pour chaque message de l'historique :
    *   Calcule un hash invariant (Rôle + Contenu + Fichiers).
    *   Recherche dans la SQLite locale (`EchoStateManager`) s'il existe une version "riche" (contenant des images, des signatures de pensées ou des JSON techniques).
    *   Réinjecte les `thoughtSignature` réelles capturées lors des tours précédents pour maintenir la validité de l'arbre de raisonnement de Gemini.
3.  **Appel SSE** : Traduit le contexte en format Gemini (Contents/Parts) et appelle l'API Google via un client HTTPX asynchrone (HTTP/2 activé).
4.  **Stream Processing** : Décode le flux SSE de Google, extrait les pensées (`thought`) pour les encapsuler dans des balises `<think>`, et convertit les `functionCall` en format compréhensible par Open WebUI.

### 2.2 Le Filtre de Contexte (`new_context_filter.py`)
Agit comme une unité de pré-traitement (Inlet) et de post-traitement (Outlet).
1.  **Aiguillage de Fichiers (Smart Context)** : Si un fichier est joint :
    *   Petit texte/image -> Injection binaire directe.
    *   Fichier volumineux -> Appel à Gemini Flash pour générer un "Smart Summary" technique.
2.  **Injection `etat_echo`** : Génère un bloc JSON immuable pour le tour en cours (Heure, Lieu, Version) inséré dans le message utilisateur.
3.  **Interception OAuth** : Détecte les codes d'autorisation Google (4/...) saisis par l'utilisateur pour finaliser le bypass PKCE.

---

## 3. Gestion Spécifique de Gemini

### 3.1 Signatures de Pensées (`thoughtSignature`)
*   **Capture** : Lors de la génération, ECHO capture le champ `thoughtSignature` renvoyé par l'API Gemini.
*   **Persistance** : Stocké dans la SQLite utilisateur lié au hash cumulatif du message.
*   **Restauration** : Réinjecté dans les messages `model` de l'historique. Si absent, ECHO utilise la clé de secours `context_engineering_is_the_way_to_go` (uniquement pour les messages avec outils) pour éviter les erreurs de protocole.

### 3.2 Conformité du Cache (Implicit Caching)
L'algorithme garantit la mobilisation du cache de Google par la **stabilité du préfixe**.
*   Bien que `etat_echo` contienne une horodate, ECHO stocke la version textuelle exacte du message envoyé au tour N.
*   Au tour N+1, il ne recalcule pas l'heure du tour N, il renvoie le bloc exact stocké.
*   Le flux envoyé à Google reste donc identique bit-à-bit d'une requête à l'autre, permettant à Google de réutiliser le contexte déjà traité.

---

## 4. Authentification et Bypass PKCE
ECHO détourne l'endpoint `cloudcode-pa.googleapis.com` (Cloud Code) pour utiliser les capacités Google One / AI Pro.
1.  **PKCE (Proof Key for Code Exchange)** : ECHO génère un `code_verifier` (secret) et un `code_challenge` (S256).
2.  **Flow** : L'utilisateur est redirigé vers l'URL Google. Le code retourné est intercepté par le filtre.
3.  **Échange** : Le `pipe_engine` échange le code + le verifier contre un `access_token` et un `refresh_token`.
4.  **Maintenance** : `echo_utils.py` gère le rafraîchissement automatique du token en arrière-plan avant expiration.

---

## 5. Stockage et Isolation (SQLite)
ECHO n'utilise pas la base de données d'Open WebUI pour son intelligence.
*   **Emplacement** : `/app/backend/data/user_dbs/user-{uid}.db`.
*   **Tables Clés** :
    *   `auth_data` : Tokens et secrets OAuth.
    *   `rich_payloads` : Versions multi-parts des messages (images base64, etc.).
    *   `cognitive_data` : Signatures de pensées et métadonnées de raisonnement.
    *   `context_stats` : Historique de consommation de tokens.

---

## 6. Annexes : Outils (Tools)

### 6.1 Navigation Engine (`navigation_engine_tool.py`)
*   **Moteur** : Playwright (Chromium) dans un conteneur isolé (`browser-agent`).
*   **Algo** : 
    1.  `goto(url)` -> Charge la page.
    2.  `highlight` -> Exécute un JS pour marquer les éléments interactifs avec des index numériques.
    3.  `HUD` -> Injecte un moniteur en temps réel (Cockpit) dans l'interface de l'utilisateur via `event_call`.
    4.  `interaction` -> Clic/Saisie par index pour éviter les erreurs de sélecteurs CSS complexes.

### 6.2 Python Code Executor (`python_code_executor.py`)
*   **Moteur** : `python-worker` (Flask + Multiprocessing).
*   **Algo** : Exécute le code dans un processus OS distinct avec un `TemporaryDirectory`. Capture `stdout` et intercepte les fichiers images (plots Matplotlib) pour les renvoyer en Base64 au modèle.

### 6.3 Cognitive Core (`cognitive_core.py`)
*   **Algo** : Permet au modèle d'instancier des "sous-réflexions" (Deep Reasoning ou Quick Intel) via des appels récursifs à Gemini Flash ou Pro, avec des paramètres de température et de thinking level spécifiques.

---

## 7. Annexe : Infrastructure et Déploiement

### 7.1 Déploiement Hyper-V (`deploy-hyperv.ps1`)
1.  **Génération d'Identité** : Définit la version de la stack (depuis `VERSION`) et la branche cible.
2.  **ZIP Injection** : Compresse le code source local en Base64.
3.  **Cloud-Init** : Génère un fichier `user-data` qui :
    *   Crée les répertoires `/opt/echo-framework`.
    *   Décompresse le code.
    *   Installe Docker et Docker Compose.
    *   Lance `install-stack.sh`.
4.  **Provisioning VM** : Crée la VM Hyper-V et attache le disque de seed contenant le Cloud-Init.

### 7.2 Orchestration Docker (`stack-echo.yml` / `bunkerweb-stack.yml`)
*   **`echo-network`** : Réseau isolé pour la communication inter-conteneurs.
*   **`open-webui`** : Interface utilisateur, connectée à la SQLite ECHO via des volumes montés.
*   **`bunkerweb`** : WAF et Reverse Proxy gérant le SSL automatique.
*   **`admin-manager`** : Micro-service de maintenance (Backup, Purge, Stats).

### 7.3 Configuration Automatique (`config-owui.sh`)
*   Utilise l'API interne d'Open WebUI pour importer les paramètres (`webui-settings.json`), créer le modèle ECHO (`model-config.json`) et lier les outils/filtres sans intervention manuelle.

---
© 2026 ECHO Framework - Document Technique Confidentiel.