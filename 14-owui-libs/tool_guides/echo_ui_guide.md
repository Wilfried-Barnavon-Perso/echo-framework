---
domain_id: echo_ui_guide
version: 6.25.3.1
updated_at: 2026-11-28
name: Guide de l'Interface Utilisateur ECHO-UI (Navigation, Onglets, Commandes & Réglages)
description: Cartographie opérationnelle des écrans, panneaux latéraux, onglets de session, commandes slash, Vannes (routage cognitif AUTO/MANUAL et cascade), mode vocal, import de fichiers et réglages manipulables directement par L'Utilisateur dans ECHO-UI.
tools: []
keywords: [interface, interface_utilisateur, echo-ui, aide_utilisateur, barre_laterale, sidebar, espace_de_travail, onglet, panneau_administration, reglages, settings, vannes, valves, cognitive_mode, cognitive_model, raccourci, slash, split_view, mode_vocal, conversation_temporaire, poursuivre, nouvelle_conversation, authentification_llm, genre_grammatical, icone_echo, notifications]
---

# MANUEL OPÉRATIONNEL : INTERFACE UTILISATEUR ECHO-UI (`echo_ui_guide`)

## 1. RÈGLE D'EXPLOITATION POUR LE MODÈLE
- Lorsque L'Utilisateur demande comment accéder à une fonctionnalité, configurer un secret, consulter un fichier, piloter un Sous-Agent ou utiliser une commande dans ECHO-UI, Le Modèle **DOIT** fournir le chemin exact dans l'interface à l'aide des intitulés affichés.
- Il est **ABSOLUMENT INTERDIT** d'exposer des noms de briques logicielles, de fichiers sources, de routes techniques ou d'identifiants internes.

---

## 2. BARRE LATÉRALE GAUCHE & MENU UTILISATEUR
- **En-tête & Boîte de réception** : Bouton de **Notifications** (boîte de réception avec suppression unitaire ou globale) et bouton de repli/ouverture de la barre latérale.
- **Actions & Navigation épinglée** :
  - **Nouveau chat** et **Rechercher** dans l'historique.
  - Raccourcis épinglés en tête de barre latérale : seul **Espace de travail** est épinglé par défaut ; **Calendrier** et **Automatisations** peuvent être épinglés ou désépinglés en maintenant `Shift` sur leur entrée dans le menu utilisateur.
- **Dossiers & Historique des conversations** : Classement par dossiers et étiquettes. Le menu contextuel (`...`) de chaque conversation expose **Planifier une automatisation**, **Partager**, **Télécharger** (`.json`, `.txt`, `.pdf`), **Renommer**, **Marquer comme non lu**, **Épingler**, **Archiver** / **Désarchiver**, **Cloner** (`Clone`), **Poursuivre dans un nouveau chat** (migre un résumé distillé de la session, ses fichiers et sa mémoire vers un nouveau fil), **Déplacer** (vers un dossier) et **Supprimer**.
- **Menu utilisateur (bas de barre latérale)** : Accès profil (**Compte** et **`Mettre à jour votre statut`**), **Espace de travail**, **Calendrier**, **Automatisations**, **Clavier** (raccourcis), **Panneau d'administration** (administrateurs uniquement), **Réglages** et **Déconnexion**.

---

## 3. ESPACE DE TRAVAIL (`Espace de travail`) — 3 ONGLETS
1. **`Skills`** : Consultation, création, édition et suppression des compétences métier utilisées par les Sous-Agents.
2. **`Mémoire`** : Consultation, recherche textuelle ou par étiquette, filtres de catégorie (**`Tout`**, **`Mes Préférences`**, **`Apprentissages`**), filtre cumulable **`★ Règles absolues (Niveau 5)`**, création (**`Nouvelle entrée`**), édition, promotion en règle absolue (niveau 5) et suppression des souvenirs long-terme s'effectuant exclusivement dans cet onglet.
3. **`Prompts`** : Bibliothèque personnelle de modèles de prompts réutilisables (invocables ensuite via `/` dans la zone de saisie).

