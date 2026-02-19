# **Analyse Architecturale Approfondie des Filtres et de la Gestion des Fichiers dans Open-WebUI (v0.5.x \- v0.8.x)**

## **Introduction**

L'évolution rapide des interfaces utilisateur pour les grands modèles de langage (LLM) a vu l'émergence d'Open-WebUI comme une solution auto-hébergée de premier plan, offrant une flexibilité comparable aux solutions propriétaires tout en garantissant la souveraineté des données. Au cœur de cette flexibilité réside le système de "Fonctions" (Functions), et plus spécifiquement l'architecture des **Filtres**, qui permet aux développeurs d'intercepter et de manipuler les requêtes avant qu'elles n'atteignent le modèle d'inférence. Cette capacité d'interception est cruciale pour l'intégration de logiques métiers complexes, telles que l'analyse contextuelle externe, la modération, ou, comme dans le cas qui nous occupe, le traitement conditionnel de fichiers via des API tierces comme Smart Context.

Le présent rapport se propose de décortiquer l'architecture technique des filtres dans Open-WebUI, en se concentrant sur les versions v0.5.x à v0.8.x. Cette plage de versions est particulièrement significative car elle englobe une transition majeure dans la gestion des pipelines de données, l'introduction de Pydantic v2 pour la validation des schémas, et une refonte de la gestion des fichiers pour le RAG (Retrieval-Augmented Generation). L'analyse répondra à une problématique d'ingénierie précise : comment concevoir un filtre inlet idempotent capable de distinguer les nouveaux fichiers téléchargés par l'utilisateur des fichiers historiques persistant dans le contexte de la conversation, afin d'optimiser les appels API et de préserver les ressources.

Nous explorerons en détail le cycle de vie d'une requête de chat, la structure exacte de la charge utile JSON (payload) transmise aux filtres, la sémantique des objets fichiers (body\['files'\] vs body\['messages'\]), et les mécanismes de persistance du contexte. Cette analyse s'appuiera sur une étude rigoureuse du code source du backend (FastAPI), des comportements observés dans les discussions de la communauté de développement, et des documents techniques officiels.

## **1\. Architecture Middleware et Cycle de Vie des Requêtes**

Pour comprendre comment isoler efficacement les nouveaux fichiers, il est impératif de visualiser le cheminement d'une requête au sein de l'architecture d'Open-WebUI. Contrairement à une architecture monolithique simple, Open-WebUI adopte une approche modulaire basée sur des intergiciels (middlewares) et des pipelines de traitement qui transforment séquentiellement les données.

### **1.1 Le Rôle Central de process\_chat\_payload**

Au cœur du backend d'Open-WebUI, situé généralement dans le module backend/open\_webui/utils/middleware.py, réside une fonction que l'on pourrait qualifier de "fonction Dieu" du système : process\_chat\_payload.1 Cette fonction orchestre la préparation de toutes les données avant l'interaction avec le LLM. C'est à ce niveau précis que la complexité de la gestion des fichiers prend racine.

Lorsque l'utilisateur envoie un message depuis l'interface Svelte, une requête POST est émise vers l'endpoint de complétion de chat. Cette requête contient un corps JSON brut qui inclut l'historique des messages, les paramètres du modèle, et les métadonnées des fichiers. La fonction process\_chat\_payload intercepte cette charge utile brute et effectue plusieurs opérations critiques : elle résout les paramètres spécifiques au modèle depuis la base de données, active les fonctionnalités contextuelles comme la recherche web ou la mémoire à long terme, et, point crucial pour notre analyse, elle prépare le contexte des fichiers pour le système RAG.2

C'est uniquement après ces étapes préliminaires que le système invoque les filtres définis par l'utilisateur via la fonction process\_pipeline\_inlet\_filter ou process\_filter\_functions.2 Cette séquence d'exécution est déterminante : le filtre inlet reçoit une charge utile (body) qui est déjà une version enrichie et structurée de la requête initiale de l'utilisateur, mais qui n'a pas encore été finalisée pour l'inférence du modèle. Cela offre une fenêtre d'opportunité pour modifier, nettoyer ou, comme requis, extraire des données vers une API externe.

### **1.2 Distinction Fondamentale : Filtres vs Pipes vs Outils**

Il est essentiel de lever toute ambiguïté sur la nature des composants d'Open-WebUI, car leur accès aux données diffère. Les documents techniques distinguent trois types de fonctions 3 :

