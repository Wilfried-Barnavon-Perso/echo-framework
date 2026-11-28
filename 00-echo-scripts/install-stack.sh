#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : install-stack.sh (VERSION COMPOSE STANDARDISÉE)
# VERSION : 6.25.0.1
# AUTEUR  : Wilfried BARNAVON
# ==============================================================================
# ROLE : PROVISIONING ET LANCEMENT VIA DOCKER COMPOSE (ARCHITECTURE STANDALONE)
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.25.0.1: Suppression du bloc Hot Reload post-déploiement (réservé exclusivement à echo-update.sh).
# 6.18.15.1: Alignement helpers echo-globals.sh, attente Docker post-restart, garde /etc/cron.daily, exemption /swapfile WSL2 + chattr +C Btrfs.
# 6.8.0.1: Arrêt gracieux temporaire de echo-watchtower (trap EXIT) avant build/up et purge des conteneurs temporaires Compose en état Created.
# 6.6.12.1: Factorisation COMPOSE_ARGS et pré-construction isolée de echo-webui (protection RAM V8 6 Go).
# 6.5.3.1: Renommage de l'image et du conteneur echo-open-webui vers echo-webui.

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
init_echo_compose_cmd

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
    
    local expected_subnet=""
    if [ "$net_name" = "echo-network" ]; then
        expected_subnet="${ECHO_NET_MAIN_PREFIX}.0/24"
    elif [ "$net_name" = "echo-sandbox" ]; then
        expected_subnet="${ECHO_NET_SANDBOX_PREFIX}.0/24"
    fi

    if docker network inspect "$net_name" >/dev/null 2>&1; then
        if [ -n "$expected_subnet" ]; then
            local current_subnet
            current_subnet=$(docker network inspect "$net_name" -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null)
            if [ "$current_subnet" != "$expected_subnet" ]; then
                echo "   ⚠️  Sous-réseau de '$net_name' incorrect ($current_subnet). Recréation forcée..."
                # Déconnexion des conteneurs pour permettre la suppression
                for container in $(docker network inspect "$net_name" -f '{{range $k, $v := .Containers}}{{$k}} {{end}}' 2>/dev/null); do
                    docker network disconnect -f "$net_name" "$container" 2>/dev/null || true
                done
                docker network rm "$net_name" >/dev/null 2>&1 || true
                docker network create --subnet="$expected_subnet" "$net_name"
                return
            fi
        fi
        echo "   ✅ Réseau '$net_name' détecté."
    else
        echo "   🆕 Création réseau '$net_name'..."
        if [ -n "$expected_subnet" ]; then
            docker network create --subnet="$expected_subnet" "$net_name"
        else
            docker network create "$net_name"
        fi
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
            restart_docker_daemon_and_wait
        else
            echo "   ✅ Politique de logs déjà conforme (10 Mo)."
        fi
    else
        echo "   ⚠️  Erreur jq lors de l'application de l'Autosafety."
    fi
    rm -f "$temp_file"

    # --- Ajout du Cron de Nettoyage Centralisé (Idempotent) ---
    local cron_target="/etc/cron.daily/echo-clean"
    echo "   🔄 Configuration de la maintenance centralisée (echo-clean)..."
    if [ -f "$ECHO_SCRIPTS/echo-clean.sh" ]; then
        # Le script a été copié physiquement ici par echo-sync.sh
        # Nous l'injectons dans le moteur cron pour l'exécution automatique chaque nuit
        mkdir -p /etc/cron.daily
        ln -sf "$ECHO_SCRIPTS/echo-clean.sh" "$cron_target"
        echo "   ✅ Cron quotidien configuré ($cron_target)."
    else
        echo "   ⚠️ Fichier echo-clean.sh introuvable, maintenance ignorée."
    fi
}

ensure_swap_and_vm_tuning() {
    echo "🧠 Vérification du Swap et du tuning mémoire noyau..."
    if is_wsl2; then
        echo "   ℹ️  WSL2 détecté : swap gouverné par l'hôte Windows (.wslconfig), création de /swapfile ignorée."
    elif [ -z "$(swapon --show --noheadings 2>/dev/null)" ]; then
        if [ ! -f /swapfile ]; then
            echo "   🆕 Aucun swap actif détecté. Création de /swapfile (4 Go)..."
            : > /swapfile
            chattr +C /swapfile 2>/dev/null || true
            fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
            chmod 600 /swapfile
            mkswap /swapfile >/dev/null
        fi
        swapon /swapfile 2>/dev/null || true
        [ -f /etc/fstab ] || touch /etc/fstab
        grep -q "/swapfile" /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
    else
        echo "   ✅ Swap existant actif ($(swapon --show=NAME,SIZE --noheadings | tr '\n' ' '))."
    fi
    sysctl -w vm.swappiness=25 >/dev/null 2>&1 || true
    mkdir -p /etc/sysctl.d
    echo "vm.swappiness=25" > /etc/sysctl.d/99-echo-vm.conf
}

WATCHTOWER_WAS_RUNNING=0

# Restaure echo-watchtower si le script s'interrompt avant docker compose up -d
restore_watchtower_on_exit() {
    if [ "${WATCHTOWER_WAS_RUNNING:-0}" -eq 1 ]; then
        if ! docker ps -q --filter "name=^echo-watchtower$" | grep -q .; then
            echo "▶️  Relance de echo-watchtower..."
            docker start echo-watchtower >/dev/null 2>&1 || true
        fi
        WATCHTOWER_WAS_RUNNING=0
    fi
}