---

## 4. BARRE SUPÉRIEURE, ZONE DE SAISIE, MODÈLE & JAUGES
- **Barre supérieure** : Affiche le titre de la conversation, l'indicateur **`Sous-agents`** (ouvre directement l'onglet **Sous-Agents & Outils**), le minuteur d'attente actif (compte à rebours masquable par `×`), le basculeur **Conversation temporaire** (et bouton d'enregistrement lorsqu'elle est active), le menu d'actions (`...`) exposant notamment **`Aperçu`**, **`Commentaires`**, **`Copier`** et la gestion des **étiquettes**, ainsi que le bouton de panneau latéral droit (**`Tabs`** avec badge compteur des onglets secondaires ouverts).
- **Barre inférieure du compositeur de saisie** :
  - **Menu `+`** : **`Téléverser des fichiers`** (import direct de fichiers et médias jusqu'à **4 Go** avec progression en `%`), **`Prise de vue`** (capture d'écran sur ordinateur ou caméra sur mobile), **`Joindre des fichiers`** et **`Permissions des outils`** (lorsque ce contrôle est actif).
  - **Bouton `Vannes`** : Ouvre les paramètres utilisateur du modèle pour configurer le routage cognitif (`COGNITIVE_MODE` : **`AUTO`** dynamique avec cascade descendante automatique sur erreur technique API jusqu'à `MAX_CASCADE_ATTEMPTS`, ou **`MANUAL`** figé sans cascade sur le fil principal), le palier cible (`COGNITIVE_MODEL` : `MODEL_ZEPHYR`, `MODEL_APOLLO`, `MODEL_PROMETHEUS` ou `MODEL_ATHENA` — plafond en `AUTO`, forcé en `MANUAL` ; note : sur les Sous-Agents et outils cognitifs, la cascade descendante automatique jusqu'à `MODEL_ZEPHYR` reste toujours active en cas d'indisponibilité API ou de blocage par filtre d'inférence, même en `MANUAL`), l'autorisation des crédits Google One AI (`ENABLE_PAID_CREDITS`), `MAX_CASCADE_ATTEMPTS` et `ECHO_AUTO_CONTINUE_MAX`.
  - **Jauge de charge contextuelle** : Barre redimensionnable affichant le taux d'occupation du contexte (Cache / Prompt actif / Génération) et le différentiel de tour ; un clic ouvre le détail de la session, de l'identité Google et des quotas Cloud (fenêtres de 5 heures et hebdomadaire), et un **double-clic** force le rafraîchissement des quotas et métriques. Lorsque le contexte approche la saturation, L'Utilisateur **PEUT** utiliser **Poursuivre dans un nouveau chat** dans le menu `...` de la conversation.
  - **Badge `WebGPU`** : Affiche l'état de la vectorisation locale sur le poste (avec panneau de détail au clic).
  - **Pilule modèle `Écho`** : Verrouillée sur **Écho**, affiche dynamiquement à droite le palier cognitif actif (`Zéphyr`, `Apollon`, `Prométhée` ou `Athéna`), avec infobulle `Cortex Auto` ou `Cortex Manual` (`· <Palier actif>`).
- **Commandes `/` dans la saisie** :
  - **Commandes Écho** : `/help` (alias `/aide`), `/status` (alias `/statut`), `/plan`, `/goal`, `/résumé` (alias `/resume`), `/rédacteur` (alias `/redacteur`), `/coach`, `/prof`, `/persona`.
  - **Raccourcis d'interface & Prompts** : `/temporary` (activer/désactiver le mode temporaire sur une nouvelle conversation non enregistrée), `/comments` (ouvrir les commentaires sur une conversation enregistrée), `/fork` (alias `/dupliquer`), `/settings` (aliases `/reglages`, `/réglages`), ainsi que les **Prompts** personnels créés dans **Espace de travail → Prompts**.
  - **Contrainte d'usage** : `:nom` ouvre le sélecteur d'émojis, tandis que les déclencheurs `@`, `#` et `$` sont inactifs dans la zone de saisie du chat principal.