Le **Filtre (Filter)** agit comme un "hook" ou un intercepteur. Il possède deux méthodes principales : inlet (entrée) et outlet (sortie). L'inlet modifie la requête *vers* le modèle, tandis que l'outlet modifie la réponse *du* modèle. Le filtre ne remplace pas le modèle ; il s'insère dans le flux. C'est l'outil adéquat pour la tâche de pré-traitement des fichiers sans altérer la capacité générative du modèle sous-jacent.

Le **Pipe**, en revanche, agit comme un "modèle virtuel". Il reçoit la requête et est responsable de la génération complète de la réponse. Si l'on utilisait un Pipe, on aurait accès à une variable \_\_files\_\_ qui, selon les rapports de la communauté, a tendance à accumuler l'intégralité des fichiers de la conversation, rendant la distinction des nouveaux fichiers plus complexe sans une logique de comparaison d'état explicite.4

Les **Outils (Tools)** sont des fonctions appelables par le modèle lui-même (function calling) et interviennent à un stade différent du raisonnement, souvent après une première passe d'analyse par le LLM. Ils ne conviennent pas pour une interception systématique des fichiers en amont de toute inférence.

### **1.3 Évolution de l'Architecture : v0.5.x vers v0.8.x**

L'analyse doit tenir compte de l'évolution rapide de la plateforme. La transition vers la version v0.8.x marque un tournant architectural avec l'adoption de standards plus stricts et une meilleure modularité.

Dans les versions v0.5.x, la gestion des fichiers était souvent monolithique. Lorsqu'un fichier était téléchargé, il était immédiatement traité pour l'extraction de texte et l'intégration vectorielle (embedding). Le lien entre le fichier et le contexte de chat était géré de manière relativement statique. Les développeurs ont souvent noté que le contenu extrait des fichiers était parfois injecté directement dans le message système, enveloppé dans des balises de template RAG, ce qui rendait le comptage de tokens ou la modification du contenu difficile pour les filtres inlet qui recevaient le corps de la requête avant cette injection finale.6

Avec l'arrivée des versions v0.8.x, bien que la logique fondamentale de middleware persiste, on observe une structuration plus fine des données. L'introduction de Pydantic v2 pour la validation des schémas impose une rigueur accrue dans la définition des objets.7 De plus, des correctifs ont été apportés pour assurer la persistance des métadonnées modifiées par les filtres, un problème récurrent dans les versions antérieures où les modifications du system prompt ou des paramètres dans un filtre étaient parfois écrasées par les processus en aval.6 Cependant, la dualité entre le stockage des fichiers dans la base de données et leur représentation dans la charge utile JSON de la requête demeure une source de complexité que nous analyserons dans la section suivante.

## ---

**2\. Anatomie de la Charge Utile (Payload) : body\['files'\] vs body\['messages'\]**

La réponse à la problématique de la détection des "nouveaux" fichiers réside entièrement dans la compréhension de la structure de données transmise à la fonction inlet. Cette structure est un dictionnaire Python (résultat de la désérialisation du JSON) qui contient l'état actuel de la conversation.

### **2.1 La Liste Globale body\['files'\] : Le Contexte Actif**

La variable body\['files'\] est souvent la source de confusion principale pour les développeurs. Dans l'architecture d'Open-WebUI, cette liste ne représente pas l'historique des téléchargements, ni spécifiquement les fichiers du dernier message. Elle représente le **Contexte de Récupération Actif (Active Retrieval Context)**.

Lorsque l'utilisateur active le mode RAG ou attache des fichiers, le frontend (l'interface utilisateur) compile une liste de tous les fichiers qui doivent être considérés par le moteur de recherche vectorielle pour générer la réponse courante. Par défaut, dans une conversation continue, l'interface tend à maintenir les fichiers précédemment téléchargés dans cette liste active pour assurer que le modèle conserve une "mémoire" des documents précédents. C'est ce comportement qui explique pourquoi, lors du deuxième ou troisième tour de conversation, le filtre reçoit toujours les anciens fichiers dans body\['files'\].6 Si le filtre itère aveuglément sur cette liste pour déclencher un appel API, il ré-analysera inévitablement les fichiers historiques, provoquant la redondance observée.

Il est crucial de noter que cette liste body\['files'\] est une instruction pour le backend : "Voici les ressources que tu dois utiliser pour répondre". Elle est décorrélée de l'action utilisateur immédiate d'attacher un fichier.

