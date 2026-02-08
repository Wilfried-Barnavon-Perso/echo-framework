#!/bin/bash
# ==============================================================================
# SCRIPT : sync-echo.sh
# VERSION : 3.9
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : 
# 1. SYNCHRONISATION DU CODE SOURCE (GitHub -> Local Source)
# 2. DÉPLOIEMENT DES FICHIERS (Local Source -> /opt/...)
# ==============================================================================

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"
BRANCH_FILE="/opt/ECHO_BRANCH"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/${CURRENT_SCRIPT##*/}"
MY_OWN_ORIGIN="/opt/echo-scripts/${CURRENT_SCRIPT##*/}"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"; chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"; exit 0
fi


# --- 1. DÉTERMINATION BRANCHE CIBLE ---
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then 
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
fi

# Parsing Arguments
LOCAL_ONLY="false"
for arg in "$@"; do
    if [ "$arg" == "--local-only" ]; then
        LOCAL_ONLY="true"
    fi
done

if [ "$LOCAL_ONLY" == "true" ]; then
    echo "🔄 [SYNC] 1/2 Mode LOCAL-ONLY détecté. Synchronisation Git ignorée."
    echo "   📂 Utilisation des sources présentes dans : $SRC_DIR"
else
    echo "🔄 [SYNC] 1/2 Synchronisation GitHub (Branche: $TARGET_BRANCH)..."

    # --- LOGIQUE GIT ROBUSTE ---
    if [ ! -d "$SRC_DIR/.git" ]; then
        # Scénario A : Clonage Initial
        echo "   🆕 Dépôt introuvable. Clonage propre..."
        rm -rf "$SRC_DIR"
        git clone "$GIT_REPO" "$SRC_DIR"
        if [ $? -ne 0 ]; then echo "❌ Erreur critique : Impossible de cloner."; exit 1; fi
        
        cd "$SRC_DIR" || exit
        if git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
            git checkout "$TARGET_BRANCH"
        else
            echo "❌ ERREUR : La branche '$TARGET_BRANCH' n'existe pas."
            exit 1
        fi
    else
        # Scénario B : Mise à jour (Reset Hard pour intégrité totale)
        echo "   📥 Alignement avec le dépôt distant..."
        cd "$SRC_DIR" || exit
        git fetch origin
        git reset --hard HEAD
        git clean -fd
        
        if git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
            git checkout "$TARGET_BRANCH"
            git reset --hard "origin/$TARGET_BRANCH"
        else
            echo "❌ ERREUR : Branche distante '$TARGET_BRANCH' introuvable."
            exit 1
        fi
    fi
fi

# --- 2. DÉPLOIEMENT FICHIERS ---
echo "📂 [SYNC] 2/2 Déploiement des fichiers..."

mkdir -p /opt/.owui-secrets

sync_resource() {
    local src="$1"; local dest="$2"
    # Si la source n'existe pas dans le repo, on ne fait rien
    [ -e "$src" ] || return 0

    if [ -d "$src" ]; then
        # CAS DOSSIER : rsync pour synchro miroir parfaite, sinon rm/cp
        mkdir -p "$dest"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete "$src/" "$dest/"
        else
            rm -rf "$dest"/*
            cp -rf "$src"/. "$dest"/
        fi
    elif [ -f "$src" ]; then
        # CAS FICHIER
        if [ -d "$dest" ]; then
            cp -f "$src" "$dest/"
        else
            mkdir -p "$dest" 2>/dev/null || true
            cp -f "$src" "$dest/"
        fi
    fi
}

# Synchro Dossiers
sync_resource "$SRC_DIR/00-echo-scripts"       "/opt/echo-scripts"
sync_resource "$SRC_DIR/01-config"             "/opt/config"
sync_resource "$SRC_DIR/12-owui-tools"         "/opt/owui-tools"
sync_resource "$SRC_DIR/10-owui-pipes"         "/opt/owui-pipes"
sync_resource "$SRC_DIR/13-owui-actions"       "/opt/owui-actions"
sync_resource "$SRC_DIR/11-owui-filters"       "/opt/owui-filters"
sync_resource "$SRC_DIR/_assets/images"        "/opt/echo-images"

# Synchro Fichiers (Code Python Containers)
# Admin Manager : Dossier complet requis pour le Dockerfile
sync_resource "$SRC_DIR/20-docker-admin-manager"    "/opt/docker-admin-manager"
sync_resource "$SRC_DIR/21-docker-python-worker/worker_api.py" "/opt/docker-python-worker"
sync_resource "$SRC_DIR/22-docker-browser-agent/browser_api.py" "/opt/docker-browser-agent"

# Lien symboliques (Automatique pour tous les scripts .sh)
echo "   🔗 Création des liens symboliques globaux..."
for script_path in /opt/echo-scripts/*.sh; do
    [ -e "$script_path" ] || continue
    # Extraction du nom sans extension (ex: update-echo.sh -> update-echo)
    script_name=$(basename "$script_path" .sh)
    ln -sf "$script_path" "/usr/local/bin/$script_name"
done

# Alias spécifique (Exception pour rebuild-echo qui n'est pas un fichier physique)
ln -sf /opt/echo-scripts/upgrade-echo.sh /usr/local/bin/rebuild-echo

# Versioning
if [ -f "$SRC_DIR/VERSION" ]; then cp "$SRC_DIR/VERSION" "/opt/ECHO_VERSION"; chmod 644 "/opt/ECHO_VERSION"; fi

# Nettoyage et Permissions (Fix Windows EOL)
echo "   🧹 Nettoyage des caractères Windows et permissions..."
find /opt/echo-scripts /opt/config /opt/docker-admin-manager /opt/docker-python-worker /opt/docker-browser-agent -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yml" -o -name "VERSION" \) -exec sed -i '1s/^\xEF\xBB\xBF//' {} +
find /opt/echo-scripts /opt/config /opt/docker-admin-manager /opt/docker-python-worker /opt/docker-browser-agent -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yml" -o -name "VERSION" \) -exec sed -i 's/\r$//' {} +
chmod +x /opt/echo-scripts/*.sh

# RELANCE DU SCRIPT SI MIS A JOUR
if ! diff "$MY_OWN_ORIGIN"  "$CURRENT_SCRIPT" > /dev/null 2>&1  ; then
    exec "$MY_OWN_ORIGIN" "$@"; exit 0
fi

echo "✅ [SYNC] Code source déployé avec succès."