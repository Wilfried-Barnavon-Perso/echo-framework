#!/bin/bash
# ==============================================================================
# AFFICHAGE SECURISE DES IDENTIFIANTS ADMIN
# ==============================================================================

ADMIN_SECRET_FILE="/opt/config/.owui-admin-secret"
HUMAN_EMAIL="admin@echo.local"

if [ ! -f "$ADMIN_SECRET_FILE" ]; then
    echo "❌ Erreur : Le fichier secret ($ADMIN_SECRET_FILE) est introuvable."
    echo "   L'initialisation a-t-elle été effectuée ?"
    exit 1
fi

ADMIN_PWD=$(cat "$ADMIN_SECRET_FILE")

# Nettoyage écran (compatible clear)
printf "\033c"

echo "=========================================="
echo "      🔐 ECHO ADMIN CREDENTIALS"
echo "=========================================="
echo ""
echo "Email    : $HUMAN_EMAIL"
echo "Password : $ADMIN_PWD"
echo ""
echo "=========================================="
echo "⚠️  ATTENTION : Ce mot de passe va être masqué."
echo "⏳ Masquage automatique dans 30 secondes..."

sleep 30

# Nettoyage et affichage masqué
printf "\033c"
echo "=========================================="
echo "      🔐 ECHO ADMIN CREDENTIALS"
echo "=========================================="
echo ""
echo "Email    : $HUMAN_EMAIL"
echo "Password : ****************"
echo ""
echo "=========================================="
echo "✅ Affichage sécurisé terminé."
