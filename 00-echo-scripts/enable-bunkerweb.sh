#!/bin/bash
# ==============================================================================
# SCRIPT : enable-bunkerweb.sh
# VERSION : 4.0
# AUTEUR : Wilfried BARNAVON (ECHO Framework)
# ==============================================================================
# ROLE : Activation de la couche de sécurité BunkerWeb (Secure Edge).
# CHANGELOG 4.0 : set -euo pipefail, idempotence (détection BW déjà actif),
#                 validation format domaine, meilleur feedback post-déploiement.
# ==============================================================================

set -euo pipefail

# --- INITIALISATION : CORE ECHO GLOBALS ---
ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ]; then
    source "$GLOBALS_FILE"
else
    echo "❌ CRITIQUE : Fichier global introuvable ($GLOBALS_FILE)."
    exit 1
fi
# ------------------------------------------

export COMPOSE_PROJECT_NAME="echo"

CONFIG_DIR="$ECHO_CONFIG"
ENV_FILE="$ECHO_ENV_FILE"
BW_STACK_FILE="$CONFIG_DIR/bunkerweb-stack.yml"
ECHO_STACK_FILE="$CONFIG_DIR/stack-echo.yml"

# Détection automatique du moteur Compose (V1 vs V2)
DOCKER_COMPOSE_CMD="docker-compose"
if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
fi

# Vérification root
if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

clear
echo "=================================================="
echo "🛡️  ECHO SECURE EDGE (Standard Edition v4.0)"
echo "=================================================="
echo ""

# --- 1. CONFIGURATION DOMAINE ---
DOMAIN=""
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --domain) DOMAIN="$2"; shift ;;
        *) echo "Paramètre inconnu : $1"; exit 1 ;;
    esac
    shift
done

