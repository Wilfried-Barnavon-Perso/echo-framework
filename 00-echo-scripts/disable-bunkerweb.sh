#!/bin/bash
# ==============================================================================
# SCRIPT : disable-bunkerweb.sh
# VERSION : 1.5
# AUTEUR : Wilfried BARNAVON (ECHO Framework)
# ==============================================================================
# ROLE : Désactivation de la couche de sécurité BunkerWeb (Secure Edge)
#        et retour au mode d'accès local direct (HTTP).
# ==============================================================================

export COMPOSE_PROJECT_NAME="echo"

CONFIG_DIR="/opt/config"
# Utilisation du .env centralisé
ENV_FILE="/opt/.env"
BW_STACK_FILE="$CONFIG_DIR/bunkerweb-stack.yml"
ECHO_STACK_FILE="$CONFIG_DIR/stack-echo.yml"
DOCKER_COMPOSE_CMD="docker-compose"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
fi

echo "=================================================="
echo "🔓 ECHO SECURITY DISABLE (v1.5)"
echo "=================================================="
echo "Ce script va désactiver le WAF et le HTTPS."
echo "Vos applications seront accessibles uniquement en LOCAL (HTTP)."
echo ""
read -p "Confirmer la désactivation ? (y/N) : " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then echo "Annulé."; exit 0; fi

# --- 1. DÉTECTION CORS & IP (Maintien de l'accès local) ---
echo "🌍 Calcul des origines CORS locales..."
OWUI_PORT=$(grep -A 10 "open-webui:" "$ECHO_STACK_FILE" | grep -m 1 "\- \"[0-9]*:[0-9]*\"" | cut -d'"' -f2 | cut -d: -f1)
if [ -z "$OWUI_PORT" ]; then OWUI_PORT="3000"; fi
HOST_IPS=$(hostname -I 2>/dev/null || ip addr show | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | cut -d/ -f1)
ECHO_DETECTED_ORIGINS=""
for ip in $HOST_IPS; do
    if [ -z "$ECHO_DETECTED_ORIGINS" ]; then ECHO_DETECTED_ORIGINS="http://$ip:$OWUI_PORT"; else ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS;http://$ip:$OWUI_PORT"; fi
done

# --- 2. MISE À JOUR .ENV (Désactivation Domaine) ---
if [ -f "$ENV_FILE" ]; then
    echo "📝 Désactivation du domaine dans le fichier d'environnement..."
    # On vide ECHO_DOMAIN pour repasser en mode local, mais on garde le reste (secrets)
    sed -i "s|^ECHO_DOMAIN=.*|ECHO_DOMAIN=|" "$ENV_FILE"
    # Mise à jour du CORS local
    if grep -q "^ECHO_DETECTED_ORIGINS=" "$ENV_FILE"; then
        sed -i "s|^ECHO_DETECTED_ORIGINS=.*|ECHO_DETECTED_ORIGINS=$ECHO_DETECTED_ORIGINS|" "$ENV_FILE"
    else
        echo "ECHO_DETECTED_ORIGINS=$ECHO_DETECTED_ORIGINS" >> "$ENV_FILE"
    fi
fi

# Export immédiat pour le process courant
export ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS"

# --- 3. ARRÊT COMPLET ---
echo "🛑 Arrêt de l'infrastructure sécurisée..."
$DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" -f "$BW_STACK_FILE" -f "$ECHO_STACK_FILE" down --remove-orphans || true

# Nettoyage préventif de TOUS les conteneurs (Alignement install-stack.sh)
echo "🧹 Nettoyage préventif des conteneurs..."
for d in $(docker ps -a --format '{{.Names}}') ; do 
    echo "   ⚠️  Suppression : $d"
    docker rm -f $d >/dev/null 2>&1
done

# --- 4. RELANCE EN MODE LOCAL ---
echo "🚀 Redémarrage en mode Local..."
$DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" -f "$ECHO_STACK_FILE" up -d --remove-orphans

# --- 5. RECONSTRUCTION ET RECONFIGURATION ---
echo "🔧 Reconfiguration des paramètres internes d'Open WebUI (CORS, URLs)..."
if [ -f "/opt/echo-scripts/config-owui.sh" ]; then
    /bin/bash /opt/echo-scripts/config-owui.sh
    echo "   ✅ Reconfiguration terminée."
else
    echo "   ⚠️  Script 'config-owui.sh' non trouvé."
fi

echo ""
echo "✅ SÉCURITÉ DÉSACTIVÉE."
echo "-----------------------------------------------------------"
echo "🌐 LOCAL UI    : http://IP-LOCALE:3000"
echo "🔧 LOCAL ADMIN : http://IP-LOCALE:3001"
echo "-----------------------------------------------------------"
echo "⚠️  L'accès HTTPS est coupé. Le WAF est éteint."
echo "-----------------------------------------------------------"
