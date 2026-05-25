#!/bin/bash
# ==============================================================================
# SCRIPT : install-wsl2.sh
# VERSION : 1.0
# AUTEUR  : Wilfried BARNAVON
# ==============================================================================
# ROLE :
#   Script d'installation ECHO pour WSL2 (Windows Subsystem for Linux 2).
#   Installe Docker Engine natif dans WSL2 (pas Docker Desktop).
#   Invocable directement depuis GitHub sans clonage préalable.
#
# USAGE :
#   curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-wsl2.sh | sudo bash
#   sudo bash <(curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-wsl2.sh) --branch dev
#
# OPTIONS :
#   --branch <name>   Branche Git à déployer (défaut: main)
#
# PRÉREQUIS WSL2 :
#   - WSL2 activé sur l'hôte Windows (wsl --install)
#   - Distribution Ubuntu 22.04+ recommandée
#   - Pour systemd dans WSL2, ajouter dans /etc/wsl.conf :
#       [boot]
#       systemd=true
#     puis relancer WSL2 : wsl --shutdown (depuis PowerShell)
# ==============================================================================

set -euo pipefail

# --- CONSTANTES ---
readonly GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
readonly ECHO_ROOT="/opt/ECHO"
readonly ECHO_SOURCE="$ECHO_ROOT/source"
readonly YQ_VERSION="v4.40.5"
readonly YQ_URL="https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64"

# --- COULEURS ---
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'

# ==============================================================================
# FONCTIONS UTILITAIRES
# ==============================================================================

log_info()    { echo -e "${CYAN}ℹ️  $*${NC}"; }
log_ok()      { echo -e "${GREEN}✅ $*${NC}"; }
log_warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
log_error()   { echo -e "${RED}❌ ERREUR : $*${NC}" >&2; exit 1; }
log_section() { echo -e "\n${BLUE}━━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ==============================================================================
# 0. PARSING DES ARGUMENTS
# ==============================================================================
TARGET_BRANCH="main"
for arg in "$@"; do
    case "$arg" in
        --branch) shift; TARGET_BRANCH="${1:-main}" ;;
        --branch=*) TARGET_BRANCH="${arg#*=}" ;;
    esac
done

# ==============================================================================
# 1. PRE-FLIGHT CHECKS
# ==============================================================================
log_section "PRE-FLIGHT"

# Vérification root
if [ "$EUID" -ne 0 ]; then
    log_error "Ce script doit être exécuté en tant que root (sudo)."
fi

# Vérification WSL2 (obligatoire pour ce script)
if ! grep -qi "microsoft" /proc/version 2>/dev/null; then
    log_error "Ce script est réservé à WSL2. Pour Linux natif, utilisez : install-linux.sh"
fi

# Vérification WSL version 2 (pas WSL 1)
WSL_INTEROP=$(ls /run/WSL 2>/dev/null || true)
WSL_VERSION_FILE="/proc/sys/fs/binfmt_misc/WSLInterop"
if [ ! -e "$WSL_VERSION_FILE" ] && [ -z "$WSL_INTEROP" ]; then
    log_warn "Impossible de confirmer WSL2 (vs WSL1). Vérifiez avec 'wsl -l -v' depuis PowerShell."
fi

# Détection distrib
if [ -f /etc/os-release ]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    DISTRIB_ID="${ID:-unknown}"
else
    log_error "Impossible de détecter la distribution Linux (/etc/os-release manquant)."
fi

if [[ "$DISTRIB_ID" != "ubuntu" && "$DISTRIB_ID" != "debian" ]]; then
    log_error "Distribution non supportée : '$DISTRIB_ID'. Ce script supporte Ubuntu et Debian uniquement."
fi

# Détection systemd
SYSTEMD_ACTIVE=false
if pidof systemd > /dev/null 2>&1 || [ "$(ps -p 1 -o comm=)" = "systemd" ]; then
    SYSTEMD_ACTIVE=true
fi

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       ECHO FRAMEWORK — INSTALLATEUR WSL2             ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
echo -e "   Distribution : ${GREEN}${PRETTY_NAME:-$DISTRIB_ID}${NC}"
echo -e "   Branche cible : ${GREEN}$TARGET_BRANCH${NC}"
echo -e "   Destination   : ${GREEN}$ECHO_ROOT${NC}"
echo -e "   Systemd actif : ${GREEN}$SYSTEMD_ACTIVE${NC}"
echo ""

# Avertissement si systemd absent
if [ "$SYSTEMD_ACTIVE" = "false" ]; then
    log_warn "Systemd non actif. Docker sera démarré via 'service docker start'."
    log_warn "Pour activer systemd définitivement dans WSL2, ajoutez dans /etc/wsl.conf :"
    echo -e "      ${YELLOW}[boot]${NC}"
    echo -e "      ${YELLOW}systemd=true${NC}"
    echo -e "   puis exécutez ${YELLOW}wsl --shutdown${NC} depuis PowerShell et relancez WSL2."
    echo ""
fi

# ==============================================================================
# 2. DÉPENDANCES SYSTÈME
# ==============================================================================
log_section "DÉPENDANCES SYSTÈME"

log_info "Mise à jour des paquets..."
apt-get update -qq

log_info "Installation des paquets requis..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    git \
    unzip \
    jq \
    docker.io \
    docker-compose-v2 \
    docker-buildx-plugin

# Note : chrony n'est pas installé en WSL2 (la synchro temps est gérée par l'hôte Windows)
log_ok "Paquets système installés."

# Installation de yq (absent des dépôts apt standards)
if ! command -v yq &>/dev/null; then
    log_info "Installation de yq $YQ_VERSION..."
    curl -fsSL "$YQ_URL" -o /usr/local/bin/yq
    chmod +x /usr/local/bin/yq
    log_ok "yq installé : $(yq --version)"
