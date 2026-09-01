#!/bin/bash
# ==============================================================================
# SCRIPT : install-stack.sh (VERSION COMPOSE STANDARDISÉE)
# VERSION : 6.27
# AUTEUR  : Wilfried BARNAVON
# ==============================================================================
# CHANGELOG 6.27 : Redémarrage parallèle des services Python lors du Hot Reload.
# CHANGELOG 6.26 : Centralisation du cron de nettoyage Docker et ajustement des logs à 10 Mo.
# CHANGELOG 6.25 : Ajout de ensure_docker_autosafety() pour rotation logs globale (idempotent).
# CHANGELOG 6.24 : Génération dynamique de ECHO_SSO_SECRET pour le Forward Auth hybride.
# ROLE : PROVISIONING ET LANCEMENT VIA DOCKER COMPOSE (ARCHITECTURE STANDALONE)
# ==============================================================================

set -e # Arrêt en cas d'erreur critique

# --- INITIALISATION : CORE ECHO GLOBALS ---
ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ]; then
    source "$GLOBALS_FILE"
else
    echo "❌ CRITIQUE : Fichier global introuvable ($GLOBALS_FILE)."
    exit 1
fi
# ------------------------------------------

# --- ETAPE 0 : GESTION VERSION & ENV ---
REPO_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
SOURCE_VERSION_FILE="$REPO_ROOT/VERSION"
SYSTEM_VERSION_FILE="$ECHO_VERSION_FILE"
COMPOSE_FILE="$ECHO_CONFIG/stack-echo.yml"

export COMPOSE_PROJECT_NAME="echo"

# Mise à jour du fichier de version système si une nouvelle source existe
if [ -f "$SOURCE_VERSION_FILE" ]; then
    cp -f "$SOURCE_VERSION_FILE" "$SYSTEM_VERSION_FILE"
    chmod 644 "$SYSTEM_VERSION_FILE"
fi

ECHO_VERSION="unknown"
if [ -f "$SYSTEM_VERSION_FILE" ]; then
    ECHO_VERSION=$(cat "$SYSTEM_VERSION_FILE")
fi

BRANCH_FILE="$ECHO_BRANCH_FILE"
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
fi

# Détection automatique du moteur Compose (V1 vs V2)
DOCKER_COMPOSE_CMD="docker-compose"
if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
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

ensure_volume() {
    local vol_name=$1
    vol_name=$(echo "$vol_name" | tr -d ': ')
    if [ -z "$vol_name" ]; then return; fi
    if docker volume inspect "$vol_name" >/dev/null 2>&1; then
        echo "   ✅ Volume '$vol_name' existe déjà."
    else
        echo "   🆕 Création volume '$vol_name'..."
        docker volume create "$vol_name"
    fi
}

ensure_network() {
    local net_name=$1
    net_name=$(echo "$net_name" | tr -d ': ')
    if [ -z "$net_name" ]; then return; fi
    if docker network inspect "$net_name" >/dev/null 2>&1; then
        echo "   ✅ Réseau '$net_name' détecté."
    else
        echo "   🆕 Création réseau '$net_name'..."
        docker network create "$net_name"
    fi
}

ensure_docker_autosafety() {
    echo "🛡️  Vérification de l'Autosafety Docker (Rotation des logs et Nettoyage)..."
    if ! command -v jq >/dev/null 2>&1; then
        echo "   ⚠️  jq non trouvé, configuration ignorée."
        return
    fi

    local daemon_file="/etc/docker/daemon.json"
    mkdir -p /etc/docker
    
    if [ ! -f "$daemon_file" ]; then
        echo '{}' > "$daemon_file"
    fi

    # Injection idempotente avec jq (préserve le reste, force les logs à 10m)
    local temp_file=$(mktemp)
    if jq '. + { "log-driver": "json-file", "log-opts": ((.["log-opts"] // {}) + { "max-size": "10m", "max-file": "3" }) }' "$daemon_file" > "$temp_file"; then
        if ! cmp -s "$daemon_file" "$temp_file"; then
            echo "   🔄 Mise à jour de la politique de logs Docker (10 Mo)..."
            cat "$temp_file" > "$daemon_file"
            if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet docker; then
                systemctl restart docker
            elif command -v service >/dev/null 2>&1; then
                service docker restart || true
            fi
        else
            echo "   ✅ Politique de logs déjà conforme (10 Mo)."
        fi
    else
        echo "   ⚠️  Erreur jq lors de l'application de l'Autosafety."
    fi
    rm -f "$temp_file"

    # --- Ajout du Cron de Nettoyage Centralisé (Idempotent) ---
    local cron_target="/etc/cron.daily/clean-echo"
    echo "   🔄 Configuration de la maintenance centralisée (clean-echo)..."
    if [ -f "$ECHO_SCRIPTS/clean-echo.sh" ]; then
        # Le script a été copié physiquement ici par sync-echo.sh
        # Nous l'injectons dans le moteur cron pour l'exécution automatique chaque nuit
        ln -sf "$ECHO_SCRIPTS/clean-echo.sh" "$cron_target"
        echo "   ✅ Cron quotidien configuré ($cron_target)."
    else
        echo "   ⚠️ Fichier clean-echo.sh introuvable, maintenance ignorée."
    fi
}

