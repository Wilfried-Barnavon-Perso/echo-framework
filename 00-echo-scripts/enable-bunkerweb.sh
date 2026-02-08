#!/bin/bash
# ==============================================================================
# SCRIPT : enable-bunkerweb.sh
# VERSION : 3.1
# AUTEUR : Wilfried BARNAVON (ECHO Framework)
# ==============================================================================
# ROLE : Activation de la couche de sécurité BunkerWeb (Secure Edge).
# ==============================================================================

export COMPOSE_PROJECT_NAME="echo"

CONFIG_DIR="/opt/config"
BW_DATA_DIR="/opt/bunkerweb"
# Stockage du .env HORS du dossier config synchronisé pour éviter l'écrasement
ENV_FILE="$BW_DATA_DIR/.env"
BW_STACK_FILE="$CONFIG_DIR/bunkerweb-stack.yml"
ECHO_STACK_FILE="$CONFIG_DIR/stack-echo.yml"
DOCKER_COMPOSE_CMD="docker-compose"

# Vérification root
if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# Vérification Docker Compose
if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
fi

clear
echo "=================================================="
echo "🛡️  ECHO SECURE EDGE (Standard Edition v3.0)"
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
        DEFAULT_DOMAIN=$(grep "ECHO_DOMAIN" "$ENV_FILE" | cut -d '=' -f2)
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

# --- 2. GENERATION .ENV ---
echo "📝 Génération du fichier d'environnement standard ($ENV_FILE)..."

cat > "$ENV_FILE" <<EOF
# Configuration ECHO Framework
# Généré par enable-bunkerweb.sh le $(date)

# Domaine racine pour le routing BunkerWeb
ECHO_DOMAIN=$DOMAIN

# Fuseau horaire des conteneurs
TZ=Europe/Paris
EOF

# --- 3. PREPARATION SYSTEME ---
mkdir -p "$BW_DATA_DIR"

# --- 4. LANCEMENT UNIFIÉ ---
echo "🐳 Redémarrage de l'infrastructure..."

# Création préventive du réseau (Requis car external: true dans les YAML)
docker network create echo-network 2>/dev/null || true

# Arrêt pour prise en compte des nouvelles variables .env
$DOCKER_COMPOSE_CMD -f "$BW_STACK_FILE" -f "$ECHO_STACK_FILE" down --remove-orphans

# Lancement
# Docker Compose chargera automatiquement le .env car il est dans le même dossier que les YAML
# (si on lance depuis ce dossier, ou si on précise --env-file)
# Par sécurité, on se place dans le dossier config pour lancer
cd "$CONFIG_DIR" || exit 1

$DOCKER_COMPOSE_CMD \
    -f "bunkerweb-stack.yml" \
    -f "stack-echo.yml" \
    up -d --build --quiet --remove-orphans

echo ""
echo "✅ INSTALLATION TERMINÉE !"
echo "-----------------------------------------------------------"
echo "🤖 UI IA     : https://ui.$DOMAIN"
echo "🔧 ADMIN     : https://am.$DOMAIN"
echo "-----------------------------------------------------------"
echo "⏳ Le WAF va demander vos certificats SSL (1-2 min)."
echo "-----------------------------------------------------------"
