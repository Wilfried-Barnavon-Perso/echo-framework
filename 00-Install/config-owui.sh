#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (MODE SECRET-BASED)
# VERSION : 6.7
# AUTEUR  : Wilfried BARNAVON
# DATE    : 2026-01-26
# ==============================================================================

# --- CONFIGURATION ---
# Port modifié à 8080 pour correspondre à la stack v6 (Standalone)
OWUI_URL="http://localhost:8080"
SECRET_FILE="/opt/.owui-setting-secret"

# Compte de Service (Automate)
SERVICE_EMAIL="install-stack@echo.local"
SERVICE_NAME="Install Stack Service"

# Compte Admin Humain
HUMAN_EMAIL="admin@echo.local"
HUMAN_NAME="ECHO Architect"
HUMAN_DEFAULT_PASSWORD="password"

# Dossiers Ressources
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="/opt/owui-tools"
FILTERS_DIR="/opt/owui-filters"
PIPES_DIR="/opt/owui-pipes"
ACTIONS_DIR="/opt/owui-actions"

echo "🔧 [Config] Démarrage de l'initialisation ECHO..."

# --- 1. ATTENTE DISPONIBILITE API ---
echo "⏳ [Config] Attente API (Max 10 min)..."
MAX_RETRIES=300
COUNT=0
until curl -s -f "$OWUI_URL/health" > /dev/null; do
    sleep 2
    ((COUNT++))
    if [ "$COUNT" -ge "$MAX_RETRIES" ]; then
        echo "❌ [FATAL] Timeout : L'API ne répond pas."
        exit 1
    fi
    echo -n "."
done
echo " OK."

# --- 2. GESTION AUTHENTIFICATION (STRATEGIE SECRET-BASED) ---

TOKEN=""
SERVICE_PWD=""

# Cas A : Le secret existe déjà
if [ -f "$SECRET_FILE" ]; then
    echo "🔑 [AUTH] Secret trouvé (Caché). Tentative de login..."
    SERVICE_PWD=$(cat "$SECRET_FILE" | tr -d '[:space:]')
    
    # Sign-in pour obtenir le JWT temporaire
    LOGIN_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signin" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"$SERVICE_EMAIL\", \"password\": \"$SERVICE_PWD\"}")
    
    TOKEN=$(echo "$LOGIN_RESP" | jq -r '.token // empty')
    
    if [ -n "$TOKEN" ] && [ "$TOKEN" != "null" ]; then
        echo "   ✅ Authentification réussie."
    else
        echo "   ⚠️  Echec login avec le secret stocké. Le secret ou la DB a changé."
        TOKEN=""
    fi
fi

# Cas B : Pas de secret ou login échoué (Premier lancement ou Reset)
if [ -z "$TOKEN" ]; then
    echo "🆕 [AUTH] Initialisation d'un nouveau Compte de Service..."
    
    # 1. Génération d'un mot de passe ultra-durci (64 chars, haute entropie)
    SERVICE_PWD=$(LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*()_+=-' </dev/urandom | head -c 64)
    
    # 2. Création du compte Service (Devient Admin si c'est le 1er user)
    echo "   Creating Service Account ($SERVICE_EMAIL)..."
    SIGNUP_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signup" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$SERVICE_NAME\", \"email\": \"$SERVICE_EMAIL\", \"password\": \"$SERVICE_PWD\"}")
    
    TOKEN=$(echo "$SIGNUP_RESP" | jq -r '.token // empty')
    
    if [ -n "$TOKEN" ] && [ "$TOKEN" != "null" ]; then
        # 3. Sauvegarde immédiate du secret avec permissions restreintes
        echo "$SERVICE_PWD" > "$SECRET_FILE"
        chown root:root "$SECRET_FILE"
        chmod 400 "$SECRET_FILE"
        echo "   ✅ Secret sauvegardé et sécurisé dans $SECRET_FILE"
        
        # 4. Création de l'Admin Humain (via le compte service)
        echo "   👤 Création de l'Admin Humain ($HUMAN_EMAIL)..."
        curl -s -X POST "$OWUI_URL/api/v1/auths/add" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"name\": \"$HUMAN_NAME\", \"email\": \"$HUMAN_EMAIL\", \"password\": \"$HUMAN_DEFAULT_PASSWORD\", \"role\": \"admin\"}" > /dev/null
        echo "   ✅ Admin Humain prêt."
    else
        echo "   ❌ [FATAL] Impossible de créer le compte service (Existe déjà ?)."
        echo "   Si la base de données n'est pas vide, restaurez le secret dans $SECRET_FILE."
        exit 1
    fi