### **2.2 La Liste Locale body\['messages'\]\[-1\]\['files'\] : L'Intention Immédiate**

À l'opposé, la clé messages contient la liste chronologique des interactions. L'élément body\['messages'\]\[-1\] correspond invariablement au dernier message envoyé par l'utilisateur, celui qui a déclenché l'événement actuel.

L'objet message possède sa propre propriété files (ou parfois imbriquée dans des métadonnées selon les versions mineures, mais généralement accessible directement). Cette liste locale a une sémantique différente : elle représente les **Pièces Jointes Explicites** de ce tour de parole.

Dans le flux de travail standard de l'interface Open-WebUI :

1. L'utilisateur tape un message.  
2. L'utilisateur clique sur le bouton "Upload" et sélectionne un fichier.  
3. Le frontend crée un objet message temporaire contenant ce fichier spécifique.  
4. L'utilisateur envoie le message.

En conséquence, body\['messages'\]\[-1\]\['files'\] ne contient que les fichiers qui sont visuellement attachés à la bulle de message courante. Si un fichier a été envoyé trois messages plus tôt, il apparaîtra dans body\['messages'\]\[-3\]\['files'\], mais pas dans le dernier message, à moins que l'utilisateur ne l'ait explicitement rattaché une seconde fois (ce qui est une action utilisateur distincte et intentionnelle).

Cette distinction structurelle est la clé de voûte de notre solution : body\['files'\] est pour le *modèle* (contexte), tandis que body\['messages'\]\[-1\]\['files'\] est pour l' *historique* (action).

### **2.3 Analyse Comparative des Métadonnées : id, file\_id, et itemId**

La robustesse d'un filtre dépend de la fiabilité des identifiants utilisés pour suivre les fichiers. Les documents de recherche révèlent une certaine disparité dans la nomenclature des identifiants selon qu'on se place côté frontend ou backend.

Le tableau ci-dessous synthétise les propriétés observées des objets fichiers dans les différentes parties du système :

| Propriété | Contexte d'Apparition | Persistance | Usage Recommandé |
| :---- | :---- | :---- | :---- |
| **id** | Backend (Base de données), Objet File | **Haute** (UUID) | **Primaire**. C'est l'identifiant canonique généré par le serveur lors de l'upload initial. |
| **file\_id** | Citations RAG, Métadonnées filtres | **Haute** | Synonyme de id dans certains contextes RAG. À utiliser si id est absent. |
| **itemId** | Frontend (Svelte), Listes temporaires | **Faible** / Éphémère | Souvent utilisé par le framework UI pour le drag-and-drop. Ne pas utiliser pour la logique backend.8 |
| **\_\_files\_\_** | Pipe Functions (Variables globales) | **Session** | Contient *tous* les fichiers de la session. Source de confusion pour la détection de nouveauté.4 |

L'analyse des snippets 9 et 10 confirme que le backend génère un UUID unique (champ id) pour chaque fichier stocké. Cet ID est immuable. Même si un utilisateur télécharge le même fichier deux fois, le système peut soit générer un nouvel ID (s'il le traite comme une nouvelle entité), soit référencer le hachage existant, mais dans le contexte du message, l'ID reste la référence fiable. L'usage de itemId est à proscrire pour une logique de filtre robuste car il est trop couplé à l'état transitoire de l'interface utilisateur.

## ---

**3\. Mécanismes de Persistance et RAG : Impacts des Modifications**

Une question critique posée concerne les effets de bord potentiels de la manipulation de body\['files'\] dans le filtre. La compréhension du mécanisme RAG d'Open-WebUI est ici indispensable.

### **3.1 Le Flux RAG et l'Injection de Contexte**

Lorsque le backend reçoit la requête, si des fichiers sont présents dans body\['files'\], le système déclenche le pipeline RAG.1 Ce pipeline :

1. Vérifie si les fichiers sont déjà vectorisés (embeddings). Sinon, il lance l'extraction et la vectorisation.  
2. Effectue une recherche sémantique dans la base vectorielle (ChromaDB) en utilisant la requête de l'utilisateur comme vecteur de requête.  
3. Récupère les fragments (chunks) les plus pertinents.  
4. Injecte ces fragments dans le system prompt ou crée une section de contexte dédiée dans la liste des messages, souvent invisible pour l'utilisateur final mais visible pour le modèle.

### **3.2 L'Effet Éphémère du Nettoyage de body\['files'\]**

