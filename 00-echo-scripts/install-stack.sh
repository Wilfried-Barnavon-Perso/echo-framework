#!/bin/bash
# ==============================================================================
# SCRIPT : install-stack.sh (VERSION COMPOSE STANDARDISÉE)
# VERSION : 6.3
# AUTEUR  : Wilfried BARNAVON
# ==============================================================================
# ROLE : PROVISIONING ET LANCEMENT VIA DOCKER COMPOSE (ARCHITECTURE STANDALONE)
# ==============================================================================

set -e # Arrêt en cas d'erreur critique

# --- ETAPE 0 : GESTION VERSION & ENV ---
REPO_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
SOURCE_VERSION_FILE="$REPO_ROOT/VERSION"
SYSTEM_VERSION_FILE="/opt/ECHO_VERSION"
COMPOSE_FILE="/opt/config/stack-echo.yml"

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

BRANCH_FILE="/opt/ECHO_BRANCH"
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
fi

# FORCE LEGACY - Pas de détection auto pour votre environnement
DOCKER_COMPOSE_CMD="docker-compose"

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
    # Nettoyage du nom (suppression espaces et deux-points éventuels)
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
    # Nettoyage du nom
    net_name=$(echo "$net_name" | tr -d ': ')

    if [ -z "$net_name" ]; then return; fi

    if docker network inspect "$net_name" >/dev/null 2>&1; then
        echo "   ✅ Réseau '$net_name' détecté."
    else
        echo "   🆕 Création réseau '$net_name'..."
        docker network create "$net_name"
    fi
}

# --- 1. PRE-FLIGHT CHECKS ---
wait_for_docker
chmod +x /opt/echo-scripts/*.sh 2>/dev/null || true

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ CRITIQUE : Fichier $COMPOSE_FILE introuvable !"
    exit 1
fi

# FIX : Téléchargement explicite de l'image alpine pour les outils de maintenance
# Cela évite l'erreur "Unable to find image" si elle n'est pas présente.
echo "📦 Vérification de l'image utilitaire (alpine)..."
docker pull alpine:latest >/dev/null 2>&1 || echo "⚠️  Impossible de télécharger alpine:latest (déjà présent ?)"

# --- 2. PROVISIONING RESSOURCES (AUTOMATIQUE) ---
echo "🏗️  Analyse du fichier Docker Compose pour les ressources externes..."

# 2.1 Réseaux Externes (Détection Dynamique)
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

# 2.2 Volumes Externes (Détection Dynamique)
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

# --- 3. LANCEMENT DOCKER COMPOSE ---
echo "🎼 Démarrage de la Stack via Docker Compose (Projet: $COMPOSE_PROJECT_NAME)..."
$DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" pull --quiet

# --- SUPPRESSION PRÉVENTIVE (HARD CLEAN GLOBAL) ---
# Nécessaire pour supprimer proprement les conteneurs BW obsolètes
set +e 
for d in $(docker ps -a --format '{{.Names}}') ; do 
    echo "⚠️ Suppression préventive du conteneur $d..."
    docker rm -f $d >/dev/null 2>&1
done

for ((d=0 ; d < 10 ; d++ )) ; do
    echo "⌚ $((10-$d)) secondes avant construction..."
    REMAINING=$(docker ps -a --format '{{.Names}}' || true)
    [ -z "$REMAINING" ] && break
    sleep 1 
done
echo "🏗️ Go !"
set -e

# Démarrage final
$DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" up -d --remove-orphans

if [ $? -eq 0 ]; then
    echo "✅ Stack Docker Compose active."
else
    echo "❌ Erreur lors du lancement Docker Compose."
    exit 1
fi

# --- 4. POST-INSTALL (CONFIG) ---
echo "⏳ Attente disponibilité Open WebUI (Healthcheck sur localhost:3000)..."
# Note: Port modifié à 3000 (Mapping direct) au lieu de 8080
MAX_RETRIES=300
COUNT=0
set +e
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 2
    ((COUNT++))
    if [ "$COUNT" -ge "$MAX_RETRIES" ]; then
        echo "❌ Timeout attente Open WebUI."
        break
    fi
    echo -n "."
done
set -e
echo " UP."

echo "🔧 Configuration Auto (API Host-Driven)..."
if [ -f "/opt/echo-scripts/config-owui.sh" ]; then
    /bin/bash /opt/echo-scripts/config-owui.sh
else
    echo "⚠️ Script de configuration introuvable (/opt/echo-scripts/config-owui.sh)"
fi

# Nettoyage images orphelines
docker image prune -f >/dev/null 2>&1
echo "✅ DEPLOIEMENT TERMINÉ."
echo "-----------------------------------------------------------"
echo "🌐 APPLICATION ECHO : http://IP-LOCALE:3000"
echo "🔧 CONSOLE ADMIN    : http://IP-LOCALE:3001"
echo "⚠️  N'oubliez pas de configurer votre WAF si public !"
echo "-----------------------------------------------------------"