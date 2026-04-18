#!/bin/bash
# ==============================================================================
# SCRIPT : enable-bunkerweb.sh
# VERSION : 3.4
# AUTEUR : Wilfried BARNAVON (ECHO Framework)
# ==============================================================================
# ROLE : Activation de la couche de sécurité BunkerWeb (Secure Edge).
# ==============================================================================

export COMPOSE_PROJECT_NAME="echo"

CONFIG_DIR="/opt/config"
# Centralisation du .env à la racine de /opt pour survie aux updates
ENV_FILE="/opt/.env"
BW_STACK_FILE="$CONFIG_DIR/bunkerweb-stack.yml"
ECHO_STACK_FILE="$CONFIG_DIR/stack-echo.yml"
DOCKER_COMPOSE_CMD="docker-compose"

# Vérification root
if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# Vérification Docker Compose
if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
fi

# Initialisation du .env si absent
touch "$ENV_FILE" && chmod 600 "$ENV_FILE"

clear
echo "=================================================="
echo "🛡️  ECHO SECURE EDGE (Standard Edition v3.4)"
echo "=================================================="
echo "Ce script configure votre domaine pour l'accès HTTPS."
echo ""

# --- 1. CONFIGURATION DOMAINE ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --domain) DOMAIN="$2"; shift ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "$DOMAIN" ]; then
    # Tentative de lecture de l'ancien domaine si .env existe
    DEFAULT_DOMAIN=""
    if [ -f "$ENV_FILE" ]; then
        DEFAULT_DOMAIN=$(grep "^ECHO_DOMAIN=" "$ENV_FILE" | cut -d '=' -f2)
    fi

    echo "Architecture cible :"
    echo " - IA    : https://ui.DOMAINE"
    echo " - Admin : https://am.DOMAINE"
    echo ""
    read -p "Entrez votre domaine racine [${DEFAULT_DOMAIN:-echo-ai.eu}] : " INPUT_DOMAIN
    DOMAIN="${INPUT_DOMAIN:-$DEFAULT_DOMAIN}"
fi

if [ -z "$DOMAIN" ]; then DOMAIN="echo-ai.eu"; fi # Valeur par défaut ultime

echo "🚀 Configuration pour : $DOMAIN"

# --- 2. DÉTECTION CORS & IP ---
echo "🌍 Calcul des origines CORS locales..."
OWUI_PORT=$(grep -A 10 "open-webui:" "$ECHO_STACK_FILE" | grep -m 1 "\- \"[0-9]*:[0-9]*\"" | cut -d'"' -f2 | cut -d: -f1)
if [ -z "$OWUI_PORT" ]; then OWUI_PORT="3000"; fi
HOST_IPS=$(hostname -I 2>/dev/null || ip addr show | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | cut -d/ -f1)
ECHO_DETECTED_ORIGINS=""
for ip in $HOST_IPS; do
    if [ -z "$ECHO_DETECTED_ORIGINS" ]; then ECHO_DETECTED_ORIGINS="http://$ip:$OWUI_PORT"; else ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS;http://$ip:$OWUI_PORT"; fi
done

# --- 3. MISE À JOUR .ENV ---
echo "📝 Mise à jour du fichier d'environnement centralisé ($ENV_FILE)..."

update_env() {
    local key=$1
    local value=$2
    if grep -q "^$key=" "$ENV_FILE"; then
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
    if ! grep -q "^$key=" "$ENV_FILE"; then
        local secret=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$length")
        echo "$key=$secret" >> "$ENV_FILE"
        echo "   🔑 Génération du secret : $key"
    fi
}

generate_secret "BW_DB_PASSWORD" 24
generate_secret "SEARXNG_SECRET" 64

# Export immédiat pour le process courant
export ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS"

# --- 4. LANCEMENT UNIFIÉ ---
echo "🐳 Redémarrage de l'infrastructure..."

# Création préventive du réseau
docker network create echo-network 2>/dev/null || true

# Arrêt PROPRE avec le .env pour éviter les warnings
$DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" -f "$BW_STACK_FILE" -f "$ECHO_STACK_FILE" down --remove-orphans

# Lancement
$DOCKER_COMPOSE_CMD \
    --env-file "$ENV_FILE" \
    -f "$BW_STACK_FILE" \
    -f "$ECHO_STACK_FILE" \
    up -d --build --quiet --remove-orphans

# --- 5. RECONSTRUCTION ET RECONFIGURATION ---
echo "🔧 Reconfiguration des paramètres internes d'Open WebUI (CORS, URLs)..."
if [ -f "/opt/echo-scripts/config-owui.sh" ]; then
    /bin/bash /opt/echo-scripts/config-owui.sh
    echo "   ✅ Reconfiguration terminée."
else
    echo "   ⚠️  Script 'config-owui.sh' non trouvé."
fi

echo ""
echo "✅ INSTALLATION TERMINÉE !"
echo "-----------------------------------------------------------"
echo "🤖 UI IA     : https://ui.$DOMAIN"
echo "🔧 ADMIN     : https://am.$DOMAIN"
echo "-----------------------------------------------------------"
echo "⏳ Le WAF va demander vos certificats SSL (1-2 min)."
echo "-----------------------------------------------------------"
