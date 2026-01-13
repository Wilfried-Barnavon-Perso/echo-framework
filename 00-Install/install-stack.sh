#!/bin/bash
# ==============================================================================
# SCRIPT : install-stack.sh
# VERSION : v5.3.2
# AUTEUR  : Wilfried BARNAVON
# DATE    : 2026-01-02
#
# ROLE : ORCHESTRATION DU DÉPLOIEMENT DES CONTENEURS DOCKER
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script est le point d'entrée principal pour lancer ou relancer la stack ECHO.
# Il déploie 5 conteneurs interconnectés :
# 1. Watchtower (Mise à jour auto des images)
# 2. Python Worker (Exécution code sandboxé)
# 3. Browser Agent (Navigation web headless)
# 4. Admin Manager (Monitoring, Backups, Maintenance)
# 5. Open WebUI (Interface Chat, RAG, Auth)
#
# --- POURQUOI (WHY) ---
# Pourquoi un script Bash plutôt qu'un Docker Compose ?
# 1. Gestion dynamique : Permet de lire des fichiers de version ou de configuration
#    avant de lancer les conteneurs (ex: ECHO_VERSION).
# 2. Nettoyage conditionnel : La fonction cleanup_container permet de gérer proprement
#    le redémarrage sans erreurs "Name already in use".
# 3. Séquencement strict : On s'assure que le réseau est prêt avant les conteneurs.
#
# --- COMMENT (HOW - ALGO) ---
# 1. PRE-REQUIS : Vérifie Docker, les permissions, et le réseau 'ai-net'.
# 2. VERSIONING : Synchronise le fichier version local avec le système (/opt/ECHO_VERSION).
# 3. DÉPLOIEMENT SÉQUENTIEL : Pour chaque service :
#    a. Stop & Remove l'ancien conteneur.
#    b. Run le nouveau avec les bons volumes montés.
# 4. POST-INSTALL : Attend le Healthcheck de OWUI et injecte la configuration interne.
# ==============================================================================

# --- ETAPE 0 : GESTION DE LA VERSION SYSTÈME ---
# But : Garantir que la VM "sait" quelle version elle fait tourner.
# La source de vérité est le fichier VERSION dans le dépôt git local.
REPO_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
SOURCE_VERSION_FILE="$REPO_ROOT/VERSION"
SYSTEM_VERSION_FILE="/opt/ECHO_VERSION"

# Copie atomique de la version pour que les autres scripts (Admin, Shell) puissent la lire
if [ -f "$SOURCE_VERSION_FILE" ]; then
    cp "$SOURCE_VERSION_FILE" "$SYSTEM_VERSION_FILE"
    # Permission 644 : Lecture autorisée pour tout le monde (User 'echo', 'www-data', etc.)
    chmod 644 "$SYSTEM_VERSION_FILE"
fi

# Lecture pour l'affichage utilisateur (Feedback immédiat)
if [ -f "$SYSTEM_VERSION_FILE" ]; then
    ECHO_VERSION=$(cat "$SYSTEM_VERSION_FILE")
    # Nettoyage cosmétique : on retire le 'v' s'il est présent pour l'affichage propre
    ECHO_VERSION=$(echo "$ECHO_VERSION" | sed 's/^v//')
else
    ECHO_VERSION="unknown"
    echo "⚠️  Attention : Fichier de version introuvable ($SYSTEM_VERSION_FILE)."
fi

# --- CONFIGURATION BRANCHE DYNAMIQUE (V133+) ---
# Permet de savoir sur quelle branche git on se trouve pour le log
BRANCH_FILE="/opt/ECHO_BRANCH"
TARGET_BRANCH="main" # Défaut
if [ -f "$BRANCH_FILE" ]; then
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
fi

echo "🚀 ECHO FRAMEWORK INSTALLER [v$ECHO_VERSION] sur branche [$TARGET_BRANCH]"
echo "==========================================================="

# ------------------------------------------------------------------------------
# FONCTIONS UTILITAIRES (TOOLBOX)
# ------------------------------------------------------------------------------

# Fonction : wait_for_docker
# But : Bloquer l'exécution tant que le démon Docker n'est pas prêt.
# Utilité : Évite que le script plante au boot si Docker met du temps à démarrer.
wait_for_docker() {
    echo "⏳ Attente du démon Docker..."
    until docker info > /dev/null 2>&1; do
        sleep 2
        echo -n "."
    done
    echo " OK."
}

# Fonction : cleanup_container
# But : Nettoyer proprement un conteneur avant de le recréer.
# Algo :
# 1. Vérifie si un conteneur avec ce nom existe (en cours ou arrêté).
# 2. Si oui, tente de l'arrêter (stop) en ignorant les erreurs si déjà stoppé.
# 3. Supprime le conteneur (rm) pour libérer le nom.
cleanup_container() {
    local container_name=$1
    if [ "$(docker ps -aq -f name=^/${container_name}$)" ]; then
        echo "♻️  Reset conteneur $container_name..."
        docker stop $container_name >/dev/null 2>&1 || true
        docker rm $container_name >/dev/null 2>&1 || true
    fi
}

# ------------------------------------------------------------------------------
# PRÉPARATION DU SYSTÈME (PRE-FLIGHT CHECKS)
# ------------------------------------------------------------------------------

wait_for_docker

