#!/bin/bash
# ==============================================================================
# SCRIPT : install-stack.sh (VERSION COMPOSE STANDARDISÉE)
# VERSION : 5.6.5
# ==============================================================================
# ROLE : PROVISIONING ET LANCEMENT VIA DOCKER COMPOSE
# ==============================================================================

# --- ETAPE 0 : GESTION VERSION & ENV ---
REPO_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
SOURCE_VERSION_FILE="$REPO_ROOT/VERSION"
SYSTEM_VERSION_FILE="/opt/ECHO_VERSION"

export COMPOSE_PROJECT_NAME="echo"

if [ -f "$SOURCE_VERSION_FILE" ]; then
    cp "$SOURCE_VERSION_FILE" "$SYSTEM_VERSION_FILE"
    chmod 644 "$SYSTEM_VERSION_FILE"
fi

ECHO_VERSION="unknown"
if [ -f "$SYSTEM_VERSION_FILE" ]; then
    ECHO_VERSION=$(cat "$SYSTEM_VERSION_FILE" | sed 's/^v//')
fi

BRANCH_FILE="/opt/ECHO_BRANCH"
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
fi

# --- DETECTION DOCKER COMPOSE V2 (FIX KeyError: 'ContainerConfig') ---
if docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker compose"
else
    if command -v docker-compose >/dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker-compose"
    else
        echo "❌ Erreur : Ni 'docker compose' (v2) ni 'docker-compose' (v1) ne sont installés."
        exit 1
    fi
fi

echo "🚀 ECHO FRAMEWORK [COMPOSE LAUNCHER] v$ECHO_VERSION (Branche: $TARGET_BRANCH)"
echo "   Moteur Compose : $DOCKER_COMPOSE_CMD"
echo "==========================================================="

# --- TOOLBOX ---
wait_for_docker() {
    echo "⏳ Attente du démon Docker..."
    until docker info > /dev/null 2>&1; do
        sleep 2
        echo -n "."
    done
    echo " OK."
}

ensure_network() {
    local net_name=$1
    if docker network inspect "$net_name" >/dev/null 2>&1; then
        echo "✅ Réseau '$net_name' détecté."
    else
        echo "🆕 Création réseau '$net_name'..."
        docker network create "$net_name"
    fi
}

ensure_volume() {
    local vol_name=$1
    if docker volume inspect "$vol_name" >/dev/null 2>&1; then
        echo "✅ Volume '$vol_name' détecté."
    else
        echo "🆕 Création volume '$vol_name'..."
        docker volume create "$vol_name"
    fi
}

# --- 1. PRE-FLIGHT CHECKS ---
wait_for_docker
chmod +x /opt/owui-scripts/*.sh 2>/dev/null || true

# --- 2. PROVISIONING RESSOURCES ---
echo "🏗️  Vérification de l'infrastructure persistante..."

ensure_network "ai-net"

# Volumes standardisés (Convention echo-*)
# "open-webui" devient "echo-webui-data" pour cohérence globale
ensure_volume "echo-webui-data"
ensure_volume "echo-worker-data"
ensure_volume "echo-browser-data"
ensure_volume "echo-backups"
ensure_volume "watchtower"

# --- 3. LANCEMENT DOCKER COMPOSE ---
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ CRITIQUE : Fichier $COMPOSE_FILE introuvable !"
    exit 1
fi

echo "🎼 Démarrage de la Stack via Docker Compose (Projet: $COMPOSE_PROJECT_NAME)..."
$DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" pull --quiet

# --- FIX COMPATIBILITÉ DOCKER-COMPOSE 1.x ---
# Si on utilise l'ancien docker-compose, on force la suppression du conteneur
# pour éviter l'erreur 'KeyError: ContainerConfig' lors de la recreation.
if [ "$DOCKER_COMPOSE_CMD" = "docker-compose" ]; then
    if docker ps -a --format '{{.Names}}' | grep -q "^echo-webui-core$"; then
        echo "⚠️  [Compatibilité v1] Suppression préventive du conteneur echo-webui-core pour éviter le crash..."
        docker rm -f echo-webui-core >/dev/null 2>&1
    fi
fi

$DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" up -d --remove-orphans

if [ $? -eq 0 ]; then
    echo "✅ Stack Docker Compose active."
else
    echo "❌ Erreur lors du lancement Docker Compose."
    exit 1
fi

# --- 4. POST-INSTALL (CONFIG) ---
echo "⏳ Attente disponibilité Open WebUI (Healthcheck)..."
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 5
    echo -n "."
done
echo " UP."

echo "🔧 Configuration Auto (API)..."
# Utilisation de docker-compose exec pour cibler le SERVICE 'open-webui' défini dans le YAML.
# Note: Le conteneur réel s'appelle 'echo-webui-core', mais compose utilise le nom du service.
$DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" exec -T open-webui /bin/bash /opt/owui-scripts/config-owui.sh

# Nettoyage
docker image prune -f >/dev/null 2>&1
echo "✅ DEPLOIEMENT TERMINÉ."