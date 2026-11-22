#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : echo-ps.sh
# VERSION : 6.18.15.1
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : Afficher l'état consolidé de tous les conteneurs de la stack ECHO
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.15.1: Alignement sur init_echo_compose_cmd, build_echo_compose_args et echo_compose.
# 5.231.42.1: Passage explicite de --env-file "$ECHO_ENV_FILE" à docker compose pour supprimer les warnings de variables non définies.
# 5.231.39.1: Renommage de ps-echo.sh en echo-ps.sh.
# 5.226.18.0: Alignement sur le versioning global ECHO.

ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ]; then
    source "$GLOBALS_FILE"
fi

COMPOSE_FILE="${ECHO_CONFIG:-$ECHO_ROOT/config}/stack-echo.yml"
export COMPOSE_PROJECT_NAME="echo"

echo "📊 Statut des services ECHO :"
if [ -f "$COMPOSE_FILE" ] && declare -F init_echo_compose_cmd >/dev/null 2>&1 && init_echo_compose_cmd 2>/dev/null; then
    build_echo_compose_args
    echo_compose "${ECHO_COMPOSE_ARGS[@]}" ps
else
    docker ps -a --filter "name=^/?echo-"
fi
