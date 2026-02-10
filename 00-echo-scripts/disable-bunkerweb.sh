#!/bin/bash
# ==============================================================================
# SCRIPT : disable-bunkerweb.sh
# VERSION : 1.2
# AUTEUR : Wilfried BARNAVON (ECHO Framework)
# ==============================================================================
# ROLE : Désactivation de la couche de sécurité BunkerWeb (Secure Edge)
#        et retour au mode d'accès local direct (HTTP).
# ==============================================================================

export COMPOSE_PROJECT_NAME="echo"

CONFIG_DIR="/opt/config"
BW_DATA_DIR="/opt/bunkerweb"
ENV_FILE="$BW_DATA_DIR/.env"
BW_STACK_FILE="$CONFIG_DIR/bunkerweb-stack.yml"
ECHO_STACK_FILE="$CONFIG_DIR/stack-echo.yml"
DOCKER_COMPOSE_CMD="docker-compose"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
fi

echo "=================================================="
echo "🔓 ECHO SECURITY DISABLE"
echo "=================================================="
echo "Ce script va désactiver le WAF et le HTTPS."
echo "Vos applications seront accessibles uniquement en LOCAL (HTTP)."
echo ""
read -p "Confirmer la désactivation ? (y/N) : " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then echo "Annulé."; exit 0; fi

# 1. Arrêt complet de la stack unifiée
echo "🛑 Arrêt de l'infrastructure sécurisée..."
if [ -f "$ENV_FILE" ]; then
    $DOCKER_COMPOSE_CMD \
        --env-file "$ENV_FILE" \
        -f "$BW_STACK_FILE" \
        -f "$ECHO_STACK_FILE" \
        down --remove-orphans || true
else
    # Fallback si le .env a déjà disparu
    $DOCKER_COMPOSE_CMD \
        -f "$BW_STACK_FILE" \
        -f "$ECHO_STACK_FILE" \
        down --remove-orphans || true
fi

# Nettoyage agressif pour éviter le bug KeyError: 'ContainerConfig' de docker-compose 1.29
echo "🧹 Nettoyage préventif des conteneurs..."
docker rm -f echo-webui-core echo-admin-manager echo-python-worker echo-browser-agent echo-watchtower bunkerweb bw-autoconf bw-scheduler bw-docker bw-db 2>/dev/null || true

# 2. Désactivation de la configuration (Renommage .env)
if [ -f "$ENV_FILE" ]; then
    echo "📦 Archivage de la configuration (.env -> .env.bak)..."
    mv "$ENV_FILE" "$ENV_FILE.bak"
fi

# 3. Relance en mode Local (Stack ECHO seule)
echo "🚀 Redémarrage en mode Local..."
$DOCKER_COMPOSE_CMD -f "$ECHO_STACK_FILE" up -d --remove-orphans

echo ""
echo "✅ SÉCURITÉ DÉSACTIVÉE."
echo "-----------------------------------------------------------"
echo "🌐 LOCAL UI    : http://IP-LOCALE:3000"
echo "🔧 LOCAL ADMIN : http://IP-LOCALE:3001"
echo "-----------------------------------------------------------"
echo "⚠️  L'accès HTTPS est coupé. Le WAF est éteint."
echo "-----------------------------------------------------------"