# Stoppe gracieusement echo-watchtower pour éviter toute race condition sur echo-searxng pendant build/up
stop_watchtower_for_upgrade() {
    if docker ps -q --filter "name=^echo-watchtower$" | grep -q .; then
        WATCHTOWER_WAS_RUNNING=1
        echo "⏸️  Arrêt temporaire de echo-watchtower pendant le build/déploiement..."
        docker stop echo-watchtower >/dev/null 2>&1 || true
    fi
}

# Purge les conteneurs temporaires Compose (<12hex>_echo-*) laissés en état Created après un run avorté
cleanup_compose_staging_containers() {
    local orphan_containers
    orphan_containers=$(docker ps -a --filter "status=created" --format "{{.Names}}" 2>/dev/null | grep -E '^[0-9a-f]{12}_echo-' || true)
    if [ -n "$orphan_containers" ]; then
        echo "🧹 Nettoyage des conteneurs temporaires Compose en état Created..."
        echo "$orphan_containers" | xargs -r docker rm -f >/dev/null 2>&1 || true
    fi
}

# --- 1. PRE-FLIGHT CHECKS ---
wait_for_docker
ensure_swap_and_vm_tuning
ensure_docker_autosafety
chmod +x "$ECHO_SCRIPTS"/*.sh 2>/dev/null || true

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "❌ CRITIQUE : Fichier $COMPOSE_FILE introuvable !"
    exit 1
fi

echo "📦 Vérification du cycle de mise à jour des images de base (:latest)..."
PULL_FLAG=""
LAST_PULL_FILE="$ECHO_ROOT/.last_base_pull"

if [ ! -f "$LAST_PULL_FILE" ] || [ -n "$(find "$LAST_PULL_FILE" -mtime +30 -print -quit 2>/dev/null)" ]; then
    echo "📅 Cycle mensuel (30 jours). Mise à jour forcée des images de base autorisée."
    docker pull alpine:latest >/dev/null 2>&1
    docker pull n8nio/n8n:latest >/dev/null 2>&1
    PULL_FLAG="--pull"
    touch "$LAST_PULL_FILE"
else
    echo "⏳ Images de base verrouillées (Mise à jour mensuelle non atteinte)."
fi

# --- 2. PROVISIONING RESSOURCES (AUTOMATIQUE) ---
echo "🏗️  Analyse du fichier Docker Compose pour les ressources externes..."

# 2.0 Gestion des Secrets Centralisés
ENV_FILE="$ECHO_ENV_FILE"
ensure_echo_secrets

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
compute_echo_detected_origins "$COMPOSE_FILE"
echo "   ✅ Port détecté : $OWUI_PORT"
echo "   ✅ Origines IP  : $ECHO_DETECTED_ORIGINS"


# --- 2.4 Point de montage modèle Gemma (provisionné automatiquement par le conteneur) ---
mkdir -p "$ECHO_ROOT/models"

# --- 2.5 Pré-construction de l'Image de Base ---
trap restore_watchtower_on_exit EXIT
stop_watchtower_for_upgrade

echo "🏗️ Construction de l'image de base partagée (echo-python-base)..."
docker build -t echo-python-base:latest "$ECHO_ROOT/docker-python-base"

# --- 3. LANCEMENT DOCKER COMPOSE ---
echo "🎼 Démarrage de la Stack via Docker Compose (Projet: $COMPOSE_PROJECT_NAME)..."

BW_STACK_FILE="$ECHO_CONFIG/bunkerweb-stack.yml"
ENV_FILE="$ECHO_ENV_FILE"

export COMPOSE_DOCKER_CLI_BUILD=1
export DOCKER_BUILDKIT=1

export COMPOSE_PARALLEL_LIMIT=2

COMPOSE_ARGS=(--env-file "$ENV_FILE")
if [ -f "$BW_STACK_FILE" ] && has_active_echo_domain "$ENV_FILE"; then
    echo "🔒 Mode SECURE EDGE détecté. Lancement de l'infrastructure complète (ECHO + BunkerWeb)..."
    COMPOSE_ARGS+=(-f "$BW_STACK_FILE" -f "$COMPOSE_FILE")
else
    echo "🔓 Mode STANDARD (Local) détecté."
    COMPOSE_ARGS+=(-f "$COMPOSE_FILE")
fi

echo "🎨 Pré-construction isolée de echo-webui (protection mémoire V8 6 Go)..."
echo_compose "${COMPOSE_ARGS[@]}" build $PULL_FLAG echo-webui
echo_compose "${COMPOSE_ARGS[@]}" build $PULL_FLAG
cleanup_compose_staging_containers
echo_compose "${COMPOSE_ARGS[@]}" up -d --remove-orphans

if [ $? -eq 0 ]; then
    echo "✅ Stack Docker Compose active."
    WATCHTOWER_WAS_RUNNING=0
    trap - EXIT
else
    echo "❌ Erreur lors du lancement Docker Compose."
    exit 1
fi

echo "🧠 Provisioning déclaratif ECHO V6 assuré nativement par le lifespan de echo-webui."

docker image prune -f >/dev/null 2>&1

echo "✅ DEPLOIEMENT TERMINÉ."
echo "-----------------------------------------------------------"
echo "🌐 LOCAL UI    : http://IP-LOCALE:3000"
echo "🔧 LOCAL ADMIN : http://IP-LOCALE:3001"
echo "🔑 CREDENTIALS : Tapez 'echo-show-admin'"
echo "-----------------------------------------------------------"
echo "🛡️  POUR ACTIVER L'ACCÈS PUBLIC SÉCURISÉ (BunkerWeb) :"
echo "   Lancez la commande : enable-bunkerweb"
echo "-----------------------------------------------------------"