# Rendre les scripts exécutables est vital. 
# Si on oublie ça, les 'docker exec' plus bas échoueront avec 'Permission denied'.
chmod +x /opt/owui-scripts/*.sh 2>/dev/null || true

# Création du réseau Docker 'ai-net' s'il n'existe pas.
# Ce réseau 'bridge' permet aux conteneurs de se parler par leur nom DNS (ex: ping python-worker).
# Isolation : Les conteneurs ne sont pas exposés sur le réseau hôte par défaut (sauf ports publiés).
docker network inspect ai-net >/dev/null 2>&1 || docker network create ai-net

# ------------------------------------------------------------------------------
# DÉPLOIEMENT DES CONTENEURS (SERVICES)
# ------------------------------------------------------------------------------

# --- 1. WATCHTOWER (AUTO-UPDATE) ---
# Rôle : Surveiller les images Docker et les mettre à jour automatiquement.
# Config : Vérifie toutes les heures (3600s). Supprime les vieilles images (--cleanup).
# Volume : A besoin du socket Docker (/var/run/docker.sock) pour piloter le démon.
cleanup_container "watchtower"
docker run -d --name watchtower --network ai-net --restart always \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower --interval 3600 --cleanup

# --- 2. PYTHON WORKER (SANDBOX CODE) ---
# Rôle : Exécuter du code Python généré par l'IA dans un environnement isolé.
# Image : python:3.11-slim (Léger mais suffisant).
# Cmd : Installe les libs de Data Science au démarrage puis lance l'API.
# Volumes :
# - Code : /opt/python-worker/worker_api.py (Le serveur Flask)
# - Data : echo-worker-data (Volume persistant pour stocker les fichiers générés/analysés)
cleanup_container "python-worker"
docker run -d --name python-worker --network ai-net --restart always \
  -v /opt/python-worker/worker_api.py:/app/app.py \
  -v echo-worker-data:/app/data \
  python:3.11-slim \
  /bin/bash -c "pip install --no-cache-dir flask flask-cors requests pandas numpy scipy scikit-learn matplotlib seaborn yfinance beautifulsoup4 openpyxl regex sympy && python /app/app.py"

# --- 3. BROWSER AGENT (NAVIGATION WEB) ---
# Rôle : Naviguer sur le web réel via un Chrome headless piloté par DrissionPage.
# Dépendances : Installation lourde (chromium, fonts, libs graphiques) requise dans l'image.
# IPC=host : Nécessaire pour éviter les crashs de mémoire partagée de Chrome (/dev/shm).
cleanup_container "browser-agent"
docker run -d --name browser-agent --network ai-net --restart always \
  --ipc=host \
  -v /opt/browser-agent/browser_api.py:/app/app.py \
  -v echo-browser-data:/app/data \
  -e PIP_ROOT_USER_ACTION=ignore \
  python:3.11-slim \
  /bin/bash -c "apt-get update && apt-get install -y chromium fonts-liberation libasound2 libnss3 libgbm1 libnspr4 xdg-utils && pip install --no-cache-dir flask flask-cors DrissionPage && python /app/app.py"

# --- 4. ADMIN MANAGER (OPS & MONITORING) ---
# Rôle : Dashboard d'administration, Backups, et Maintenance des Signatures Gemini.
# Volumes Critiques :
# - /var/run/docker.sock : Pour redémarrer les autres conteneurs.
# - /app/backend/data : Montage du volume 'open-webui' pour nettoyer les signatures périmées.
# - /backups : Volume dédié pour stocker les archives .tar.gz.
cleanup_container "admin-manager"
docker run -d --name admin-manager --network ai-net --restart always \
  -p 3001:3001 \
  --add-host=host.docker.internal:host-gateway \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/admin-manager/server.py:/app/server.py \
  -v echo-backups:/backups \
  -v open-webui:/data \
  -v open-webui:/app/backend/data \
  python:3.11 \
  /bin/bash -c "pip install --no-cache-dir flask flask-cors docker psutil paramiko APScheduler schedule && python /app/server.py"

# --- 5. OPEN WEBUI (INTERFACE PRINCIPALE) ---
# Rôle : Le frontend Chat, la gestion RAG, et le moteur d'inférence via Pipe.
# Image : ghcr.io/open-webui/open-webui:main (Version Edge/Dev pour les features récentes).
# Volumes "Injection" :
# On monte nos scripts locaux (/opt/owui-...) directement DANS le conteneur.
# Cela permet de modifier le code (ex: pipe_engine.py) sur l'hôte et de juste redémarrer le conteneur
# pour que ce soit pris en compte, sans reconstruire l'image (Hot Reloading).
cleanup_container "open-webui"
echo "🧠 Démarrage Open WebUI (:main)..."
docker run -d --name open-webui --network ai-net --restart always \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  -v /opt/owui-pipes:/opt/owui-pipes \
  -v /opt/owui-tools:/opt/owui-tools \
  -v /opt/owui-filters:/opt/owui-filters \
  -v /opt/owui-actions:/opt/owui-actions \
  -v /opt/owui-scripts:/opt/owui-scripts \
  ghcr.io/open-webui/open-webui:main

# ------------------------------------------------------------------------------
# POST-INSTALLATION (CONFIGURATION APPLICATIVE)
# ------------------------------------------------------------------------------
echo "⏳ Attente disponibilité Open WebUI (Healthcheck)..."
# On boucle tant que l'URL /health ne renvoie pas un code 200 OK.
# Cela évite de lancer la config alors que le serveur n'est pas prêt.
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 5
    echo -n "."
done
echo " UP."

# Une fois le serveur UP, on lance le script de configuration interne
# (Création admin, chargement du modèle Pipe, activation des outils).
echo "🔧 Configuration Auto..."
docker exec open-webui /bin/bash /opt/owui-scripts/config-owui.sh

# Petit nettoyage final pour gagner de la place disque (supprime les images non utilisées)
docker image prune -f >/dev/null 2>&1
echo "✅ DEPLOIEMENT STABILISÉ [v$ECHO_VERSION] sur [$TARGET_BRANCH]."