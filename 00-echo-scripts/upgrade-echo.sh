#!/bin/bash
# ==============================================================================
# SCRIPT : upgrade-echo.sh (VERSION LEGACY COMPOSE V1)
# VERSION : 6.8
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À NIVEAU MAJEURE (IMAGES DOCKER + CODE + RECREATION CONTAINERS)
# ==============================================================================

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

DOCKER_COMPOSE_CMD="docker-compose"
if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
fi

SYNC_SCRIPT="$ECHO_SCRIPTS/sync-echo.sh"
COMPOSE_FILE="$ECHO_CONFIG/stack-echo.yml"
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

clear

if [ "$0" == "/usr/local/bin/rebuild-echo" ] ; then
    echo "⚠️  RECONSTRUCTION COMPLETE DE LA STACK ECHO"
    echo "    Cette opération va :"
    echo "    1. Arrêter tous les services de la stack"
    echo "    2. Supprimer TOUS LES VOLUMES des conteneurs"
    echo "    3. Supprimer toutes les images dockerfile"
    echo "    4. Supprimer les fichiers secrets locaux (Reset Auth)"
    echo ""
    read -p "Tapez 'CONFIRMER' : " CONFIRM
    [ "$CONFIRM" != "CONFIRMER" ] && exit 1
    export CONFIRMED_YET="yes"
    docker stop $(docker ps -aq) > /dev/null 2>&1 && echo "Services arrêtés"
    docker rm $(docker ps -aq) > /dev/null 2>&1 && echo "Conteneurs supprimés"
    docker volume rm $(docker volume ls -q) > /dev/null 2>&1 && echo "Volumes actifs supprimés"
    docker system prune -a --volumes -f > /dev/null 2>&1 && echo "Volumes orphelins et images supprimé"
    rm -rf "$ECHO_SECRETS" && echo "Fichiers secrets supprimés"
fi

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/${CURRENT_SCRIPT##*/}"
MY_OWN_ORIGIN="$ECHO_SCRIPTS/${CURRENT_SCRIPT##*/}"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"; chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"; exit 0
fi

# --- CONFIRMATION ---
BRANCH_FILE="$ECHO_BRANCH_FILE"
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]'); fi

echo "⚠️  UPGRADE MAJEUR via DOCKER-COMPOSE"
echo "    Cette opération va :"
echo "    1. Synchroniser le code et écraser les modifications locales"
echo "    2. Lancer le redéploiement complet de la stack (pull, build, up)"
echo "    Branche cible : $TARGET_BRANCH"
echo ""
[ -v CONFIRMED_YET ] || { 
        read -p "Tapez 'CONFIRMER' : " CONFIRM
        [ "$CONFIRM" != "CONFIRMER" ] && exit 1
}

# --- 1. SYNC & DEPLOY (Centralisé) ---
if [ -f "$SYNC_SCRIPT" ]; then
    /bin/bash "$SYNC_SCRIPT" || exit 1
else
    echo "⚠️  Script de sync introuvable ($SYNC_SCRIPT). Installation corrompue ?"
    exit 1
fi

# RELANCE DU SCRIPT SI MIS A JOUR
if ! diff "$MY_OWN_ORIGIN"  "$CURRENT_SCRIPT" > /dev/null 2>&1  ; then
    exec "$MY_OWN_ORIGIN" "$@"; exit 0
fi

# --- 2. DELEGATION AU LAUNCHER ---
# --- 3. REBUILD / RELAUNCH ---
echo "🚀 [UPGRADE] Relance de la stack (via install-stack.sh)..."
/bin/bash "$ECHO_SCRIPTS/install-stack.sh"

echo "✨ UPGRADE TERMINÉ."
