#!/bin/bash
# ==============================================================================
# SCRIPT : update-echo.sh (VERSION LEGACY COMPOSE V1)
# VERSION : 6.3
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À JOUR RAPIDE (CODE ONLY) + HOT RELOAD
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

COMPOSE_FILE="$ECHO_CONFIG/stack-echo.yml"
SYNC_SCRIPT="$ECHO_SCRIPTS/sync-echo.sh"
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/${CURRENT_SCRIPT##*/}"
MY_OWN_ORIGIN="$ECHO_SCRIPTS/${CURRENT_SCRIPT##*/}"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"; chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"; exit 0
fi


echo "🚀 DÉMARRAGE MISE À JOUR RAPIDE (UPDATE)..."

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

# --- 2. HOT RELOAD ---
echo "⚡ [UPDATE] Redémarrage des services Python..."
CONTAINERS_TO_RELOAD=$(docker ps \
    --filter "label=echo.hot-reload=true" \
    --format "{{.Names}}" | tr '\n' ' ')
if [ -n "$CONTAINERS_TO_RELOAD" ]; then
    docker restart $CONTAINERS_TO_RELOAD
    echo "   ✅ Services rechargés : $CONTAINERS_TO_RELOAD"
else
    echo "   ⚠️  Aucun service marqué echo.hot-reload=true trouvé."
fi

# --- 3. CONFIG API ---
echo "🤖 [UPDATE] Configuration Open WebUI..."
if $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" ps -q open-webui >/dev/null 2>&1; then
    /bin/bash "$ECHO_SCRIPTS/config-owui.sh"
fi

echo "✨ UPDATE TERMINÉ."
