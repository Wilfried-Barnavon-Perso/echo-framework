#!/bin/bash
# ==============================================================================
# SCRIPT : disable-bunkerweb.sh
# VERSION : 2.0
# AUTEUR : Wilfried BARNAVON (ECHO Framework)
# ==============================================================================
# ROLE : Désactivation de la couche de sécurité BunkerWeb (Secure Edge)
#        et retour au mode d'accès local direct (HTTP).
# CHANGELOG 2.0 : set -euo pipefail.
#                 CORRECTION CRITIQUE : suppression du "docker rm -f $(docker ps -a)"
#                 global qui détruisait TOUS les conteneurs de la machine.
#                 Arrêt ciblé via label com.docker.compose.project=echo uniquement.
#                 Idempotence : vérifie que BunkerWeb tourne avant de tenter l'arrêt.
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

# Détection automatique du moteur Compose (V2 prioritaire)
DOCKER_COMPOSE_CMD="docker compose"
if ! command -v $DOCKER_COMPOSE_CMD &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
fi

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

echo "=================================================="
echo "🔓 ECHO SECURITY DISABLE (v2.0)"
echo "=================================================="
echo "Ce script va désactiver le WAF et le HTTPS."
echo "ℹ️  Mode Local : Le portail ECHO Auth (MFA/SSO) ne sera pas actif (pas d'exposition internet)."
echo "Vos applications seront accessibles uniquement en LOCAL (HTTP)."
echo ""

# --- 1. IDEMPOTENCE : Vérification que BunkerWeb tourne ---
BW_RUNNING=$(docker ps --filter "name=bunkerweb" --filter "status=running" --format "{{.Names}}" 2>/dev/null | head -1 || true)
if [ -z "$BW_RUNNING" ]; then
    echo "ℹ️  BunkerWeb n'est pas actif. Rien à désactiver."
    echo ""
    # Vérifie si ECHO tourne quand même en mode local
    OWUI_RUNNING=$(docker ps --filter "name=echo-open-webui" --filter "status=running" --format "{{.Names}}" 2>/dev/null | head -1 || true)
    if [ -n "$OWUI_RUNNING" ]; then
        echo "✅ ECHO tourne déjà en mode local."
    else
        echo "⚠️  ECHO ne semble pas actif du tout. Lancez install-stack.sh."
    fi
    exit 0
fi

read -r -p "Confirmer la désactivation ? (y/N) : " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then echo "Annulé."; exit 0; fi

# --- 2. DÉTECTION CORS & IP (Maintien de l'accès local) ---
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

# --- 3. MISE À JOUR .ENV (Désactivation Domaine) ---
if [ -f "$ENV_FILE" ]; then
    echo "📝 Désactivation du domaine dans le fichier d'environnement..."
    sed -i "s|^ECHO_DOMAIN=.*|ECHO_DOMAIN=|" "$ENV_FILE"
    if grep -q "^ECHO_DETECTED_ORIGINS=" "$ENV_FILE"; then
        sed -i "s|^ECHO_DETECTED_ORIGINS=.*|ECHO_DETECTED_ORIGINS=\"$ECHO_DETECTED_ORIGINS\"|" "$ENV_FILE"
    else
        echo "ECHO_DETECTED_ORIGINS=\"$ECHO_DETECTED_ORIGINS\"" >> "$ENV_FILE"
    fi
fi

export ECHO_DETECTED_ORIGINS="$ECHO_DETECTED_ORIGINS"

# --- 4. ARRÊT CIBLÉ (Uniquement les conteneurs du projet ECHO) ---
echo "🛑 Arrêt de l'infrastructure sécurisée (projet : $COMPOSE_PROJECT_NAME)..."

# Création préventive du répertoire BunkerWeb (bind mount bw-data, requis par Compose)
mkdir -p "$ECHO_ROOT/bunkerweb"

# Arrêt propre via Compose (ciblé sur le projet echo, préserve les volumes)
$DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" \
    -f "$BW_STACK_FILE" -f "$ECHO_STACK_FILE" \
    down --remove-orphans || true

# Nettoyage préventif ciblé : uniquement les conteneurs ECHO restants
# (Filtre strict par label du projet Compose)
ECHO_CONTAINERS=$(docker ps -a \
    --filter "label=com.docker.compose.project=echo" \
    --format "{{.Names}}" 2>/dev/null || true)

if [ -n "$ECHO_CONTAINERS" ]; then
    echo "   ✅ (Les conteneurs ECHO résiduels seront gérés par le redémarrage)"
else
    echo "   ✅ Aucun conteneur ECHO résiduel."
fi

# --- 5. RELANCE EN MODE LOCAL ---
echo "🚀 Redémarrage en mode Local (sans BunkerWeb)..."
$DOCKER_COMPOSE_CMD --env-file "$ENV_FILE" \
    -f "$ECHO_STACK_FILE" \
    up -d --remove-orphans

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

echo ""
echo "✅ SÉCURITÉ DÉSACTIVÉE."
echo "-----------------------------------------------------------"
echo "🌐 LOCAL UI    : http://IP-LOCALE:3000"
echo "🔧 LOCAL ADMIN : http://IP-LOCALE:3001"
echo "ℹ️  AUTH LOCAL  : Bypass (Pas de MFA en local)"
echo "-----------------------------------------------------------"
echo "⚠️  L'accès HTTPS est coupé. Le WAF est éteint."
echo "-----------------------------------------------------------"
