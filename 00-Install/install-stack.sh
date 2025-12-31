#!/bin/bash
# ==============================================================================
# ECHO v5.12 - INSTALLATION STACK (ROBUST ADMIN IMAGE)
# ==============================================================================
# CORRECTIFS :
# 1. Admin Manager : Passage à l'image 'python:3.11' (Full) pour inclure GCC natif
#    (Résout définitivement les crashs de compilation psutil/crypto)
# 2. Montage volume open-webui (confirmé)
# 3. Browser Agent : Dépendances graphiques (confirmé)
# ==============================================================================

# Fonction pour attendre que Docker soit prêt
wait_for_docker() {
    echo "⏳ Attente du démon Docker..."
    until docker info > /dev/null 2>&1; do
        sleep 2
        echo -n "."
    done
    echo " OK."
}

# Fonction de nettoyage propre
cleanup_container() {
    local container_name=$1
    if [ "$(docker ps -aq -f name=^/${container_name}$)" ]; then
        echo "♻️  Reset conteneur $container_name..."
        docker stop $container_name >/dev/null 2>&1 || true
        docker rm $container_name >/dev/null 2>&1 || true
    fi
}

wait_for_docker

# On garde /opt pour les scripts (lecture seule), mais on laisse Docker gérer la DATA
chmod +x /opt/owui-scripts/*.sh 2>/dev/null || true
docker network inspect ai-net >/dev/null 2>&1 || docker network create ai-net

# --- 1. WATCHTOWER (Maintenance) ---
cleanup_container "watchtower"
docker run -d --name watchtower --network ai-net --restart always \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower --interval 3600 --cleanup

# --- 2. PYTHON WORKER (Data & Calc) ---
cleanup_container "python-worker"
docker run -d --name python-worker --network ai-net --restart always \
  -v /opt/python-worker/worker_api.py:/app/app.py \
  -v echo-worker-data:/app/data \
  python:3.11-slim \
  /bin/bash -c "pip install --no-cache-dir flask flask-cors requests pandas numpy scipy scikit-learn matplotlib seaborn yfinance beautifulsoup4 openpyxl regex sympy && python /app/app.py"

# --- 3. BROWSER AGENT (DRISSIONPAGE) ---
cleanup_container "browser-agent"
docker run -d --name browser-agent --network ai-net --restart always \
  --ipc=host \
  -v /opt/browser-agent/browser_api.py:/app/app.py \
  -v echo-browser-data:/app/data \
  -e PIP_ROOT_USER_ACTION=ignore \
  python:3.11-slim \
  /bin/bash -c "apt-get update && apt-get install -y chromium fonts-liberation libasound2 libnss3 libgbm1 libnspr4 xdg-utils && pip install --no-cache-dir flask flask-cors DrissionPage && python /app/app.py"

# --- 4. ADMIN MANAGER (Ops) ---
# FIX: Passage à python:3.11 (Full) au lieu de slim pour garantir la présence de GCC/Headers
# Suppression des apt-get devenus inutiles.
cleanup_container "admin-manager"
docker run -d --name admin-manager --network ai-net --restart always \
  -p 3001:3001 \
  --add-host=host.docker.internal:host-gateway \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/admin-manager/server.py:/app/server.py \
  -v echo-backups:/backups \
  -v open-webui:/data \
  python:3.11 \
  /bin/bash -c "pip install --no-cache-dir flask flask-cors docker psutil paramiko APScheduler && python /app/server.py"

# --- 5. OPEN WEBUI (Cœur) ---
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

# --- 6. POST-INSTALLATION ---
echo "⏳ Attente disponibilité Open WebUI (Healthcheck)..."
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 5
    echo -n "."
done
echo " UP."

echo "🔧 Configuration Auto..."
docker exec open-webui /bin/bash /opt/owui-scripts/config-owui.sh

docker image prune -f >/dev/null 2>&1
echo "✅ DEPLOIEMENT STABILISÉ."