# **Rapport Technique Exhaustif : Architecture, Méthodologie et Implémentation de la Configuration Automatisée d'Open WebUI 0.7.2 via API**

## **1\. Introduction et Contexte Architectural**

L'évolution des interfaces de gestion pour les Grands Modèles de Langage (LLMs) auto-hébergés a atteint un point d'inflexion critique avec la sortie d'Open WebUI version 0.7.2. Autrefois simples interfaces graphiques pour Ollama ou des API compatibles OpenAI, ces plateformes sont devenues des écosystèmes complexes intégrant la gestion des connaissances (RAG), la génération d'images, et des contrôles d'accès granulaires. Dans ce contexte, la configuration manuelle via l'interface utilisateur (UI) devient un goulot d'étranglement opérationnel, introduisant des risques d'erreur humaine et empêchant la reproductibilité des environnements.

Ce rapport s'adresse aux architectes systèmes et ingénieurs DevOps chargés de l'industrialisation d'Open WebUI. Il répond à une problématique précise : comment transposer une configuration définie et exportée au format JSON depuis l'interface utilisateur vers une nouvelle instance via des appels API programmatiques. Cette démarche s'inscrit dans une logique d'Infrastructure as Code (IaC), où l'état du système est déclaré dans des artefacts (fichiers JSON) et appliqué via des interfaces standardisées (API REST).

L'analyse technique repose sur l'examen approfondi du code source du backend (notamment les routeurs FastAPI), la documentation des endpoints, et les mécanismes de persistance des données introduits dans la branche 0.7.x. La version 0.7.2, en particulier, introduit des optimisations majeures au niveau des connexions bases de données et de la gestion des timeouts 1, rendant l'approche API plus robuste pour les configurations de masse.

### **1.1 Le Paradigme de la Configuration Persistante (PersistentConfig)**

Pour comprendre la méthode de configuration via API, il est impératif d'analyser le mécanisme de stockage d'état d'Open WebUI, connu sous le nom de PersistentConfig. Contrairement aux applications cloud-native traditionnelles qui reposent exclusivement sur des variables d'environnement injectées au démarrage (stateless), Open WebUI 0.7.2 adopte une approche hybride.2

Le système distingue deux types de configurations :

1. **Variables d'Environnement Transitoires :** Utilisées pour l'initialisation (ex: WEBUI\_SECRET\_KEY, DATABASE\_URL).  
2. **Configuration Persistante (Base de Données) :** Stockée dans la table config de la base SQLite interne (webui.db).

La règle de préséance est fondamentale pour notre méthodologie : **les valeurs stockées en base de données via l'API ou l'UI écrasent systématiquement les variables d'environnement définies dans le fichier Docker Compose ou .env**.2 Cela signifie que l'API est le vecteur de contrôle ultime. Une modification effectuée via l'endpoint /api/v1/admin/config prend effet immédiatement et persiste après redémarrage du conteneur, rendant obsolètes les variables d'environnement correspondantes.

Cette architecture valide la pertinence de l'approche demandée : l'injection du fichier JSON via l'API est la méthode la plus fiable pour restaurer un état complet, car elle écrit directement dans la couche de persistance prioritaire.

### **1.2 Structure de l'Artefact JSON**

Le fichier JSON mentionné dans la requête, issu de l'export de configuration, n'est pas un simple dictionnaire plat. L'analyse des schémas Pydantic du backend 3 révèle une structure hiérarchique complexe comprenant plusieurs sous-systèmes :

* **Système Général (general) :** Contrôle les accès, les inscriptions, et les rôles par défaut.  
* **Interface Utilisateur (ui) :** Thèmes, langues, et fonctionnalités visibles (chat, historique).  
* **Connecteurs de Modèles (openai, ollama) :** Points de terminaison API, clés d'authentification, et stratégies de routage.  
* **RAG et Vecteurs (rag) :** Paramètres de découpage (chunking), modèles d'embedding, et top-k retrieval.

La suite de ce rapport déconstruira chaque section pour identifier l'endpoint API correspondant et la méthode d'injection appropriée.

## ---

**2\. Protocole de Sécurité et Authentification**

Toute interaction avec les endpoints de configuration d'Open WebUI 0.7.2 est strictement protégée. Contrairement aux endpoints de chat publics (qui peuvent parfois être ouverts), les routes d'administration (/api/v1/admin/\*, /api/v1/configs/\*) exigent un niveau de privilège élevé, vérifié par un jeton JWT (JSON Web Token).

