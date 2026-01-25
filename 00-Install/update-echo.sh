#!/bin/bash
# ==============================================================================
# SCRIPT : update-echo.sh (VERSION LEGACY COMPOSE V1)
# VERSION : 5.17
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À JOUR RAPIDE (CODE ONLY) + HOT RELOAD
# ==============================================================================

DOCKER_COMPOSE_CMD="docker-compose"
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"
SYNC_SCRIPT="/opt/owui-scripts/sync-echo.sh"
BW_SECRET_FILE="/opt/.bw-setting-secret"
BW_DB_SECRET_FILE="/opt/.bw-db-secret"
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

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
if [ -f "$BW_DB_SECRET_FILE" ]; then
    export BW_DB_PASSWORD=$(cat "$BW_DB_SECRET_FILE" | tr -d '[:space:]')
fi


echo "🚀 DÉMARRAGE MISE À JOUR RAPIDE (UPDATE)..."

# --- 1. SYNC & DEPLOY (Centralisé) ---
# Si le script sync existe déjà, on l'utilise. Sinon, on avertit.
# Note : Pour un premier déploiement, c'est install-stack.sh qui fait le travail.
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
echo "⚡ [UPDATE] Redémarrage des services Python (Hot Reload)..."
if [ -f "$COMPOSE_FILE" ]; then
    $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" restart admin-manager python-worker browser-agent
else
    docker restart echo-admin-manager echo-python-worker echo-browser-agent
fi

# --- 3. CONFIG API ---
echo "🤖 [UPDATE] Configuration Open WebUI..."
if $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" ps -q open-webui >/dev/null 2>&1; then
    /bin/bash /opt/owui-scripts/config-owui.sh
fi

echo "✨ UPDATE TERMINÉ."