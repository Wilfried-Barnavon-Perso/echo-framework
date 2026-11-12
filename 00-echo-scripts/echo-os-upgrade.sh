#!/bin/bash
# ==============================================================================
# SCRIPT : echo-os-upgrade.sh
# VERSION : 6.5.0.1
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : MISE À NIVEAU DU SYSTÈME HÔTE (UBUNTU) VIA APT + MAINTENANCE ECHO
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.5.0.1: Création initiale du script de mise à niveau OS sécurisée avec flush Docker et détection reboot noyau.

# --- JOURNALISATION ---
LOG_FILE="/var/log/echo-os-upgrade.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=================================================="
echo "🚀 Début de la mise à niveau système : $(date)"

# --- INITIALISATION : CORE ECHO GLOBALS ---
ECHO_ROOT="/opt/ECHO"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ]; then
    source "$GLOBALS_FILE"
else
    echo "❌ CRITIQUE : Fichier global introuvable ($GLOBALS_FILE)."
    exit 1
fi

if [ "$EUID" -ne 0 ]; then
    echo "❌ Exécution root requise (sudo)."
    exit 1
fi

# --- PROTECTION AUTO-RUN (/tmp) ---
CURRENT_SCRIPT=$(readlink -f "$0")
TMP_SCRIPT="/tmp/${CURRENT_SCRIPT##*/}"
MY_OWN_ORIGIN="$ECHO_SCRIPTS/${CURRENT_SCRIPT##*/}"
if [[ "$CURRENT_SCRIPT" != "/tmp/"* ]]; then
    cp "$CURRENT_SCRIPT" "$TMP_SCRIPT"
    chmod +x "$TMP_SCRIPT"
    exec "$TMP_SCRIPT" "$@"
    exit 0
fi

# --- GESTION DES ARGUMENTS ---
ASSUME_YES=false
for arg in "$@"; do
    case "$arg" in
        -y|--yes)
            ASSUME_YES=true
            ;;
    esac
done

if [ "$ASSUME_YES" = false ]; then
    echo "⚠️  Cette opération va :"
    echo "    1. Arrêter temporairement les conteneurs Docker pour préserver les bases de données"
    echo "    2. Mettre à niveau les paquets de la VM Ubuntu via apt dist-upgrade"
    echo "    3. Purger les anciens noyaux et fichiers temporaires via echo-clean.sh"
    echo "    4. Redémarrer la machine si un nouveau noyau est détecté, ou relancer la stack"
    echo ""
    read -p "Confirmer la mise à niveau système ? [o/N] : " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[oOyY]$ ]]; then
        echo "❌ Opération annulée."
        exit 0
    fi
fi

# --- 1. ATTENTE ACTIVE DES VERROUS APT ---
echo "⏳ Vérification et attente des verrous APT..."
MAX_WAIT=120
WAIT_COUNT=0
while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
    echo "   Verrou APT actif (autre processus en cours). Attente 5s ($WAIT_COUNT/$MAX_WAIT)..."
    sleep 5
    WAIT_COUNT=$((WAIT_COUNT + 5))
    if [ "$WAIT_COUNT" -ge "$MAX_WAIT" ]; then
        echo "❌ DÉLAI DÉPASSÉ : Les verrous APT sont toujours actifs. Abandon."
        exit 1
    fi
done
echo "   ✅ Verrous APT disponibles."

# --- 2. ARRÊT PROPRE DES CONTENEURS DOCKER ---
ACTIVE_CONTAINERS=$(docker ps -q 2>/dev/null || true)
if [ -n "$ACTIVE_CONTAINERS" ]; then
    echo "📦 Arrêt propre des conteneurs pour sceller les bases de données (WAL SQLite, PostgreSQL, Qdrant)..."
    docker stop $ACTIVE_CONTAINERS
    echo "   ✅ Conteneurs arrêtés avec succès."
else
    echo "ℹ️  Aucun conteneur en cours d'exécution."
fi

# --- 3. MISE À NIVEAU DU SYSTÈME (APT) ---
echo "🔄 Mise à jour de la liste des paquets..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y

echo "📦 Application des mises à niveau du système (dist-upgrade)..."
apt-get dist-upgrade -y \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold"

# --- 4. PURGE ET MAINTENANCE ECHO ---
echo "🧹 Purge des paquets obsolètes..."
apt-get autoremove --purge -y
apt-get clean

CLEAN_SCRIPT="$ECHO_SCRIPTS/echo-clean.sh"
if [ -f "$CLEAN_SCRIPT" ]; then
    echo "🧹 Exécution de la maintenance système ECHO (echo-clean.sh)..."
    bash "$CLEAN_SCRIPT"
fi

# --- 5. GESTION DU REDÉMARRAGE ET RELANCE ---
if [ -f /var/run/reboot-required ]; then
    echo ""
    echo "🚨 REBOOT REQUIS : Un nouveau noyau Linux ou un composant critique du système a été installé."
    if [ "$ASSUME_YES" = false ]; then
        read -p "Souhaitez-vous redémarrer la VM maintenant ? [O/n] : " REBOOT_CONFIRM
        if [[ ! "$REBOOT_CONFIRM" =~ ^[nN]$ ]]; then
            echo "🔄 Redémarrage de la machine en cours..."
            reboot
            exit 0
        else
            echo "⚠️  Redémarrage ignoré. N'oubliez pas d'exécuter 'sudo reboot' dès que possible."
        fi
    else
        echo "⚠️  Mode non-interactif : aucun redémarrage automatique n'est forcé. Exécutez 'sudo reboot' dès que possible."
    fi
fi

INSTALL_SCRIPT="$ECHO_SCRIPTS/install-stack.sh"
if [ -f "$INSTALL_SCRIPT" ]; then
    echo "🚀 Relance de la stack applicative ECHO..."
    bash "$INSTALL_SCRIPT"
fi

PS_SCRIPT="$ECHO_SCRIPTS/echo-ps.sh"
if [ -f "$PS_SCRIPT" ]; then
    echo "📊 État des services ECHO :"
    bash "$PS_SCRIPT"
fi

echo "✨ Mise à niveau système terminée : $(date)"
