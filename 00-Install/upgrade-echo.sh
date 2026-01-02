#!/bin/bash
# ==============================================================================
# SCRIPT : upgrade-echo.sh
# VERSION : v5.3.1
# ROLE : MISE À NIVEAU MAJEURE (SAFE FORCE UPDATE)
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script met à jour TOUT : le code (scripts) ET les binaires (images Docker).
# C'est une opération "lourde" et destructrice (interruption de service).
#
# --- POURQUOI (WHY) ---
# À utiliser quand une nouvelle version d'Open WebUI sort (ex: v0.5 -> v0.6)
# ou quand on change une dépendance système fondamentale (Python 3.10 -> 3.11).
#
# --- COMMENT (HOW - ALGO) ---
# 1. SELF-RUN : Le script se copie dans /tmp pour s'exécuter.
#    Pourquoi ? Car il va probablement se mettre à jour lui-même pendant l'opération.
#    S'il s'écrasait pendant qu'il tourne, le shell crasherait.
# 2. GIT RESET HARD : Force la synchronisation stricte avec GitHub (Branche Cible).
#    On abandonne toute modification locale pour garantir un état propre.
# 3. DOCKER PULL : Télécharge les nouvelles images depuis le registre.
# 4. REBUILD : Appelle 'install-stack.sh' pour recréer tous les conteneurs à neuf.
# ==============================================================================

# --- ETAPE 0 : MECANISME SELF-RUN (AUTO-PROTECTION) ---
CURRENT_SCRIPT=$(readlink -f "$0")
TMP_SCRIPT="/tmp/upgrade-echo-running.sh"

if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    echo "🔄 Préparation de l'environnement de mise à jour..."
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"
    chmod +x "$TMP_SCRIPT"
    echo "🚀 Bascule vers l'exécution temporaire..."
    exec "$TMP_SCRIPT" "$@"
    exit 0
fi

# ==============================================================================
# LE CODE CI-DESSOUS S'EXECUTE DEPUIS /tmp/upgrade-echo-running.sh
# ==============================================================================

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"
BRANCH_FILE="/opt/ECHO_BRANCH"

# Sécurité Root
if [ "$EUID" -ne 0 ]; then
  echo "❌ Run as root (sudo)."
  exit 1
fi

# --- DÉTERMINATION DE LA BRANCHE CIBLE ---
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
    echo "🌿 Cible : Branche '$TARGET_BRANCH'"
else
    echo "🌿 Cible : Branche 'main' (défaut)"
fi

# --- SECURITE UTILISATEUR ---
# Confirmation explicite requise car l'opération coupe le service.
clear
echo "⚠️  ATTENTION : UPGRADE MAJEUR (DESTRUCTIF)"
echo "    Cette opération va :"
echo "    1. Basculer sur la branche : $TARGET_BRANCH"
echo "    2. Écraser TOUTES les modifications locales"
echo "    3. Redéployer les conteneurs (Interruption de service)"
if [ -n "$SUDO_USER" ]; then
    echo "🔒 Confirmation requise :"
    sudo -k; if ! sudo -u "$SUDO_USER" sudo -v; then exit 1; fi
else
    read -p "Tapez 'CONFIRMER' : " CONFIRM
    [ "$CONFIRM" != "CONFIRMER" ] && exit 1
fi

# --- ETAPE 1 : SYNCHRONISATION GIT (AUTO-RÉPARATION) ---
echo "🔄 [1/4] SYNC GITHUB (FORCE MODE)..."

# Scénario A : Pas de git -> Clone
if [ ! -d "$SRC_DIR/.git" ]; then
    echo "   🆕 Dépôt Git introuvable. Clonage initial..."
    rm -rf "$SRC_DIR" 
    git clone "$GIT_REPO" "$SRC_DIR"
    if [ $? -ne 0 ]; then
        echo "❌ Erreur critique : Impossible de cloner le dépôt."
        exit 1
    fi
    
    cd "$SRC_DIR" || exit
    
    # Vérification branche
    if git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
        git checkout "$TARGET_BRANCH"
    else
        echo "❌ ERREUR FATALE : La branche '$TARGET_BRANCH' n'existe pas."
        exit 1
    fi
    
