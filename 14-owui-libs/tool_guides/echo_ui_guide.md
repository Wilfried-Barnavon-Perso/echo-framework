---
domain_id: echo_ui_guide
version: 6.13.2.1
updated_at: 2026-11-20
name: Guide de l'Interface Utilisateur ECHO-UI (Navigation, Onglets, Commandes & Réglages)
description: Cartographie opérationnelle des écrans, panneaux latéraux, onglets de session, commandes slash, mode vocal, import de fichiers et réglages manipulables directement par L'Utilisateur dans ECHO-UI.
tools: []
keywords: [ui, interface, echo-ui, ecran, onglet, panneau, sidebar, barre_laterale, workspace, espace_de_travail, cognition, codex, navigateur, browser, coffre, vault, applications, canvas, terminal, commande, slash, raccourci, reglages, settings, valves, jauge, contexte, quotas, voix, vocal, audio, import, upload, fichier, clip, mp4, dvr, epingle, automatisations, calendrier, memoire, axiomes, competences, skills, prompts, dupliquer, poursuivre, nouvelle_conversation, aide_utilisateur]
---

# MANUEL OPÉRATIONNEL : INTERFACE UTILISATEUR ECHO-UI (`echo_ui_guide`)

## 1. RÈGLE D'EXPLOITATION POUR LE MODÈLE
- Lorsque L'Utilisateur demande comment accéder à une fonctionnalité, configurer un secret, consulter un fichier, piloter un Sous-Agent ou utiliser une commande dans ECHO-UI, Le Modèle **DOIT** fournir le chemin exact dans l'interface à l'aide des intitulés affichés.
- Il est **ABSOLUMENT INTERDIT** d'exposer des noms de briques logicielles, de fichiers sources, de routes techniques ou d'identifiants internes.

---

## 2. BARRE LATÉRALE GAUCHE & ESPACES PRINCIPAUX
- **Historique des conversations** : Recherche dans l'historique, classement par dossiers et étiquettes. Le menu contextuel de chaque conversation (`...`) expose **Planifier une automatisation** (`Schedule automation`) ainsi que la duplication de conversation.
- **Sections de navigation en tête de barre latérale** :
  1. **Espace de travail** (`Workspace`) : Gestion des compétences, de la mémoire long-terme et des modèles de prompts.
  2. **Automatisations** (`Automations`) : Gestion des tâches planifiées récurrentes.
  3. **Calendrier** (`Calendar`) : Agenda intégré de L'Utilisateur.

---

## 3. ESPACE DE TRAVAIL (`Workspace`) — 3 ONGLETS
1. **Compétences** (`Skills`) : Consultation, création, édition et suppression des compétences métier utilisées par les Sous-Agents.
2. **Mémoire** (`Méta-Artéfacts`) : Recherche, consultation, filtre rapide **`Axiomes (Niveau 5)`** et suppression des faits mémorisés (`Profil d'Alignement` et `Hypothèses d'Apprentissage`).
3. **Prompts** : Bibliothèque personnelle de modèles de prompts réutilisables.

---

## 4. BARRE SUPÉRIEURE, MODÈLE, JAUGE DE CONTEXTE & ZONE DE SAISIE
- **Sélecteur de Modèle & Valves** : Modèle unique verrouillé sur **Écho** (infobulle affichant les 4 paliers cognitifs : `Zéphyr · Apollon · Prométhée · Athéna`). Le choix du mode de routage cognitif (`AUTO`, `AUTO_PROM`, `MANUEL`) s'effectue via le bouton des **Valves** (paramètres du modèle de la conversation).
- **Jauge de charge contextuelle** : Barre redimensionnable affichant l'occupation du contexte et les quotas Cloud (fenêtres de 5 heures et de semaine) ; un **double-clic** sur la jauge force le rafraîchissement des quotas et de l'état de session. Lorsque le contexte approche la saturation, L'Utilisateur **PEUT** déclencher l'action **Poursuivre dans une nouvelle conversation** pour migrer le résumé de session et ses ressources vers un nouveau fil.
- **Indicateur d'activité des Sous-Agents** (barre supérieure) : Badge affichant l'activité cognitive en cours et ouvrant directement l'onglet **Cognition**.
- **Menu `+` (Joindre des fichiers)** : Import direct de fichiers et médias jusqu'à **4 Go** avec barre de progression en pourcentage.
- **Badge d'inférence locale** dans la barre de saisie : Affiche l'état de la vectorisation locale sur le poste (WebGPU).
- **Commandes `/` dans la saisie** :
  - **Commandes Écho** : `/help` (alias `/aide`), `/status` (alias `/statut`), `/plan`, `/goal`, `/résumé` (alias `/resume`), `/rédacteur` (alias `/redacteur`), `/coach`, `/prof`, `/persona`.
  - **Raccourcis d'interface** : `/dupliquer`, `/reglages`.
  - **Contrainte d'usage** : Les déclencheurs `@`, `#` et `$` ne sont pas actifs dans la zone de saisie du chat principal.
