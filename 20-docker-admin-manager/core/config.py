# -*- coding: utf-8 -*-
r"""
# ================================================================================
# MODULE : ECHO ADMIN MANAGER SERVER
# VERSION : 5.136 (Gouvernance SSoT webui.db Zero-Fault & Volumes echo-n8n-data / echo-mcp-cache)
# --- CHANGELOG 5.136 ---
# - Feature : Intégration complète des volumes `echo-n8n-data` (`/app/n8n-data`) et `echo-mcp-cache` (`/app/mcp_cache`) dans le cycle de Sauvegarde/Restauration `tar.xz`, le calcul de progression temps-réel, le `VACUUM` SQLite et la Garbage Collection.
# - Sécurité (Critique / Fail-Closed) : Verrouillage strict sur la SSoT `webui.db::user` (« Base injoignable = Zéro Action »). Vérification préalable `PRAGMA quick_check(1)`, présence de `admin@echo.local`, filtrage de `db_valid_chats` par `valid_ids`, verrou d'intégrité `n8n_whitelist_complete` (empêchant la suppression de démons N8N si un `session.db` est verrouillé), et transaction Fail-Closed dans `auth_delete_user`.
--- CHANGELOG 5.135 ---
- Feature : Suivi en temps réel (`MAINTENANCE_STATE` : `task_type`, `percent`, `status`) de toutes les phases de sauvegarde (`tar -cJf` via `psutil.io_counters()`, `rclone copy` via `/proc/<pid>/fdinfo`, `sftp.put` via callback natif Paramiko), d'élagage sémantique et de consolidation.
- Feature : Bandeau de progression Bootstrap animé (`#task-progress-card`) dans le Dashboard et enregistrement systématique d'un rapport final HTML (`save_maint_report`) lors des sauvegardes manuelles, consultable dans Système & Maintenance et dans l'onglet Sauvegardes.
--- CHANGELOG 5.134 ---
- Fix (UI/HTML5) : Désactivation dynamique (`disabled`) des champs dans les sections masquées (`#wiz_s3`, `#wiz_webdav`) via `toggleWizardFields()`, passage de `webdav_url` en `type="text" autocomplete="off"`, et échappement du tiret dans `pattern="[a-zA-Z0-9_\-]+"` (requis par le moteur RegExp Unicode `/v` de Chrome). Empêche le blocage silencieux du bouton "Générer le Profil" (`An invalid form control with name='webdav_url' is not focusable`).
--- CHANGELOG 5.133 ---
- Fix (Critique) : Remplacement de la machine à états interactive `rclone config create` par une écriture INI directe (`configparser`) dans `/backups/rclone/rclone.conf` (et `rclone obscure` pour WebDAV), éliminant le blocage ou la sortie sans écriture sur Google Drive (`--non-interactive`).
- Fix : Nettoyage automatique des sauts de ligne (`\r\n`) injectés par le copier-coller PowerShell au milieu du Token JSON OAuth Google Drive, extraction de l'objet `{...}` et validation JSON stricte.
--- CHANGELOG 5.132 ---
- Fix (Critique) : Correction du champ JSON Qdrant `/points/scroll` dans `memory_consolidation.py` (`"with_vector": True` au lieu de `"with_vectors": True`), rétablissant la récupération des vecteurs et la promotion des souvenirs Lvl1 -> Lvl2. Ajout de `"exact": True` sur `/points/count`.
- Fix (Critique) : Réactivation permanente de `auto_cleanup=True` dans `settings_io.py` et `routes.py` (écrasé à `False` lors de la sauvegarde du formulaire UI dépourvu de checkbox dédiée) + exécution immédiate de la rétention à l'enregistrement.
- Fix : Fiabilisation de l'externalisation SFTP (`_sftp_mkdir_p` récursif, fermeture garantie du `paramiko.Transport`) et Rclone (`--non-interactive`, création de `RCLONE_CONF_DIR`, chemin `deletefile` sans double `:` si sous-dossier vide, champs `drive_root_folder_id` et `s3_region`, sélection exacte du profil dans la modale et remontée d'`ext_error` dans le rapport de maintenance).
--- CHANGELOG 5.131 ---
- Refactor : Découpage du monolithe server.py en 4 packages (core, services, routes, templates) avec conservation de server.py comme point d'entrée léger.
- Fix : Singleton backup_scheduler dans core.config, préservation de MAINT_HISTORY_FILE et résolution explicite des imports inter-modules (0 warning Pyflakes/Flake8 F).
--- CHANGELOG 5.130 ---
- Fix (Critique) : Suppression des `import shutil` locaux dans la GC des Drop Zones de `_run_semantic_pruning`. Ces imports rendaient `shutil` local à toute la fonction : en l'absence d'orphelin dans `/app/downloads`, la purge FS levait `UnboundLocalError`, interrompant la purge des chats/fichiers, la synchronisation Qdrant (orphelins, TTL, consolidation) et la synchronisation BDD.
- Observabilité : L'exception du bloc DB/Espace Personnel est journalisée avec sa trace complète et remontée dans le rapport d'élagage (fin de l'échec silencieux).
--- CHANGELOG 5.129 ---
- Fix (Critique) : Propagation des exceptions de la tâche de sauvegarde (tar) vers le séquenceur pour lever les faux positifs (rapport vert malgré échec du disque).
- Fix (Critique) : Propagation du TimeoutError (60 min) lors du redémarrage Qdrant/OWUI pour afficher "ÉCHEC" dans le rapport de purge.
- UX : Refonte du workflow UI pour la maintenance. Le bouton "Lancer le Cycle" valide et sauvegarde d'abord la configuration (évitant le piège des cases cochées non sauvegardées) et s'exécute instantanément (suppression du sleep initial).
--- CHANGELOG 5.128 ---
- Fix (Critique) : Résolution du plantage silencieux de la maintenance dû à l'évaluation de {{}} en tant que set de dictionnaire (TypeError).
--- CHANGELOG 5.127 ---
- Refactor : Le séquenceur nocturne génère désormais un rapport HTML esthétique formaté en grille Bootstrap, affiché nativement dans l'UI des logs, remplaçant l'arborescence texte. La fonction de purge renvoie un dictionnaire structuré au lieu d'une chaîne, annulant tout recours aux regex et limitant la redondance d'écriture.
--- CHANGELOG 5.126 ---
- Feature : Refonte du séquenceur de maintenance. Fusion des modules (apscheduler) en une chaîne maître fiable gérant la sauvegarde, le redémarrage de Qdrant, et la purge avec consolidation.
--- CHANGELOG 5.125 ---
- Feature : Refonte détaillée des logs de purge temporelle (stats catégorisées, espace disque libéré) et ajout de l'audit de consolidation Lvl1->Lvl2 dans l'historique UI.
--- CHANGELOG 5.124 ---
- Feature : Ajout des statistiques détaillées (CPU, RAM, Taille, Démarrage) et du tri dynamique des conteneurs Docker dans le Dashboard.
--- CHANGELOG 5.123 ---
- Feature : Ajout de la Phase 4 (Synchronisation DB). Expurgation atomique de la table `file` d'Open-WebUI pour éliminer les fichiers fantômes supprimés par le GC FS.
--- CHANGELOG 5.122 ---
- Feature : Remplacement de l'élagage des fichiers orphelins (supprimé en 5.105) par un Garbage Collector FS (RefCounting). Purge asynchrone des fichiers du Vault Global et des uploads résiduels via inspection des liens symboliques.
--- CHANGELOG 5.121 ---
- Fix (Critique) : Assouplissement de la sonde Qdrant dans run_semantic_pruning pour interroger `/collections` de manière globale. Cela empêche l'inhibition silencieuse de la purge vectorielle si la collection META_ARTIFACTS n'est pas encore créée (ex: lors de l'usage exclusif du RAG conversationnel).
--- CHANGELOG 5.120 ---
--- CHANGELOG 5.119 ---
- Fix : Réactivation du N8N Safeguard (Whitelist GC) désactivé en v5.118 suite
  au changement N8N 2.34.6. L'API Worker /prune est opérationnelle depuis
  n8n_api.py v2.16 (timeout=30s, authentification API Key validée).
- Fix : Timeout aligné de 120s à 60s (worker interne = 30s).
- Fix : Le Safeguard N8N est désormais non-bloquant. Un échec de communication
  N8N ne paralyse plus le pruning global (FS + Qdrant).
--- CHANGELOG 5.118 ---
- Fix (Contournement) : Désactivation temporaire du N8N Safeguard lors de la purge sémantique suite à la mise à jour N8N 2.34.6 (Erreur Timeout API Worker).
--- CHANGELOG 5.117 ---
- Fix (Critique) : Résolution du "UnboundLocalError" SQLite dans la sonde db_ok de run_semantic_pruning.
--- CHANGELOG 5.116 ---
- Fix : Modification de get_dir_stats pour utiliser os.lstat() sur les liens symboliques (afin de ne pas surfacturer artificiellement la taille du Transit/Uploads causée par ECHO).
--- CHANGELOG 5.115 ---
- Fix (Critique) : Modification de la sonde Qdrant dans run_semantic_pruning pour interroger /collections au lieu d'une collection spécifique, empêchant l'erreur HTTP 404 de paralyser la purge lorsque la base vectorielle est vide.
--- CHANGELOG 5.114 ---
- Fix (Critique) : Implémentation du N8N Safeguard et d'une logique Garbage Collection (Whitelist) pour effacer les staticData orphelines dans la base interne N8N avant la destruction des dossiers de chats.
--- CHANGELOG 5.113 ---
- UI/UX : Correction de l'affichage désynchronisé des noms entre l'onglet SSO et l'onglet OWUI en forçant la lecture dynamique depuis webui.db pour le rendu visuel.
- Fix : Le bouton "Importer DB" met désormais à jour de force les noms dans le SSO (auth.db) pour les utilisateurs existants si leur nom a été modifié manuellement dans Open WebUI.
--- CHANGELOG 5.112 ---
- UI/UX : Remplacement de la durée par défaut (Infinie) des sessions SSO par 3 mois (2160 heures) pour les nouvelles installations.
- Securité : Ajout du bouton "Purge Globale" dans le panneau Admin pour déconnecter de force tous les utilisateurs.
--- CHANGELOG 5.111 ---
- Fix : Synchronisation forte et synchrone du nom utilisateur depuis l'Admin Manager vers la base de données d'Open WebUI (webui.db) pour les comptes existants.
--- CHANGELOG 5.110 ---
- Fix (Critique) : Inversion de la logique du wait-for-it (Sleep-First) dans l'élagage sémantique pour désamorcer définitivement la race condition de 03:00 avec la tâche de sauvegarde.
--- CHANGELOG 5.109 ---
- UI/UX : Regroupement des IPs d'une même session SSO au sein d'une seule ligne dans l'Admin Manager pour éviter les doublons lors des changements de réseau, avec support multi-GeoIP.
--- CHANGELOG 5.108 ---
- Fix (Critique) : Ajout de la validation explicite du endpoint /health d'Open WebUI dans la boucle wait-for-it de run_semantic_pruning pour empêcher l'erreur [Errno 111] lors du redémarrage post-sauvegarde.
--- CHANGELOG 5.107 ---
- Mod: Achèvement de la gestion des noms depuis Admin Manager et Auth Manager.
--- CHANGELOG 5.106 ---
- Mod: Intégration de la propagation du champ Name (ECHO Auth) depuis le SSO vers OWUI.
--- CHANGELOG 5.105 ---
- Architecture : Ablation totale de la purge temporelle des fichiers et du RefCounting (A.bis) devenu dangereux avec MarkItDown (fichiers convertis .md vus comme orphelins). Le nettoyage est désormais exclusivement assuré par la purge stricte des chats/utilisateurs (avec synchronisation instantanée Qdrant).
--- CHANGELOG 5.104 ---
- Correctif : Propagation du artifact_name lors du clustering (Atrophie) pour maintenir la synchronisation avec les filtres RAG.
--- CHANGELOG 5.103 ---
- Architecture : Mise à jour des noms des collections Qdrant (echo_meta_artifacts, echo_session_rag) pour la purge temporelle et orpheline.
--- CHANGELOG 5.102 ---
- Fix (Critique) : Ajout de la purge du Vault (ECHO_USERS_ROOT) dans `run_semantic_pruning` (le paramètre vault_days était ignoré).
- Fix (Critique) : Ajout d'une protection dans `maint_loop` (réinitialisation du scheduler) pour éviter une boucle de plantage silencieuse en cas d'exception non interceptée.
--- CHANGELOG 5.101 ---
- UX : Persistance dynamique de la navigation (Sidebar et Sous-onglets) lors des rechargements de page (actions POST) via `localStorage`.
--- CHANGELOG 5.100 ---
- Sécurité : Ajout du support natif du SSO (`X-Webui-User`) dans l'API Safeguard. Contourne l'erreur HTTP 401 si le backend Open WebUI rejette les requêtes JWT pures en mode SSO.
--- CHANGELOG 5.99 ---
- Fix : L'élagage (pruning) échouait silencieusement avec un code HTTP 307 dû à la présence d'un trailing slash sur l'API `/api/v1/users/`.
- Sécurité (Critique) : La purge des chats orphelins (Qdrant) utilisait l'arborescence physique, supprimant par erreur la mémoire des chats sans fichiers joints. Elle interroge désormais directement la base SQLite `db_valid_chats`.
--- CHANGELOG 5.98 ---
- Sécurité : Ajout de garde-fous stricts annulant le pruning si la base SQLite d'Open WebUI est inaccessible, corrompue ou vide.
- Fix : Restauration de la logique de purge par répertoire (chats structurés par chat_id sous forme de sous-dossiers).
- Fix : Résolution du blocage API Safeguard en obtenant un vrai jeton JWT via signin (le fichier secret contient le mot de passe en clair, pas un token Bearer).
--- CHANGELOG 5.97 ---
- Sécurité : Purge utilisateur totale et dynamique par introspection SQLite (supprime tous les orphelins dans OWUI).
--- CHANGELOG 5.96 ---
- Feature : Amélioration de la constante "Sessions Actives". Affiche désormais le ratio "OWUI DB / Fichiers Physiques".
--- CHANGELOG 5.95 ---
- UI : Refonte structurelle complète. Abandon du système Gridstack au profit d'une navigation Sidebar asynchrone pour catégoriser les fonctions (Identités, Système, Sauvegardes, Sécurité, Docker).
- UI : Simplification du code CSS et nettoyage des imports de librairies non utilisées.
--- CHANGELOG 5.94 ---
- UI : Fusion des tuiles "ECHO Auth" et "Sessions par Utilisateur" dans une méga-tuile centralisée.
- UI : Symétrisation du layout Gridstack (Bunkerweb, Backups, Maintenance) sur la ligne 6.
--- CHANGELOG 5.93 ---
- Feature : Surveillance en temps réel des sessions SSO actives avec géolocalisation JS.
- Feature : Capacité de révocation ciblée par session (Kill-Switch).
- UI : Agrandissement de la tuile ECHO Auth avec onglets (Comptes / Sessions Actives).
--- CHANGELOG 5.92 ---
- Feature : Enregistrement de la date du dernier enrôlement MFA (`last_enrollment`) dans la DB.
- UI : Affichage dynamique des dates de validité (Exp.) pour les enrôlements en attente.
- UI : Affichage de la date d'enrôlement (Enrôlé le) pour les comptes actifs.
--- CHANGELOG 5.91 ---
- Feature : Route et bouton 'Désactiver' pour couper l'accès WAN d'un compte (suppression SSO) sans affecter Open WebUI.
- Sécurité : Génération d'un vrai mot de passe complexe lors de l'import Admin au lieu d'une phrase en dur.
- UX : Ajout du délai d'expiration (en heures) directement dans le message de succès des mots de passe temporaires.
- WAF : Compatibilité des entêtes SSO (BunkerWeb auth_request_set) alignée pour l'Admin Manager.
--- CHANGELOG 5.90 ---
- Sécurité : Création d'une constante `SYSTEM_ADMIN_EMAIL` pour protéger de façon granulaire le compte système `admin@echo.local`.
- Feature : Bouton exclusif "Import Admin" pour importer spécifiquement l'administrateur système de OWUI vers l'IdP.
- Sécurité : Interdiction définitive de la suppression du compte `admin@echo.local` depuis le manager.
--- CHANGELOG 5.89 ---
- Sécurité : Paramétrage dynamique de la complexité des mots de passe SSO via la modale de sécurité Auth.
- Feature : Ajout d'une date d'expiration des mots de passe temporaires.
--- CHANGELOG 5.88 ---
- Feature : Bouton de bascule du rôle administrateur dans la vue des sessions utilisateur.
- Sécurité : Interdiction de supprimer ou rétrograder le dernier compte administrateur humain non-système.
--- CHANGELOG 5.87 ---
- Sécurité : Filtrage des comptes système (*@echo.local) lors de la création et de l'importation SSO.
--- CHANGELOG 5.86 ---
- Sécurité : Ajout de la dépendance passlib[argon2] manquante pour la création d'utilisateurs.
- Feature : Ajout du bouton d'importation des utilisateurs depuis la base de données Open WebUI.
--- CHANGELOG 5.85 ---
- Sécurité : Ajout des vérifications de session manquantes sur les routes API et de configuration.
--- CHANGELOG 5.84 ---
- Sécurité : Suppression de l'auto-login dangereux sur le LAN. Restauration de la mire SSH pour les accès directs sans WAF.
--- CHANGELOG 5.83 ---
- Sécurité : Application du Zero Trust. Disparition du fallback de login ; requiert un WAF pour l'accès.
- Fix UI : Correction du GridStack (tuile Containers repassée à 12 colonnes et LAYOUT incrémenté).
--- CHANGELOG 5.82 ---
- Fix : Incrémentation LAYOUT_VERSION (3→4) pour forcer le recalcul du layout Gridstack après ajout de la tuile ECHO Auth.
- Sécurité : Intégration complète du module ECHO Auth (SSO, MFA, Gestion des utilisateurs) et backup de la base dédiée.
--- CHANGELOG 5.79 ---
- Sécurité : Tri par date décroissante et enrichissement des IPs bannies avec données AS/WHOIS via ip-api (batch).
--- CHANGELOG 5.78 ---
- UI : Fusion de l'onglet Bans Actifs dans l'onglet de Bannissement et amélioration de la lisibilité des IP (rouge/grand).
--- CHANGELOG 5.77 ---
- Sécurité : Parsage intelligent de la sortie CLI de BunkerWeb pour les bans actifs.
--- CHANGELOG 5.76 ---
- UI : Ascenseur ajouté pour la tuile des conteneurs Docker.
- SQLite : Compteur de sessions par utilisateur corrigé.
- Sécurité : Ajout de la liste des bans actifs dans la modale BunkerWeb.
--- CHANGELOG 5.75 ---
- Sécurité : Intégration de BunkerWeb (Gestion des IPs, extraction logs d'attaque) via une modale dédiée.
--- CHANGELOG 5.66 ---
- Correctif critique : load_maint_config() utilisait une copie superficielle (copy()). Les dicts imbriqués
  (retention, memory_ttl, consolidation) étaient des références partagées vers DEFAULT_MAINT_CONFIG,
  ce qui mutait silencieusement les défauts en mémoire à chaque sauvegarde. Remplacé par copy.deepcopy().
- Correctif : save_maint_config() swallowait toute exception sans log. Corrigé pour logger l'erreur
  et retourner un booléen de succès, permettant au flash de refléter l'état réel de l'opération.
- Correctif UI : Les checkboxes 'purge orphelins' n'avaient pas de liaison id/for entre input et label.
  Le clic sur le texte ne toggleait pas le switch. De plus, le data-bs-toggle="tooltip" sur le label
  interceptait le clic. Tooltip déplacé sur l'input, liaison id/for ajoutée.
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-06-03

--- DESCRIPTION ARCHITECTURALE ---
Ce micro-service assure la régulation et le monitoring du framework ECHO.
Architecture ECHO-Native avec distinction entre stockage et sessions.

--- CHANGELOG 5.64 ---
- Correctif (Critique) : Implémentation d'un mécanisme asynchrone (Wait-for-it) de 60 minutes au démarrage de la Purge Temporelle (run_semantic_pruning). Résout la Race Condition avec la tâche de sauvegarde (03:00) qui rendait l'API Open WebUI et Qdrant temporairement indisponibles et annulait la purge silencieusement.
--- CHANGELOG 5.63 ---
- Nettoyage : Suppression du provider SFTP du wizard Rclone (doublon avec le SFTP natif paramiko).
--- CHANGELOG 5.62 ---
- UI : Refonte complète du style des tuiles CPU et RAM en Flexbox haut de gamme pour corriger le bug d'alignement du symbole %.
--- CHANGELOG 5.61 ---
- Correctif : Remplacement d'orjson par json standard pour restaurer la sauvegarde des configurations.
- UI : Ajout de la fonction JS manquante toggleWizardFields pour l'assistant Rclone.
--- CHANGELOG 5.60 ---
- Qualité : Audit complet du code. Suppression du code mort, correction des imports,
  remplacement des bare except:, suppression de shell=True, whitelist providers Rclone.
--- CHANGELOG 5.59 ---
- Backups : Externalisation native (SFTP et Rclone/Google Drive) avec politiques de rétention séparées pour le Cloud.
- UI : Fenêtre modale de configuration des exports avec champs dynamiques.
--- CHANGELOG 5.58 ---
- Sécurité : Exclusion stricte du sous-dossier `codex` de la Purge Temporelle du Vault.
--- CHANGELOG 5.57 ---
- UX/UI : Refonte du Dashboard en grille dynamique (Gridstack.js). Tuiles redimensionnables, déplaçables et auto-ajustables.
- Persistance : Sauvegarde de la disposition locale dans le localStorage du navigateur client avec possibilité de réinitialisation.
--- CHANGELOG 5.56 ---
- Sauvegarde : Inclusion de la base vectorielle Qdrant.
- Optimisation : Passage à la compression XZ (LZMA) pour des archives nettement plus petites.
- Rétrocompatibilité : Les anciennes archives tar.gz restent supportées à la restauration.
--- CHANGELOG 5.55 ---
- UX : Ajout de labels sémantiques explicites (Trivial, Mineur, Utile, Majeur, Axiome) au-dessus des champs de rétention TTL pour une meilleure lisibilité administrative.
--- CHANGELOG 5.51 ---
- UX : Ajout de champs de configuration dans le Dashboard pour personnaliser les durées de rétention (Purge Temporelle des Souvenirs) des souvenirs de la base vectorielle (Niveaux 1 à 5).
--- CHANGELOG 5.50 ---
- Optimisation : Centralisation du processus de Purge Temporelle des Souvenirs de la base vectorielle dans l'Admin Manager pour alléger le traitement temps-réel du filtre conversationnel.
--- CHANGELOG 5.41 ---
- Correctif : Restauration des constantes QDRANT_URL et COLLECTION_META_ARTIFACTS.
- UX : Ajout du label "Logs" sur le bouton d'historique de maintenance.
--- CHANGELOG 5.40 ---
- Ajout : Synchronisation automatique de la base vectorielle des souvenirs (Qdrant) pour éliminer les souvenirs orphelins (utilisateurs ou chats supprimés).
- Ajout : Historique d'audit persistant (1 an de rétention) affichable directement depuis l'interface UI.
--- CHANGELOG 5.30 ---
- Correction : Fallback robuste pour la copie du mot de passe Admin (support HTTP/Non-Secure).
- Ajout : Route API /api/backups pour le rafraîchissement dynamique.
- Ajout : Route /download/<filename> pour la récupération des sauvegardes.
- Sécurité : Vérification d'existence du fichier avant restauration destructive.
- Harmonisation : Usage strict de OWUI_ADMIN_SECRET_PATH.
--- CHANGELOG 5.25 ---
- Correction : Suppression définitive du décorateur @app.after_request orphelin (ligne 78).
--- CHANGELOG 5.24 ---
- Vérité Sémantique : Le compteur "Sessions Actives" ne cible plus que les .db de chats.
- Correction du décalage : Exclusion de identity.db et des fichiers docs du compteur de sessions.
- Maintien du Volume Global : Le volume Espace Personnel reste inclusif (docs + bases).
- Parité UX intégrale maintenue (Horloge, Tooltips, Monitoring).
================================================================================
"""

