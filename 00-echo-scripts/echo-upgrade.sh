#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : echo-upgrade.sh
# VERSION : 6.22.1.1
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À NIVEAU MAJEURE (IMAGES DOCKER + CODE + RECREATION CONTAINERS)
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.22.1.1: Purge effective des secrets .secrets/.env lors d'un echo-rebuild en conservant ECHO_DOMAIN et TZ.
# 6.21.0.1: Export de CONFIRMED_YET avant auto-relance pour éviter la double confirmation interactive.
# 6.18.16.1: Suppression des variables Compose inutilisées (DOCKER_COMPOSE_CMD, COMPOSE_FILE).
# 6.8.1.1: Propagation du code d'échec de install-stack.sh (|| exit 1) afin de ne pas afficher 'UPGRADE TERMINÉ' en cas d'erreur.
# 5.231.39.1: Renommage en echo-upgrade.sh, commande destructrice echo-rebuild et ciblage de echo-sync.sh.

# --- INITIALISATION : CORE ECHO GLOBALS ---
ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ]; then
    source "$GLOBALS_FILE"
else
    echo "❌ CRITIQUE : Fichier global introuvable ($GLOBALS_FILE)."
    exit 1
fi
# ------------------------------------------

SYNC_SCRIPT="$ECHO_SCRIPTS/echo-sync.sh"
export COMPOSE_PROJECT_NAME="echo"

if [ "$EUID" -ne 0 ]; then echo "❌ Run as root (sudo)."; exit 1; fi

clear

if [[ "$0" == *"/echo-rebuild" || "$0" == "echo-rebuild" ]] ; then
    echo "⚠️  RECONSTRUCTION COMPLETE DE LA STACK ECHO"
    echo "    Cette opération va :"
    echo "    1. Arrêter tous les services de la stack"
    echo "    2. Supprimer TOUS LES VOLUMES des conteneurs"
    echo "    3. Supprimer toutes les images dockerfile"
    echo "    4. Supprimer les fichiers secrets locaux (Reset Auth)"
    echo ""
    read -p "Tapez 'CONFIRMER' pour initier la procédure de destruction : " CONFIRM
    [ "$CONFIRM" != "CONFIRMER" ] && exit 1
    
    echo ""
    echo "🚨 AVERTISSEMENT CRITIQUE 🚨"
    echo "Tu es sur le point d'effacer DÉFINITIVEMENT toutes les données persistantes :"
    echo "- Historiques de conversation et base SQLite Open WebUI"
    echo "- Workflows et identifiants N8N"
    echo "- Base de données vectorielle (RAG)"
    echo "- Fichiers secrets IdP"
    echo "IL N'Y AURA AUCUN RETOUR EN ARRIÈRE POSSIBLE."
    echo ""
    read -p "Tape 'DETRUIRE' pour confirmer l'effacement total : " CONFIRM2
    [ "$CONFIRM2" != "DETRUIRE" ] && exit 1
    
    export CONFIRMED_YET="yes"
    docker stop $(docker ps -aq) > /dev/null 2>&1 && echo "Services arrêtés"
    docker rm $(docker ps -aq) > /dev/null 2>&1 && echo "Conteneurs supprimés"
    docker volume rm $(docker volume ls -q) > /dev/null 2>&1 && echo "Volumes actifs supprimés"
    docker system prune -a --volumes -f > /dev/null 2>&1 && echo "Volumes orphelins et images supprimé"
    docker buildx prune -a -f > /dev/null 2>&1 && echo "Cache de construction purgé"
    local_env_src="${ECHO_ENV_FILE:-$ECHO_SECRETS/.env}"
    [ -f "$local_env_src" ] || local_env_src="$ECHO_ROOT/.env"
    SAVED_DOMAIN=""
    SAVED_TZ=""
    if [ -f "$local_env_src" ]; then
        SAVED_DOMAIN=$(grep -E '^ECHO_DOMAIN=' "$local_env_src" 2>/dev/null | tail -n 1 | cut -d '=' -f2- | tr -d "\"'[:space:]" || true)
        SAVED_TZ=$(grep -E '^TZ=' "$local_env_src" 2>/dev/null | tail -n 1 | cut -d '=' -f2- | tr -d "\"'[:space:]" || true)
    fi
    rm -rf "$ECHO_SECRETS" "$ECHO_ROOT/.env" && echo "Fichiers secrets et .env supprimés"
    mkdir -p "$ECHO_SECRETS" && chmod 700 "$ECHO_SECRETS"
    (umask 077 && touch "$ECHO_SECRETS/.env") && chmod 600 "$ECHO_SECRETS/.env"
    [ -n "$SAVED_DOMAIN" ] && echo "ECHO_DOMAIN=\"$SAVED_DOMAIN\"" >> "$ECHO_SECRETS/.env"
    [ -n "$SAVED_TZ" ] && echo "TZ=\"$SAVED_TZ\"" >> "$ECHO_SECRETS/.env"
    ln -sfn ".secrets/.env" "$ECHO_ROOT/.env"
fi

# --- SELF RUN (Protection) ---
CURRENT_SCRIPT=$(readlink -f "$0"); TMP_SCRIPT="/tmp/${CURRENT_SCRIPT##*/}"
MY_OWN_ORIGIN="$ECHO_SCRIPTS/${CURRENT_SCRIPT##*/}"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"; chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"; exit 0
fi

# --- CONFIRMATION ---
BRANCH_FILE="$ECHO_BRANCH_FILE"
TARGET_BRANCH="main"
if [ -f "$BRANCH_FILE" ]; then TARGET_BRANCH=$(cat "$BRANCH_FILE" | tr -d '[:space:]'); fi

echo "⚠️  UPGRADE MAJEUR via DOCKER-COMPOSE"
echo "    Cette opération va :"
echo "    1. Synchroniser le code et écraser les modifications locales"
echo "    2. Lancer le redéploiement complet de la stack (pull, build, up)"
echo "    Branche cible : $TARGET_BRANCH"
echo ""
[ -v CONFIRMED_YET ] || { 
        read -p "Tapez 'CONFIRMER' : " CONFIRM
        [ "$CONFIRM" != "CONFIRMER" ] && exit 1
        export CONFIRMED_YET="yes"
}

# --- 1. SYNC & DEPLOY (Centralisé) ---
if [ -f "$SYNC_SCRIPT" ]; then
    /bin/bash "$SYNC_SCRIPT" || exit 1
else
    echo "⚠️  Script de sync introuvable ($SYNC_SCRIPT). Installation corrompue ?"
    exit 1
fi

# RELANCE DU SCRIPT SI MIS A JOUR
if [ -f "$MY_OWN_ORIGIN" ] && ! diff "$MY_OWN_ORIGIN" "$CURRENT_SCRIPT" > /dev/null 2>&1 ; then
    exec "$MY_OWN_ORIGIN" "$@"; exit 0
fi

# --- 2. DELEGATION AU LAUNCHER ---
# --- 3. REBUILD / RELAUNCH ---
echo "🚀 [UPGRADE] Relance de la stack (via install-stack.sh)..."
/bin/bash "$ECHO_SCRIPTS/install-stack.sh" || { echo "❌ ÉCHEC DE L'UPGRADE."; exit 1; }

echo "✨ UPGRADE TERMINÉ."