Si un développeur écrit body\['files'\] \= dans la fonction inlet, il modifie l'objet *en mémoire* qui est passé aux étapes suivantes du pipeline de traitement de *cette requête spécifique*.

**Conséquences immédiates :**

Le moteur RAG, ne voyant plus de fichiers dans la liste active, ne déclenchera pas la recherche vectorielle pour cette itération. Le modèle répondra donc sans connaissance du contenu des fichiers, à moins que ce contenu n'ait été injecté ailleurs (par exemple, copié-collé dans le texte du message).

**Conséquences sur l'historique (Persistance) :** Crucialement, cette modification est **non destructive** pour la base de données persistante.12 Les fichiers restent associés à l'entrée de chat dans la base de données SQLite/PostgreSQL. Si l'utilisateur rafraîchit la page ou envoie un nouveau message, le frontend reconstruira la charge utile en se basant sur l'état persistant stocké côté client ou rechargé depuis le serveur. Par conséquent, vider body\['files'\] dans un filtre n'efface pas les fichiers de l'historique de la conversation. Ils seront de nouveau présents lors de la prochaine requête, ce qui réactivera le RAG pour le tour suivant si la logique du filtre ne intervient pas à nouveau.

Cette distinction est vitale : modifier le body dans l'inlet altère le **traitement** (processing), pas le **stockage** (storage). Pour l'objectif visé (envoyer à une API externe sans perturber le RAG natif), il est impératif de **ne pas** vider body\['files'\] si l'on souhaite que le LLM d'Open-WebUI puisse toujours répondre aux questions sur ces fichiers après que l'API externe les ait traités. Si l'objectif est de déléguer *entièrement* le traitement à l'API externe (Smart Context) et d'empêcher le RAG natif de consommer des ressources, alors vider la liste est une stratégie valide, mais elle privera le LLM local du contexte direct.

### **3.3 Garantie de Contenu pour body\['messages'\]\[-1\]\['files'\]**

Les données recueillies indiquent une forte garantie structurelle concernant body\['messages'\]\[-1\]\['files'\]. Dans l'architecture REST d'Open-WebUI, chaque message est une entité atomique lors de sa création. Le frontend Svelte construit l'objet du dernier message au moment de l'envoi. La liste files qui lui est attachée correspond strictement aux fichiers présents dans la zone de composition (staging area) au moment du clic sur "Envoyer".

Il n'y a pas de mécanisme automatique côté backend qui "injecterait" des fichiers historiques dans l'objet du *dernier* message utilisateur. Les fichiers historiques sont gérés soit via la liste globale files, soit en étant présents dans les objets messages antérieurs (messages\[-2\], messages\[-3\], etc.). Par conséquent, on peut affirmer avec un haut degré de certitude que tout fichier présent dans body\['messages'\]\[-1\]\['files'\] est une intention explicite d'attachement pour le tour actuel. Cependant, "intention explicite" n'est pas strictement synonyme de "nouveau fichier jamais vu". Un utilisateur pourrait théoriquement ré-attacher un fichier déjà envoyé précédemment. Pour une robustesse absolue, une vérification par différence d'ensembles (Set Difference) est recommandée.

## ---

**4\. Stratégie Algorithmique pour l'Idempotence**

Sur la base de l'analyse précédente, nous pouvons formuler une stratégie algorithmique fiable pour résoudre le problème de re-traitement. L'absence d'un indicateur booléen natif is\_new oblige à implémenter une logique de comparaison d'état.

### **4.1 L'Algorithme de Différence d'Ensembles (Set Difference)**

La méthode la plus sûre pour identifier les fichiers qui nécessitent un traitement API est de comparer les fichiers du message courant avec l'ensemble de tous les fichiers déjà vus dans l'historique de la conversation.

**Logique :**

1. **Identification du Message Courant :** Cibler body\['messages'\]\[-1\]. Vérifier que son rôle est bien user.  
2. **Extraction des Candidats :** Récupérer la liste des IDs de fichiers présents dans ce dernier message. Soit l'ensemble ![][image1] (Current).  
3. **Construction de l'Historique :** Parcourir tous les messages précédents (body\['messages'\]\[:-1\]) et agréger tous les IDs de fichiers qu'ils contiennent. Soit l'ensemble ![][image2] (History).  
4. **Calcul de la Différence :** Les fichiers à traiter sont définis par ![][image3] (les éléments présents dans C mais pas dans H).

Cette approche couvre tous les cas limites :