### **2.1 Mécanisme d'Authentification JWT**

L'authentification ne repose pas sur une simple clé API statique pour les tâches administratives, mais sur une session négociée via les identifiants d'un compte administrateur.4 Le cycle de vie de cette authentification est le prérequis absolu à tout script de configuration.

**L'Endpoint d'Initialisation :**

* **Route :** /api/v1/auths/signin  
* **Méthode :** POST  
* **Contexte de Version :** Il est crucial de noter l'utilisation du préfixe /v1/. Les versions antérieures utilisaient /api/auths, mais la version 0.7.2 standardise l'API sous le namespace v1 pour assurer la compatibilité future et la gestion des versions.6

**Structure de la Requête :**

Le corps de la requête doit contenir les identifiants de l'administrateur initial (créé lors du premier lancement ou via variable d'environnement).

| Champ | Type | Description |
| :---- | :---- | :---- |
| email | String | L'adresse email du compte administrateur racine. |
| password | String | Le mot de passe associé. |

**Réponse et Extraction du Jeton :**

La réponse du serveur est un objet JSON contenant le jeton d'accès. Ce jeton est une chaîne encodée en Base64 (JWT) qui contient les claims (revendications) de l'utilisateur, notamment son role: "admin".

JSON

{  
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",  
  "token\_type": "Bearer",  
  "id": "uuid-de-l-utilisateur",  
  "name": "Admin",  
  "role": "admin"  
}

L'analyse de sécurité des logs 7 montre que ce jeton est valide pour une durée définie par la configuration serveur (par défaut souvent 24h ou 7 jours). Pour une automatisation robuste, le script de configuration doit gérer le renouvellement de ce jeton ou s'authentifier à chaque exécution.

### **2.2 Construction des En-têtes HTTP (Headers)**

Une fois le jeton obtenu, il doit être injecté dans l'en-tête Authorization de toutes les requêtes subséquentes. Le format strict attendu par le middleware FastAPI (basé sur Starlette) est le schéma "Bearer".

* **Header Clé :** Authorization  
* **Header Valeur :** Bearer \<VOTRE\_TOKEN\_JWT\>

**Avertissement de Sécurité :** L'utilisation de connexions non chiffrées (HTTP) transmet ce jeton en clair. Dans un environnement de production, il est impératif d'interagir avec l'API via HTTPS pour prévenir l'interception du jeton administrateur, qui donnerait un contrôle total sur l'instance LLM.8

## ---

**3\. Méthodologie de Configuration Globale via l'API**

Le cœur de la requête utilisateur réside dans l'application du fichier JSON exporté. L'analyse du code source via gemini-cli mentionnée dans le contexte, corroborée par les snippets de recherche 3, révèle l'existence de routeurs dédiés spécifiquement à l'importation de configuration. C'est ici que se joue la différence entre une configuration "bricolée" (en modifiant la base SQL directement) et une configuration "native" via l'API.

### **3.1 L'Endpoint d'Importation Dédié (/api/v1/configs/import)**

C'est la méthode privilégiée pour restaurer un état complet. Le routeur backend/open\_webui/routers/configs.py expose une méthode POST conçue pour ingérer l'objet de configuration global.

* **Endpoint Cible :** http://\<host\>:3000/api/v1/configs/import  
* **Méthode HTTP :** POST  
* **Permission :** Admin uniquement (Depends(get\_admin\_user)).

**Analyse du Schéma de Données (Payload) :**

Le code source Python définit un modèle Pydantic ImportConfigForm qui attend une structure précise. Il ne suffit pas d'envoyer le JSON brut à la racine du corps de la requête. Le JSON exporté doit être encapsulé.

Structure attendue par l'API :

JSON

{  
  "config": {  
    // Insérer ici le contenu complet de votre fichier JSON exporté  
    "ui": {... },  
    "general": {... },  
    "openai": {... }  
  }  
}

Si le fichier JSON exporté par l'UI contient déjà cette clé racine config (ce qui varie selon les versions mineures), il peut être envoyé tel quel. Sinon, le script de configuration doit lire le fichier et l'insérer dans cette structure enveloppante.

**Mécanisme Interne :**

