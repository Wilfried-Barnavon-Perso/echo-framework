#!/bin/bash
# ==============================================================================
# SCRIPT : update-echo.sh (VERSION LEGACY COMPOSE V1)
# VERSION : 5.10
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À JOUR RAPIDE (CODE ONLY) + HOT RELOAD
# ==============================================================================

DOCKER_COMPOSE_CMD="docker-compose"
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"
SYNC_SCRIPT="/opt/owui-scripts/sync-echo.sh"
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

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