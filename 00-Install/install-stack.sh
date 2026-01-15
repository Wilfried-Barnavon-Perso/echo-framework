#!/bin/bash
# ==============================================================================
# SCRIPT : install-stack.sh
# VERSION : 5.6.0
# AUTEUR  : Wilfried BARNAVON
# DATE    : 2026-01-15
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
# 2. VERSIONING : Synchronise le fichier VERSION local.
# 3. DÉPLOIEMENT : Boucle sur chaque service pour le lancer.
# 4. CONFIGURATION : Attend que Open WebUI soit UP, puis injecte les configurations
#    et les dépendances critiques (orjson pour la stratégie Turbo).
# ==============================================================================

# --- CONFIGURATION ---
NETWORK_NAME="ai-net"
ECHO_VERSION=$(cat /opt/ECHO_VERSION 2>/dev/null || echo "5.6.0")

echo "==============================================="
echo "   ECHO FRAMEWORK INSTALLER - $ECHO_VERSION"
echo "==============================================="

# 1. RÉSEAU
if ! docker network ls | grep -q "$NETWORK_NAME"; then
    echo "🌐 Création du réseau Docker : $NETWORK_NAME"
    docker network create $NETWORK_NAME
else
    echo "✅ Réseau $NETWORK_NAME existant."
fi

# Fonction Helper
cleanup_container() {
    local name=$1
    if docker ps -a --format '{{.Names}}' | grep -Eq "^${name}\$"; then
        echo "♻️  Nettoyage ancien conteneur : $name"
        docker rm -f $name >/dev/null
    fi
}

# 2. SERVICES BACKEND
cleanup_container "watchtower"
docker run -d --name watchtower --network $NETWORK_NAME \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower --interval 3600 --cleanup

cleanup_container "python-worker"
docker run -d --name python-worker --network $NETWORK_NAME --restart always \
  -v /opt/python-worker:/app \
  python:3.11-slim python /app/worker_api.py

cleanup_container "browser-agent"
docker run -d --name browser-agent --network $NETWORK_NAME --restart always \
  -v /opt/browser-agent:/app \
  python:3.11-slim python /app/browser_api.py

cleanup_container "admin-manager"
docker run -d --name admin-manager --network $NETWORK_NAME --restart always \
  -v /opt/admin-manager:/app \
  -v /var/run/docker.sock:/var/run/docker.sock \
  python:3.11-slim python /app/server.py

# 3. OPEN WEBUI (COEUR DU SYSTÈME)
# Note : Nous montons les volumes Pipe/Tools pour que les fichiers Python soient
# accessibles et modifiables à chaud depuis l'hôte.
cleanup_container "open-webui"
echo "🧠 Démarrage Open WebUI (:main)..."
docker run -d --name open-webui --network $NETWORK_NAME --restart always \
  -p 3000:8080 \
  -v open-webui:/app/backend/data \
  -v /opt/owui-pipes:/opt/owui-pipes \
  -v /opt/owui-tools:/opt/owui-tools \
  -v /opt/owui-filters:/opt/owui-filters \
  -v /opt/owui-actions:/opt/owui-actions \
  -v /opt/owui-scripts:/opt/owui-scripts \
  ghcr.io/open-webui/open-webui:main

# ------------------------------------------------------------------------------
# POST-INSTALLATION (CONFIGURATION APPLICATIVE & OPTIMISATIONS)
# ------------------------------------------------------------------------------
echo "⏳ Attente disponibilité Open WebUI (Healthcheck)..."
# On boucle tant que l'URL /health ne renvoie pas un code 200 OK.
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 5
    echo -n "."
done
echo " UP."

# --- STRATÉGIE 1: INSTALLATION DES DÉPENDANCES HAUTE PERFORMANCE (TURBO) ---
echo "⚡ [OPTIMISATION] Installation de orjson (Rust JSON Engine) dans Open WebUI..."
# On force l'installation de orjson et ujson dans l'environnement du conteneur.
# Cela permet au Pipe Python d'utiliser ces bibliothèques pour accélérer le traitement JSON
# et réduire l'usage CPU/RAM lors de la sérialisation de gros fichiers Base64.
if docker exec -u 0 open-webui pip install orjson ujson > /dev/null 2>&1; then
    echo "✅ Optimisation 'Turbo JSON' activée (orjson installé)."
else
    echo "⚠️ Echec de l'installation de orjson. Le Pipe utilisera le mode standard (plus lent)."
fi

# Configuration interne (Création admin, etc.)
echo "🔧 Lancement script de configuration interne..."
if [ -f "/opt/owui-scripts/config-owui.sh" ]; then
    docker exec -u 0 open-webui bash /opt/owui-scripts/config-owui.sh
fi

echo "🎉 DÉPLOIEMENT TERMINÉ (5.6.0) !"