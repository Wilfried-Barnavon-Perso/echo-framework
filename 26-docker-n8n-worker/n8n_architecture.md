# ECHO N8N : Guidelines Architecturales Strictes

Ce document définit les règles absolues d'interaction avec l'instance N8N d'ECHO. Il est la source de vérité pour la création et la modification de workflows.

## 1. Topologie des Exécutions

L'infrastructure N8N d'ECHO gère deux modes distincts : le mode Sandbox Éphémère (One-Shot) et le mode Démon (Background).

### 1.1 Mode Sandbox Éphémère (CLI One-Shot)
Utilisé pour tester un script, scraper une page, ou exécuter une tâche immédiate via la ligne de commande isolée.
- **Règle absolue d'entrée** : Le moteur CLI v1 refuse catégoriquement de démarrer si le graphe ne contient pas le noeud spécial `Execute Workflow Trigger` (Type interne: `n8n-nodes-base.executeWorkflowTrigger`). 
- TOUT workflow destiné à être testé en One-Shot DOIT commencer par ce noeud, sans exception.
- L'erreur `Missing node to start execution` indique spécifiquement l'absence de ce noeud.

### 1.2 Mode Démon (Déploiement Permanent)
Utilisé pour les workflows "Réactifs" (écoute de Webhooks, polling d'Emails, exécution planifiée via Cron/Schedule).
- **Règle** : Les workflows réactifs doivent posséder leur trigger réel (ex: `Schedule Trigger`, `Webhook`) et être déployés via l'API REST de déploiement d'ECHO. La Sandbox One-Shot ne PEUT PAS être utilisée en production pour ces flux.

## 2. Le Protocole de Mocking (Simulation de Données)

Le test des workflows réactifs en mode Sandbox requiert obligatoirement un Mocking.
- **Problématique** : Un `Email Read Trigger` ou un `Webhook` produit des données asynchrones. Si vous tentez de tester un tel flux en ligne de commande, le CLI va soit échouer, soit geler.
- **Solution (Pattern de Mocking)** :
  1. Commencez la construction du workflow de test avec un `Execute Workflow Trigger`.
  2. Connectez-y un noeud `Code` ou `Set` configuré pour **simuler** (mocker) la payload JSON attendue en production (ex: simuler un objet `{"body": "contenu email", "subject": "Test"}`).
  3. Câblez le reste du flux logique à ce noeud de simulation.
  4. Testez le workflow en Sandbox.
  5. Une fois le succès validé, supprimez l'Execute Trigger et le noeud de Mocking, placez le vrai Trigger, et demandez le déploiement.

## 3. Gestion des Identifiants (ECHO Vault)
- N'inventez jamais de credentials en dur (ex: tokens d'API, mots de passe) dans les noeuds.
- Utilisez exclusivement la macro `__ECHO_SECRET_NOM_CLE__` injectée dynamiquement depuis l'Identity Vault d'ECHO.

## 4. Écriture sur Disque & Limites de Volumétrie (`__ECHO_DOWNLOAD_DIR__`)
- **Sandbox Synchrone (`sync=True`)** : Plafond strict de sortie à **8 Ko**.
- **Exécution Asynchrone (`sync=False`)** : Plafond d'ingestion directe à **64 Ko**.
- **Règle d'Architecture Critique (`Write Binary File` / `Read/Write Files from Disk`)** :
  Le Modèle DOIT concevoir le graphe N8N pour qu'il filtre lui-même ses données (via des nœuds `Item Lists`, `Aggregate`, ou `Code`) AVANT la restitution finale. Si la charge utile finale dépasse 64 Ko ou si le workflow (Oneshot ou Démon) produit un fichier ou un rapport, il DOIT se conclure par un nœud `Write Binary File` (`n8n-nodes-base.writeBinaryFile`) ou `Read/Write Files from Disk` (`n8n-nodes-base.readWriteFile`) dont le paramètre `fileName` / `filePath` cible **STRICTEMENT** la macro :
  `__ECHO_DOWNLOAD_DIR__/nom_du_fichier.ext`
  Tout chemin relatif ou ne commençant pas par `__ECHO_DOWNLOAD_DIR__/` est rejeté par la validation pré-vol et provoque un échec `ENOENT` (`fs.realpath`) dans le worker N8N.

## 5. Documentation Officielle (Serveur MCP Kapa.ai)
- Si vous avez besoin de consulter la documentation officielle de N8N (pour comprendre le fonctionnement complexe d'un noeud ou chercher un exemple), vous pouvez vous connecter dynamiquement au serveur MCP officiel de N8N propulsé par Kapa.ai.
- **URL Endpoint** : `https://n8n.mcp.kapa.ai` (Transport HTTP).
- **Usage** : Vous pouvez forger un petit script Python temporaire dans votre Sandbox pour interroger ce serveur MCP si vous bloquez sur l'usage d'un noeud spécifique.

---
**Version Cible** : Compatible strictement avec N8N v1+ (CLI Restrictive).
