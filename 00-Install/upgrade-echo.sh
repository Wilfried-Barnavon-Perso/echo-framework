#!/bin/bash
# ==============================================================================
# ECHO FRAMEWORK - UPGRADE MAJEUR (SAFE FORCE UPDATE)
# ==============================================================================
# DESCRIPTION :
# Ce script effectue une mise à jour complète et destructrice de l'environnement ECHO.
# Il est conçu pour être lancé rarement (lors des changements de version majeurs).
#
# PHILOSOPHIE "SOURCE OF TRUTH" :
# La vérité absolue est le dépôt GitHub. Toute modification locale non commitée
# sur la VM sera impitoyablement écrasée pour garantir une cohérence parfaite.
#
# ALGORITHME GLOBAL :
# 1. Self-Run : Se copie dans /tmp pour ne pas s'auto-détruire pendant la mise à jour.
# 2. Sync Git : Force la synchronisation avec GitHub (Clone ou Hard Reset).
# 3. Mode Miroir : Remplace les fichiers de la VM (/opt/...) par ceux du Git.
# 4. Nettoyage : Supprime les caractères Windows (CRLF) qui cassent les scripts Linux.
# 5. Docker Pull : Télécharge les nouvelles versions des images (binaires).
# 6. Rebuild : Relance toute la stack via install-stack.sh.
# ==============================================================================

# --- ETAPE 0 : MECANISME SELF-RUN (AUTO-PROTECTION) ---
# Problème : Si le script se met à jour lui-même pendant qu'il s'exécute, le shell peut crasher.
# Solution : On copie le script dans /tmp (mémoire vive) et on exécute la copie.
CURRENT_SCRIPT=$(readlink -f "$0")
TMP_SCRIPT="/tmp/upgrade-echo-running.sh"

if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    echo "🔄 Préparation de l'environnement de mise à jour..."
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"
    chmod +x "$TMP_SCRIPT"
    echo "🚀 Bascule vers l'exécution temporaire..."
    # 'exec' remplace le processus actuel par le nouveau : le script d'origine est libéré.
    exec "$TMP_SCRIPT" "$@"
    exit 0
fi

# ==============================================================================
# LE CODE CI-DESSOUS S'EXECUTE DEPUIS /tmp/upgrade-echo-running.sh
# ==============================================================================

GIT_REPO="https://github.com/Wilfried-Barnavon-Perso/echo-framework.git"
SRC_DIR="/opt/echo-framework-source"

# Sécurité : Seul root peut toucher aux dossiers système /opt
if [ "$EUID" -ne 0 ]; then
  echo "❌ Run as root (sudo)."
  exit 1
fi

# --- SECURITE UTILISATEUR ---
# On demande une confirmation explicite car l'opération est irréversible.
clear
echo "⚠️  ATTENTION : UPGRADE MAJEUR (DESTRUCTIF)"
echo "    Cette opération va :"
echo "    1. Écraser TOUTES les modifications locales dans /opt/echo-framework-source"
echo "    2. Redéployer les conteneurs (indisponibilité temporaire)"
echo "    Assurez-vous d'avoir sauvegardé vos données."
if [ -n "$SUDO_USER" ]; then
    echo "🔒 Confirmation requise :"
    sudo -k; if ! sudo -u "$SUDO_USER" sudo -v; then exit 1; fi
else
    read -p "Tapez 'CONFIRMER' : " CONFIRM
    [ "$CONFIRM" != "CONFIRMER" ] && exit 1
fi

# --- ETAPE 1 : SYNCHRONISATION GIT (AUTO-RÉPARATION) ---
echo "🔄 [1/4] SYNC GITHUB (FORCE MODE)..."

# Cas A : Le dossier git n'existe pas (ex: installation via VM-install V5.2)
# Action : On clone le dépôt pour créer le lien avec GitHub.
if [ ! -d "$SRC_DIR/.git" ]; then
    echo "   🆕 Dépôt Git introuvable. Clonage initial..."
    rm -rf "$SRC_DIR" # On nettoie un éventuel dossier vide ou corrompu
    git clone "$GIT_REPO" "$SRC_DIR"
    if [ $? -ne 0 ]; then
        echo "❌ Erreur critique : Impossible de cloner le dépôt."
        exit 1
    fi
