#!/bin/bash
# ==============================================================================
# SCRIPT : update-echo.sh
# VERSION : 5.6.0
# AUTEUR  : Wilfried BARNAVON
# DATE    : 2026-01-16
# ROLE : MISE À JOUR RAPIDE (HOT FIX & SYNC)
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script met à jour le CODE SOURCE de l'application (Python, Bash) sur la VM,
# sans toucher aux images Docker lourdes. C'est l'équivalent d'un "git pull" amélioré
# et orchestré.
#
# --- POURQUOI (WHY) ---
# Télécharger les images Docker (upgrade) est long (> 5min) et coupe le service.
# 99% du temps, les mises à jour concernent la logique métier (Pipe, Tools, Admin).
# Ce script déploie ces changements en quelques secondes (< 10s).
#
# --- COMMENT (HOW - ALGO) ---
# 1. SÉLECTION BRANCHE : Lit /opt/ECHO_BRANCH pour savoir quelle version suivre.
# 2. AUTO-RÉPARATION GIT : Si le dossier /opt/echo-framework-source n'est pas un git valide,
#    il le clone automatiquement. Si la branche n'existe pas, il prévient.
# 3. PULL : Il récupère les dernières modifs depuis GitHub.
# 4. MIROIR : Il copie les fichiers du dépôt Git vers les dossiers de production /opt/owui-*.
#    - Utilise 'sync_mirror' (destructif) pour les dossiers gérés intégralement.
#    - Utilise 'sync_mirror_file' (additif) pour les dossiers partagés.
# 5. RELOAD : Il redémarre les services impactés pour charger le nouveau code.
# ==============================================================================

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"
BRANCH_FILE="/opt/ECHO_BRANCH"

# Sécurité Root : Les manipulations dans /opt requièrent les privilèges
if [ "$EUID" -ne 0 ]; then
  echo "❌ Run as root (sudo)."
  exit 1
fi

# --- ETAPE 1 : DÉTERMINATION DE LA CIBLE ---
# On lit le fichier de branche système. Si absent, on vise 'main'.
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then
    # Lecture propre (suppression des espaces/sauts de ligne éventuels)
    TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]')
    echo "🌿 Cible : Branche '$TARGET_BRANCH' (définie dans $BRANCH_FILE)"
else
    echo "🌿 Cible : Branche 'main' (défaut)"
fi

echo "🔄 [1/4] SYNC GITHUB..."

# --- ETAPE 2 : LOGIQUE GIT AUTO-REPARATRICE ---
# Scénario A : Le dépôt n'a jamais été cloné (Install via VM-install V5.2)
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
    # On écrase les modifications locales accidentelles pour éviter les conflits de merge.
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

echo "📂 [2/4] DEPLOIEMENT FICHIERS (MODE MIROIR)..."

# Fonction : sync_mirror (Mode Strict)
# But : Faire en sorte que DEST soit exactement égal à SRC.
# Danger : SUPPRIME tout ce qui est dans DEST avant de copier.
# Usage : Pour les dossiers de scripts/outils qu'on maîtrise à 100%.
sync_mirror() {
    local src="$1"
    local dest="$2"
    if [[ "$dest" == /opt/* ]]; then # Sécurité anti-rm /
        mkdir -p "$dest"
        rm -rf "$dest"/* # Clean
        cp -rf "$src"/. "$dest"/ # Copy
    fi
}

sync_mirror "$SRC_DIR/00-Install"       "/opt/owui-scripts"
sync_mirror "$SRC_DIR/04-OWUI-tools"    "/opt/owui-tools"
sync_mirror "$SRC_DIR/03-OWUI-pipes"    "/opt/owui-pipes"
sync_mirror "$SRC_DIR/07-OWUI-actions"  "/opt/owui-actions"
# INTEGRATION FILTRE : Le dossier filters est synchronisé intégralement (incluant bypass_rag.py)
sync_mirror "$SRC_DIR/05-OWUI-filters"  "/opt/owui-filters"

# VERSIONING UPDATE : Mise à jour du fichier système (/opt/ECHO_VERSION)
if [ -f "$SRC_DIR/VERSION" ]; then
    cp "$SRC_DIR/VERSION" "/opt/ECHO_VERSION"
    chmod 644 "/opt/ECHO_VERSION"
fi

# Fonction : sync_mirror_file (Mode Additif)
# But : Mettre à jour un fichier spécifique sans toucher aux voisins.
# Usage : Pour les services (Admin, Worker) qui ont leurs propres fichiers de données/logs.
sync_mirror_file() {
    local src_file="$1"
    local dest_dir="$2"
    if [ ! -f "$src_file" ]; then echo "⚠️ Fichier manquant: $src_file"; return; fi
    if [[ "$dest_dir" == /opt/* ]]; then
        mkdir -p "$dest_dir"
        cp -f "$src_file" "$dest_dir/" # Écrase seulement le fichier cible
    fi
}

sync_mirror_file "$SRC_DIR/01-docker-admin-manager/server.py"     "/opt/admin-manager"
sync_mirror_file "$SRC_DIR/02-docker-python-worker/worker_api.py" "/opt/python-worker"
sync_mirror_file "$SRC_DIR/06-docker-browser-agent/browser_api.py" "/opt/browser-agent"

chmod +x /opt/owui-scripts/*.sh

echo "⚡ [3/4] HOT RELOAD SERVICES..."
# Redémarrage des micro-services pour prendre en compte le nouveau code Python
docker restart admin-manager python-worker browser-agent > /dev/null 2>&1

echo "🤖 [4/4] CONFIG API OPEN WEBUI..."
# Appel à l'API interne d'Open WebUI pour recharger les configurations (Pipes, Tools, Filters)
# Cela évite de devoir redémarrer Open WebUI (qui est long) juste pour un changement de Pipe.
if docker ps | grep -q open-webui; then
    docker exec open-webui /bin/bash /opt/owui-scripts/config-owui.sh
else
    echo "⚠️ Open WebUI non démarré."
fi

echo "✅ UPDATE TERMINÉ (Branche: $TARGET_BRANCH)."