#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : install-wsl2.sh
# VERSION : 6.18.16.1
# AUTEUR  : Wilfried BARNAVON
# ==============================================================================
# ROLE :
#   Script d'installation ECHO pour WSL2 (Windows Subsystem for Linux 2).
#   Installe Docker Engine natif dans WSL2 (pas Docker Desktop).
#   Invocable directement depuis GitHub sans clonage préalable.
#   Utilise le dépôt officiel Docker (docker-ce + docker-compose-plugin).
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
#
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.16.1: Mapping dynamique yq, détection Docker Desktop scopée par distribution, contrôles RAM+Swap/disque, installation Docker non-destructive hors PATH Windows et traçabilité wsl2: dans echo_deploy_script_version.
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
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'

# ==============================================================================
# FONCTIONS UTILITAIRES
# ==============================================================================

log_info()    { echo -e "${CYAN}ℹ️  $*${NC}"; }
log_ok()      { echo -e "${GREEN}✅ $*${NC}"; }
log_warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
log_error()   { echo -e "${RED}❌ ERREUR : $*${NC}" >&2; exit 1; }
log_section() { echo -e "\n${BLUE}━━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

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

_is_docker_desktop_integrated() {
    local sock_path="${ECHO_TEST_DOCKER_SOCK:-/var/run/docker.sock}"
    local bin_path="${ECHO_TEST_DOCKER_BIN:-/usr/bin/docker}"
    local sock_target="" bin_target=""
    if [ -e "$sock_path" ] || [ -L "$sock_path" ]; then
        sock_target=$(readlink -f "$sock_path" 2>/dev/null || true)
    fi
    if [ -e "$bin_path" ] || [ -L "$bin_path" ]; then
        bin_target=$(readlink -f "$bin_path" 2>/dev/null || true)
    fi
    if [[ "$sock_target" == *"/docker-desktop"* ]] || [[ "$bin_target" == *"/docker-desktop"* ]]; then
        return 0
    fi
    if [ -x "$bin_path" ] && "$bin_path" info --format '{{.OperatingSystem}}' 2>/dev/null | grep -qi "Docker Desktop"; then
        return 0
    fi
    return 1
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

# Vérification WSL2 (obligatoire pour ce script)
if ! grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
    log_error "Ce script est réservé à WSL2. Pour Linux natif, utilisez : install-linux.sh"
fi

# Détection bloquante de l'intégration Docker Desktop WSL2 sur cette distribution
if _is_docker_desktop_integrated; then
    log_error "Intégration Docker Desktop WSL2 détectée sur cette distribution. Désactivez-la dans Docker Desktop (Settings -> Resources -> WSL Integration) puis exécutez 'wsl --shutdown', ou utilisez une distribution WSL2 dédiée avant de relancer install-wsl2.sh."
fi

# Contrôles matériels pré-vol WSL2 (RAM + Swap et espace disque)
if [ -r /proc/meminfo ]; then
    MEM_TOTAL_KB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
    SWAP_TOTAL_KB=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
    MEM_TOTAL_MB=$(( ${MEM_TOTAL_KB:-0} / 1024 ))
    SWAP_TOTAL_MB=$(( ${SWAP_TOTAL_KB:-0} / 1024 ))
    TOTAL_VM_MB=$((MEM_TOTAL_MB + SWAP_TOTAL_MB))
    if [ "$MEM_TOTAL_MB" -gt 0 ] && [ "$MEM_TOTAL_MB" -lt 7500 ]; then
        log_warn "RAM WSL2 détectée : ${MEM_TOTAL_MB} Mo (< 7500 Mo) | Swap : ${SWAP_TOTAL_MB} Mo."
        log_warn "Le build V6 de echo-webui utilise Vite (--max-old-space-size=6144). En cas d'OOM, configurez %UserProfile%\\.wslconfig :"
        echo -e "      ${YELLOW}[wsl2]${NC}"
        echo -e "      ${YELLOW}memory=8GB${NC}"
        echo -e "      ${YELLOW}swap=4GB${NC}"
        echo -e "   puis exécutez ${YELLOW}wsl --shutdown${NC} depuis PowerShell."
        if [ "$TOTAL_VM_MB" -lt 6800 ]; then
            log_warn "Mémoire totale RAM+Swap critique (${TOTAL_VM_MB} Mo < 6800 Mo) : risque élevé d'OOM lors du build."
            confirm_mem=""
            if [ -t 0 ]; then
                read -r -p "Continuer malgré la mémoire limitée ? [o/N] " confirm_mem
                [[ "${confirm_mem,,}" == "o" ]] || exit 0
            elif [ -e /dev/tty ] && (exec < /dev/tty) 2>/dev/null; then
                read -r -p "Continuer malgré la mémoire limitée ? [o/N] " confirm_mem < /dev/tty
                [[ "${confirm_mem,,}" == "o" ]] || exit 0
            fi
        fi
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

# Détection distrib
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

# Détection systemd (Ubuntu 22.04+ avec [boot] systemd=true dans /etc/wsl.conf)
SYSTEMD_ACTIVE=false
if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then
    SYSTEMD_ACTIVE=true
fi

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       ECHO FRAMEWORK — INSTALLATEUR WSL2             ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
echo -e "   Distribution : ${GREEN}${DISTRIB_NAME}${NC}"
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

log_info "Installation des prérequis (transport HTTPS, GPG, utilitaires)..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    git \
    unzip \
    jq \
    cron \
    rsync \
    psmisc
# Note : chrony non installé en WSL2 (synchro temps assurée par l'hôte Windows)

# --- Installation Docker Engine + Compose V2 (non-destructive, isolée du PATH Windows) ---
_LINUX_DOCKER_BIN=""
if [ -x /usr/bin/docker ]; then
    _LINUX_DOCKER_BIN="/usr/bin/docker"
elif [ -x /usr/local/bin/docker ]; then
    _LINUX_DOCKER_BIN="/usr/local/bin/docker"
fi

if [ -n "$_LINUX_DOCKER_BIN" ] && "$_LINUX_DOCKER_BIN" compose version >/dev/null 2>&1; then
    log_ok "Docker Engine et Compose V2 déjà présents ($("$_LINUX_DOCKER_BIN" --version)) — installation existante conservée."
elif dpkg-query -W -f='${Status}' docker.io 2>/dev/null | grep -q "install ok installed"; then
    log_info "Paquet Ubuntu/Debian docker.io détecté — ajout des plugins Compose V2 et Buildx natifs sans couper le démon..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker-compose-v2 docker-buildx 2>/dev/null \
        || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker-compose-plugin docker-buildx-plugin
    log_ok "Docker installé : $(docker --version)"
    log_ok "Compose installé : $(docker compose version)"
else
    # --- Ajout du dépôt officiel Docker ---
    log_info "Ajout du dépôt officiel Docker..."

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
# 3. DOCKER (avec gestion spécifique WSL2)
# ==============================================================================
log_section "DOCKER (WSL2)"

_start_docker_wsl2() {
    if [ "$SYSTEMD_ACTIVE" = "true" ]; then
        # Systemd disponible → comportement identique à Linux natif
        systemctl enable --now docker
        systemctl enable --now cron 2>/dev/null || true
        log_ok "Docker activé via systemd."
    else
        # Fallback : démarrage manuel (WSL2 sans systemd)
        log_info "Démarrage du démon Docker (mode service)..."
        if service docker status > /dev/null 2>&1; then
            log_ok "Docker est déjà en cours d'exécution."
        else
            service docker start || {
                log_warn "Démarrage via service échoué. Tentative nohup dockerd..."
                nohup dockerd > /var/log/dockerd.log 2>&1 &
                sleep 4
            }
        fi

        # Attente que le démon réponde (max 20 secondes)
        local retries=10
        while ! docker info > /dev/null 2>&1; do
            retries=$((retries - 1))
            [ $retries -eq 0 ] && log_error "Le démon Docker ne répond pas. Consultez /var/log/dockerd.log"
            sleep 2
        done

        service cron start >/dev/null 2>&1 || true

        log_warn "Docker devra être redémarré au prochain démarrage WSL2 : sudo service docker start"
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

# Écriture du fichier de branche (requis par echo-sync.sh)
echo "$TARGET_BRANCH" > "$ECHO_ROOT/ECHO_BRANCH"
chmod 644 "$ECHO_ROOT/ECHO_BRANCH"
log_info "Branche enregistrée : $TARGET_BRANCH"

# Traçabilité du script de déploiement (lu par echo-version.sh)
rm -f "$ECHO_ROOT/echo_deploy_script_version"
printf 'wsl2:%s\n' "$SCRIPT_VERSION" > "$ECHO_ROOT/echo_deploy_script_version"
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
WSL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     ✅  ECHO FRAMEWORK DÉPLOYÉ AVEC SUCCÈS (WSL2)    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "   🌐 Interface (WSL2)   : ${CYAN}http://${WSL_IP}:3000${NC}"
echo -e "   🖥️  Interface (Windows) : ${CYAN}http://localhost:3000${NC}"
echo -e "   🔧 Admin Manager       : ${CYAN}http://localhost:3001${NC}"
echo -e "   🔑 Auth PKCE (SSH)     : ${CYAN}ports 8020–8024 exposés${NC}"
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

if [ "$SYSTEMD_ACTIVE" = "false" ]; then
    echo -e "   ${YELLOW}⚠️  Rappel WSL2 sans systemd :${NC}"
    echo -e "      Au prochain démarrage WSL2, relancez Docker :"
    echo -e "      ${YELLOW}sudo service docker start${NC}"
    echo ""
fi

echo -e "   📄 Log d'installation : $ECHO_ROOT/install.log"
echo ""
