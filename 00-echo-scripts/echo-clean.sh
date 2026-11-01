#!/bin/bash
# ==============================================================================
# SCRIPT : echo-clean.sh
# VERSION : 5.231.39.1
# ROLE   : Nettoyage système et gestion dynamique du cache Docker
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.39.1: Renommage de clean-echo.sh en echo-clean.sh et alignement du fichier de log.
# 5.226.18.0: Alignement sur le versioning global ECHO.

LOG_FILE="/var/log/echo-clean.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=================================================="
echo "🧹 Début de la maintenance système : $(date)"

# 1. Nettoyage APT
echo "-> Nettoyage APT..."
apt-get clean
apt-get autoremove --purge -y

# 2. Nettoyage Journald
echo "-> Compression des logs..."
journalctl --vacuum-size=100M

# 3. Évaluation Disque & Docker Prune
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')

# Purge inconditionnelle des images orphelines (dangling) et du build cache orphelin
# Cette étape ne supprime aucune image nommée, mais libère instantanément les déchets de build.
echo "-> Nettoyage inconditionnel des images orphelines (<none>:<none>)..."
docker image prune -f

if [ "$DISK_USAGE" -ge 90 ]; then
    echo "🚨 ALERTE CRITIQUE ($DISK_USAGE%) : Survie système menacée."
    echo "-> Purge Docker MAXIMALE (Aucun délai de conservation)..."
    docker system prune -af
elif [ "$DISK_USAGE" -ge 85 ]; then
    echo "⚠️ ALERTE DISQUE ($DISK_USAGE%) : Espace restreint."
    echo "-> Purge Docker D'URGENCE (Sans délai, volumes préservés)..."
    docker system prune -af
else
    echo "✅ Espace disque sain ($DISK_USAGE%)."
    echo "-> Purge Docker de ROUTINE (Conservation : 7 jours)..."
    docker system prune -af --filter "until=168h"
    docker buildx prune -f --filter "until=168h"
fi

echo "✅ Fin de la maintenance."