fi

# --- FONCTIONS UTILITAIRES ---

api_upsert() {
    local endpoint_base="$1"
    local id="$2"
    local payload="$3"
    local type_desc="$4"

    RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint_base/create" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload")
    
    HTTP_CODE=$(echo "$RESPONSE" | tail -n1 | cut -d: -f2)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 201 ]; then
        echo "   ✅ $type_desc créé."
    elif echo "$BODY" | grep -q "already registered" || [ "$HTTP_CODE" -eq 409 ]; then
        echo "   🔄 $type_desc existe déjà. Mise à jour..."
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/update" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$payload" > /dev/null
        echo "   ✅ $type_desc mis à jour."
    else
        echo "   ❌ ERREUR ($HTTP_CODE): $BODY"
    fi
}

ensure_active() {
    local endpoint_base="$1"
    local id="$2"
    
    STATE_RESP=$(curl -s -X GET "$OWUI_URL/api/v1/$endpoint_base/id/$id" -H "Authorization: Bearer $TOKEN")
    IS_ACTIVE=$(echo "$STATE_RESP" | jq -r '.is_active')
    
    if [ "$IS_ACTIVE" == "true" ]; then
        # Refresh Cache
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/toggle" -H "Authorization: Bearer $TOKEN" > /dev/null
        sleep 0.5
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/toggle" -H "Authorization: Bearer $TOKEN" > /dev/null
        echo "      🔄 $id : Cache rafraîchi."
    else
        # Force ON
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/toggle" -H "Authorization: Bearer $TOKEN" > /dev/null
        echo "      🚀 $id : Activé."
    fi
}

get_display_name() {
    local file="$1"
    local default_id="$2"
    # Extrait la ligne '# ECHO CONFIG NAME : Mon Nom'
    # Sécurité : Recherche limitée aux 10 premières lignes uniquement
    local custom_name=$(head -n 10 "$file" | grep "^# ECHO CONFIG NAME :" | head -n 1 | sed 's/^# ECHO CONFIG NAME :[[:space:]]*//')
    
    if [ -n "$custom_name" ]; then
        echo "$custom_name"
    else
        echo "$default_id"
    fi
}

# --- 3. PARAMETRES GLOBAUX ---
echo "⚙️ [SETTINGS] Configuration..."
curl -s -X POST "$OWUI_URL/api/v1/admin/settings/update" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
    "ui": { 
        "banner": "ECHO v5 Infrastructure", 
        "title": "ECHO Framework", 
        "show_admin_details": true,
        "default_model_id": "pipe_engine"
    },
    "features": { 
        "enable_artifacts": true, 
        "enable_memory": true,
        "enable_signup": false
    }
}' > /dev/null

# --- 4. IMPORT OUTILS ---
echo "🛠️ [TOOLS] Traitement des Outils..."
if [ -d "$TOOLS_DIR" ]; then
    for file in $TOOLS_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        TOOL_ID="${FILENAME%.*}"
        DISPLAY_NAME=$(get_display_name "$file" "$TOOL_ID")
        
        echo "   -> Traitement de $TOOL_ID (Nom: $DISPLAY_NAME)..."
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$TOOL_ID" --arg name "$DISPLAY_NAME" --arg content "$CONTENT" \
                  '{id: $id, name: $name, content: ($content | fromjson), meta: {description: "ECHO Tool", manifest: {}}}')
        api_upsert "tools" "$TOOL_ID" "$PAYLOAD" "Outil"
    done
fi

