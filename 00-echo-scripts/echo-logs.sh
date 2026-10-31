#!/bin/bash
# ==============================================================================
# SCRIPT : logs-echo.sh
# VERSION : 5.226.18.0
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : Suivre les journaux (logs) en temps réel de la stack ou d'un service
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

if [ -z "$1" ]; then
    echo "📜 Affichage des journaux de toute la stack ECHO (Ctrl+C pour quitter)..."
    $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" logs -f --tail=100
else
    echo "📜 Affichage des journaux pour : $1 (Ctrl+C pour quitter)..."
    $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" logs -f --tail=100 "$1"
fi
