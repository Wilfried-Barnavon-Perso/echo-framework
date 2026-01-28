#!/bin/bash
# ==============================================================================
# AFFICHAGE SECURISE DES IDENTIFIANTS ADMIN
# Version : 1.1
# Auteur : Wilfried BARNAVON
# ==============================================================================

ADMIN_SECRET_FILE="/opt/config/.owui-admin-secret"
HUMAN_EMAIL="admin@echo.local"

if [ ! -f "$ADMIN_SECRET_FILE" ]; then
    echo "❌ Erreur : Le fichier secret ($ADMIN_SECRET_FILE) est introuvable."
    echo "   L'initialisation a-t-elle été effectuée ?"
    exit 1
fi

ADMIN_PWD=$(cat "$ADMIN_SECRET_FILE")

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
