#!/bin/bash
# ==============================================================================
# SCRIPT : upgrade-echo.sh (VERSION LEGACY COMPOSE V1)
# VERSION : 5.16
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À NIVEAU MAJEURE (IMAGES DOCKER + CODE + RECREATION CONTAINERS)
# ==============================================================================

DOCKER_COMPOSE_CMD="docker-compose"
SYNC_SCRIPT="/opt/owui-scripts/sync-echo.sh"
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"
BW_SECRET_FILE="/opt/.bw-setting-secret"
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

if [ "$0" == "/usr/local/bin/rebuild-echo" ] ; then
    clear
    echo "⚠️  RECONSTRUCTION COMPLETE DE LA STACK ECHO"
    echo "    Cette opération va :"
    echo "    1. Arrêter tous les services de la stack"
    echo "    2. Supprimer TOUS LES VOLUMES des conteneurs"
    echo "    3. Supprimer toutes les images dockerfile"
    echo ""
    read -p "Tapez 'CONFIRMER' : " CONFIRM
    [ "$CONFIRM" != "CONFIRMER" ] && exit 1
    export CONFIRMED_YET="yes"
    docker stop $(docker ps -aq) > /dev/null 2>&1 && echo "Services arrêtés"
    docker volume rm $(docker volume ls -q) > /dev/null 2>&1 && echo "Volumes actifs supprimés"
    docker system prune -a --volumes -f > /dev/null 2>&1 && echo "Volumes orphelins et images supprimé"
fi

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/${CURRENT_SCRIPT##*/}"
MY_OWN_ORIGIN="/opt/owui-scripts/${CURRENT_SCRIPT##*/}"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"; chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"; exit 0
fi

# --- 0. CHARGEMENT DES SECRETS (Pour éviter les warnings Docker Compose) ---
if [ -f "$BW_SECRET_FILE" ]; then
    export BW_PASSWORD=$(cat "$BW_SECRET_FILE" | tr -d '[:space:]')
fi


# --- CONFIRMATION ---
BRANCH_FILE="/opt/ECHO_BRANCH"
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]'); fi

clear
echo "⚠️  UPGRADE MAJEUR via DOCKER-COMPOSE"
echo "    Cette opération va :"
echo "    1. Synchroniser le code et écraser les modifications locales"
echo "    2. Télécharger les dernières images Docker"
echo "    3. Redémarrer toute la stack"
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

# --- 2. DOCKER COMPOSE PULL ---
echo "🐳 [UPGRADE] Téléchargement des images Docker..."
if [ -f "$COMPOSE_FILE" ]; then
    $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" pull
else
    echo "❌ Critique : docker-compose.yml introuvable."
    exit 1
fi

# --- 3. REBUILD / RELAUNCH ---
echo "🚀 [UPGRADE] Relance de la stack (via install-stack.sh)..."
/bin/bash /opt/owui-scripts/install-stack.sh

echo "✨ UPGRADE TERMINÉ."