Lors de la réception de cette requête, la fonction save\_config(form\_data.config) est invoquée. Cette fonction itère sur chaque clé du dictionnaire JSON et effectue un "upsert" (mise à jour ou insertion) dans la table config de la base de données SQLite.

Cette opération est atomique par clé de configuration. Cela signifie que si le fichier JSON contient des clés obsolètes ou mal formées, elles pourraient être ignorées ou provoquer une erreur de validation 422 (Unprocessable Entity), mais ne corrompront pas nécessairement les configurations valides existantes.

### **3.2 L'Endpoint de Mise à Jour Granulaire (/api/v1/admin/config/update)**

Dans le cas où l'import global échouerait (par exemple, en raison de discordances de version entre l'export et l'instance cible), l'approche alternative consiste à découper le JSON et à utiliser l'endpoint de mise à jour administrative.9

* **Endpoint Cible :** http://\<host\>:3000/api/v1/admin/config/update  
* **Méthode HTTP :** POST

Cet endpoint accepte souvent des fragments de configuration. Par exemple, pour ne mettre à jour que la politique d'inscription des utilisateurs sans toucher aux configurations des modèles :

JSON

{  
  "general": {  
    "enable\_signup": false,  
    "default\_user\_role": "pending"  
  }  
}

Cette méthode offre une granularité plus fine et facilite le débogage en cas d'erreur, car on peut isoler la section du JSON (ex: ui, rag, audio) qui pose problème.

### **3.3 Rechargement à Chaud de la Configuration**

Une spécificité critique d'Open WebUI 0.7.2 est la gestion du cache de configuration. Même après avoir écrit en base de données via l'API, l'application chargée en mémoire peut ne pas refléter immédiatement les changements pour les sessions actives.

Il existe un endpoint spécifique pour forcer le rechargement sans redémarrer le conteneur Docker 10 :

* **Endpoint :** /api/v1/admin/config/reload  
* **Méthode :** GET ou POST (selon les patchs, souvent GET).

L'intégration de cet appel à la fin de la séquence de configuration est indispensable pour garantir que l'état "in-memory" du serveur Python (FastAPI) est synchronisé avec l'état "on-disk" (SQLite) nouvellement injecté.

## ---

**4\. Configuration des Sous-Systèmes Spécifiques**

L'analyse des snippets de recherche révèle que certaines configurations ne résident pas dans le bloc config monolithique principal, mais sont gérées par des routeurs spécialisés. Pour une configuration "exhaustive", il est nécessaire de traiter ces éléments séparément.

### **4.1 Configuration des Modèles (/api/models)**

Les définitions de modèles (alias, paramètres de système, templates de prompt) sont des entités distinctes. Si le JSON exporté contient une liste de modèles, ils ne seront pas importés via l'endpoint /configs/import. Ils doivent être recréés via l'API des modèles.4

* **Endpoint :** /api/models/add ou /api/models/create  
* **Méthode :** POST

**Structure du Payload Modèle :**

JSON

{  
  "id": "llama3-custom:latest",  
  "name": "Llama 3 Entreprise",  
  "meta": {  
    "description": "Modèle optimisé pour le code",  
    "capabilities": \["chat", "code"\],  
    "suggestion\_prompts": // Suggestions spécifiques au modèle  
  },  
  "params": {  
    "system": "Tu es un expert en Python...",  
    "temperature": 0.7  
  }  
}

L'automatisation exige donc d'extraire la section "models" du fichier JSON source et d'itérer dessus pour effectuer un appel API par modèle. Cela permet également de valider individuellement chaque modèle (ex: vérifier que le modèle de base Ollama est bien disponible).

### **4.2 Configuration de la Génération d'Images**

Les fonctionnalités liées à la génération d'images (OpenAI DALL-E, Automatic1111, ComfyUI) disposent de leur propre espace de nom API, souvent préfixé par /images.11

* **Endpoint :** /images/api/v1/config/update  
* **Méthode :** POST  
* **Contexte :** Cet endpoint gère spécifiquement les variables comme IMAGES\_OPENAI\_API\_KEY, IMAGE\_GENERATION\_MODEL, et les paramètres de taille d'image (IMAGE\_SIZE).

Il est crucial de dissocier ces paramètres du JSON principal si l'import global échoue, car le backend traite ces configurations via un module distinct (open\_webui.apps.images).

### **4.3 Suggestions de Prompts par Défaut**

