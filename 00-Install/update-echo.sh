#!/bin/bash
# ==============================================================================
# SCRIPT : update-echo.sh
# ROLE : MISE À JOUR RAPIDE (HOT FIX)
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script met à jour le CODE SOURCE de l'application (Python, Bash) sur la VM,
# sans toucher aux images Docker lourdes. C'est l'équivalent d'un "git pull" amélioré.
#
# --- POURQUOI (WHY) ---
# Télécharger les images Docker (upgrade) est long et coupe le service.
# 99% du temps, on veut juste pousser une modif dans `pipe_engine.py` ou `server.py`.
# Ce script fait cela en quelques secondes.
#
# --- COMMENT (HOW - ALGO) ---
# 1. AUTO-RÉPARATION GIT : Si le dossier /opt/echo-framework-source n'est pas un git valide,
#    il le clone automatiquement.
# 2. PULL : Il récupère les dernières modifs depuis GitHub.
# 3. MIROIR : Il copie les fichiers du dépôt Git vers les dossiers de production /opt/owui-*.
#    - Utilise 'sync_mirror' (destructif) pour les dossiers gérés intégralement.
#    - Utilise 'sync_mirror_file' (additif) pour les dossiers partagés.
# 4. RELOAD : Il redémarre les services impactés pour charger le nouveau code.
# ==============================================================================

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"

# Sécurité Root
if [ "$EUID" -ne 0 ]; then
  echo "❌ Run as root (sudo)."
  exit 1
fi

echo "🔄 [1/4] SYNC GITHUB..."
# --- LOGIQUE GIT AUTO-REPARATRICE ---
# Vérifie si le dossier .git existe. Si non, c'est que l'installation initiale
# n'a pas cloné le repo (mode "fichiers seuls"). On répare en clonant.
if [ ! -d "$SRC_DIR/.git" ]; then
    echo "   🆕 Dépôt Git introuvable. Clonage initial..."
    rm -rf "$SRC_DIR" # Nettoyage préventif
    git clone "$GIT_REPO" "$SRC_DIR"
    if [ $? -ne 0 ]; then
        echo "❌ Erreur critique : Impossible de cloner le dépôt."
        exit 1
    fi
else
    # Si le repo existe, on met à jour proprement
    echo "   📥 Pull updates..."
    cd "$SRC_DIR" || exit
    git reset --hard HEAD # Écrase les modifs locales accidentelles (Source de vérité = GitHub)
    git pull origin $(git rev-parse --abbrev-ref HEAD) || echo "⚠️ Git pull failed (continuing with local files if present)"
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
sync_mirror "$SRC_DIR/03-OWUI-functions" "/opt/owui-functions"
sync_mirror "$SRC_DIR/05-OWUI-filters"  "/opt/owui-filters"

# VERSIONING : Mise à jour du fichier système
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
# Appel à l'API interne d'Open WebUI pour recharger les configurations (Pipes, Tools)
if docker ps | grep -q open-webui; then
    docker exec open-webui /bin/bash /opt/owui-scripts/config-owui.sh
else
    echo "⚠️ Open WebUI non démarré."
fi

echo "✅ UPDATE TERMINÉ."