#!/bin/bash
# ==============================================================================
# SCRIPT : install-linux.sh
# VERSION : 1.1
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
# CHANGELOG :
#   1.1 : Dépôt Docker officiel (docker-ce + docker-compose-plugin).
#         Fix parsing --branch (while loop).
#         Ajout ports SSH PKCE (8020-8024) dans le récap final.
# ==============================================================================

set -euo pipefail

# --- CONSTANTES ---
readonly GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
readonly ECHO_ROOT="/opt/ECHO"
readonly ECHO_SOURCE="$ECHO_ROOT/source"
readonly YQ_VERSION="v4.40.5"
readonly YQ_URL="https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64"

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
if grep -qi "microsoft" /proc/version 2>/dev/null; then
    log_warn "Environnement WSL2 détecté."
    log_warn "Pour WSL2, utilisez plutôt : install-wsl2.sh"
    echo ""
    # Lecture stdin explicite (compatible curl | bash)
    exec < /dev/tty
    read -r -p "Continuer quand même sur WSL2 ? [o/N] " confirm
    [[ "${confirm,,}" == "o" ]] || exit 0
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

log_info "Installation des prérequis (transport HTTPS, GPG)..."
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    git \
    unzip \
    jq \
    chrony

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
# 3. DOCKER
# ==============================================================================
log_section "DOCKER"

systemctl enable --now docker
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
echo -e "      ${YELLOW}update-echo${NC}      → Mise à jour rapide du code"
echo -e "      ${YELLOW}upgrade-echo${NC}     → Mise à niveau majeure (rebuild)"
echo -e "      ${YELLOW}show-echo-admin${NC}  → Afficher les identifiants"
echo -e "      ${YELLOW}enable-bunkerweb${NC} → Activer l'accès public sécurisé"
echo ""
echo -e "   📄 Log d'installation : $ECHO_ROOT/install.log"
echo ""
