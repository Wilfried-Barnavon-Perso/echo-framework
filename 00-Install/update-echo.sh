#!/bin/bash
# ==============================================================================
# SCRIPT : update-echo.sh (VERSION COMPOSE / HYBRIDE V2)
# VERSION : 5.6.4
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À JOUR DU CODE (SCRIPTS, TOOLS, PIPES) + HOT RELOAD
#
# NOTE : Restauration de la logique sync_mirror et hot-reload.
# Intégration de la détection Docker Compose V2 pour compatibilité.
# ==============================================================================

# --- DETECTION DOCKER COMPOSE V2 (FIX COMPATIBILITÉ) ---
if docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker compose"
else
    if command -v docker-compose >/dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="docker-compose"
    else
        # Fallback critique (ne devrait pas arriver si install-stack a marché)
        echo "⚠️ Docker Compose introuvable. Tentative avec 'docker-compose'."
        DOCKER_COMPOSE_CMD="docker-compose"
    fi
fi

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"
BRANCH_FILE="/opt/ECHO_BRANCH"
COMPOSE_FILE="/opt/owui-scripts/docker-compose.yml"

# Fixer le nom du projet
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# --- 1. SYNC GITHUB (IDENTIQUE LEGACY) ---
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then 
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
    echo "🌿 Cible : Branche '$TARGET_BRANCH' (définie dans $BRANCH_FILE)"
else
    echo "🌿 Cible : Branche 'main' (défaut)"
fi

echo "🔄 [1/4] SYNC GITHUB..."

# LOGIQUE GIT AUTO-REPARATRICE (RESTAURÉE)
# Scénario A : Le dépôt n'a jamais été cloné
if [ ! -d "$SRC_DIR/.git" ]; then
    echo "   🆕 Dépôt Git introuvable. Clonage initial..."
    rm -rf "$SRC_DIR" # Nettoyage préventif
    
    # Clone initial
    git clone "$GIT_REPO" "$SRC_DIR"
    if [ $? -ne 0 ]; then
        echo "❌ Erreur critique : Impossible de cloner le dépôt."
        exit 1
    fi
    
    cd "$SRC_DIR" || exit
    
    # Vérification de l'existence de la branche cible avant de checkout
    if git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
        git checkout "$TARGET_BRANCH"
    else
        echo "❌ ERREUR FATALE : La branche '$TARGET_BRANCH' n'existe pas sur le dépôt distant."
        exit 1
    fi

# Scénario B : Le dépôt existe déjà
else
    echo "   📥 Mise à jour..."
    cd "$SRC_DIR" || exit
    
    # Reset local : On considère GitHub comme la source de vérité absolue.
    git reset --hard HEAD
    
    # Fetch : Récupération des méta-données distantes
    git fetch origin
    
    # Vérification de sécurité : La branche cible existe-t-elle toujours ?
    if ! git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
        echo "❌ ERREUR FATALE : La branche cible '$TARGET_BRANCH' est introuvable sur le remote."
        exit 1
    fi

    # Bascule et Pull
    git checkout "$TARGET_BRANCH" || exit 1
    git pull origin "$TARGET_BRANCH" || echo "⚠️ Git pull failed (continuing with local files if present)"
fi

# --- 2. DEPLOIEMENT FICHIERS (IDENTIQUE LEGACY) ---
echo "📂 [2/4] DEPLOIEMENT FICHIERS..."

sync_mirror() {
    local src="$1"; local dest="$2"
    # Protection contre les chemins vides
    if [ -z "$src" ] || [ -z "$dest" ]; then return; fi
    
    if [[ "$dest" == /opt/* ]]; then 
        mkdir -p "$dest"
        # On utilise rsync si dispo pour plus de propreté, sinon rm/cp
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete "$src/" "$dest/"
        else
            rm -rf "$dest"/*
            cp -rf "$src"/. "$dest"/
        fi
    fi
}

sync_mirror_file() {
    local src="$1"; local dest="$2"
    if [[ "$dest" == /opt/* ]]; then mkdir -p "$dest"; cp -f "$src" "$dest/"; fi
}

sync_mirror "$SRC_DIR/00-Install"       "/opt/owui-scripts"
sync_mirror "$SRC_DIR/04-OWUI-tools"    "/opt/owui-tools"
sync_mirror "$SRC_DIR/03-OWUI-pipes"    "/opt/owui-pipes"
sync_mirror "$SRC_DIR/07-OWUI-actions"  "/opt/owui-actions"
sync_mirror "$SRC_DIR/05-OWUI-filters"  "/opt/owui-filters"

# Mise à jour des scripts Python des conteneurs
sync_mirror_file "$SRC_DIR/01-docker-admin-manager/server.py"     "/opt/admin-manager"
sync_mirror_file "$SRC_DIR/02-docker-python-worker/worker_api.py" "/opt/python-worker"
sync_mirror_file "$SRC_DIR/06-docker-browser-agent/browser_api.py" "/opt/browser-agent"

if [ -f "$SRC_DIR/VERSION" ]; then cp "$SRC_DIR/VERSION" "/opt/ECHO_VERSION"; chmod 644 "/opt/ECHO_VERSION"; fi

# Nettoyage Windows et permissions
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yml" -o -name "VERSION" \) -exec sed -i '1s/^\xEF\xBB\xBF//' {} +
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yml" -o -name "VERSION" \) -exec sed -i 's/\r$//' {} +
chmod +x /opt/owui-scripts/*.sh

# --- 3. HOT RELOAD (VIA COMPOSE V2 ou V1) ---
echo "⚡ [3/4] HOT RELOAD SERVICES..."

if [ -f "$COMPOSE_FILE" ]; then
    # On restart pour prendre en compte le nouveau code Python
    # Note: On restart les services dont le code a changé
    # Utilisation de la commande détectée ($DOCKER_COMPOSE_CMD) au lieu de docker-compose en dur
    $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" restart admin-manager python-worker browser-agent
else
    echo "⚠️ Docker Compose introuvable ($COMPOSE_FILE). Fallback manuel."
    docker restart echo-admin-manager echo-python-worker echo-browser-agent
fi

# --- 4. CONFIG API ---
echo "🤖 [4/4] CONFIG API OPEN WEBUI..."
# On vérifie si le service est up via compose ps
if $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" ps -q open-webui >/dev/null 2>&1; then
    $DOCKER_COMPOSE_CMD -f "$COMPOSE_FILE" exec -T open-webui /bin/bash /opt/owui-scripts/config-owui.sh
fi

echo "✅ UPDATE TERMINÉ."