import os
import secrets
from flask import Flask  # pyright: ignore[reportMissingImports]

# ==============================================================================
# SECTION 1 : GESTION DES DÉPENDANCES
# ==============================================================================
try:
    import docker
    import paramiko
    DOCKER_AVAILABLE = True
except ImportError:
    docker = None
    paramiko = None
    DOCKER_AVAILABLE = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    httpx = None
    HAS_HTTPX = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    BackgroundScheduler = None
    HAS_SCHEDULER = False

# ==============================================================================
# SECTION 2 : CONFIGURATION & CHEMINS
# ==============================================================================
app = Flask(__name__, static_folder='/app/static')
app.secret_key = secrets.token_hex(32)
app.config['JSON_AS_ASCII'] = False

backup_scheduler = BackgroundScheduler() if HAS_SCHEDULER else None

# Traqueur d'État Global pour les tâches asynchrones (Sauvegarde, Pruning, Consolidation)
MAINTENANCE_STATE = {
    "is_running": False,
    "task_type": "",
    "status": "",
    "percent": 0,
    "start_time": None
}
SYSTEM_ADMIN_EMAIL = "admin@echo.local"
TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'echo-open-webui')
BACKUP_DIR = "/backups"
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")

OWUI_DATA_ROOT = "/app/backend/data"
ECHO_USERS_ROOT = os.path.join(OWUI_DATA_ROOT, "users") 
UPLOADS_DIR = os.path.join(OWUI_DATA_ROOT, "uploads")
WEBUI_DB_PATH = os.path.join(OWUI_DATA_ROOT, "webui.db")
RCLONE_CONF_DIR = os.path.join(OWUI_DATA_ROOT, "rclone")

