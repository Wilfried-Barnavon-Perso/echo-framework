#!/bin/bash
# ==============================================================================
# SCRIPT : ps-echo.sh
# VERSION : 5.226.18.0
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : Afficher l'état consolidé de tous les conteneurs de la stack ECHO
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.0: Alignement sur le versioning global ECHO.

ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ]; then source "$GLOBALS_FILE"; fi

DOCKER_COMPOSE_CMD="docker-compose"
if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then DOCKER_COMPOSE_CMD="docker compose"; fi

COMPOSE_FILE="${ECHO_CONFIG:-$ECHO_ROOT/config}/stack-echo.yml"
export COMPOSE_PROJECT_NAME="echo"

echo "📊 Statut des services ECHO :"
if [ -f "$COMPOSE_FILE" ]; then
    $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" ps
else
    docker ps -a --filter "name=^/?echo-"
fi
