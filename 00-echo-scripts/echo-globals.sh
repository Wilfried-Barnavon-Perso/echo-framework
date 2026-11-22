#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : echo-globals.sh
# VERSION : 6.18.15.1
# AUTEUR : Wilfried BARNAVON
# ROLE : Centralisation des constantes de chemins pour le framework ECHO
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.15.1: Factorisation détection WSL2/systemd, init_echo_compose_cmd, echo_compose, build_echo_compose_args, has_active_echo_domain, detect_echo_webui_port, compute_echo_detected_origins, restart_docker_daemon_and_wait.
# 6.0.0.2: Remplacement de ECHO_SERVICE_ACCOUNT par ECHO_ADMIN_EMAIL et synchronisation bidirectionnelle .owui-admin-secret <-> ECHO_ADMIN_PASSWORD.
# 6.0.0.1: Ajout de la constante ECHO_DOCKER_WEBUI pour le service docker-echo-webui.
# 5.236.5.1: Ajout de la constante ECHO_DOCKER_USER pour le service 27-docker-user-manager.
# 5.231.21.2: Factorisation de generate_secret (sans SIGPIPE sous pipefail) et ensure_echo_secrets incluant ECHO_VAULT_ENCRYPTION_KEY (64 chars).

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

# --- DÉTECTION ENVIRONNEMENT & MOTEUR DOCKER COMPOSE (SSOT) ---
is_wsl2() {
    grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

is_systemd_active() {
    [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]
}

has_active_echo_domain() {
    local target_env="${1:-${ENV_FILE:-$ECHO_ENV_FILE}}"
    [ -f "$target_env" ] && [ -r "$target_env" ] || return 1
    local domain_val
    domain_val=$(grep -E '^ECHO_DOMAIN=' "$target_env" 2>/dev/null | tail -n 1 | cut -d '=' -f2- | tr -d "\"'[:space:]" || true)
    [ -n "$domain_val" ]
}

ECHO_COMPOSE_CMD=()
DOCKER_COMPOSE_CMD=""
init_echo_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        ECHO_COMPOSE_CMD=("docker" "compose")
    elif command -v docker-compose >/dev/null 2>&1; then
        ECHO_COMPOSE_CMD=("docker-compose")
    else
        echo "❌ CRITIQUE : Ni 'docker compose' (V2) ni 'docker-compose' (V1) n'est installé." >&2
        return 1
    fi
    DOCKER_COMPOSE_CMD="${ECHO_COMPOSE_CMD[*]}"
    export DOCKER_COMPOSE_CMD
}

echo_compose() {
    [ "${#ECHO_COMPOSE_CMD[@]}" -gt 0 ] || init_echo_compose_cmd
    "${ECHO_COMPOSE_CMD[@]}" "$@"
}

ECHO_COMPOSE_ARGS=()
build_echo_compose_args() {
    local target_env="${ENV_FILE:-$ECHO_ENV_FILE}"
    local bw_file="${BW_STACK_FILE:-$ECHO_CONFIG/bunkerweb-stack.yml}"
    local stack_file="${COMPOSE_FILE:-$ECHO_CONFIG/stack-echo.yml}"
    ECHO_COMPOSE_ARGS=()
    if [ -f "$target_env" ] && [ -r "$target_env" ]; then
        ECHO_COMPOSE_ARGS+=(--env-file "$target_env")
    fi
    if [ -f "$bw_file" ] && has_active_echo_domain "$target_env"; then
        ECHO_COMPOSE_ARGS+=(-f "$bw_file" -f "$stack_file")
    else
        ECHO_COMPOSE_ARGS+=(-f "$stack_file")
    fi
}

detect_echo_webui_port() {
    local compose_file="${1:-$ECHO_CONFIG/stack-echo.yml}"
    local port=""
    if [ -f "$compose_file" ]; then
        if command -v yq >/dev/null 2>&1; then
            port=$(yq '.services["echo-webui"].ports[0] // ""' "$compose_file" 2>/dev/null | tr -d "\"'[:space:]" | cut -d: -f1 || true)
        fi
        if [[ ! "$port" =~ ^[0-9]+$ ]]; then
            port=$(awk '
                /^  echo-webui:/ { in_svc=1; next }
                in_svc && /^  [a-zA-Z0-9_-]+:/ { in_svc=0 }
                in_svc && /^[[:space:]]*ports:/ { in_ports=1; next }
                in_ports && /^[[:space:]]*[a-zA-Z0-9_-]+:/ { in_ports=0 }
                in_ports && !/^[[:space:]]*#/ && match($0, /[0-9]+:[0-9]+/) {
                    s = substr($0, RSTART, RLENGTH)
                    sub(/:.*/, "", s)
                    print s
                    exit
                }
            ' "$compose_file" 2>/dev/null || true)
        fi
    fi
    [[ "$port" =~ ^[0-9]+$ ]] || port="3000"
    printf '%s' "$port"
}

compute_echo_detected_origins() {
    local compose_file="${1:-$ECHO_CONFIG/stack-echo.yml}"
    local owui_port
    owui_port=$(detect_echo_webui_port "$compose_file")
    local host_ips
    host_ips=$(hostname -I 2>/dev/null || ip -4 addr show 2>/dev/null | awk '/inet / && $2 !~ /^127\./ {sub(/\/.*/, "", $2); print $2}' || true)
    local origins=""
    local ip
    for ip in $host_ips; do
        if [ -z "$origins" ]; then
            origins="http://${ip}:${owui_port}"
        else
            origins="${origins};http://${ip}:${owui_port}"
        fi
    done
    export OWUI_PORT="$owui_port"
    export ECHO_DETECTED_ORIGINS="$origins"
}

restart_docker_daemon_and_wait() {
    if is_systemd_active && command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet docker; then
        systemctl restart docker
    elif command -v service >/dev/null 2>&1 && service docker status >/dev/null 2>&1; then
        service docker restart || true
    elif pgrep -x dockerd >/dev/null 2>&1; then
        pkill -TERM -x dockerd 2>/dev/null || true
        local w=0
        while pgrep -x dockerd >/dev/null 2>&1 && [ "$w" -lt 10 ]; do
            sleep 1
            w=$((w + 1))
        done
        nohup dockerd > /var/log/dockerd.log 2>&1 &
    fi
    local retries=30
    until docker info >/dev/null 2>&1; do
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
            echo "❌ CRITIQUE : Le démon Docker ne répond plus après redémarrage." >&2
            return 1
        fi
        sleep 2
    done
}
