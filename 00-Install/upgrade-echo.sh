#!/bin/bash
# ==============================================================================
# SCRIPT : upgrade-echo.sh
# VERSION : 5.6.0
# AUTEUR  : Wilfried BARNAVON
# ROLE : MISE À NIVEAU MAJEURE (SAFE FORCE UPDATE)
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script met à jour TOUT : le code (scripts) ET les binaires (images Docker).
# C'est une opération "lourde" et destructrice (interruption de service).
#
# --- POURQUOI (WHY) ---
# À utiliser quand une nouvelle version d'Open WebUI sort (ex: 0.5 -> 0.6)
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
# 5. POST-OPTIM : Réapplique les dépendances critiques (orjson).
# ==============================================================================

# --- ETAPE 0 : MECANISME SELF-RUN (AUTO-PROTECTION) ---
SELF_PATH=$(realpath "$0")
TMP_SCRIPT="/tmp/upgrade-echo-running.sh"

if [ "$1" != "--self-run" ]; then
    echo "🔄 Copie du script en mémoire temporaire..."
    cp "$SELF_PATH" "$TMP_SCRIPT"
    chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" --self-run
fi

echo "🚀 Démarrage de la mise à jour ECHO 5.6.0..."

# --- ETAPE 1 : BACKUP RAPIDE ---
DATE_TAG=$(date +%Y%m%d_%H%M)
BACKUP_DIR="/opt/backups/pre_upgrade_$DATE_TAG"
mkdir -p "$BACKUP_DIR"
cp /opt/owui-pipes/*.py "$BACKUP_DIR/" 2>/dev/null || true
echo "💾 Sauvegarde préventive des Pipes dans $BACKUP_DIR"

# --- ETAPE 2 : SYNCHRONISATION CODE (GIT PULL FORCE) ---
# Note : En prod réelle, on ferait un git pull. Ici on simule la copie depuis /opt sources.
# On suppose que les nouveaux fichiers ont été déposés (ex: via scp ou le script deploy).
SRC_DIR="/opt/echo-framework-src" # Chemin théorique
if [ -d "$SRC_DIR" ]; then
    echo "📂 Synchronisation des scripts depuis $SRC_DIR..."
    cp -r "$SRC_DIR/00-Install/"* /opt/owui-scripts/
    cp -r "$SRC_DIR/03-OWUI-pipes/"* /opt/owui-pipes/
    cp -r "$SRC_DIR/04-OWUI-tools/"* /opt/owui-tools/
fi

# Helper function
sync_mirror_file() {
    local src_file=$1
    local dest_dir=$2
    if [ -f "$src_file" ]; then
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
# Télécharge les dernières versions sans redémarrer tout de suite
docker pull ghcr.io/open-webui/open-webui:main
docker pull python:3.11-slim
docker pull containrrr/watchtower

# --- ETAPE 5 : REBUILD COMPLET ---
echo "🏗️ [4/4] RECONSTRUCTION DE LA STACK..."
# On appelle le script d'install qui gère le redémarrage propre
if [ -f "/opt/owui-scripts/install-stack.sh" ]; then
    bash /opt/owui-scripts/install-stack.sh
else
    echo "❌ Erreur critique : install-stack.sh introuvable !"
    exit 1
fi

echo "✅ Mise à jour terminée avec succès."