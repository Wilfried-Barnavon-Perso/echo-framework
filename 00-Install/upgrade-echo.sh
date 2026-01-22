#!/bin/bash
# ==============================================================================
# SCRIPT : upgrade-echo.sh (VERSION LEGACY COMPOSE V1)
# VERSION : 5.10
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À NIVEAU MAJEURE (IMAGES DOCKER + CODE + RECREATION CONTAINERS)
# ==============================================================================

DOCKER_COMPOSE_CMD="docker-compose"
SYNC_SCRIPT="/opt/owui-scripts/sync-echo.sh"
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"
export COMPOSE_PROJECT_NAME="echo"

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/upgrade-echo-running.sh"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"; chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"; exit 0
fi

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# --- CONFIRMATION ---
BRANCH_FILE="/opt/ECHO_BRANCH"
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]'); fi

clear
echo "⚠️  UPGRADE MAJEUR via DOCKER COMPOSE (LEGACY V1)"
echo "    Cette opération va :"
echo "    1. Synchroniser le code et écraser les modifications locales"
echo "    2. Télécharger les dernières images Docker"
echo "    3. Redémarrer toute la stack"
echo "    Branche cible : $TARGET_BRANCH"
echo ""
read -p "Tapez 'CONFIRMER' : " CONFIRM
[ "$CONFIRM" != "CONFIRMER" ] && exit 1

# --- 1. SYNC & DEPLOY (Centralisé) ---
if [ -f "$SYNC_SCRIPT" ]; then
    /bin/bash "$SYNC_SCRIPT" || exit 1
else
    # Fallback critique : Si sync n'est pas là, on tente de le récupérer manuellement depuis le repo
    echo "⚠️  Script sync introuvable. Tentative de récupération manuelle..."
    SRC_DIR="/opt/echo-framework-source"
    if [ ! -d "$SRC_DIR/.git" ]; then
        git clone "https://github.com/Wilfried-Barnavon-Perso/echo-framework.git" "$SRC_DIR"
    fi
    cd "$SRC_DIR" || exit
    git fetch origin
    git reset --hard "origin/$TARGET_BRANCH"
    
    # Copie minimale pour avoir le sync
    mkdir -p "/opt/owui-scripts"
    cp "$SRC_DIR/00-Install/sync-echo.sh" "$SYNC_SCRIPT"
    chmod +x "$SYNC_SCRIPT"
    
    # Exécution du sync maintenant qu'on l'a
    /bin/bash "$SYNC_SCRIPT" || exit 1
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