# --- 5. IMPORT FILTRES ---
echo "🛡️ [FILTERS] Traitement des Filtres..."
if [ -d "$FILTERS_DIR" ]; then
    for file in $FILTERS_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        FILTER_ID="${FILENAME%.*}"
        DISPLAY_NAME=$(get_display_name "$file" "$FILTER_ID")

        echo "   -> Traitement de $FILTER_ID (Nom: $DISPLAY_NAME)..."
        if [ "$FILTER_ID" == "bypass_rag" ]; then echo "      ⚠️  Filtre Critique détecté : Bypass RAG (Audit Aligned)"; fi
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$FILTER_ID" --arg name "$DISPLAY_NAME" --arg content "$CONTENT" \
                  '{id: $id, name: $name, content: ($content | fromjson), type: "filter", meta: {description: "ECHO Filter", manifest: {}}, is_active: true, is_global: true}')
        api_upsert "functions" "$FILTER_ID" "$PAYLOAD" "Filtre"
        ensure_active "functions" "$FILTER_ID"
    done
fi

# --- 6. IMPORT PIPES ---
echo "🧩 [PIPES] Traitement des Pipes..."
if [ -d "$PIPES_DIR" ]; then
    for file in $PIPES_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        FUNC_ID="${FILENAME%.*}"
        DISPLAY_NAME=$(get_display_name "$file" "$FUNC_ID")

        echo "   -> Traitement de $FUNC_ID (Nom: $DISPLAY_NAME)..."
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$FUNC_ID" --arg name "$DISPLAY_NAME" --arg content "$CONTENT" \
                  '{id: $id, name: $name, content: ($content | fromjson), type: "pipe", meta: {description: "ECHO Pipe", manifest: {}}, is_active: true}')
        api_upsert "functions" "$FUNC_ID" "$PAYLOAD" "Fonction (Pipe)"
        ensure_active "functions" "$FUNC_ID"
    done
fi

# --- 7. IMPORT ACTIONS ---
echo "🎬 [ACTIONS] Traitement des Actions..."
if [ -d "$ACTIONS_DIR" ]; then
    for file in $ACTIONS_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        ACTION_ID="${FILENAME%.*}"
        DISPLAY_NAME=$(get_display_name "$file" "$ACTION_ID")

        echo "   -> Traitement de $ACTION_ID (Nom: $DISPLAY_NAME)..."
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$ACTION_ID" --arg name "$DISPLAY_NAME" --arg content "$CONTENT" \
                  '{id: $id, name: $name, content: ($content | fromjson), type: "action", is_active: true, meta: {description: "ECHO Action", manifest: {}}}')
        api_upsert "functions" "$ACTION_ID" "$PAYLOAD" "Action"
        ensure_active "functions" "$ACTION_ID"
    done
fi

# --- 8. CONFIGURATION MODELE ---
echo "🧠 [MODEL] Configuration du Modèle..."
MODEL_CONFIG_FILE="$SCRIPT_DIR/model-config.json"
SYSTEM_PROMPT_FILE="$SCRIPT_DIR/system-prompt.json"
MODEL_ID="pipe_engine"

if [ -f "$MODEL_CONFIG_FILE" ] && [ -f "$SYSTEM_PROMPT_FILE" ]; then
    EXTRACTED_PROMPT=$(jq -r '.content // .system_prompt // empty' "$SYSTEM_PROMPT_FILE" 2>/dev/null)
    [ -z "$EXTRACTED_PROMPT" ] && SYSTEM_PROMPT=$(cat "$SYSTEM_PROMPT_FILE") || SYSTEM_PROMPT="$EXTRACTED_PROMPT"
    MODEL_PAYLOAD=$(jq --arg system "$SYSTEM_PROMPT" '.[0] | del(.user_id, .created, .updated_at, .created_at, .access_control) | .params.system = $system | .is_active = true' "$MODEL_CONFIG_FILE")
    CHECK_MODEL=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$OWUI_URL/api/v1/models/$MODEL_ID" -H "Authorization: Bearer $TOKEN")
    if [ "$CHECK_MODEL" -eq 200 ]; then
        echo "   🔄 Mise à jour modèle..."
        curl -s -X POST "$OWUI_URL/api/v1/models/model/update" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$MODEL_PAYLOAD" > /dev/null
    else
        echo "   🆕 Création modèle..."
        curl -s -X POST "$OWUI_URL/api/v1/models/add" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$MODEL_PAYLOAD" > /dev/null
    fi
    echo "   ✅ Modèle configuré."
fi

echo "✅ [Config] Terminé avec succès."