#!/bin/bash
# ==============================================================================
# ECHO FRAMEWORK - UPDATE RAPIDE (MIROIR & HOT RELOAD)
# ==============================================================================
# Actions :
# 1. Sync Git (Source)
# 2. SYNC MIROIR (Supprime vieux fichiers /opt -> Copie nouveaux)
# 3. Hot-Reload Python
# 4. Re-conf API
# ==============================================================================

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"

if [ "$EUID" -ne 0 ]; then
  echo "❌ Run as root (sudo)."
  exit 1
fi

echo "🔄 [1/4] SYNC GITHUB..."
if [ ! -d "$SRC_DIR" ]; then
    echo "   🆕 Clonage initial..."
    git clone "$GIT_REPO" "$SRC_DIR"
else
    echo "   📥 Pull updates..."
    cd "$SRC_DIR" && git pull origin $(git rev-parse --abbrev-ref HEAD) || echo "⚠️ Git pull failed (continuing local)"
fi

echo "📂 [2/4] DEPLOIEMENT FICHIERS (MODE MIROIR)..."

sync_mirror() {
    local src="$1"
    local dest="$2"
    if [[ "$dest" == /opt/* ]]; then
        mkdir -p "$dest"
        rm -rf "$dest"/* # Nettoyage
        cp -rf "$src"/. "$dest"/ # Copie
    fi
}

sync_mirror "$SRC_DIR/00-Install"       "/opt/owui-scripts"
sync_mirror "$SRC_DIR/04-OWUI-tools"    "/opt/owui-tools"
sync_mirror "$SRC_DIR/03-OWUI-functions" "/opt/owui-functions"
sync_mirror "$SRC_DIR/05-OWUI-filters"  "/opt/owui-filters"

sync_mirror_file() {
    local src_file="$1"
    local dest_dir="$2"
    if [[ "$dest_dir" == /opt/* ]]; then
        mkdir -p "$dest_dir"
        rm -rf "$dest_dir"/*
        cp -f "$src_file" "$dest_dir/"
    fi
}

sync_mirror_file "$SRC_DIR/01-docker-admin-manager/server.py"     "/opt/admin-manager"
sync_mirror_file "$SRC_DIR/02-docker-python-worker/worker_api.py" "/opt/python-worker"
sync_mirror_file "$SRC_DIR/06-docker-browser-agent/browser_api.py" "/opt/browser-agent"

chmod +x /opt/owui-scripts/*.sh

echo "⚡ [3/4] HOT RELOAD SERVICES..."
docker restart admin-manager python-worker browser-agent > /dev/null 2>&1

echo "🤖 [4/4] CONFIG API OPEN WEBUI..."
if docker ps | grep -q open-webui; then
    docker exec open-webui /bin/bash /opt/owui-scripts/config-owui.sh
else
    echo "⚠️ Open WebUI non démarré."
fi

echo "✅ UPDATE TERMINÉ."