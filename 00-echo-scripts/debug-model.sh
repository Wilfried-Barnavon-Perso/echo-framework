#!/bin/bash
# ==============================================================================
# DEBUG MODEL ECHO
# ==============================================================================

# --- CONFIGURATION ---
OWUI_URL="http://localhost:3000"
SECRET_FILE="/opt/.owui-setting-secret"
MODEL_ID="pipe_engine"

echo "🔍 [DEBUG] Récupération du modèle $MODEL_ID..."

# --- 1. AUTHENTIFICATION ---
if [ -f "$SECRET_FILE" ]; then
    SERVICE_PWD=$(cat "$SECRET_FILE" | tr -d '[:space:]')
    LOGIN_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signin" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"install-stack@echo.local\", \"password\": \"$SERVICE_PWD\"}")
    TOKEN=$(echo "$LOGIN_RESP" | jq -r '.token // empty')
fi

if [ -z "$TOKEN" ] || [ "$TOKEN" == "null" ]; then
    echo "❌ Erreur Auth."
    exit 1
fi

# --- 2. GET MODEL ---
echo "📥 GET /api/v1/models/$MODEL_ID"
RESPONSE=$(curl -s -X GET "$OWUI_URL/api/v1/models/$MODEL_ID" -H "Authorization: Bearer $TOKEN")

echo "--- JSON COMPLET ---"
echo "$RESPONSE" | jq .
echo "--------------------"

# --- 3. VERIFICATION ---
echo "--- ANALYSE ---"
echo "ID: $(echo "$RESPONSE" | jq -r '.id')"
echo "Tools (Root): $(echo "$RESPONSE" | jq '.tools')"
echo "Meta (Root): $(echo "$RESPONSE" | jq '.meta')"
echo "Info (Root): $(echo "$RESPONSE" | jq '.info')"
echo "--------------------"