- **Mode Vocal** : Dictée vocale (micro) et mode appel vocal bidirectionnel accessibles depuis la barre de saisie.
- **Blocs de code, Liens Codex & Barre d'actions sous chaque message** :
  - Chaque bloc de code dispose d'un bouton **`Envoyer au Codex`** (enregistre le fichier dans l'espace `Main` et ouvre l'onglet **Codex & Fichiers**). Un clic direct sur un lien Codex ouvre le fichier ciblé dans cet onglet.
  - La barre d'actions sous chaque message inclut **`Print / PDF`** pour l'impression ou l'export PDF ciblé exclusivement sur le message sélectionné, ainsi que **Dupliquer la conversation** (fork de branche depuis ce message).

---

## 5. PANNEAU LATÉRAL DROIT DE LA CONVERSATION (5 ONGLETS + TERMINAL)
Lorsque le panneau est vide, une grille d'accès rapide affiche les vues disponibles. Le bouton **`+`** de la barre d'onglets permet d'ouvrir les 5 vues dédiées ou **`Nouveau terminal`**, et les onglets supportent le **Split View** (affichage côte à côte).
- **Sous-Agents & Outils** : Comporte 2 sous-vues **`Conversations`** et **`Appels d'outils`** (avec filtre `Ce groupe` / `Tous les sous-agents`). Suivi temps réel des Sous-Agents, tables rondes (Conseils) et Phalanges multi-niveaux (Stratège, Capitaine, Hoplite) ; filtre par participant, bascule **`🔴 Live`** / **`⏸ Pause`** avec compteur de nouvelles étapes, contrôles **`⏸ Pause`**, **`▶ Reprendre (+N)`**, **`⏹ Stop`**, **`✕ Fermer`** (bascule la session en archive lecture seule **`🗄️ Purgé (Lecture seule)`** conservée 24 h avec affichage de l'échéance d'effacement et bouton **`🗑️ Supprimer définitivement`**), et zone d'envoi direct de consignes à un ou plusieurs Sous-Agents (ciblage `@nom` ou `📢 Tous`).
- **Codex & Fichiers** : Sélecteur de conversation (consultation en lecture seule des dépôts d'autres conversations), sélecteur d'espace (**`Main [RW]`**, **`Sandbox [RW]`**, **`Plans [RO]`**, **`Generated [RO]`**, **`Fichiers [RO]`**) avec badges de droit **`LECTURE-ÉCRITURE`** / **`LECTURE SEULE`**, sous-vues **`Éditeur`** et **`Historique Git`**, explorateur arborescent repliable avec **`Nouveau fichier`**, **`Nouveau dossier`**, **`Renommer`** et **`Supprimer`**, éditeur de code avec sélecteur de langage, barre d'état `Ln/Col` et sauvegarde **`Ctrl+S`** / **`Enregistrer (Commit)`**, mode **`👁️ Aperçu`** multi-formats (Markdown, HTML, SVG, CSV/TSV, images, PDF, audio, vidéo, vue hexadécimale), édition assistée par IA (actions rapides, consigne libre, prévisualisation **`Diff IA`** / **`Texte complet`** avant acceptation), **`Historique Git`** / Diff / **`⏮️ Restaurer ce commit`** sur les espaces versionnés, et actions **`Importer`** (y compris glisser-déposer), **`Exporter`**, **`Copier`** et **`Imprimer`**.
- **Navigateur Web** : Suivi en temps réel (`🔴 EN DIRECT`) avec affichage du curseur d'action, navigation temporelle DVR (`⏸️ DIFFÉRÉ`) avec bouton **`🔴 Retour au temps réel`**, bascule **`🎬 Clips`**, lecture et téléchargement des clips vidéo d'action (**`🎬 Voir l'action`**, **`🖼️ Image HD`**, **`⬇️ MP4`**, lecture continue), bouton d'épinglage **`📌 Conserver`** / **`📌 Conservée`** (protège la capture et son clip de la purge automatique), zoom/déplacement, recadrage par 8 poignées (**`✂️ Recadrer`**), copie (**`📋 Copier`**) et export **`PNG`** (**`💾 Exporter PNG`**).
- **Coffre & Identité** : Consultation du statut/quotas actifs (bouton **`Actualiser`**) et ajout, modification ou suppression sécurisée des identifiants (clés API, SSH avec champs multi-lignes masquables pour clé privée et certificat OpenSSH `*-cert.pub`, Google Workspace, Microsoft 365, N8N, serveurs MCP) via **`+ Secret personnalisé`**, **`Configurer`** / **`+ Ajouter un compte`**, avec directives de configuration dépliables par connecteur.
- **Applications** : Affichage et interaction avec les vues HTML statiques et les applications web client-serveur temps réel isolées, avec pastille d'état (**`Actif`** / **`Arrêté`**), bouton **`Arrêter`** (pour stopper un serveur actif), bouton **Recharger** et fermeture par `×`.
- **Terminal (`+` → `Nouveau terminal local` / `Terminal SSH`)** : Permet d'ouvrir un **Terminal local** (shell local de la session, déverrouillé par défaut pour l'agent) ou un **Terminal SSH** distant (sélection d'un compte SSH du Coffre via `Terminal SSH · <compte>`, ou accès direct à la configuration SSH dans **Coffre & Identité** via **`Nouveau terminal SSH…`** / **`Configurer un hôte SSH…`**, verrouillé en lecture seule par défaut) dès que la conversation est enregistrée (plafond de **`2` terminaux locaux** et **`8` terminaux SSH** simultanés par conversation). La barre d'en-tête affiche le badge **`Local`** / **`SSH`**, le nom de la session (`Terminal` par défaut en mode local), son `#ID` court copiable au clic, et le bouton bascule **Verrou (`🔒 Lecture seule` / `🔓 Agent autorisé`)** permettant à L'Utilisateur d'autoriser ou de bloquer l'écriture et la fermeture par l'agent.

---

## 6. CARTES INTERACTIVES, AUTOMATISATIONS, CALENDRIER, RÉGLAGES & ADMINISTRATION
- **Cartes interactives & Modales** : Saisie libre, choix uniques ou multiples, confirmation Oui/Non, questionnaires multi-étapes (avec compte à rebours) et modales de validation avec compte à rebours pour les actions sensibles (lancement de plan, modification du Coffre, suppression de Skill).
- **Automatisations & Calendrier** : Planification de tâches récurrentes (nouvelle conversation à chaque exécution, conversation existante, ou conversation dédiée réutilisée), activation/pause et badge d'origine automatisée sur les messages déclenchés ; agenda intégré dans **Calendrier**.
- **Réglages personnels (`Réglages` ou `/settings` / `/reglages`)** : 10 onglets personnels (**`Général`**, **`Interface utilisateur`**, **`Notifications`**, **`Raccourcis`**, **`Audio`**, **`Contrôles des données`**, **`Conversations archivées`**, **`Compte`** — incluant le sélecteur **`Genre grammatical`** et le bouton **`Icône ECHO`** —, **`Authentification LLM`** — connexion Google OAuth2 via tunnel SSH éphémère, gestion des clés API Google AI Studio Primaire/Secondaire, quotas Cloud et réinitialisation —, **`À propos`**).
- **Panneau d'administration (administrateurs uniquement)** : Accessible via le menu utilisateur → **`Panneau d'administration`** : onglets **`Utilisateurs`** (lecture seule), **`Fonctions`** (lecture seule) et **`Réglages`** (**`Général`** et **`Interface utilisateur`** globaux).