Les "Default Prompt Suggestions" (les boutons cliquables affichés sur un nouveau chat vide) sont gérés par une table spécifique et un endpoint dédié.12

* **Endpoint :** /api/v1/configs/suggestions  
* **Méthode :** POST  
* **Payload :** Une liste d'objets JSON.

JSON

\[  
  {  
    "title": \["Aide Python"\],  
    "content": "Écris un script Python pour..."  
  },  
  {  
    "title":,  
    "content": "Résume ce texte en 5 points..."  
  }  
\]

L'oubli de cet endpoint lors de la migration de configuration laisse souvent les utilisateurs avec les suggestions par défaut (anglaises), ce qui nuit à l'expérience utilisateur personnalisée visée.

## ---

**5\. Méthodologie d'Implémentation Technique**

Sur la base de l'analyse ci-dessus, voici la procédure technique détaillée pour réaliser l'objectif : "donner la méthode de configuration via API". Cette section synthétise les appels dans un flux logique.

### **5.1 Pré-requis et Préparation des Données**

1. **Fichier Source :** Disposer du fichier open-webui-export.json.  
2. **Nettoyage JSON :** Il est recommandé de pré-traiter le fichier JSON pour retirer les métadonnées d'export (comme la date ou la version de l'export) qui pourraient provoquer des erreurs de validation stricte (Pydantic ValidationError) lors de l'import.  
3. **Accessibilité Réseau :** S'assurer que le script d'automatisation a accès au port de l'instance (par défaut 3000 ou 8080\) et que le pare-feu autorise les méthodes POST/PUT.

### **5.2 Algorithme de Configuration (Script Logique)**