* **Cas Standard :** L'utilisateur envoie un fichier pour la première fois. Il est dans ![][image1], pas dans ![][image2]. \-\> Traitement.  
* **Cas du Tour Suivant :** L'utilisateur pose une question de suivi sans fichier. ![][image1] est vide. \-\> Aucun traitement.  
* **Cas de Ré-attachement :** L'utilisateur ré-envoie le même fichier (peu probable mais possible). Il est dans ![][image1] et dans ![][image2]. \-\> Aucun traitement (filtré par la différence). Si la logique métier exige de re-traiter un fichier explicitement ré-envoyé, il suffit de supprimer l'étape de soustraction de ![][image2] et de se fier uniquement à la présence dans ![][image1].

### **4.2 Implémentation Technique dans le Filtre inlet**

Le code Python ci-dessous concrétise cette stratégie, en intégrant les meilleures pratiques pour la gestion asynchrone et la gestion des erreurs, essentielles dans un environnement v0.8.x.

Python

"""  
title: Smart Context Connector  
author: Expert Architect  
version: 1.0.2  
description: Filtre inlet pour envoyer les nouveaux fichiers uniquement à l'API Smart Context.  
"""

from pydantic import BaseModel, Field  
from typing import Optional, List, Set  
import aiohttp  
import logging

\# Configuration du logging pour tracer les opérations dans la console d'Open-WebUI  
logging.basicConfig(level=logging.INFO)  
logger \= logging.getLogger(\_\_name\_\_)

class Filter:  
    class Valves(BaseModel):  
        \# Configuration exposée dans l'interface graphique d'Open-WebUI  
        api\_url: str \= Field(  
            default="https://api.smartcontext.ai/v1/ingest",  
            description="URL de l'endpoint API Smart Context"  
        )  
        api\_key: str \= Field(  
            default="",  
            description="Clé API pour l'authentification"  
        )  
        timeout\_seconds: int \= Field(  
            default=10,  
            description="Timeout pour l'appel API externe"  
        )

    def \_\_init\_\_(self):  
        self.valves \= self.Valves()

    async def inlet(self, body: dict, \_\_user\_\_: Optional\[dict\] \= None) \-\> dict:  
        """  
        Hook exécuté avant l'envoi au modèle.  
        Détecte les nouveaux fichiers et les envoie à l'API externe.  
        """  
        messages \= body.get("messages",)  
          
        \# Sécurité : Si pas de messages, on ne fait rien  
        if not messages:  
            return body

        \# 1\. Identifier le dernier message utilisateur  
        last\_message \= messages\[-1\]  
        if last\_message.get("role")\!= "user":  
            return body

        \# 2\. Extraire les fichiers attachés EXPLICITEMENT à ce message  
        \# C'est la garantie architecturale discutée en section 2.2  
        current\_files \= last\_message.get("files",)  
          
        if not current\_files:  
            logger.debug("Aucun fichier attaché au message courant.")  
            return body

        \# 3\. Construire l'ensemble des IDs historiques (Set Difference Strategy)  
        \# On parcourt tous les messages sauf le dernier  
        history\_ids: Set\[str\] \= set()  
        for msg in messages\[:-1\]:  
            for file\_item in msg.get("files",):  
                \# Gestion robuste : l'objet file peut être un dict ou un objet Pydantic selon la version  
                f\_id \= file\_item.get("id") if isinstance(file\_item, dict) else getattr(file\_item, "id", None)  
                if f\_id:  
                    history\_ids.add(f\_id)

        \# 4\. Filtrer : Garder uniquement les fichiers qui ne sont PAS dans l'historique  
        \# (Ou simplement tous ceux du message courant si on veut permettre le re-traitement explicite)  
        new\_files\_to\_process \=  
        for f in current\_files:  
            f\_id \= f.get("id")  
            \# Condition : ID valide ET non présent dans l'historique  
            if f\_id and f\_id not in history\_ids:  
                new\_files\_to\_process.append(f)

        \# 5\. Traitement externe (Appel API)  
        if new\_files\_to\_process:  
            logger.info(f"Détection de {len(new\_files\_to\_process)} nouveaux fichiers. Envoi à Smart Context...")  
              
            \# Préparation des headers et du payload pour l'API externe  
            headers \= {  
                "Authorization": f"Bearer {self.valves.api\_key}",  
                "Content-Type": "application/json"  
            }  
              
            \# On envoie uniquement les métadonnées ou le contenu selon le besoin.  
            \# Ici, on suppose que l'API a besoin des IDs et peut-être des URLs ou du contenu base64  
            payload \= {  
                "user\_id": \_\_user\_\_.get("id") if \_\_user\_\_ else "anonymous",  
                "files": new\_files\_to\_process  
            }

            try:  
                \# Utilisation de aiohttp pour ne pas bloquer le thread principal (FastAPI est async)  
                async with aiohttp.ClientSession() as session:  
                    async with session.post(  
                        self.valves.api\_url,   
                        json=payload,   
                        headers=headers,  
                        timeout=self.valves.timeout\_seconds  
                    ) as response:  
                        if response.status \== 200:  
                            logger.info("Succès : Fichiers traités par Smart Context.")  
                            \# Optionnel : On peut injecter une confirmation dans le corps du message  
                            \# last\_message\["content"\] \+= "\\n\\n"  
                        else:  
                            logger.error(f"Erreur API Smart Context : {response.status}")  
                            response\_text \= await response.text()  
                            logger.error(f"Détails : {response\_text}")  
                              
            except Exception as e:  
                logger.error(f"Exception lors de l'appel API Smart Context : {str(e)}")  
                \# Stratégie Fail-Open : On ne bloque pas le chat si l'API externe échoue  
          
        else:  
            logger.info("Fichiers présents mais déjà traités (historique). Ignorés.")

        \# 6\. Retourner le body intact pour que le RAG natif d'Open-WebUI fonctionne  
        \# Si on voulait désactiver le RAG natif, on ferait : body\['files'\] \=  
        return body

