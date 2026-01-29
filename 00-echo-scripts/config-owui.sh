#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (MODE DIAGNOSTIC)
# VERSION : 7.22
# ==============================================================================

# --- CONFIGURATION ---
OWUI_URL="http://localhost:3000"
SECRET_FILE="/opt/.owui-setting-secret"
ADMIN_SECRET_FILE="/opt/.owui-admin-secret"
MODEL_ID="pipe_engine"

echo "🔍 [DIAGNOSTIC] Démarrage du mode inspection..."

# --- 1. ATTENTE API ---
# (On garde l'attente pour être sûr que le serveur répond)
until curl -s -f "$OWUI_URL/health" > /dev/null; do
    sleep 1
done

# --- 2. AUTHENTIFICATION ---
TOKEN=""
if [ -f "$SECRET_FILE" ]; then
    SERVICE_PWD=$(cat "$SECRET_FILE" | tr -d '[:space:]')
    LOGIN_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signin" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"install-stack@echo.local\", \"password\": \"$SERVICE_PWD\"}")
    TOKEN=$(echo "$LOGIN_RESP" | jq -r '.token // empty')
fi

# Fallback Admin
if [ -z "$TOKEN" ] || [ "$TOKEN" == "null" ]; then
    echo "⚠️  Echec auth service, tentative avec le token admin..."
    # Ici on ne peut pas deviner le mot de passe admin s'il n'est pas dans un fichier, 
    # mais on suppose que le compte service fonctionne si l'install est fraîche.
    echo "❌ [FATAL] Impossible de s'authentifier pour le diagnostic."
    exit 1
fi
echo "✅ Authentification réussie."

# --- 3. LECTURE DU MODELE ---
echo "📥 Récupération de la configuration du modèle '$MODEL_ID'வுகளை..."

MODELS_LIST=$(curl -s -X GET "$OWUI_URL/api/v1/models" -H "Authorization: Bearer $TOKEN")
REMOTE_MODEL=$(echo "$MODELS_LIST" | jq -r --arg id "$MODEL_ID" '.data[] | select(.id == $id)')

if [ -n "$REMOTE_MODEL" ] && [ "$REMOTE_MODEL" != "null" ]; then
    echo ""
    echo "=================================================================="
    echo "🔎 STRUCTURE JSON EXACTE DU MODELE (Configuré manuellement)"
    echo "=================================================================="
    echo "$REMOTE_MODEL" | jq .
    echo "=================================================================="
    echo ""
    
    # Analyse rapide des Tools
    echo "📊 Analyse rapide :"
    echo "   - Tools (root) : $(echo "$REMOTE_MODEL" | jq '.tools')"
    echo "   - Tools (info) : $(echo "$REMOTE_MODEL" | jq '.info.meta.toolIds')"
    echo "   - Image        : $(echo "$REMOTE_MODEL" | jq '.info.meta.profile_image_url // .meta.profile_image_url')"
else
    echo "❌ Le modèle '$MODEL_ID' est introuvable."
fi

echo "✅ Fin du diagnostic."
exit 0

# ==============================================================================
# ZONE DÉSACTIVÉE (ANCIEN CODE)
# ==============================================================================
: <<'END_OF_DISABLED_CODE'
# ... (Tout le reste du script original est ici virtuellement) ...
# Fichiers de Configuration
CONFIG_DIR="/opt/config"
MODEL_CONFIG_FILE="$CONFIG_DIR/model-config.json"
# ...
END_OF_DISABLED_CODE