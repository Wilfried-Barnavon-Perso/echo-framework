#!/bin/bash
# ==============================================================================
# SCRIPT : echo-globals.sh
# VERSION : 1.0
# AUTEUR : Wilfried BARNAVON
# ROLE : Centralisation des constantes de chemins pour le framework ECHO
# ==============================================================================

# Racine principale du framework
export ECHO_ROOT="/opt/ECHO"

# Sous-répertoires structurels
export ECHO_SCRIPTS="$ECHO_ROOT/echo-scripts"
export ECHO_CONFIG="$ECHO_ROOT/config"
export ECHO_SOURCE="$ECHO_ROOT/source"
export ECHO_IMAGES="$ECHO_ROOT/echo-images"

# Stockage et Environnement
export ECHO_SECRETS="$ECHO_ROOT/.secrets"
export ECHO_ENV_FILE="$ECHO_ROOT/.env"
export ECHO_VERSION_FILE="$ECHO_ROOT/ECHO_VERSION"

# Ressources Injectées (Open WebUI)
export ECHO_OWUI_LIBS="$ECHO_ROOT/owui-libs"
export ECHO_OWUI_TOOLS="$ECHO_ROOT/owui-tools"
export ECHO_OWUI_FILTERS="$ECHO_ROOT/owui-filters"
export ECHO_OWUI_PIPES="$ECHO_ROOT/owui-pipes"
export ECHO_OWUI_ACTIONS="$ECHO_ROOT/owui-actions"

# Dossiers Docker (Build Contexts)
export ECHO_DOCKER_ADMIN="$ECHO_ROOT/docker-admin-manager"
export ECHO_DOCKER_WORKER="$ECHO_ROOT/docker-python-worker"
export ECHO_DOCKER_BROWSER="$ECHO_ROOT/docker-browser-agent"
export ECHO_DOCKER_EMBEDDING="$ECHO_ROOT/docker-embedding-worker"

# Fichiers de contrôle
export ECHO_BRANCH_FILE="$ECHO_ROOT/ECHO_BRANCH"
export ECHO_DEPLOY_VER_FILE="$ECHO_ROOT/echo_deploy_script_version"

# Comptes Systèmes
export ECHO_SERVICE_ACCOUNT="install-stack@echo.local"

