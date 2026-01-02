#!/bin/bash
# ==============================================================================
# SCRIPT : install-stack.sh
# VERSION : v5.3.1
# ROLE : DÉPLOIEMENT & ORCHESTRATION DES CONTENEURS DOCKER
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script est le "maître d'œuvre" du déploiement. Il est responsable de :
# 1. Vérifier que Docker est prêt.
# 2. Créer le réseau virtuel pour isoler les conteneurs.
# 3. Lancer ou relancer chaque service (conteneur) avec la bonne configuration.
# 4. Initialiser la configuration interne d'Open WebUI une fois démarré.
#
# --- POURQUOI (WHY) ---
# Docker Compose est souvent utilisé pour cela, mais un script Bash offre plus de
# contrôle pour :
# - La gestion dynamique des volumes (nettoyage conditionnel).
# - L'enchaînement séquentiel strict (attendre que A soit prêt avant B).
# - La compatibilité maximale (pas besoin d'installer docker-compose binaire).
#
# --- COMMENT (HOW - ALGO) ---
# 1. PRE-CHECK : On attend que le socket Docker réponde.
# 2. VERSIONING : On lit le fichier /opt/ECHO_VERSION pour savoir ce qu'on déploie.
# 3. NETWORKING : On crée un bridge network 'ai-net' dédié.
# 4. DEPLOY LOOP : Pour chaque service (Watchtower, Worker, Browser, Admin, OWUI) :
#    a. On arrête l'ancien conteneur s'il existe (cleanup_container).
#    b. On supprime l'ancien conteneur.
#    c. On lance le nouveau avec 'docker run' et tous les volumes montés.
# 5. POST-INSTALL : On attend que OWUI réponde sur le port 3000, puis on injecte sa config.
# ==============================================================================

# --- ETAPE 0 : VERSIONING SYSTEME ---
# On s'assure que le fichier de version système est synchronisé avec le dépôt git local.
# Cela garantit que la version affichée est bien celle du code présent sur le disque.
REPO_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
SOURCE_VERSION_FILE="$REPO_ROOT/VERSION"
SYSTEM_VERSION_FILE="/opt/ECHO_VERSION"

if [ -f "$SOURCE_VERSION_FILE" ]; then
    # Copie de la source vers la destination système
    cp "$SOURCE_VERSION_FILE" "$SYSTEM_VERSION_FILE"
    # Permission 644 : Lecture pour tous (nécessaire pour Admin Dashboard non-root)
    chmod 644 "$SYSTEM_VERSION_FILE"
fi

# Lecture pour affichage utilisateur
if [ -f "$SYSTEM_VERSION_FILE" ]; then
    ECHO_VERSION=$(cat "$SYSTEM_VERSION_FILE")
    # Nettoyage cosmétique : on retire le 'v' s'il est présent pour l'affichage
    ECHO_VERSION=$(echo "$ECHO_VERSION" | sed 's/^v//')
else
    ECHO_VERSION="unknown"
    echo "⚠️  Attention : Fichier de version introuvable ($SYSTEM_VERSION_FILE)."
fi

echo "🚀 ECHO FRAMEWORK INSTALLER [v$ECHO_VERSION]"
echo "=========================================="

# ------------------------------------------------------------------------------
# FONCTIONS UTILITAIRES
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
# PRÉPARATION DU SYSTÈME
# ------------------------------------------------------------------------------

wait_for_docker

# On rend les scripts exécutables pour éviter les erreurs "Permission denied" plus tard.
chmod +x /opt/owui-scripts/*.sh 2>/dev/null || true

# Création du réseau Docker 'ai-net' s'il n'existe pas.
# Ce réseau permet aux conteneurs de se parler par leur nom (ex: ping python-worker).
docker network inspect ai-net >/dev/null 2>&1 || docker network create ai-net

# ------------------------------------------------------------------------------
# DÉPLOIEMENT DES CONTENEURS
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
# IPC=host : Nécessaire pour éviter les crashs de mémoire partagée de Chrome.
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
# pour que ce soit pris en compte, sans reconstruire l'image.
cleanup_container "open-webui"
echo "🧠 Démarrage Open WebUI (:main)..."
docker run -d --name open-webui --network ai-net --restart always \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  -v /opt/owui-functions:/opt/owui-functions \
  -v /opt/owui-tools:/opt/owui-tools \
  -v /opt/owui-filters:/opt/owui-filters \
  -v /opt/owui-scripts:/opt/owui-scripts \
  ghcr.io/open-webui/open-webui:main

# ------------------------------------------------------------------------------
# POST-INSTALLATION
# ------------------------------------------------------------------------------
echo "⏳ Attente disponibilité Open WebUI (Healthcheck)..."
# On boucle tant que l'URL /health ne renvoie pas un code 200 OK.
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 5
    echo -n "."
done
echo " UP."

# Une fois le serveur UP, on lance le script de configuration interne
# (Création admin, chargement du modèle Pipe, activation des outils).
echo "🔧 Configuration Auto..."
docker exec open-webui /bin/bash /opt/owui-scripts/config-owui.sh

# Petit nettoyage final pour gagner de la place disque
docker image prune -f >/dev/null 2>&1
echo "✅ DEPLOIEMENT STABILISÉ [v$ECHO_VERSION]."