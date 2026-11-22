#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : echo-logs.sh
# VERSION : 6.18.15.1
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : Suivre les journaux (logs) en temps réel de la stack ou d'un service
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.15.1: Alignement sur init_echo_compose_cmd, build_echo_compose_args et echo_compose.
# 5.231.42.1: Passage explicite de --env-file "$ECHO_ENV_FILE" à docker compose pour supprimer les warnings de variables non définies.
# 5.231.39.1: Renommage de logs-echo.sh en echo-logs.sh.
# 5.226.18.0: Alignement sur le versioning global ECHO.

ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ]; then
    source "$GLOBALS_FILE"
else
    echo "❌ CRITIQUE : Fichier global introuvable ($GLOBALS_FILE)." >&2
    exit 1
fi

export COMPOSE_PROJECT_NAME="echo"
init_echo_compose_cmd || exit 1
build_echo_compose_args

if [ $# -eq 0 ]; then
    echo "📜 Affichage des journaux de toute la stack ECHO (Ctrl+C pour quitter)..."
    echo_compose "${ECHO_COMPOSE_ARGS[@]}" logs -f --tail=100
else
    echo "📜 Affichage des journaux pour : $* (Ctrl+C pour quitter)..."
    echo_compose "${ECHO_COMPOSE_ARGS[@]}" logs -f --tail=100 "$@"
fi