# Cas B : Le dépôt existe déjà
# Action : On force la mise à jour (Hard Reset) pour écraser toute dérive locale.
else
    echo "   🧹 Nettoyage des modifications locales..."
    cd "$SRC_DIR" || exit
    
    # FIX CRITIQUE : git reset --hard HEAD
    # Annule toutes les modifications non commitées dans le dossier de travail.
    # git clean -fd : Supprime les nouveaux fichiers/dossiers non trackés.
    git reset --hard HEAD
    git clean -fd
    
    echo "   📥 Pull updates..."
    # On tire la dernière version de la branche principale
    git pull origin main || echo "⚠️ Git pull failed"
fi

# --- ETAPE 2 : DÉPLOIEMENT FICHIERS (MODE MIROIR) ---
echo "📂 [2/4] DEPLOIEMENT SCRIPTS (MODE MIROIR)..."

# Fonction utilitaire pour synchroniser un dossier entier
# Elle supprime le contenu de destination avant de copier pour éviter les fichiers fantômes.
sync_mirror() {
    local src="$1"
    local dest="$2"
    
    if [ ! -d "$src" ]; then
        echo "⚠️ Source manquante: $src (Ignoré)"
        return
    fi
    # Sécurité : On ne touche qu'aux dossiers dans /opt
    if [[ "$dest" != /opt/* ]]; then echo "⛔ Refus: $dest"; return; fi
    if [ ! -d "$dest" ]; then mkdir -p "$dest"; fi

    # Suppression propre et copie récursive
    rm -rf "$dest"/*
    cp -rf "$src"/. "$dest"/
}

# Déploiement des dossiers clés
sync_mirror "$SRC_DIR/00-Install"       "/opt/owui-scripts"
sync_mirror "$SRC_DIR/04-OWUI-tools"    "/opt/owui-tools"
sync_mirror "$SRC_DIR/03-OWUI-functions" "/opt/owui-functions"
sync_mirror "$SRC_DIR/05-OWUI-filters"  "/opt/owui-filters"

# Fonction utilitaire pour synchroniser un fichier unique
sync_mirror_file() {
    local src_file="$1"
    local dest_dir="$2"
    if [ ! -f "$src_file" ]; then echo "⚠️ Fichier manquant: $src_file"; return; fi
    if [[ "$dest_dir" == /opt/* ]]; then
        rm -rf "$dest_dir"/* # On vide le dossier cible (ex: /opt/admin-manager)
        mkdir -p "$dest_dir"
        cp -f "$src_file" "$dest_dir/"
    fi
}

# Déploiement des services Python (Admin, Worker, Browser)
sync_mirror_file "$SRC_DIR/01-docker-admin-manager/server.py"     "/opt/admin-manager"
sync_mirror_file "$SRC_DIR/02-docker-python-worker/worker_api.py" "/opt/python-worker"
sync_mirror_file "$SRC_DIR/06-docker-browser-agent/browser_api.py" "/opt/browser-agent"

# --- ETAPE 3 : NETTOYAGE ENCODING (WINDOWS FIX) ---
# Problème : Les fichiers édités sous Windows peuvent avoir des fins de ligne CRLF (\r\n) ou un BOM UTF-8.
# Ces caractères invisibles font planter les scripts Bash et Python sous Linux.
# Solution : On utilise 'sed' pour les retirer massivement.
echo "🧹 Nettoyage des caractères Windows (CRLF + BOM)..."
# 1. Suppression du BOM (Byte Order Mark) en début de fichier
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" \) -exec sed -i '1s/^\xEF\xBB\xBF//' {} +
# 2. Conversion CRLF -> LF (Suppression du \r en fin de ligne)
find /opt/owui-scripts /opt/admin-manager /opt/python-worker /opt/browser-agent -type f \( -name "*.sh" -o -name "*.py" \) -exec sed -i 's/\r$//' {} +

# On rend les scripts exécutables
chmod +x /opt/owui-scripts/*.sh

# --- ETAPE 4 : MISE A JOUR DES IMAGES DOCKER ---
echo "🐳 [3/4] DOCKER PULL..."
# On demande à Docker de télécharger les dernières versions depuis le registre.
# C'est ce qui fait la différence entre 'update' (code) et 'upgrade' (binaires).
docker pull ghcr.io/open-webui/open-webui:main
docker pull python:3.11-slim
docker pull containrrr/watchtower

# --- ETAPE 5 : RECONSTRUCTION DE L'INFRASTRUCTURE ---
echo "🚀 [4/4] RECONSTRUCTION..."
# Nettoyage des vieux conteneurs/images inutilisés pour gagner de la place
docker system prune -f > /dev/null 2>&1

# Appel du script d'installation standard pour relancer les conteneurs
# avec les nouvelles images et les nouveaux montages de volumes.
/bin/bash /opt/owui-scripts/install-stack.sh

echo "✨ UPGRADE TERMINÉ."