RCLONE_CONF_PATH = os.path.join(RCLONE_CONF_DIR, "rclone.conf")
OWUI_ADMIN_SECRET_PATH = "/app/secrets/.owui-admin-secret"
DOWNLOADS_DIR = "/app/downloads"
N8N_DATA_DIR = "/app/n8n-data"
N8N_DB_PATH = os.path.join(N8N_DATA_DIR, "database.sqlite")
MCP_CACHE_DIR = "/app/mcp_cache"

WEBUI_URL = "http://echo-open-webui:8080"
QDRANT_URL = "http://echo-qdrant:6333"
N8N_WORKER_URL = "http://echo-n8n-worker:5003"
COLLECTION_META_ARTIFACTS = "echo_meta_artifacts"
COLLECTION_SESSION_RAG = "echo_session_rag"

DIRS = {
    "uploads": UPLOADS_DIR,
    "echo_vault": ECHO_USERS_ROOT,
    "debug_logs": os.path.join(OWUI_DATA_ROOT, "debug_logs"),
    "rclone": RCLONE_CONF_DIR
}

DEFAULT_BACKUP_CONFIG = {
    "auto_backup": True, "auto_cleanup": True, "cleanup_mode": "count",
    "cleanup_value": 5, "backup_time": "03:00", "interval_days": 1,
    "ext_mode": "local", "ext_cleanup_mode": "count", "ext_cleanup_value": 5,
    "sftp_host": "", "sftp_port": "22", "sftp_user": "", "sftp_pass": "", "sftp_path": "/backups/echo",
    "rclone_remote": ""
}

DEFAULT_MAINT_CONFIG = {
    "cleanup_hour": "03:00", "last_run": "Never",
    "retention": {},
    "memory_ttl": { "lvl1": 30, "lvl2": 60, "lvl3": 180, "lvl4": 365, "lvl5": 540 },
    "consolidation": {
        "enabled": True,
        "trigger_threshold": 10,   # Nb de points lvl1 par user déclenchant la consolidation
        "min_cluster_size": 3,     # Nb minimum de points similaires pour fusionner
        "similarity_threshold": 0.75  # Score cosinus minimal pour appartenir à un cluster
    },
    "purge_orphaned_chats": True,
    "purge_orphaned_users": True
}

AUTH_SETTINGS_PATH = "/app/auth-data/auth-settings.json"
DEFAULT_AUTH_SETTINGS = {
    "pwd_min_length": 12,
    "pwd_require_upper": True,
    "pwd_require_lower": True,
    "pwd_require_digit": True,
    "pwd_require_special": True,
    "temp_pass_hours": 24,
    "totp_tolerance": 1,
    "session_timeout": 2160
}

MAINT_HISTORY_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_history.json")
