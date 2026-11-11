#!/bin/bash
# ==============================================================================
# SCRIPT : echo-globals.sh
# VERSION : 6.0.0.2
# AUTEUR : Wilfried BARNAVON
# ROLE : Centralisation des constantes de chemins pour le framework ECHO
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.0.0.2: Remplacement de ECHO_SERVICE_ACCOUNT par ECHO_ADMIN_EMAIL et synchronisation bidirectionnelle .owui-admin-secret <-> ECHO_ADMIN_PASSWORD.
# 6.0.0.1: Ajout de la constante ECHO_DOCKER_WEBUI pour le service docker-echo-webui.
# 5.236.5.1: Ajout de la constante ECHO_DOCKER_USER pour le service 27-docker-user-manager.
# 5.231.21.2: Factorisation de generate_secret (sans SIGPIPE sous pipefail) et ensure_echo_secrets incluant ECHO_VAULT_ENCRYPTION_KEY (64 chars).
# 5.226.18.1: Ajout de la constante ECHO_OWUI_EVENTS pour le sous-système 15-owui-events.

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
export ECHO_OWUI_EVENTS="$ECHO_ROOT/owui-events"

# Dossiers Docker (Build Contexts)
export ECHO_DOCKER_WEBUI="$ECHO_ROOT/docker-echo-webui"
export ECHO_DOCKER_ADMIN="$ECHO_ROOT/docker-admin-manager"
export ECHO_DOCKER_USER="$ECHO_ROOT/docker-user-manager"
export ECHO_DOCKER_WORKER="$ECHO_ROOT/docker-python-worker"
export ECHO_DOCKER_BROWSER="$ECHO_ROOT/docker-browser-agent"
export ECHO_DOCKER_EMBEDDING="$ECHO_ROOT/docker-embedding-worker"

# Fichiers de contrôle
export ECHO_BRANCH_FILE="$ECHO_ROOT/ECHO_BRANCH"
export ECHO_DEPLOY_VER_FILE="$ECHO_ROOT/echo_deploy_script_version"

# Comptes Systèmes
export ECHO_ADMIN_EMAIL="admin@echo.local"

# --- NETWORKING (SOUS-RÉSEAUX IP) ---
export ECHO_NET_BW_UNIVERSE_PREFIX="10.20.30"
export ECHO_NET_MAIN_PREFIX="10.20.40"
export ECHO_NET_SANDBOX_PREFIX="10.20.50"

# --- GESTION CENTRALISÉE DES SECRETS (.env) ---
generate_secret() {
    local key=$1
    local length=$2
    local target_env="${ENV_FILE:-$ECHO_ENV_FILE}"
    (umask 077 && touch "$target_env")
    chmod 600 "$target_env"
    if ! grep -qE "^${key}=[^\"'[:space:]]+" "$target_env" 2>/dev/null; then
        local secret
        secret=$(dd if=/dev/urandom bs=512 count=1 2>/dev/null | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c "$length")
        sed -i "/^${key}=/d" "$target_env" 2>/dev/null || true
        echo "${key}=${secret}" >> "$target_env"
        echo "   🔑 Génération du secret : $key"
    fi
}

ensure_echo_secrets() {
    generate_secret "BW_DB_PASSWORD" 24
    generate_secret "SEARXNG_SECRET" 64
    generate_secret "ECHO_SSO_SECRET" 32
    generate_secret "N8N_ENCRYPTION_KEY" 64
    generate_secret "ECHO_VAULT_ENCRYPTION_KEY" 64

    # Synchronisation bidirectionnelle idempotente (.owui-admin-secret <-> ECHO_ADMIN_PASSWORD)
    # Préserve le secret existant en migration V5->V6 pour 20-docker-admin-manager et echo-show-admin.sh
    local target_env="${ENV_FILE:-$ECHO_ENV_FILE}"
    local admin_secret_file="$ECHO_SECRETS/.owui-admin-secret"
    if [ -s "$admin_secret_file" ] && ! grep -qE "^ECHO_ADMIN_PASSWORD=[^\"'[:space:]]+" "$target_env" 2>/dev/null; then
        local existing_admin_pwd
        existing_admin_pwd=$(tr -d '[:space:]' < "$admin_secret_file")
        sed -i "/^ECHO_ADMIN_PASSWORD=/d" "$target_env" 2>/dev/null || true
        echo "ECHO_ADMIN_PASSWORD=${existing_admin_pwd}" >> "$target_env"
    else
        generate_secret "ECHO_ADMIN_PASSWORD" 16
    fi
    local admin_pwd
    admin_pwd=$(grep "^ECHO_ADMIN_PASSWORD=" "$target_env" 2>/dev/null | cut -d '=' -f2- || true)
    if [ -n "$admin_pwd" ] && [ ! -s "$admin_secret_file" ]; then
        mkdir -p "$ECHO_SECRETS"
        (umask 077 && printf "%s" "$admin_pwd" > "$admin_secret_file")
        chmod 400 "$admin_secret_file"
    fi
}