if [ -z "$DOMAIN" ]; then
    DEFAULT_DOMAIN=""
    if [ -f "$ENV_FILE" ]; then
        DEFAULT_DOMAIN=$(grep "^ECHO_DOMAIN=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2 || true)
    fi

    echo "Architecture cible :"
    echo " - IA    : https://ui.DOMAINE"
    echo " - Admin : https://am.DOMAINE"
    echo ""
    read -r -p "Entrez votre domaine racine [${DEFAULT_DOMAIN:-echo-ai.eu}] : " INPUT_DOMAIN
    DOMAIN="${INPUT_DOMAIN:-${DEFAULT_DOMAIN:-echo-ai.eu}}"
fi

# Validation basique du format du domaine (évite les injections dans .env)
if ! echo "$DOMAIN" | grep -qE '^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'; then
    echo "❌ Format de domaine invalide : '$DOMAIN'"
    echo "   Attendu : ex. echo-ai.eu, mon-domaine.com"
    exit 1
fi

echo "🚀 Configuration pour : $DOMAIN"

# --- 2. IDEMPOTENCE : Détection BunkerWeb déjà actif ---
BW_RUNNING=$(docker ps --filter "name=bunkerweb" --filter "status=running" --format "{{.Names}}" 2>/dev/null | head -1 || true)
CURRENT_DOMAIN=""
if [ -f "$ENV_FILE" ]; then
    CURRENT_DOMAIN=$(grep "^ECHO_DOMAIN=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2 || true)
fi

if [ -n "$BW_RUNNING" ] && [ "$CURRENT_DOMAIN" = "$DOMAIN" ]; then
    echo ""
    echo "ℹ️  BunkerWeb est déjà actif pour le domaine '$DOMAIN'."
    echo "   Aucun redémarrage nécessaire."
    echo ""
    read -r -p "Forcer la reconfiguration complète ? (y/N) : " FORCE
    if [[ "$FORCE" != "y" && "$FORCE" != "Y" ]]; then
        echo "✅ Reconfiguration Open WebUI uniquement..."
        if [ -f "$ECHO_SCRIPTS/config-owui.sh" ]; then
            /bin/bash "$ECHO_SCRIPTS/config-owui.sh"
        fi
        exit 0
    fi
fi

# --- 3. DÉTECTION CORS & IP ---
echo "🌍 Calcul des origines CORS locales..."
OWUI_PORT=$(grep -A 10 "open-webui:" "$ECHO_STACK_FILE" | grep -m 1 "\- \"[0-9]*:[0-9]*\"" | cut -d'"' -f2 | cut -d: -f1)
if [ -z "$OWUI_PORT" ]; then OWUI_PORT="3000"; fi
HOST_IPS=$(hostname -I 2>/dev/null || ip addr show | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | cut -d/ -f1)
ECHO_DETECTED_ORIGINS=""
for ip in $HOST_IPS; do
    if [ -z "$ECHO_DETECTED_ORIGINS" ]; then
        ECHO_DETECTED_ORIGINS="http://$ip:$OWUI_PORT"
    else
        ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS;http://$ip:$OWUI_PORT"
    fi
done

# --- 4. MISE À JOUR .ENV ---
echo "📝 Mise à jour du fichier d'environnement centralisé ($ENV_FILE)..."

touch "$ENV_FILE" && chmod 600 "$ENV_FILE"

update_env() {
    local key=$1
    local value=$2
    if grep -q "^$key=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^$key=.*|$key=$value|" "$ENV_FILE"
    else
        echo "$key=$value" >> "$ENV_FILE"
    fi
}

update_env "ECHO_DOMAIN" "$DOMAIN"
update_env "TZ" "Europe/Paris"
update_env "ECHO_DETECTED_ORIGINS" "$ECHO_DETECTED_ORIGINS"

# Génération des secrets si absents
generate_secret() {
    local key=$1
    local length=$2
    if ! grep -q "^$key=" "$ENV_FILE" 2>/dev/null; then
        local secret
        secret=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$length")
        echo "$key=$secret" >> "$ENV_FILE"
        echo "   🔑 Génération du secret : $key"
    fi
}

generate_secret "BW_DB_PASSWORD" 24
generate_secret "SEARXNG_SECRET" 64

export ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS"

# --- 5. LANCEMENT UNIFIÉ ---
echo "🐳 Redémarrage de l'infrastructure..."

# Création préventive du réseau et du répertoire BunkerWeb (bind mount bw-data)
docker network create echo-network 2>/dev/null || true
mkdir -p "$ECHO_ROOT/bunkerweb"

# Arrêt propre (ciblé sur le projet echo)
$DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" -f "$BW_STACK_FILE" -f "$ECHO_STACK_FILE" down --remove-orphans

# Lancement (sans --quiet pour voir les erreurs critiques)
$DOCKER_COMPOSE_CMD \
    --env-file "$ENV_FILE" \
    -f "$BW_STACK_FILE" \
    -f "$ECHO_STACK_FILE" \
    up -d --build --remove-orphans

# --- 6. RECONFIGURATION OPEN WEBUI ---
echo "🔧 Reconfiguration des paramètres internes d'Open WebUI (CORS, URLs)..."
if [ -f "$ECHO_SCRIPTS/config-owui.sh" ]; then
    /bin/bash "$ECHO_SCRIPTS/config-owui.sh"
    echo "   ✅ Reconfiguration terminée."
else
    echo "   ⚠️  Script 'config-owui.sh' non trouvé ($ECHO_SCRIPTS/config-owui.sh)."
fi

# --- 7. HOT RELOAD (Services Python avec code en bind mount) ---
echo "⚡ Hot Reload des services Python..."
CONTAINERS_TO_RELOAD=$(docker ps \
    --filter "label=echo.hot-reload=true" \
    --format "{{.Names}}" | tr '\n' ' ')
if [ -n "$CONTAINERS_TO_RELOAD" ]; then
    docker restart $CONTAINERS_TO_RELOAD
    echo "   ✅ Services rechargés : $CONTAINERS_TO_RELOAD"
else
    echo "   ⚠️  Aucun service marqué echo.hot-reload=true trouvé."
fi

# --- 8. FEEDBACK ÉTAT POST-DÉPLOIEMENT ---
echo ""
echo "📊 État des conteneurs :"
$DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" \
    -f "$BW_STACK_FILE" -f "$ECHO_STACK_FILE" \
    ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || docker ps --format "table {{.Names}}\t{{.Status}}" --filter "label=bunkerweb.INSTANCE=yes" || true

echo ""
echo "✅ INSTALLATION TERMINÉE !"
echo "-----------------------------------------------------------"
echo "🤖 UI IA     : https://ui.$DOMAIN"
echo "🔧 ADMIN     : https://am.$DOMAIN"
echo "-----------------------------------------------------------"
echo "⏳ Le WAF va demander vos certificats SSL (1-2 min)."
echo "-----------------------------------------------------------"
