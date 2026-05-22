#!/bin/bash
# ==============================================================================
# SCRIPT : sync-echo.sh
# VERSION : 4.5
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : 
# 1. SYNCHRONISATION DU CODE SOURCE (GitHub -> Local Source)
# 2. DÉPLOIEMENT DES FICHIERS (Local Source -> /opt/ECHO/...)
# ==============================================================================

# --- 0. INITIALISATION CORE ---
ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"

# Si le fichier global existe (déploiements futurs), on le source
if [ -f "$GLOBALS_FILE" ]; then
    source "$GLOBALS_FILE"
else
    # Configuration par défaut pour le premier déploiement
    export ECHO_ROOT="/opt/ECHO"
    export ECHO_SCRIPTS="$ECHO_ROOT/echo-scripts"
    export ECHO_CONFIG="$ECHO_ROOT/config"
    export ECHO_SOURCE="$ECHO_ROOT/source"
    export ECHO_SECRETS="$ECHO_ROOT/.secrets"
    export ECHO_VERSION_FILE="$ECHO_ROOT/ECHO_VERSION"
    export ECHO_BRANCH_FILE="$ECHO_ROOT/ECHO_BRANCH"
fi

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="$ECHO_SOURCE"
BRANCH_FILE="$ECHO_BRANCH_FILE"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/${CURRENT_SCRIPT##*/}"
MY_OWN_ORIGIN="$ECHO_SCRIPTS/${CURRENT_SCRIPT##*/}"

if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    # S'assurer que le dossier script existe pour la copie
    mkdir -p "$ECHO_SCRIPTS"
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
        # Scénario A : Clonage Initial ou Initialisation Git d'un dossier existant
        if [ -d "$SRC_DIR" ] && [ "$(ls -A "$SRC_DIR" 2>/dev/null)" ]; then
            echo "   📂 Dépôt .git manquant mais répertoire non vide. Initialisation Git locale..."
            cd "$SRC_DIR" || exit
            git init
            git remote add origin "$GIT_REPO"
            git fetch origin
            
            if git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
                git checkout -f "$TARGET_BRANCH"
                git reset --hard "origin/$TARGET_BRANCH"
                git branch --set-upstream-to="origin/$TARGET_BRANCH" "$TARGET_BRANCH" 2>/dev/null || true
            else
                echo "❌ ERREUR : La branche '$TARGET_BRANCH' n'existe pas sur le dépôt distant."
                exit 1
            fi
        else
            echo "   🆕 Dépôt introuvable. Clonage propre..."
            mkdir -p "$ECHO_ROOT"
            git clone "$GIT_REPO" "$SRC_DIR"
            if [ $? -ne 0 ]; then echo "❌ Erreur critique : Impossible de cloner."; exit 1; fi
            
            cd "$SRC_DIR" || exit
            git fetch origin
            if git rev-parse --verify "origin/$TARGET_BRANCH" > /dev/null 2>&1; then
                # reset --hard : ignore les fichiers modifiés localement lors du clone (ex: .gitignore)
                git checkout -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
                git reset --hard "origin/$TARGET_BRANCH"
            else
                echo "❌ ERREUR : La branche '$TARGET_BRANCH' n'existe pas."
                exit 1
            fi
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
echo "📂 [SYNC] 2/2 Déploiement des fichiers vers $ECHO_ROOT..."

mkdir -p "$ECHO_SECRETS"

# Table d'état : chemins déjà purgés dans ce run (évite la double suppression)
_HEALED_PATHS=""

sync_resource() {
    local src="$1"; local dest="$2"
    [ -e "$src" ] || return 0

    if [ -d "$src" ]; then
        mkdir -p "$dest"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete "$src/" "$dest/"
        else
            rm -rf "$dest"/*
            cp -rf "$src"/. "$dest"/
        fi
    elif [ -f "$src" ]; then
        # Self-Healing : si Docker a créé un dossier à la place du fichier attendu
        if [ -d "$dest" ]; then
            # Ne supprimer ce chemin qu'une seule fois par run (sécurité anti-double purge)
            if ! echo "$_HEALED_PATHS" | grep -qF "|$dest|"; then
                rm -rf "$dest"
                _HEALED_PATHS="$_HEALED_PATHS|$dest|"
            fi
        fi
        mkdir -p "$(dirname "$dest")" 2>/dev/null || true
        cp -f "$src" "$dest"
    fi
}

# Distribution des Ressources
sync_resource "$SRC_DIR/00-echo-scripts"       "$ECHO_SCRIPTS"
sync_resource "$SRC_DIR/01-config"             "$ECHO_CONFIG"
sync_resource "$SRC_DIR/12-owui-tools"         "$ECHO_ROOT/owui-tools"
sync_resource "$SRC_DIR/10-owui-pipes"         "$ECHO_ROOT/owui-pipes"
sync_resource "$SRC_DIR/13-owui-actions"       "$ECHO_ROOT/owui-actions"
sync_resource "$SRC_DIR/11-owui-filters"       "$ECHO_ROOT/owui-filters"
sync_resource "$SRC_DIR/14-owui-libs"          "$ECHO_ROOT/owui-libs"
sync_resource "$SRC_DIR/_assets/images"        "$ECHO_ROOT/echo-images"

# Docker Build Contexts
sync_resource "$SRC_DIR/20-docker-admin-manager"    "$ECHO_ROOT/docker-admin-manager"
sync_resource "$SRC_DIR/21-docker-python-worker"    "$ECHO_ROOT/docker-python-worker"
sync_resource "$SRC_DIR/22-docker-browser-agent/browser_api.py" "$ECHO_ROOT/docker-browser-agent/browser_api.py"
sync_resource "$SRC_DIR/23-docker-embedding-worker" "$ECHO_ROOT/docker-embedding-worker"

# Lien symboliques
echo "   🔗 Création des liens symboliques globaux..."
for script_path in "$ECHO_SCRIPTS"/*.sh; do
    [ -e "$script_path" ] || continue
    script_name=$(basename "$script_path" .sh)
    ln -sf "$script_path" "/usr/local/bin/$script_name"
done

ln -sf "$ECHO_SCRIPTS/upgrade-echo.sh" /usr/local/bin/rebuild-echo

# Versioning
if [ -f "$SRC_DIR/VERSION" ]; then 
    cp "$SRC_DIR/VERSION" "$ECHO_VERSION_FILE"
    chmod 644 "$ECHO_VERSION_FILE"
fi

# Nettoyage et Permissions
echo "   🧹 Nettoyage des caractères Windows et permissions..."
# Liste des dossiers à nettoyer (tous les dossiers de prod sous ECHO_ROOT)
PROD_DIRS="$ECHO_SCRIPTS $ECHO_CONFIG $ECHO_ROOT/docker-admin-manager $ECHO_ROOT/docker-python-worker $ECHO_ROOT/docker-browser-agent $ECHO_ROOT/docker-embedding-worker"

find $PROD_DIRS -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yml" -o -name "*.md" -o -name "VERSION" -o -name "Dockerfile" -o -name "requirements.txt" \) -exec sed -i '1s/^\xEF\xBB\xBF//' {} +
find $PROD_DIRS -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yml" -o -name "*.md" -o -name "VERSION" -o -name "Dockerfile" -o -name "requirements.txt" \) -exec sed -i 's/\r$//' {} +
chmod +x "$ECHO_SCRIPTS"/*.sh

# RELANCE DU SCRIPT SI MIS A JOUR
if ! diff "$MY_OWN_ORIGIN" "$CURRENT_SCRIPT" > /dev/null 2>&1 ; then
    exec "$MY_OWN_ORIGIN" "$@"; exit 0
fi

# Lecture Version Finale
FINAL_VERSION="unknown"
if [ -f "$ECHO_VERSION_FILE" ]; then FINAL_VERSION=$(cat "$ECHO_VERSION_FILE"); fi

echo "✅ [SYNC] Code source déployé avec succès ($FINAL_VERSION) dans $ECHO_ROOT."
