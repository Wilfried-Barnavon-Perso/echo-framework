#!/bin/bash
# ==============================================================================
# SCRIPT : upgrade-echo.sh (VERSION COMPOSE)
# VERSION : 5.6.1
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À NIVEAU MAJEURE (IMAGES + CODE)
# ==============================================================================

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/upgrade-echo-running.sh"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"; chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"; exit 0
fi

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"
BRANCH_FILE="/opt/ECHO_BRANCH"
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"

# Fixer le nom du projet
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# --- CONFIRMATION ---
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]'); fi

clear
echo "⚠️  UPGRADE MAJEUR via DOCKER COMPOSE"
echo "    Branche cible : $TARGET_BRANCH"
read -p "Tapez 'CONFIRMER' : " CONFIRM
[ "$CONFIRM" != "CONFIRMER" ] && exit 1

# --- 1. SYNC GITHUB ---
echo "🔄 [1/4] SYNC GITHUB..."
if [ ! -d "$SRC_DIR/.git" ]; then
    rm -rf "$SRC_DIR"; git clone "$GIT_REPO" "$SRC_DIR"
    cd "$SRC_DIR" || exit; git checkout "$TARGET_BRANCH"
else
    cd "$SRC_DIR" || exit; git fetch origin
    git reset --hard HEAD; git clean -fd
    git checkout "$TARGET_BRANCH"; git reset --hard "origin/$TARGET_BRANCH"
fi

# --- 2. DEPLOIEMENT FICHIERS ---
echo "📂 [2/4] DEPLOIEMENT..."
# Utilise la logique identique à update-echo (non répétée ici pour brièveté, mais conceptuellement la même)
cp -rf "$SRC_DIR/00-Install/." "/opt/owui-scripts/"
cp -rf "$SRC_DIR/04-OWUI-tools/." "/opt/owui-tools/"
cp -rf "$SRC_DIR/03-OWUI-pipes/." "/opt/owui-pipes/"
cp -rf "$SRC_DIR/07-OWUI-actions/." "/opt/owui-actions/"
cp -rf "$SRC_DIR/05-OWUI-filters/." "/opt/owui-filters/"
cp "$SRC_DIR/VERSION" "/opt/ECHO_VERSION"

# Clean Windows
find /opt/owui-scripts -type f -exec sed -i 's/\r$//' {} +
chmod +x /opt/owui-scripts/*.sh

# --- 3. DOCKER COMPOSE PULL ---
echo "🐳 [3/4] DOCKER COMPOSE PULL..."
if [ -f "$COMPOSE_FILE" ]; then
    # Télécharge les nouvelles images définies dans le YAML (si changées)
    docker-compose -f "$COMPOSE_FILE" pull
else
    echo "❌ Critique : docker-compose.yml introuvable après copie."
    exit 1
fi

# --- 4. REBUILD / RELAUNCH ---
echo "🚀 [4/4] RELAUNCH..."
# On délègue à install-stack.sh qui contient la logique "ensure volumes" + "compose up -d"
/bin/bash /opt/owui-scripts/install-stack.sh

echo "✨ UPGRADE TERMINÉ."