### **4.3 Analyse des Choix Techniques du Code**

1. **Typage Pydantic (BaseModel)** : L'utilisation de la classe interne Valves héritant de BaseModel est conforme aux standards v0.8.x. Cela permet à l'utilisateur de configurer l'URL de l'API et la clé directement depuis l'interface d'administration, sans toucher au code.  
2. **Asynchronisme (async/await)** : Open-WebUI utilisant FastAPI, les filtres sont exécutés dans une boucle d'événements. L'utilisation de requests (synchrone) bloquerait le traitement de toutes les autres requêtes pendant l'appel API. L'usage de aiohttp est donc impératif pour maintenir la performance du serveur.  
3. **Sécurité (Fail-Open)** : Le bloc try/except autour de l'appel API garantit que si le service "Smart Context" est hors ligne, l'utilisateur peut continuer à discuter avec le modèle (via le RAG natif ou sans contexte), évitant un déni de service complet de l'interface de chat.  
4. **Gestion de l'Historique** : La comparaison f\_id not in history\_ids implémente strictement la logique de déduplication demandée. Elle assure que le coût de l'appel API n'est payé qu'une seule fois par fichier et par session.

## ---

**5\. Réponses Précises aux Questions Posées**

En synthèse des analyses architecturales et des preuves extraites du code, voici les réponses définitives aux questions de l'utilisateur.

### **Q1 : Différence exacte entre la liste globale body\['files'\] et body\['messages'\]\[-1\]\['files'\]?**

La différence est **sémantique et fonctionnelle**.

* **body\['files'\]** est l'accumulateur de contexte pour le moteur RAG. Elle contient l'union de tous les fichiers jugés pertinents pour la conversation en cours par le système. Elle tend à croître de manière monotone au fil de la discussion.  
* **body\['messages'\]\[-1\]\['files'\]** est le conteneur d'attachement pour l'action utilisateur courante. Elle ne contient que les fichiers que l'utilisateur a physiquement liés à sa dernière intervention.  
* **Implication :** Pour déclencher une action sur un "nouveau" fichier, il faut impérativement lire body\['messages'\]\[-1\]\['files'\]. Lire body\['files'\] conduirait à re-traiter tout l'historique à chaque message.

### **Q2 : Est-il garanti que body\['messages'\]\[-1\]\['files'\] ne contient QUE les fichiers nouvellement attachés?**

**Oui, structurellement.** Dans le cycle de vie de la requête frontend (Svelte) vers backend (FastAPI), l'objet message est construit à la volée lors de l'envoi. La liste files de cet objet est peuplée uniquement avec les éléments présents dans la zone d'upload au moment du clic sur "Envoyer". Elle n'hérite pas automatiquement des fichiers des messages précédents.

