#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : install-linux.sh
# VERSION : 6.18.16.1
# AUTEUR  : Wilfried BARNAVON
# ==============================================================================
# ROLE :
#   Script d'installation ECHO pour Linux natif (Ubuntu/Debian).
#   Invocable directement depuis GitHub sans clonage préalable.
#   Utilise le dépôt officiel Docker (docker-ce + docker-compose-plugin).
#
# USAGE :
#   curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-linux.sh | sudo bash
#   sudo bash <(curl -fsSL https://raw.githubusercontent.com/Wilfried-Barnavon-Perso/echo-framework/main/install-linux.sh) --branch dev
#
# OPTIONS :
#   --branch <name>   Branche Git à déployer (défaut: main)
#
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.16.1: Mapping dynamique yq, contrôles RAM/disque, installation Docker non-destructive, paquets cron/rsync/psmisc, traçabilité linux: dans echo_deploy_script_version et garde WSL2 sans TTY.
# 5.231.39.1: Mise à jour des références vers echo-sync.sh, echo-update, echo-upgrade et echo-show-admin.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.3: Retrait de la configuration Docker redondante (déplacée dans install-stack.sh).
# 1.2: Ajout de la rotation des logs Docker et du prune hebdomadaire.

set -euo pipefail

# --- CONSTANTES ---
readonly SCRIPT_VERSION="6.18.16.1"
readonly GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
readonly ECHO_ROOT="/opt/ECHO"
readonly ECHO_SOURCE="$ECHO_ROOT/source"
readonly YQ_VERSION="v4.40.5"

# --- COULEURS ---
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'

# ==============================================================================
# FONCTIONS UTILITAIRES
# ==============================================================================

log_info()    { echo -e "${CYAN}ℹ️  $*${NC}"; }
log_ok()      { echo -e "${GREEN}✅ $*${NC}"; }
log_warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
log_error()   { echo -e "${RED}❌ ERREUR : $*${NC}" >&2; exit 1; }
log_section() { echo -e "\n${CYAN}━━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

_resolve_yq_arch() {
    local deb_arch
    deb_arch="$(dpkg --print-architecture)"
    case "$deb_arch" in
        amd64) echo "amd64" ;;
        arm64) echo "arm64" ;;
        armhf|arm) echo "arm" ;;
        i386|386) echo "386" ;;
        *) log_error "Architecture non supportée pour yq : $deb_arch" ;;
    esac
}

# ==============================================================================
# 0. PARSING DES ARGUMENTS
# ==============================================================================
TARGET_BRANCH="main"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            TARGET_BRANCH="${2:-main}"
            shift 2
            ;;
        --branch=*)
            TARGET_BRANCH="${1#*=}"
            shift
            ;;
        *)
            shift
            ;;
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

# Détection distrib : Ubuntu ou Debian uniquement
if [ -f /etc/os-release ]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    DISTRIB_ID="${ID:-unknown}"
    DISTRIB_NAME="${PRETTY_NAME:-$DISTRIB_ID}"
else
    log_error "Impossible de détecter la distribution Linux (/etc/os-release manquant)."
fi

if [[ "$DISTRIB_ID" != "ubuntu" && "$DISTRIB_ID" != "debian" ]]; then
    log_error "Distribution non supportée : '$DISTRIB_ID'. Ce script supporte Ubuntu et Debian uniquement."
fi

# Avertissement WSL2 : ce script est pour Linux natif
if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
    log_warn "Environnement WSL2 détecté."
    log_warn "Pour WSL2, utilisez plutôt : install-wsl2.sh"
    echo ""
    confirm=""
    if [ -t 0 ]; then
        read -r -p "Continuer quand même sur WSL2 ? [o/N] " confirm
    elif [ -e /dev/tty ] && (exec < /dev/tty) 2>/dev/null; then
        read -r -p "Continuer quand même sur WSL2 ? [o/N] " confirm < /dev/tty
    else
        log_error "Environnement WSL2 détecté en mode non-interactif. Utilisez install-wsl2.sh."
    fi
    [[ "${confirm,,}" == "o" ]] || exit 0
fi

# Contrôles matériels pré-vol (RAM et espace disque)
if [ -r /proc/meminfo ]; then
    MEM_TOTAL_KB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
    MEM_TOTAL_MB=$(( ${MEM_TOTAL_KB:-0} / 1024 ))
    if [ "$MEM_TOTAL_MB" -gt 0 ] && [ "$MEM_TOTAL_MB" -lt 7500 ]; then
        log_warn "RAM détectée : ${MEM_TOTAL_MB} Mo (< 7500 Mo). Le build V6 de echo-webui (Vite --max-old-space-size=6144) s'appuiera sur le fichier /swapfile de 4 Go créé par install-stack.sh (8 Go RAM recommandés)."
    fi
fi

DISK_CHECK_PATH="/opt"
[ -d "$DISK_CHECK_PATH" ] || DISK_CHECK_PATH="/"
DISK_AVAIL_MB=$(df -Pm "$DISK_CHECK_PATH" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)
if [[ "$DISK_AVAIL_MB" =~ ^[0-9]+$ ]] && [ "$DISK_AVAIL_MB" -gt 0 ]; then
    if [ "$DISK_AVAIL_MB" -lt 10240 ]; then
        log_error "Espace disque insuffisant sur $DISK_CHECK_PATH : ${DISK_AVAIL_MB} Mo disponibles (< 10 Go minimum requis)."
    elif [ "$DISK_AVAIL_MB" -lt 20480 ]; then
        log_warn "Espace disque restreint sur $DISK_CHECK_PATH : ${DISK_AVAIL_MB} Mo disponibles (< 20 Go recommandés)."
    fi
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       ECHO FRAMEWORK — INSTALLATEUR LINUX            ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
echo -e "   Distribution : ${GREEN}${DISTRIB_NAME}${NC}"
echo -e "   Branche cible : ${GREEN}$TARGET_BRANCH${NC}"
echo -e "   Destination   : ${GREEN}$ECHO_ROOT${NC}"
echo ""