# --- 1. PRE-FLIGHT CHECKS ---
wait_for_docker
ensure_docker_autosafety
chmod +x "$ECHO_SCRIPTS"/*.sh 2>/dev/null || true

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ CRITIQUE : Fichier $COMPOSE_FILE introuvable !"
    exit 1
fi

echo "📦 Vérification de l'image utilitaire (alpine)..."
docker pull alpine:latest >/dev/null 2>&1 || echo "⚠️  Impossible de télécharger alpine:latest (déjà présent ?)"

# --- 2. PROVISIONING RESSOURCES (AUTOMATIQUE) ---
echo "🏗️  Analyse du fichier Docker Compose pour les ressources externes..."

# 2.0 Gestion des Secrets Centralisés
ENV_FILE="$ECHO_ENV_FILE"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

generate_secret() {
    local key=$1
    local length=$2
    if ! grep -q "^$key=" "$ENV_FILE"; then
        local secret=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$length")
        echo "$key=$secret" >> "$ENV_FILE"
        echo "   🔑 Génération du secret : $key"
    fi
}

generate_secret "BW_DB_PASSWORD" 24
generate_secret "SEARXNG_SECRET" 64
generate_secret "ECHO_SSO_SECRET" 32
generate_secret "N8N_ENCRYPTION_KEY" 64

# Configuration N8N Headless (Propriétaire)
if ! grep -q "^N8N_INSTANCE_OWNER_MANAGED_BY_ENV=" "$ENV_FILE"; then
    echo "N8N_INSTANCE_OWNER_MANAGED_BY_ENV=true" >> "$ENV_FILE"
    echo "N8N_INSTANCE_OWNER_EMAIL=system@echo.local" >> "$ENV_FILE"
    echo "N8N_INSTANCE_OWNER_FIRST_NAME=ECHO" >> "$ENV_FILE"
    echo "N8N_INSTANCE_OWNER_LAST_NAME=System" >> "$ENV_FILE"
    
    # Mot de passe généré aléatoirement pour sécuriser l'instance
    generate_secret "N8N_INSTANCE_OWNER_PASSWORD" 32
    
    echo "   👤 Configuration N8N Owner injectée dans l'environnement."
fi

# 2.1 Réseaux Externes
echo "🔍 Recherche des réseaux externes définis dans $COMPOSE_FILE..."
NETWORKS_BLOCK=$(awk '/^networks:/{flag=1; next} /^[a-z]/{flag=0} flag' "$COMPOSE_FILE")
EXTERNAL_NETWORKS=$(echo "$NETWORKS_BLOCK" | grep -B 1 "external: true" | grep -v "external:" | grep -v "\-\-" | tr -d ': ')

if [ -z "$EXTERNAL_NETWORKS" ]; then
    echo "⚠️  Aucun réseau externe détecté."
else
    for net in $EXTERNAL_NETWORKS; do
        ensure_network "$net"
    done
fi

# 2.2 Volumes Externes
echo "🔍 Recherche des volumes externes définis dans $COMPOSE_FILE..."
VOLUMES_BLOCK=$(awk '/^volumes:/{flag=1; next} /^[a-z]/{flag=0} flag' "$COMPOSE_FILE")
EXTERNAL_VOLUMES=$(echo "$VOLUMES_BLOCK" | grep -B 1 "external: true" | grep -v "external:" | grep -v "\-\-" | tr -d ': ')

if [ -z "$EXTERNAL_VOLUMES" ]; then
    echo "⚠️  Aucun volume externe détecté."
else
    for vol in $EXTERNAL_VOLUMES; do
        ensure_volume "$vol"
    done
fi

# --- 2.3 Détection Dynamique des Origines CORS ---
echo "🌍 Calcul des origines CORS locales..."
if command -v yq >/dev/null 2>&1; then
    OWUI_PORT=$(yq '.services.open-webui.ports[0]' "$COMPOSE_FILE" | cut -d: -f1)
else
    OWUI_PORT=$(grep -A 10 "open-webui:" "$COMPOSE_FILE" | grep -m 1 "\- \"[0-9]*:[0-9]*\"" | cut -d'"' -f2 | cut -d: -f1)
fi

if [ -z "$OWUI_PORT" ]; then OWUI_PORT="3000"; fi
HOST_IPS=$(hostname -I 2>/dev/null || ip addr show | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | cut -d/ -f1)

ECHO_DETECTED_ORIGINS=""
for ip in $HOST_IPS; do
    if [ -z "$ECHO_DETECTED_ORIGINS" ]; then
        ECHO_DETECTED_ORIGINS="http://$ip:$OWUI_PORT"
    else
        ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS;http://$ip:$OWUI_PORT"
    fi
done

export ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS"
echo "   ✅ Port détecté : $OWUI_PORT"
echo "   ✅ Origines IP  : $ECHO_DETECTED_ORIGINS"


# --- 2.4 Point de montage modèle Gemma (provisionné automatiquement par le conteneur) ---
mkdir -p "$ECHO_ROOT/models"

# --- 3. LANCEMENT DOCKER COMPOSE ---
echo "🎼 Démarrage de la Stack via Docker Compose (Projet: $COMPOSE_PROJECT_NAME)..."

BW_STACK_FILE="$ECHO_CONFIG/bunkerweb-stack.yml"
ENV_FILE="$ECHO_ENV_FILE"

export COMPOSE_DOCKER_CLI_BUILD=1
export DOCKER_BUILDKIT=1

if [ -f "$BW_STACK_FILE" ] && [ -f "$ENV_FILE" ] && grep -qE "^ECHO_DOMAIN=.+" "$ENV_FILE"; then
    echo "🔒 Mode SECURE EDGE détecté. Lancement de l'infrastructure complète (ECHO + BunkerWeb)..."
    $DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" -f "$BW_STACK_FILE" -f "$COMPOSE_FILE" up -d --build --remove-orphans
else
    echo "🔓 Mode STANDARD (Local) détecté."
    $DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build --remove-orphans
fi

if [ $? -eq 0 ]; then
    echo "✅ Stack Docker Compose active."
else
    echo "❌ Erreur lors du lancement Docker Compose."
    exit 1
fi


# --- HOT RELOAD ---
# Rechargement des services dont le code est monté en bind mount (source de vérité : stack-echo.yml)
echo "⚡ Hot Reload des services Python..."
CONTAINERS_TO_RELOAD=$(docker ps \
    --filter "label=echo.hot-reload=true" \
    --format "{{.Names}}")
if [ -n "$CONTAINERS_TO_RELOAD" ]; then
    echo "$CONTAINERS_TO_RELOAD" | xargs -n 1 -P 0 docker restart >/dev/null 2>&1
    FORMATTED_LIST=$(echo "$CONTAINERS_TO_RELOAD" | tr '\n' ' ')
    echo "   ✅ Services rechargés : $FORMATTED_LIST"
else
    echo "   ⚠️  Aucun service marqué echo.hot-reload=true trouvé."
fi

echo "🔧 Configuration Auto (API Host-Driven)..."
if [ -f "$ECHO_SCRIPTS/config-owui.sh" ]; then
    /bin/bash "$ECHO_SCRIPTS/config-owui.sh"
else
    echo "⚠️ Script de configuration introuvable ($ECHO_SCRIPTS/config-owui.sh)"
fi

docker buildx prune -f >/dev/null 2>&1
docker image prune -f >/dev/null 2>&1

echo "✅ DEPLOIEMENT TERMINÉ."
echo "-----------------------------------------------------------"
echo "🌐 LOCAL UI    : http://IP-LOCALE:3000"
echo "🔧 LOCAL ADMIN : http://IP-LOCALE:3001"
echo "🔑 CREDENTIALS : Tapez 'show-echo-admin'"
echo "-----------------------------------------------------------"
echo "🛡️  POUR ACTIVER L'ACCÈS PUBLIC SÉCURISÉ (BunkerWeb) :"
echo "   Lancez la commande : enable-bunkerweb"
echo "-----------------------------------------------------------"