*Nuance :* Si l'utilisateur décide manuellement de ré-attacher un ancien fichier via l'interface, celui-ci apparaîtra dans cette liste. C'est pourquoi la vérification par ID (Set Difference) reste une couche de sécurité supplémentaire recommandée, bien que messages\[-1\]\['files'\] soit déjà un filtre très puissant par nature.

### **Q3 : Si je vide body\['files'\] dans le filtre, cela affecte-t-il l'historique ou seulement le traitement actuel?**

Cela affecte **uniquement le traitement pour le tour actuel**.

* **Portée :** La modification de la variable body est locale à la requête en cours de traitement dans le middleware.  
* **RAG :** Vider cette liste empêchera le moteur RAG natif d'Open-WebUI de trouver du contexte pour *cette* réponse spécifique.  
* **Persistance :** Cela n'efface **pas** les fichiers de la base de données, ni de l'historique visuel de la conversation. Au prochain message, si le frontend renvoie la liste complète (comportement par défaut), le RAG fonctionnera de nouveau.  
* **Recommandation :** Ne videz body\['files'\] que si votre API "Smart Context" remplace totalement le besoin du RAG natif pour ce tour de parole. Sinon, laissez-la intacte.

### **Q4 : Existe-t-il une métadonnée fiable pour distinguer un fichier 'nouveau'?**

Il n'y a **pas de flag booléen** explicite type is\_new: true dans le schéma standard.

* **Métadonnée Fiable :** La présence de l'objet fichier dans la liste du dernier message (messages\[-1\]) est l'indicateur le plus fort de nouveauté contextuelle.  
* **Identifiant :** Utilisez toujours le champ **id** (UUID). N'utilisez jamais itemId (trop volatile) ni \_\_files\_\_ (trop global dans les Pipes).  
* **Méthode de Distinction :** La fiabilité absolue s'obtient par la comparaison des ensembles d'IDs : Nouveaux \= IDs(DernierMessage) \- IDs(Historique).

## **Conclusion**

L'architecture d'Open-WebUI, bien que complexe dans sa gestion des états entre le frontend et le backend, offre via les filtres inlet le niveau de contrôle nécessaire pour votre implémentation. Le problème de re-traitement que vous rencontrez provient d'une lecture de la liste globale de contexte (body\['files'\]) plutôt que de la liste d'intention utilisateur (body\['messages'\]\[-1\]\['files'\]).

En ciblant spécifiquement la structure du dernier message et en implémentant une logique de déduplication basée sur les UUIDs persistants, vous pouvez garantir que votre API Smart Context n'est sollicitée qu'à bon escient. Cette approche respecte l'architecture modulaire de la version v0.8.x, assure la compatibilité avec les mécanismes de persistance du système, et optimise les coûts et la latence de votre solution d'intelligence artificielle.

#### **Sources des citations**