- **Mode Vocal** : Dictée vocale (micro) et mode appel vocal bidirectionnel accessibles depuis la barre de saisie.
- **Blocs de code & Liens Codex dans les réponses** : Chaque bloc de code dispose d'un bouton **`Envoyer au Codex`** (enregistre le fichier dans l'espace `main` et ouvre l'onglet **Codex & Fichiers**). Un clic direct sur les liens `[libellé](codex:...)` ouvre le fichier ciblé dans cet onglet.

---

## 5. PANNEAU LATÉRAL DROIT DE LA CONVERSATION (5 ONGLETS + TERMINAL)
- **Cognition** : Suivi en temps réel des Sous-Agents, tables rondes (Conseils) et ouvriers supervisés ; onglets par membre, filtre par groupe (`Ce groupe` / `Tous`), bascule **`Live / Pause`** avec compteur de nouvelles étapes, contrôles **`Pause`**, **`Reprendre (+N tours)`**, **`Stop`**, **`Fermer`**, et zone d'envoi direct de consignes à un ou plusieurs Sous-Agents (ciblage `@nom` ou `📢 Tous`).
- **Codex & Fichiers** : Sélecteur de conversation (consultation en lecture seule des dépôts d'autres conversations), sélecteur d'espace (`main`, `plans`, `generated`, `sandbox`, `files`), arborescence avec actions **`Renommer`** et **`Supprimer`**, prévisualisation multi-format (Markdown, HTML, SVG, CSV/TSV, images, PDF, audio, vidéo, vue hexadécimale), éditeur texte avec sauvegarde **`Ctrl+S`**, édition assistée par IA (actions rapides, consigne libre et prévisualisation diff avant acceptation), historique/diff/restauration Git sur les espaces versionnés, et actions **`Importer`** (y compris glisser-déposer), **`Exporter`**, **`Copier`**, **`Imprimer`**.
- **Navigateur Web** : Suivi en temps réel (`🔴 EN DIRECT`) avec affichage du curseur d'action, navigation temporelle DVR (`⏸️ DIFFÉRÉ`) avec bouton **`🔴 Retour au temps réel`**, lecture et téléchargement des clips vidéo d'action (**`🎬 Voir l'action`**, **`🖼️ Image HD`**, **`⬇️ MP4`**), bouton d'épinglage **`📌 Conserver`** / **`📌 Conservée`** (protège la capture de la purge automatique de fin de session), zoom/déplacement, recadrage par 8 poignées (**`✂️ Recadrer`**), copie (**`📋 Copier`**) et export **`PNG`** (**`💾 Exporter PNG`**).
- **Coffre (`Connexions & Secrets`)** : Consultation du statut des comptes/clés chiffrés et ajout, modification ou suppression sécurisée des identifiants (clés API, SSH, Google Workspace, Microsoft 365, serveurs MCP) avec directives de configuration dépliables par connecteur.
- **Applications** : Affichage et interaction avec les applications web générées depuis la Sandbox, pastille d'état (`Actif` / `Arrêté`) et fermeture par `×`.
- **Terminal (`+` → `Nouveau terminal`)** : Terminal shell interactif de la session, disponible dès que la conversation est enregistrée.

---

## 6. CARTES INTERACTIVES, AUTOMATISATIONS, CALENDRIER & RÉGLAGES
- **Cartes interactives & Modales** : Saisie libre, choix rapides, confirmation Oui/Non, questionnaires multi-étapes (avec compte à rebours) et modales de validation pour les actions sensibles (lancement de plan, modification du Coffre, suppression de compétence).
- **Automatisations & Calendrier** : Planification de tâches récurrentes (`new_chat`, `existing_chat`, `persistent_chat`), activation/pause et badge d'origine automatisée sur les messages déclenchés ; agenda intégré dans **Calendrier**.
- **Réglages (`Settings` ou `/reglages`)** : 9 onglets personnels (**Général**, **Interface**, **Notifications**, **Raccourcis**, **Audio**, **Contrôle des données**, **Conversations archivées**, **Compte**, **À propos**). Les paramètres système globaux sont accessibles aux administrateurs dans **Administration → Réglages**.
