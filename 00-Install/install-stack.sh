#!/bin/bash
# ==============================================================================
# SCRIPT : install-stack.sh (VERSION COMPOSE STANDARDISÉE)
# VERSION : 5.15 (Global Hard Clean Patch)
# AUTEUR  : Wilfried BARNAVON
# ==============================================================================
# ROLE : PROVISIONING ET LANCEMENT VIA DOCKER COMPOSE (LEGACY V1)
# ==============================================================================

set -e # Arrêt en cas d'erreur critique

# --- ETAPE 0 : GESTION VERSION & ENV ---
REPO_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
SOURCE_VERSION_FILE="$REPO_ROOT/VERSION"
SYSTEM_VERSION_FILE="/opt/ECHO_VERSION"

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

# --- 2. GESTION DES SECRETS D'INFRASTRUCTURE (AVANT TOUTE COMMANDE DOCKER) ---
# A. Secret BunkerWeb (Basic Auth)
BW_SECRET_FILE="/opt/.bw-setting-secret"
if [ ! -f "$BW_SECRET_FILE" ]; then
    echo "🆕 Génération du secret BunkerWeb (Admin UI)..."
    # Génération d'un mot de passe complexe de 16 caractères
    LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*()_+=-' </dev/urandom | head -c 16 > "$BW_SECRET_FILE"
    chmod 400 "$BW_SECRET_FILE"
fi
# Export global pour que docker-compose (pull et up) y ait accès
export BW_PASSWORD=$(cat "$BW_SECRET_FILE" | tr -d '[:space:]')

# --- 3. FIX PERMISSIONS SOCKET DOCKER (BUNKERWEB UID 101) ---
echo "🔧 [FIX] Configuration des ACL pour le socket Docker (UID 101)..."
if ! command -v setfacl >/dev/null 2>&1; then
    echo "   📦 Installation du paquet 'acl'..."
    apt-get update -qq && apt-get install -y -qq acl > /dev/null
fi
setfacl -m u:101:rw /var/run/docker.sock || echo "⚠️  Attention : Échec de l'application setfacl."

# --- 4. PROVISIONING RESSOURCES ---
echo "🏗️  Vérification de l'infrastructure persistante..."
ensure_volume "echo-webui-data"
ensure_volume "echo-worker-data"
ensure_volume "echo-browser-data"
ensure_volume "echo-backups"
ensure_volume "echo-bw-data"

# FIX CRITIQUE PERMISSIONS AIO (UID 101)
# On s'assure que le volume de données BunkerWeb est accessible en écriture pour l'utilisateur interne.
docker run --rm -v echo-bw-data:/data alpine chown -R 101:101 /data

# --- 5. LANCEMENT DOCKER COMPOSE ---
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ CRITIQUE : Fichier $COMPOSE_FILE introuvable !"
    exit 1
fi

echo "🎼 Démarrage de la Stack via Docker Compose (Projet: $COMPOSE_PROJECT_NAME)..."
# Maintenant que BW_PASSWORD est exporté, le pull ne lèvera plus de Warning
$DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" pull --quiet

# --- SUPPRESSION PRÉVENTIVE (HARD CLEAN GLOBAL) ---
# On vide TOUS les conteneurs de la machine pour garantir un déploiement sans conflit
# Attention : Cette opération est destructive pour tout conteneur tiers sur la VM.
set +e 
for d in $(docker ps -a --format '{{.Names}}') ; do 
    echo "⚠️ Suppression préventive du conteneur $d..."
    docker rm -f $d >/dev/null 2>&1
done

# On attend jusqu'à 10 secondes la mort des conteneurs
for ((d=0 ; d < 10 ; d++ )) ; do
    echo "$((10-$d)) secondes avant construction..."
    # On vérifie s'il reste encore des conteneurs actifs
    REMAINING=$(docker ps -a --format '{{.Names}}' || true)
    [ -z "$REMAINING" ] && break
    sleep 1 
done
set -e

# Démarrage final
$DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" up -d --remove-orphans

if [ $? -eq 0 ]; then
    echo "✅ Stack Docker Compose active."
else
    echo "❌ Erreur lors du lancement Docker Compose."
    exit 1
fi

# --- 6. POST-INSTALL (CONFIG) ---
echo "⏳ Attente disponibilité Open WebUI (Healthcheck sur localhost:3000)..."
# Ce check fonctionne grâce au port 3000 exposé sur l'hôte
MAX_RETRIES=60
COUNT=0
set +e
until curl -s -f http://localhost:3000/health > /dev/null; do
    sleep 2
    ((COUNT++))
    if [ "$COUNT" -ge "$MAX_RETRIES" ]; then
        echo "❌ Timeout attente Open WebUI."
        # On continue quand même pour tenter la config, au cas où c'est juste lent
        break
    fi
    echo -n "."
done
set -e
echo " UP."

echo "🔧 Configuration Auto (API Host-Driven)..."
# Exécution locale depuis l'hôte (Host-Driven)
# config-owui.sh est configuré pour taper sur localhost:3000
if [ -f "/opt/owui-scripts/config-owui.sh" ]; then
    /bin/bash /opt/owui-scripts/config-owui.sh
else
    echo "⚠️ Script de configuration introuvable (/opt/owui-scripts/config-owui.sh)"
fi

# Nettoyage images orphelines
docker image prune -f >/dev/null 2>&1
echo "✅ DEPLOIEMENT TERMINÉ."
echo "-----------------------------------------------------------"
echo "🔐 ACCÈS ADMIN BUNKERWEB (bw.echo-ai.eu) :"
echo "   User : admin"
echo "   Pass : $BW_PASSWORD"
echo "-----------------------------------------------------------"