1. Open WebUI architecture doc made by Gemini 2.0 pro \#10044 \- GitHub, consulté le février 16, 2026, [https://github.com/open-webui/open-webui/discussions/10044](https://github.com/open-webui/open-webui/discussions/10044)  
2. After I attached the knowledge base to the model, the retrieval speed was very slow. There was only one file in the knowledge base and the file content was only one line. How should I optimize it? · open-webui open-webui · Discussion \#9967 \- GitHub, consulté le février 16, 2026, [https://github.com/open-webui/open-webui/discussions/9967](https://github.com/open-webui/open-webui/discussions/9967)  
3. Functions | Open WebUI, consulté le février 16, 2026, [https://docs.openwebui.com/features/plugin/functions/](https://docs.openwebui.com/features/plugin/functions/)  
4. \_\_files\_\_ in pipe function returns all files in conversation but I just ..., consulté le février 16, 2026, [https://github.com/open-webui/open-webui/discussions/15542](https://github.com/open-webui/open-webui/discussions/15542)  
5. Access uploaded files in pipelines · Issue \#164 · open-webui/pipelines \- GitHub, consulté le février 16, 2026, [https://github.com/open-webui/pipelines/issues/164](https://github.com/open-webui/pipelines/issues/164)  
6. Filter "body" should have access to "system message" \#6930 \- GitHub, consulté le février 16, 2026, [https://github.com/open-webui/open-webui/discussions/6930](https://github.com/open-webui/open-webui/discussions/6930)  
7. Releases · open-webui/open-webui \- GitHub, consulté le février 16, 2026, [https://github.com/open-webui/open-webui/releases](https://github.com/open-webui/open-webui/releases)  
8. Desktop Studio Developer Guide \- SAP Help Portal, consulté le février 16, 2026, [https://help.sap.com/doc/f56a0f850a174e9887016e9d8f00fc6d/Cloud/en-US/loioe9a88fe573c641a5b0938abfd5bcce99.pdf](https://help.sap.com/doc/f56a0f850a174e9887016e9d8f00fc6d/Cloud/en-US/loioe9a88fe573c641a5b0938abfd5bcce99.pdf)  
9. User groups with write access to Knowledge base unable to upload files \#8889 \- GitHub, consulté le février 16, 2026, [https://github.com/open-webui/open-webui/issues/8889](https://github.com/open-webui/open-webui/issues/8889)  
10. feat: uploading files without backend processing · Issue \#12228 · open-webui/open-webui, consulté le février 16, 2026, [https://github.com/open-webui/open-webui/issues/12228](https://github.com/open-webui/open-webui/issues/12228)  
11. RAG | Open WebUI, consulté le février 16, 2026, [https://docs.openwebui.com/troubleshooting/rag/](https://docs.openwebui.com/troubleshooting/rag/)  
12. \[Feature Request\] Access to Original File Content in Pipelines/Filters for File Processing · open-webui open-webui · Discussion \#16477 \- GitHub, consulté le février 16, 2026, [https://github.com/open-webui/open-webui/discussions/16477](https://github.com/open-webui/open-webui/discussions/16477)

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA8AAAAaCAYAAABozQZiAAABnElEQVR4AeySSytEYRjHD2JICgspJQo7CyklS0qKWNtQUnwJtyyUKCn5ALKyICnKtSgSO5HFbCzEaHKPBuP3P9M7nc6ZObOYzSxmen7nubzvf97nveRaafwyW1zEzkohAMbUcUAfU3D6ApI+2IUr2IMLGINyGIBet1h5DQObMAdr0ATN0ALvsAHjENJkvG1arYNoG6LQDcvwArJPPuvwBq8QNOI8klaYgQcYhmtw2yMFbeMSHzbiCpJBqIRZuINE9kMxBGfwLbFZtYvCARyDn50zeAgRiQsJ2kGneIr/gGT2xYAW0JaiRtxI8R5UVGuEqU3iYqaVwTOEQSeN81gOlWqoB+ksfSIkakeiP+Jkphemq2wwEyTWxatdPUN1oRXMuNPr8dRS0DXZi0isy9+iqNZ11/nEbiuhMAk7oLPBWXbbv0T7sAqj0A91UAVarQ2/AEtwAnHTykp08dMEmtSDnwD90Qi+EzR2hNe54GJmxMqe+CzCEEzBCsyD2g3iPeYUa1D/rCu7JbkBvWVti9BrbrF3hk8lK/Y5nERD/wAAAP//q72hWQAAAAZJREFUAwAyclI1ZQscjwAAAABJRU5ErkJggg==>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABIAAAAZCAYAAAA8CX6UAAABpUlEQVR4AeyTSyu1URiG93fu+75ElEOUYxFSyoABZkoYGBmZKX/BL/ALGJBDpEwMGcqAZOCUHBIDOZQBiiQhXFfar9dLGTBQ7J5r32s9a7/3Xu+znvU99k6fT2RUS8kGoQ+GoAe6oBqyoAPMDaP90A0t8Dtao02SvZAO9bAFI7AEJzAB59AKCTAKM3AdNTokuQj/QdNxdA58+AI15xrD2Bhfmhygd1EjcjFfoZjBDhxBOFKYlMAZLEMQLxmVs/oPVuEUwpHLJBM02UeDiBr9YKURbsBxEeoOpJRxDSTBFPiqyENEjZJJV4FG2WhDiGbGTfAHJuFJRI3yWc0Di9iJ2gYDqFj4S8Z7sAFPImrkbn7yiwXYBYsdx3wauRUwhzxG2Mi+qGTpFqbhDuJhvQqZZMAsPIuwkf9WwC88jW00HL+Y2BJX6Do8i7iR265g1aNdQ+1iJAj7x93asDZgsBAfaOTJHJPwbqWideCO2tAysDG9Kt5D5/PkvG/f0CA08v4kkvFYnbs7d+aJWdgc1mxQ6yR/mbdDuIYxHyT39vgyer2GH69G9wAAAP//SW9z0gAAAAZJREFUAwCdFU8zK5QBUAAAAABJRU5ErkJggg==>

[image3]: <data:image/png;base64,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>