# Scénario B : Git présent -> Reset Hard
else
    echo "   🧹 Nettoyage et mise à jour..."
    cd "$SRC_DIR" || exit
    
    git fetch origin
    
    # Vérification existence branche distante
    if ! git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
        echo "❌ ERREUR FATALE : La branche '$TARGET_BRANCH' n'existe pas sur le remote."
        exit 1
    fi
    
    git reset --hard HEAD
    git clean -fd
    
    echo "   📥 Bascule sur $TARGET_BRANCH..."
    git checkout "$TARGET_BRANCH"
    # Reset HARD sur la version distante pour être 100% iso prod
    git reset --hard "origin/$TARGET_BRANCH"
fi

# --- ETAPE 2 : DÉPLOIEMENT FICHIERS (MODE MIROIR) ---
echo "📂 [2/4] DEPLOIEMENT SCRIPTS (MODE MIROIR)..."

sync_mirror() {
    local src="$1"
    local dest="$2"
    if [ ! -d "$src" ]; then echo "⚠️ Source manquante: $src"; return; fi
    if [[ "$dest" != /opt/* ]]; then echo "⛔ Refus: $dest"; return; fi
    if [ ! -d "$dest" ]; then mkdir -p "$dest"; fi
    rm -rf "$dest"/*
    cp -rf "$src"/. "$dest"/
}

sync_mirror "$SRC_DIR/00-Install"       "/opt/owui-scripts"
sync_mirror "$SRC_DIR/04-OWUI-tools"    "/opt/owui-tools"
sync_mirror "$SRC_DIR/03-OWUI-functions" "/opt/owui-functions"
sync_mirror "$SRC_DIR/05-OWUI-filters"  "/opt/owui-filters"

# VERSIONING UPDATE : Copie forcée
if [ -f "$SRC_DIR/VERSION" ]; then
    echo "   🔖 Mise à jour de la version système..."
    cp "$SRC_DIR/VERSION" "/opt/ECHO_VERSION"
    chmod 644 "/opt/ECHO_VERSION"
fi

# Copie additive (Safe)
sync_mirror_file() {
    local src_file="$1"
    local dest_dir="$2"
    if [ ! -f "$src_file" ]; then echo "⚠️ Fichier manquant: $src_file"; return; fi
    if [[ "$dest_dir" == /opt/* ]]; then
        mkdir -p "$dest_dir"
        cp -f "$src_file" "$dest_dir/"
    fi
}

sync_mirror_file "$SRC_DIR/01-docker-admin-manager/server.py"     "/opt/admin-manager"
sync_mirror_file "$SRC_DIR/02-docker-python-worker/worker_api.py" "/opt/python-worker"
sync_mirror_file "$SRC_DIR/06-docker-browser-agent/browser_api.py" "/opt/browser-agent"

# --- ETAPE 3 : NETTOYAGE ENCODING (WINDOWS FIX) ---
# Supprime le BOM (Byte Order Mark) et convertit les fins de ligne CRLF en LF.
# Vital pour les fichiers édités sous Windows et copiés sur Linux.
echo "🧹 Nettoyage des caractères Windows (CRLF + BOM)..."
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" -o -name "VERSION" \) -exec sed -i '1s/^\xEF\xBB\xBF//' {} +
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" -o -name "VERSION" \) -exec sed -i 's/\r$//' {} +

chmod +x /opt/owui-scripts/*.sh

# --- ETAPE 4 : MISE A JOUR DES IMAGES DOCKER ---
echo "🐳 [3/4] DOCKER PULL..."
# Téléchargement des dernières versions
docker pull ghcr.io/open-webui/open-webui:main
docker pull python:3.11-slim
docker pull containrrr/watchtower

# --- ETAPE 5 : RECONSTRUCTION DE L'INFRASTRUCTURE ---
echo "🚀 [4/4] RECONSTRUCTION..."
docker system prune -f > /dev/null 2>&1

# Relance via le script d'installation standard (qui utilisera les nouvelles images et scripts)
/bin/bash /opt/owui-scripts/install-stack.sh

echo "✨ UPGRADE TERMINÉ (Branche: $TARGET_BRANCH)."