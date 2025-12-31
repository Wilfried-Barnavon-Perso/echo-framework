#!/bin/bash
# ==============================================================================
# ECHO FRAMEWORK - UPGRADE MAJEUR (MIROIR & DESTRUCTIF)
# ==============================================================================
# - Fixes: BOM Removal + CRLF Removal pour compatibilité Windows/Linux
# ==============================================================================

# --- MECANISME SELF-RUN (Exécution depuis /tmp pour éviter l'auto-écrasement) ---
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

if [ "$EUID" -ne 0 ]; then
  echo "❌ Run as root (sudo)."
  exit 1
fi

# --- SECURITE ---
clear
echo "⚠️  ATTENTION : UPGRADE MAJEUR (DESTRUCTIF)"
echo "    Assurez-vous d'avoir sauvegardé vos données."
if [ -n "$SUDO_USER" ]; then
    echo "🔒 Confirmation requise :"
    sudo -k; if ! sudo -u "$SUDO_USER" sudo -v; then exit 1; fi
else
    read -p "Tapez 'CONFIRMER' : " CONFIRM
    [ "$CONFIRM" != "CONFIRMER" ] && exit 1
fi

echo "🔄 [1/4] SYNC GITHUB..."
if [ ! -d "$SRC_DIR" ]; then
    echo "   🆕 Clonage initial..."
    git clone "$GIT_REPO" "$SRC_DIR"
else
    echo "   📥 Pull updates (Branche courante)..."
    # Récupération de la branche active
    CURRENT_BRANCH=$(cd "$SRC_DIR" && git rev-parse --abbrev-ref HEAD)
    echo "      Branche active : $CURRENT_BRANCH"
    
    # Pull sur la branche active uniquement
    cd "$SRC_DIR" && git pull origin "$CURRENT_BRANCH" || echo "⚠️ Git pull failed (continuing local)"
fi

echo "📂 [2/4] DEPLOIEMENT SCRIPTS (MODE MIROIR)..."

sync_mirror() {
    local src="$1"
    local dest="$2"
    
    if [ ! -d "$src" ]; then
        echo "⚠️ Source manquante: $src (Ignoré)"
        return
    fi
    if [[ "$dest" != /opt/* ]]; then echo "⛔ Refus: $dest"; return; fi
    if [ ! -d "$dest" ]; then mkdir -p "$dest"; fi

    rm -rf "$dest"/*
    cp -rf "$src"/. "$dest"/
}

sync_mirror "$SRC_DIR/00-Install"       "/opt/owui-scripts"
sync_mirror "$SRC_DIR/04-OWUI-tools"    "/opt/owui-tools"
sync_mirror "$SRC_DIR/03-OWUI-functions" "/opt/owui-functions"
sync_mirror "$SRC_DIR/05-OWUI-filters"  "/opt/owui-filters"

sync_mirror_file() {
    local src_file="$1"
    local dest_dir="$2"
    if [ ! -f "$src_file" ]; then echo "⚠️ Fichier manquant: $src_file"; return; fi
    if [[ "$dest_dir" == /opt/* ]]; then
        rm -rf "$dest_dir"/*
        mkdir -p "$dest_dir"
        cp -f "$src_file" "$dest_dir/"
    fi
}

sync_mirror_file "$SRC_DIR/01-docker-admin-manager/server.py"     "/opt/admin-manager"
sync_mirror_file "$SRC_DIR/02-docker-python-worker/worker_api.py" "/opt/python-worker"
sync_mirror_file "$SRC_DIR/06-docker-browser-agent/browser_api.py" "/opt/browser-agent"

# --- NETTOYAGE ENCODING (WINDOWS FIX : BOM + CRLF) ---
echo "🧹 Nettoyage des caractères Windows (CRLF + BOM)..."
# 1. Suppression du BOM UTF-8 (Byte Order Mark) s'il existe sur la première ligne
# Le BOM (EF BB BF) casse le Shebang #!/bin/bash
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" \) -exec sed -i '1s/^\xEF\xBB\xBF//' {} +

# 2. Suppression des retours chariot Windows (\r)
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" \) -exec sed -i 's/\r$//' {} +

chmod +x /opt/owui-scripts/*.sh

echo "🐳 [3/4] DOCKER PULL..."
docker pull ghcr.io/open-webui/open-webui:main
docker pull python:3.11-slim
docker pull containrrr/watchtower

echo "🚀 [4/4] RECONSTRUCTION..."
docker system prune -f > /dev/null 2>&1
/bin/bash /opt/owui-scripts/install-stack.sh

echo "✨ UPGRADE TERMINÉ."