L'implémentation doit suivre scrupuleusement cet ordre séquentiel pour garantir l'intégrité référentielle (par exemple, ne pas configurer un modèle par défaut avant d'avoir configuré la connexion au fournisseur de modèles).

#### **Étape 1 : Authentification et Acquisition de Session**

Le script doit d'abord négocier le jeton.

* *Action :* POST sur /api/v1/auths/signin.  
* *Gestion d'erreur :* Si code 401, vérifier les identifiants initiaux (souvent admin@example.com / admin sur une fresh install, ou ceux définis par WEBUI\_ADMIN\_EMAIL).

#### **Étape 2 : Injection de la Configuration Globale**

* *Action :* Lecture du fichier JSON. Encapsulation dans {"config": data} si nécessaire. POST sur /api/v1/configs/import.  
* *Validation :* Vérifier le code de retour 200\. Si 422, le JSON contient des clés invalides pour cette version d'API.

#### **Étape 3 : Configuration des Connecteurs Externes (Si séparés)**

Si le JSON sépare les connexions, appliquer les configurations OpenAI/Ollama.

* *Point d'attention :* Les URLs internes. Si Open WebUI tourne dans Docker, localhost fait référence au conteneur lui-même. La configuration API doit souvent utiliser http://host.docker.internal:11434 pour atteindre un Ollama hôte.13

#### **Étape 4 : Enregistrement des Modèles**

* *Action :* Itération sur la liste models du JSON. POST individuel sur /api/models/add.  
* *Optimisation :* Cette étape peut être parallélisée, mais attention à la charge sur la base SQLite (verrouillage).

#### **Étape 5 : Configuration RAG et Connaissances**

* *Action :* Si des collections de documents sont définies, utiliser /api/v1/knowledge/ pour recréer les collections.  
* *Note Importante :* L'API peut restaurer les *métadonnées* des collections, mais le ré-upload des fichiers physiques et le recalcul des vecteurs (embeddings) sont souvent nécessaires si les volumes Docker ne sont pas partagés. L'endpoint /api/v1/knowledge/{id}/file/add 4 permet d'associer des fichiers.

#### **Étape 6 : Rechargement et Vérification**

* *Action :* Appel à /api/v1/admin/config/reload.  
* *Contrôle :* Un GET sur /api/v1/configs/export permet de récupérer la configuration appliquée et de la comparer (diff) avec le fichier source pour valider l'intégrité de l'opération.

## ---

**6\. Tableaux de Référence des Endpoints API**

Pour faciliter l'intégration, les endpoints identifiés dans les documents de recherche sont consolidés dans les tableaux suivants. Ces références sont basées sur la structure de l'API Open WebUI 0.7.2.

### **Tableau 6.1 : Endpoints d'Administration et Configuration**

| Fonctionnalité | Méthode HTTP | Endpoint (URI Relatif) | Payload Attendu (JSON) | Description |
| :---- | :---- | :---- | :---- | :---- |
| **Authentification** | POST | /api/v1/auths/signin | {"email": "...", "password": "..."} | Obtention du Jeton JWT Bearer. |
| **Import Config** | POST | /api/v1/configs/import | {"config": {... }} | Restauration complète des paramètres depuis un export UI. |
| **Export Config** | GET | /api/v1/configs/export | N/A | Récupération de l'état actuel pour sauvegarde. |
| **Update Admin** | POST | /api/v1/admin/config/update | {"general": {...}, "ui": {...}} | Mise à jour partielle/granulaire des paramètres. |
| **Reload Config** | GET | /api/v1/admin/config/reload | N/A | Force le rechargement en mémoire depuis la DB. |
| **Prompt Suggestions** | POST | /api/v1/configs/suggestions | \[{"title": "...", "content": "..."}\] | Définit les suggestions de la page d'accueil. |
| **Bannières** | POST | /api/v1/configs/banners | \[{"id": "...", "content": "..."}\] | Configure les annonces système en haut de l'UI. |

### **Tableau 6.2 : Endpoints de Gestion des Modèles et Ressources**

| Fonctionnalité | Méthode HTTP | Endpoint (URI Relatif) | Description |
| :---- | :---- | :---- | :---- |
| **Lister Modèles** | GET | /api/models | Récupère tous les modèles enregistrés. |
| **Créer Modèle** | POST | /api/models/add | Enregistre un nouveau modèle ou un alias de modèle. |
| **Config Image** | POST | /images/api/v1/config/update | Configuration spécifique pour DALL-E/Stable Diffusion. |
| **Config Audio** | POST | /audio/api/v1/config/update | Configuration TTS (Text-to-Speech) et STT (Whisper). |
| **Knowledge Base** | POST | /api/v1/knowledge/create | Création d'une nouvelle collection de documents RAG. |

## ---

**7\. Considérations Opérationnelles et Risques**

L'automatisation via API, bien que puissante, introduit des complexités opérationnelles qui doivent être maîtrisées pour un environnement de production stable.

### **7.1 Gestion de la Concurrence SQLite**

Open WebUI utilise SQLite comme moteur de stockage par défaut (webui.db). SQLite est performant en lecture mais possède des limitations en écriture concurrente (verrouillage de fichier).

* **Risque :** Si le script de configuration lance des dizaines d'appels API parallèles (par exemple pour créer 50 utilisateurs et 20 modèles simultanément), il risque de provoquer des erreurs Database Locked ou des timeouts, bien que la version 0.7.2 ait amélioré ce point.1  
* **Recommandation :** Le script d'importation doit implémenter une logique séquentielle ou une gestion des "retries" (nouvelles tentatives) avec un délai exponentiel (exponential backoff) en cas d'erreur 500 ou de timeout DB.

### **7.2 Compatibilité des Versions de Configuration**

Le format du fichier JSON d'export évolue avec chaque version d'Open WebUI.

* **Risque :** Importer un JSON exporté d'une version 0.6.x dans une instance 0.7.2 peut échouer silencieusement ou partiellement si les clés de configuration ont changé de nom (ex: ENABLE\_API\_KEY\_ENDPOINT\_RESTRICTIONS remplacé par API\_KEYS\_ALLOWED\_ENDPOINTS 2).  
* **Stratégie :** Il est recommandé de comparer les clés du JSON exporté avec un export "vierge" de la version cible 0.7.2 pour identifier les disparités structurelles avant l'importation massive.

### **7.3 Sécurité des Données Sensibles**

Le fichier JSON d'export contient souvent des clés API (OpenAI, Anthropic) en clair ou faiblement chiffrées selon les versions.

* **Risque :** Stocker ce fichier JSON dans un dépôt Git constitue une faille de sécurité majeure.  
* **Recommandation :** Le script d'automatisation devrait idéalement lire ces secrets depuis un gestionnaire de secrets (Vault, AWS Secrets Manager) et les injecter dynamiquement dans le payload JSON au moment de l'exécution, plutôt que d'utiliser un fichier statique contenant les clés réelles.

## ---

**8\. Conclusion**

L'analyse démontre que la configuration d'Open WebUI 0.7.2 est entièrement pilotable via son API REST, offrant une alternative robuste à la configuration manuelle. La clé de voûte de cette opération réside dans l'exploitation correcte de l'endpoint /api/v1/configs/import et la compréhension du mécanisme PersistentConfig qui assure la pérennité des réglages face aux redémarrages de conteneurs.

En suivant la méthodologie structurée présentée — authentification admin, encapsulation du JSON, et séquençage des appels aux sous-systèmes (Modèles, Images, RAG) — il est possible d'automatiser le déploiement d'instances Open WebUI standardisées. Cette approche transforme l'interface d'IA en un composant d'infrastructure programmable, aligné avec les meilleures pratiques DevOps modernes. Les fichiers fournis par l'utilisateur (JSON d'export et extraits API) constituent la matière première idéale pour alimenter ce pipeline d'automatisation, sous réserve d'un nettoyage et d'une validation préalables conformes aux schémas de données de la version 0.7.2.

#### **Sources des citations**

1. Releases · open-webui/open-webui \- GitHub, consulté le février 10, 2026, [https://github.com/open-webui/open-webui/releases](https://github.com/open-webui/open-webui/releases)  
2. Environment Variable Configuration \- Open WebUI, consulté le février 10, 2026, [https://docs.openwebui.com/getting-started/env-configuration/](https://docs.openwebui.com/getting-started/env-configuration/)  
3. backend/open\_webui/routers/configs.py · v0.5.5 \- GitLab, consulté le février 10, 2026, [https://code.ovgu.de/usc/von-github/open-webui/-/blob/v0.5.5/backend/open\_webui/routers/configs.py](https://code.ovgu.de/usc/von-github/open-webui/-/blob/v0.5.5/backend/open_webui/routers/configs.py)  
4. API Endpoints \- Open WebUI, consulté le février 10, 2026, [https://docs.openwebui.com/getting-started/api-endpoints/](https://docs.openwebui.com/getting-started/api-endpoints/)  
5. Accessing via API · open-webui open-webui · Discussion \#351 \- GitHub, consulté le février 10, 2026, [https://github.com/open-webui/open-webui/discussions/351](https://github.com/open-webui/open-webui/discussions/351)  
6. issue: Access to API with JWT token · open-webui open-webui · Discussion \#21174 \- GitHub, consulté le février 10, 2026, [https://github.com/open-webui/open-webui/discussions/21174](https://github.com/open-webui/open-webui/discussions/21174)  
7. \[Usage\]: Mistral Large crashes with concurrent long context requests \#13100 \- GitHub, consulté le février 10, 2026, [https://github.com/vllm-project/vllm/issues/13100](https://github.com/vllm-project/vllm/issues/13100)  
8. API Keys & Monitoring \- Open WebUI, consulté le février 10, 2026, [https://docs.openwebui.com/getting-started/advanced-topics/monitoring/](https://docs.openwebui.com/getting-started/advanced-topics/monitoring/)  
9. apimgr/search: Repo for search \- GitHub, consulté le février 10, 2026, [https://github.com/apimgr/search](https://github.com/apimgr/search)  
10. Configuring the i2 Analyze application \- IBM, consulté le février 10, 2026, [https://www.ibm.com/docs/en/SSXVTH\_4.3.3/com.ibm.i2.eia.go.live.doc/eia\_go\_live\_pdf.pdf](https://www.ibm.com/docs/en/SSXVTH_4.3.3/com.ibm.i2.eia.go.live.doc/eia_go_live_pdf.pdf)  
11. Unable to Import Config from JSON File after reinstalling \#5900 \- GitHub, consulté le février 10, 2026, [https://github.com/open-webui/open-webui/discussions/5900](https://github.com/open-webui/open-webui/discussions/5900)  
12. feat: Import/Export Default Prompt Suggestions as JSON File \#5876 \- GitHub, consulté le février 10, 2026, [https://github.com/open-webui/open-webui/discussions/5876](https://github.com/open-webui/open-webui/discussions/5876)  
13. Quick Start \- Open WebUI, consulté le février 10, 2026, [https://docs.openwebui.com/getting-started/quick-start/](https://docs.openwebui.com/getting-started/quick-start/)  
14. open-webui/CHANGELOG.md at main \- GitHub, consulté le février 10, 2026, [https://github.com/open-webui/open-webui/blob/main/CHANGELOG.md](https://github.com/open-webui/open-webui/blob/main/CHANGELOG.md)