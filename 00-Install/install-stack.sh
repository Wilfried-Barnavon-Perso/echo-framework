#!/bin/bash
# ==============================================================================
# ECHO v5.14 - INSTALLATION STACK (ROBUST ADMIN + SIGNATURE SUPPORT)
# ==============================================================================
# DESCRIPTION :
# Script de déploiement des conteneurs Docker pour la stack ECHO.
# Gère les dépendances, les volumes persistants et la configuration réseau.
#
# ARCHITECTURE DES DONNÉES (CRITIQUE) :
# 1. Volume 'open-webui' : Contient la BDD, les fichiers RAG et les SIGNATURES Gemini.
# 2. Partage : Ce volume est monté sur 'open-webui' (RW) et 'admin-manager' (RW).
#    Cela permet à l'Admin Manager de nettoyer les signatures périmées générées par le Pipe.
#
# CHOIX TECHNIQUES :
# - Image Python 3.11 Full (pas Slim) pour Admin Manager : Nécessaire pour avoir GCC
#   et les headers C++ requis par la compilation de 'psutil' et 'crypto'.
# - Network 'ai-net' : Isolation du trafic entre les conteneurs.
# ==============================================================================

# ------------------------------------------------------------------------------
# FONCTIONS UTILITAIRES
# ------------------------------------------------------------------------------

# Attend que le socket Docker soit réactif avant de lancer les commandes
wait_for_docker() {
    echo "⏳ Attente du démon Docker..."
    until docker info > /dev/null 2>&1; do
        sleep 2
        echo -n "."
    done
    echo " OK."
}

# Arrête et supprime proprement un conteneur existant pour éviter les conflits de noms
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

# On rend les scripts exécutables (au cas où)
chmod +x /opt/owui-scripts/*.sh 2>/dev/null || true

# Création du réseau dédié si inexistant
docker network inspect ai-net >/dev/null 2>&1 || docker network create ai-net

# ------------------------------------------------------------------------------
# DÉPLOIEMENT DES CONTENEURS
# ------------------------------------------------------------------------------

# --- 1. WATCHTOWER ---
# Rôle : Mise à jour automatique des images Docker.
# Config : Vérifie toutes les heures (--interval 3600), supprime les vieilles images (--cleanup).
cleanup_container "watchtower"
docker run -d --name watchtower --network ai-net --restart always \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower --interval 3600 --cleanup

# --- 2. PYTHON WORKER ---
# Rôle : Exécution de code Python sandboxé pour l'analyse de données (Pandas, Scipy, etc.).
# Note : Image 'slim' suffisante car les libs data science sont installées via pip.
cleanup_container "python-worker"
docker run -d --name python-worker --network ai-net --restart always \
  -v /opt/python-worker/worker_api.py:/app/app.py \
  -v echo-worker-data:/app/data \
  python:3.11-slim \
  /bin/bash -c "pip install --no-cache-dir flask flask-cors requests pandas numpy scipy scikit-learn matplotlib seaborn yfinance beautifulsoup4 openpyxl regex sympy && python /app/app.py"

# --- 3. BROWSER AGENT ---
# Rôle : Navigation Web autonome via DrissionPage (Chromium driver).
# Dépendances : Installation lourde de chromium et libs graphiques requise.
cleanup_container "browser-agent"
docker run -d --name browser-agent --network ai-net --restart always \
  --ipc=host \
  -v /opt/browser-agent/browser_api.py:/app/app.py \
  -v echo-browser-data:/app/data \
  -e PIP_ROOT_USER_ACTION=ignore \
  python:3.11-slim \
  /bin/bash -c "apt-get update && apt-get install -y chromium fonts-liberation libasound2 libnss3 libgbm1 libnspr4 xdg-utils && pip install --no-cache-dir flask flask-cors DrissionPage && python /app/app.py"

# --- 4. ADMIN MANAGER (OPS & MAINTENANCE) ---
# Rôle : Dashboard, Backups, Monitoring système, et MAINTENANCE DES SIGNATURES GEMINI.
#
# CRITIQUE - VOLUMES :
# - /backups : Volume dédié aux archives .tar.gz
# - /data : Montage historique (pour rétrocompatibilité scripts internes)
# - /app/backend/data : Montage du volume 'open-webui'. C'est ICI que se trouvent les signatures.
#   L'Admin Manager doit avoir accès à ce dossier pour exécuter le nettoyage LRU.
#
# CRITIQUE - DEPENDANCES :
# - 'schedule' : Librairie de planification pour lancer le nettoyage signatures la nuit.
# - 'psutil' : Pour le monitoring CPU/RAM.
# - Image Full Python : Nécessaire pour compiler psutil.
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

# --- 5. OPEN WEBUI (CŒUR) ---
# Rôle : Interface Chat, RAG, Auth Utilisateurs.
# Config : Utilise le tag :main pour les dernières fonctionnalités.
# Volumes : Montage de tous les scripts/outils/fonctions injectés dans /opt.
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
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 5
    echo -n "."
done
echo " UP."

echo "🔧 Configuration Auto (Création user admin, chargement modèles)..."
docker exec open-webui /bin/bash /opt/owui-scripts/config-owui.sh

# Nettoyage des images orphelines pour gagner de la place
docker image prune -f >/dev/null 2>&1
echo "✅ DEPLOIEMENT STABILISÉ."