# ==============================================================================
# 2. DÉPENDANCES SYSTÈME
# ==============================================================================
log_section "DÉPENDANCES SYSTÈME"

log_info "Mise à jour des paquets..."
apt-get update -qq

log_info "Installation des prérequis (transport HTTPS, GPG, utilitaires)..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    git \
    unzip \
    jq \
    chrony \
    cron \
    rsync \
    psmisc

# --- Installation Docker Engine + Compose V2 (non-destructive) ---
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log_ok "Docker Engine et Compose V2 déjà présents ($(docker --version)) — installation existante conservée."
elif dpkg-query -W -f='${Status}' docker.io 2>/dev/null | grep -q "install ok installed"; then
    log_info "Paquet Ubuntu/Debian docker.io détecté — ajout des plugins Compose V2 et Buildx natifs sans couper le démon..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker-compose-v2 docker-buildx 2>/dev/null \
        || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker-compose-plugin docker-buildx-plugin
    log_ok "Docker installé : $(docker --version)"
    log_ok "Compose installé : $(docker compose version)"
else
    # --- Ajout du dépôt officiel Docker ---
    log_info "Ajout du dépôt officiel Docker..."

    # Détection de l'architecture et du code de distrib
    ARCH="$(dpkg --print-architecture)"
    DISTRIB_CODENAME="${VERSION_CODENAME:-$(lsb_release -cs 2>/dev/null || echo noble)}"

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${DISTRIB_ID}/gpg" \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${DISTRIB_ID} ${DISTRIB_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list

    apt-get update -qq
    log_ok "Dépôt Docker ajouté."

    # --- Installation Docker Engine + Compose Plugin ---
    log_info "Installation de Docker CE + docker-compose-plugin..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin

    log_ok "Docker installé : $(docker --version)"
    log_ok "Compose installé : $(docker compose version)"
fi

# Installation de yq (absent des dépôts apt standards)
if ! command -v yq &>/dev/null; then
    YQ_ARCH="$(_resolve_yq_arch)"
    YQ_URL="https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${YQ_ARCH}"
    log_info "Installation de yq $YQ_VERSION ($YQ_ARCH)..."
    curl -fsSL "$YQ_URL" -o /usr/local/bin/yq
    chmod +x /usr/local/bin/yq
    log_ok "yq installé : $(yq --version)"
else
    log_ok "yq déjà présent : $(yq --version)"
fi

# ==============================================================================
# 3. DOCKER
# ==============================================================================
log_section "DOCKER"

systemctl enable --now docker
systemctl enable --now cron 2>/dev/null || true
log_ok "Service Docker actif."

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

# Écriture du fichier de branche (requis par echo-sync.sh)
echo "$TARGET_BRANCH" > "$ECHO_ROOT/ECHO_BRANCH"
chmod 644 "$ECHO_ROOT/ECHO_BRANCH"
log_info "Branche enregistrée : $TARGET_BRANCH"

# Traçabilité du script de déploiement (lu par echo-version.sh)
rm -f "$ECHO_ROOT/echo_deploy_script_version"
printf 'linux:%s\n' "$SCRIPT_VERSION" > "$ECHO_ROOT/echo_deploy_script_version"
chmod 444 "$ECHO_ROOT/echo_deploy_script_version"

# ==============================================================================
# 5. SYNCHRONISATION & INSTALLATION DE LA STACK
# ==============================================================================
log_section "SYNCHRONISATION DES FICHIERS"

chmod +x "$ECHO_SOURCE/00-echo-scripts/echo-sync.sh"
"$ECHO_SOURCE/00-echo-scripts/echo-sync.sh" --local-only

log_section "INSTALLATION DE LA STACK DOCKER"

bash "$ECHO_ROOT/echo-scripts/install-stack.sh" 2>&1 | tee "$ECHO_ROOT/install.log"

# ==============================================================================
# 6. FIN
# ==============================================================================
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       ✅  ECHO FRAMEWORK DÉPLOYÉ AVEC SUCCÈS         ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   🌐 Interface Web      : ${CYAN}http://${HOST_IP}:3000${NC}"
echo -e "   🔧 Admin Manager      : ${CYAN}http://${HOST_IP}:3001${NC}"
echo -e "   🔑 Auth PKCE (SSH)    : ${CYAN}ports 8020–8024 exposés${NC}"
echo ""
echo -e "   📋 Commandes utiles :"
echo -e "      ${YELLOW}echo-version${NC}     → Afficher les versions déployées"
echo -e "      ${YELLOW}echo-ps${NC}          → État des conteneurs Docker ECHO"
echo -e "      ${YELLOW}echo-logs${NC}        → Consulter les journaux des services"
echo -e "      ${YELLOW}echo-update${NC}      → Mise à jour rapide du code"
echo -e "      ${YELLOW}echo-upgrade${NC}     → Mise à niveau majeure (rebuild)"
echo -e "      ${YELLOW}echo-os-upgrade${NC}  → Mise à jour système Ubuntu/Debian (APT)"
echo -e "      ${YELLOW}echo-show-admin${NC}  → Afficher les identifiants"
echo -e "      ${YELLOW}enable-bunkerweb${NC} → Activer l'accès public sécurisé"
echo ""
echo -e "   📄 Log d'installation : $ECHO_ROOT/install.log"
echo ""