else
    log_ok "yq déjà présent : $(yq --version)"
fi

# ==============================================================================
# 3. DOCKER (avec gestion spécifique WSL2)
# ==============================================================================
log_section "DOCKER (WSL2)"

_start_docker_wsl2() {
    if [ "$SYSTEMD_ACTIVE" = "true" ]; then
        # Systemd disponible → comportement identique à Linux natif
        systemctl enable --now docker
        log_ok "Docker activé via systemd."
    else
        # Fallback : démarrage manuel du démon Docker
        log_info "Démarrage du démon Docker (mode service)..."
        if service docker status > /dev/null 2>&1; then
            log_ok "Docker est déjà en cours d'exécution."
        else
            service docker start || {
                log_warn "Démarrage via service échoué. Tentative nohup dockerd..."
                nohup dockerd > /var/log/dockerd.log 2>&1 &
                sleep 4 # laisser le démon s'initialiser
            }
        fi

        # Vérification que Docker répond
        local retries=10
        while ! docker info > /dev/null 2>&1; do
            retries=$((retries - 1))
            [ $retries -eq 0 ] && log_error "Le démon Docker ne répond pas. Consultez /var/log/dockerd.log"
            sleep 2
        done
        log_ok "Docker actif."

        # Rappel : sans systemd, Docker ne redémarrera pas automatiquement après reboot WSL2
        log_warn "Docker devra être redémarré manuellement au prochain redémarrage WSL2 : sudo service docker start"
        log_warn "→ Conseil : Activez systemd dans /etc/wsl.conf pour éviter cela."
    fi

    log_ok "Docker : $(docker --version)"
}

_start_docker_wsl2

# ==============================================================================
# 4. CLONAGE / MISE À JOUR DU REPO
# ==============================================================================
log_section "SOURCES ECHO (Branche: $TARGET_BRANCH)"

mkdir -p "$ECHO_ROOT"

if [ -d "$ECHO_SOURCE/.git" ]; then
    # --- MODE MISE À JOUR (idempotent) ---
    log_warn "Installation existante détectée dans $ECHO_SOURCE."
    log_info "Mise à jour vers la branche $TARGET_BRANCH..."
    cd "$ECHO_SOURCE"
    git fetch origin
    git checkout "$TARGET_BRANCH" 2>/dev/null || git checkout -b "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
    git reset --hard "origin/$TARGET_BRANCH"
    log_ok "Sources mises à jour."
else
    # --- MODE INSTALLATION INITIALE ---
    log_info "Clonage du dépôt ECHO (branche: $TARGET_BRANCH)..."
    if [ -d "$ECHO_SOURCE" ] && [ "$(ls -A "$ECHO_SOURCE" 2>/dev/null)" ]; then
        log_warn "Dossier $ECHO_SOURCE non vide sans .git — nettoyage..."
        rm -rf "$ECHO_SOURCE"
    fi
    git clone --branch "$TARGET_BRANCH" "$GIT_REPO" "$ECHO_SOURCE"
    log_ok "Clonage terminé."
fi

# Écriture du fichier de branche (requis par sync-echo.sh)
echo "$TARGET_BRANCH" > "$ECHO_ROOT/ECHO_BRANCH"
chmod 644 "$ECHO_ROOT/ECHO_BRANCH"
log_info "Branche enregistrée : $TARGET_BRANCH"

# ==============================================================================
# 5. SYNCHRONISATION & INSTALLATION DE LA STACK
# ==============================================================================
log_section "SYNCHRONISATION DES FICHIERS"

chmod +x "$ECHO_SOURCE/00-echo-scripts/sync-echo.sh"
"$ECHO_SOURCE/00-echo-scripts/sync-echo.sh" --local-only

log_section "INSTALLATION DE LA STACK DOCKER"

bash "$ECHO_ROOT/echo-scripts/install-stack.sh" 2>&1 | tee "$ECHO_ROOT/install.log"

# ==============================================================================
# 6. FIN
# ==============================================================================
# Récupération des IPs : WSL a une IP interne + le host Windows est accessible via localhost
WSL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     ✅  ECHO FRAMEWORK DÉPLOYÉ AVEC SUCCÈS (WSL2)    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   🌐 Accès depuis WSL2     : ${CYAN}http://${WSL_IP}:3000${NC}"
echo -e "   🖥️  Accès depuis Windows  : ${CYAN}http://localhost:3000${NC}"
echo -e "   🔧 Admin Manager (WSL2)  : ${CYAN}http://${WSL_IP}:3001${NC}"
echo -e "   🔧 Admin Manager (Win)   : ${CYAN}http://localhost:3001${NC}"
echo ""
echo -e "   📋 Commandes utiles :"
echo -e "      ${YELLOW}update-echo${NC}      → Mise à jour rapide du code"
echo -e "      ${YELLOW}upgrade-echo${NC}     → Mise à niveau majeure (rebuild)"
echo -e "      ${YELLOW}show-echo-admin${NC}  → Afficher les identifiants"
echo -e "      ${YELLOW}enable-bunkerweb${NC} → Activer l'accès public sécurisé"
echo ""

if [ "$SYSTEMD_ACTIVE" = "false" ]; then
    echo -e "   ${YELLOW}⚠️  Rappel WSL2 sans systemd :${NC}"
    echo -e "      Au prochain démarrage WSL2, relancez Docker manuellement :"
    echo -e "      ${YELLOW}sudo service docker start && sudo install-stack${NC}"
    echo ""
fi

echo -e "   📄 Log d'installation : $ECHO_ROOT/install.log"
echo ""
