#!/bin/bash
# ==============================================================================
# SCRIPT : echo-sync.sh
# VERSION : 5.231.39.1
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : 
# 1. SYNCHRONISATION DU CODE SOURCE (GitHub -> Local Source)
# 2. DÉPLOIEMENT DES FICHIERS (Local Source -> /opt/ECHO/...)
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.39.1: Renommage en echo-sync.sh et mise à jour du lien symbolique echo-rebuild.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 4.11: Ajout d'un encart visuel de versioning Git interactif (Avant/Après).
# 4.10: Déploiement déclaratif automatique via boucle regex. Suppression des fantômes.
# 4.9: Nettoyage conditionnel (sed) des caractères CRLF/BOM pour préserver le cache BuildKit.

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

# --- CAPTURE ETAT ACTUEL (AVANT SYNC) ---
OLD_VERSION="Aucune (1ère installation)"
if [ -f "$ECHO_VERSION_FILE" ]; then
    OLD_VERSION=$(cat "$ECHO_VERSION_FILE" | tr -d '\r\n')
fi

OLD_DESC="Aucun historique local"
if [ -d "$SRC_DIR/.git" ]; then
    OLD_DESC=$(git -C "$SRC_DIR" log -1 --pretty=format:"%s" 2>/dev/null || echo "Historique inaccessible")
fi

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

# --- CAPTURE NOUVEL ETAT (APRES SYNC) ---
NEW_VERSION="Inconnue"
if [ -f "$SRC_DIR/VERSION" ]; then
    NEW_VERSION=$(cat "$SRC_DIR/VERSION" | tr -d '\r\n')
fi

NEW_DESC="Aucun historique local"
if [ -d "$SRC_DIR/.git" ]; then
    NEW_DESC=$(git -C "$SRC_DIR" log -1 --pretty=format:"%s" 2>/dev/null || echo "Historique inaccessible")
fi

# --- AFFICHAGE VISUEL DES VERSIONS ---
echo ""
echo "############################################################"
echo "📌 PLAN DE SYNCHRONISATION"
echo "############################################################"
echo "🔻 Ancienne version : $OLD_VERSION"
echo "   > $OLD_DESC"
echo ""
echo "🔺 Nouvelle version : $NEW_VERSION"
echo "   > $NEW_DESC"
echo "############################################################"
echo ""

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

echo "   🧹 Purge préalable des sous-systèmes applicatifs pour éliminer les fantômes..."
find "$ECHO_ROOT" -maxdepth 1 -type d \( -name "owui-*" -o -name "docker-*" \) -exec rm -rf {} +

echo "   🔄 Synchronisation dynamique des composants (Routage Automatique)..."
for dir in "$SRC_DIR"/*; do
    [ -d "$dir" ] || continue
    base=$(basename "$dir")
    
    # Dossiers d'infrastructures
    if [[ "$base" == "00-echo-scripts" ]]; then
        sync_resource "$dir" "$ECHO_SCRIPTS"
    elif [[ "$base" == "01-config" ]]; then
        sync_resource "$dir" "$ECHO_CONFIG"
    elif [[ "$base" == "_assets" ]]; then
        sync_resource "$dir/images" "$ECHO_ROOT/echo-images"
        
    # Routage dynamique pour tout dossier 'XX-owui-*' ou 'XX-docker-*'
    elif [[ "$base" =~ ^[0-9]+-(owui|docker)-.* ]]; then
        dest_name=$(echo "$base" | sed -E 's/^[0-9]+-//')
        sync_resource "$dir" "$ECHO_ROOT/$dest_name"
    # Routage pour docker-python-base s'il n'a plus de numéro (suite au renommage)
    elif [[ "$base" =~ ^(owui|docker)-.* ]]; then
        sync_resource "$dir" "$ECHO_ROOT/$base"
    fi
done

# Lien symboliques
echo "   🔗 Création des liens symboliques globaux..."
for script_path in "$ECHO_SCRIPTS"/*.sh; do
    [ -e "$script_path" ] || continue
    script_name=$(basename "$script_path" .sh)
    ln -sf "$script_path" "/usr/local/bin/$script_name"
done

ln -sf "$ECHO_SCRIPTS/echo-upgrade.sh" /usr/local/bin/echo-rebuild

# Versioning
if [ -f "$SRC_DIR/VERSION" ]; then 
    cp "$SRC_DIR/VERSION" "$ECHO_VERSION_FILE"
    chmod 644 "$ECHO_VERSION_FILE"
fi

# Nettoyage et Permissions
echo "   🧹 Nettoyage intelligent des caractères Windows (Maintien du Cache Docker)..."
# Liste des dossiers à nettoyer (tous les dossiers de prod sous ECHO_ROOT + SRC_DIR pour éviter la recopie)
PROD_DIRS="$SRC_DIR $ECHO_SCRIPTS $ECHO_CONFIG $ECHO_ROOT/owui-* $ECHO_ROOT/docker-*"

find $PROD_DIRS -type f \( -name "*.sh" -o -name "*.py" -o -name "*.yml" -o -name "*.md" -o -name "VERSION" -o -name "Dockerfile" -o -name "requirements.txt" \) -exec bash -c '
for f; do
    if grep -q $'\''\r'\'' "$f" 2>/dev/null || head -c 3 "$f" | grep -q $'\''\xEF\xBB\xBF'\'' 2>/dev/null; then
        sed -i '\''1s/^\xEF\xBB\xBF//; s/\r$//'\'' "$f"
    fi
done
' _ {} +
chmod +x "$ECHO_SCRIPTS"/*.sh

# RELANCE DU SCRIPT SI MIS A JOUR
if [ -f "$MY_OWN_ORIGIN" ] && ! diff "$MY_OWN_ORIGIN" "$CURRENT_SCRIPT" > /dev/null 2>&1 ; then
    exec "$MY_OWN_ORIGIN" "$@"; exit 0
fi

# Lecture Version Finale
FINAL_VERSION="unknown"
if [ -f "$ECHO_VERSION_FILE" ]; then FINAL_VERSION=$(cat "$ECHO_VERSION_FILE"); fi

echo "✅ [SYNC] Code source déployé avec succès ($FINAL_VERSION) dans $ECHO_ROOT."
