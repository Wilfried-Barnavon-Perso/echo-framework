#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : echo-show-admin.sh
# VERSION : 6.22.1.1
# Auteur : Wilfried BARNAVON
# ROLE   : Affichage sécurisé des identifiants Admin
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.22.1.1: Lecture directe de ECHO_ADMIN_PASSWORD depuis .secrets/.env (repli .owui-admin-secret).
# 5.231.39.1: Renommage de show-echo-admin.sh en echo-show-admin.sh.
# 5.226.18.0: Alignement sur le versioning global ECHO.

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

ADMIN_SECRET_FILE="$ECHO_SECRETS/.owui-admin-secret"
ENV_SOURCE="${ECHO_ENV_FILE:-$ECHO_SECRETS/.env}"
[ -f "$ENV_SOURCE" ] || ENV_SOURCE="$ECHO_ROOT/.env"
HUMAN_EMAIL="admin@echo.local"

ADMIN_PWD=""
if [ -f "$ENV_SOURCE" ]; then
    ADMIN_PWD=$(grep -E '^ECHO_ADMIN_PASSWORD=' "$ENV_SOURCE" 2>/dev/null | tail -n 1 | cut -d '=' -f2- | sed -E "s/^[\"']|[\"']$//g" | tr -d '\r\n' || true)
fi
if [ -z "$ADMIN_PWD" ] && [ -f "$ADMIN_SECRET_FILE" ]; then
    ADMIN_PWD=$(tr -d '\r\n' < "$ADMIN_SECRET_FILE")
fi
if [ -z "$ADMIN_PWD" ]; then
    echo "❌ Erreur : Mot de passe initial introuvable dans $ENV_SOURCE."
    echo "   L'initialisation a-t-elle été effectuée ?"
    exit 1
fi

echo ""
echo "=========================================="
echo "      🔐 ECHO ADMIN CREDENTIALS"
echo "=========================================="
echo ""
echo "Email    : $HUMAN_EMAIL"
echo "Password : $ADMIN_PWD"
echo ""
echo "=========================================="
echo "⚠️  ATTENTION : Ce mot de passe va être masqué."
echo "⏯️ Faite ENTREE pour continuer."
echo "⏳ Masquage automatique dans 30 secondes..."

read -t 30 __junk

# Effacement des 11 lignes précédentes (remonte le curseur et efface jusqu'à la fin)
printf "\033[11A\033[0J"

# Nettoyage et affichage masqué
echo "=========================================="
echo "      🔐 ECHO ADMIN CREDENTIALS"
echo "=========================================="
echo ""
echo "Email    : $HUMAN_EMAIL"
echo "Password : ****************"
echo ""
echo "=========================================="
echo "✅ Affichage sécurisé terminé."
