#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (MODE SERVICE ACCOUNT)
# VERSION : 6.3 (Restore Audit Logs)
# AUTEUR  : Wilfried BARNAVON
# DATE    : 2026-01-22
# ==============================================================================

# --- CONFIGURATION ---
OWUI_URL="http://localhost:3000"
# Fichier caché et sécurisé pour la clé API permanente
KEY_FILE="/opt/.owui-api-setting.key"

# Compte de Service (Automate)
SERVICE_EMAIL="install-stack@echo.local"
SERVICE_NAME="Install Stack Service"

# Compte Admin Humain (Créé par l'automate)
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

# --- 2. GESTION AUTHENTIFICATION (STRATEGIE SERVICE ACCOUNT) ---

TOKEN=""

# Cas A : La clé API existe déjà
if [ -f "$KEY_FILE" ]; then
    echo "🔑 [AUTH] Clé API trouvée (Cachée). Vérification..."
    TOKEN=$(cat "$KEY_FILE" | tr -d '[:space:]')
    
    # Test de validité
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" "$OWUI_URL/api/v1/auths/")
    
    if [ "$HTTP_CODE" -eq 200 ]; then
        echo "   ✅ Clé valide. Connexion établie."
    else
        echo "   ⚠️  Clé invalide ou expirée ($HTTP_CODE). Tentative de régénération..."
        TOKEN="" 
        # Si la clé est invalide, on supprime le fichier pour permettre la réécriture si on arrive à se reconnecter
        # (Bien que sans credentials service account, le script échouera plus loin, c'est plus propre)
        rm -f "$KEY_FILE"
    fi
fi

# Cas B : Pas de clé (Premier lancement ou Reset)
if [ -z "$TOKEN" ]; then
    echo "🆕 [AUTH] Initialisation du Compte de Service..."
    
    # 1. Génération mot de passe aléatoire complexe (jetable)
    SERVICE_PWD=$(openssl rand -base64 32)
    
    # 2. Création du compte Service (Devient Admin si c'est le 1er user)
    echo "   Creating Service Account ($SERVICE_EMAIL)..."
    SIGNUP_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signup" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$SERVICE_NAME\", \"email\": \"$SERVICE_EMAIL\", \"password\": \"$SERVICE_PWD\"}")
    
    # Récupération du JWT temporaire
    TEMP_JWT=$(echo "$SIGNUP_RESP" | jq -r '.token // empty')
    
    # Si Signup échoue (ex: user existe déjà mais on a perdu la clé), on tente un SIGNIN
    if [ -z "$TEMP_JWT" ] || [ "$TEMP_JWT" == "null" ]; then
        echo "   ⚠️  Le compte service existe peut-être déjà. Impossible de récupérer l'accès sans mot de passe."
        echo "   ❌ [FATAL] Intervention manuelle requise : Supprimez le volume DB ou restaurez la clé."
        exit 1
    fi
    
    echo "   ✅ Compte Service créé."

    # 3. Génération de l'API Key Permanente
    echo "   Generating Permanent API Key..."
    API_KEY_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/api_key" \
        -H "Authorization: Bearer $TEMP_JWT" \
        -H "Content-Type: application/json")
        
    NEW_KEY=$(echo "$API_KEY_RESP" | jq -r '.api_key // empty')
    
    if [ -n "$NEW_KEY" ] && [ "$NEW_KEY" != "null" ]; then
        echo "$NEW_KEY" > "$KEY_FILE"
        # Sécurisation MAXIMALE : Lecture seule pour le propriétaire (Root)
        chown root:root "$KEY_FILE" # Propriétaire Root obligatoire
        chmod 400 "$KEY_FILE"       # Permissions r--------
        TOKEN="$NEW_KEY"
        echo "   ✅ Clé API sauvegardée et sécurisée (400) dans $KEY_FILE"
    else
        echo "   ⚠️  Echec génération API Key. Utilisation du JWT temporaire."
        TOKEN="$TEMP_JWT"
    fi
    
    # 4. Création de l'Admin Humain (via le compte service)
    echo "   👤 Création de l'Admin Humain ($HUMAN_EMAIL)..."
    curl -s -X POST "$OWUI_URL/api/v1/auths/add" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$HUMAN_NAME\", \"email\": \"$HUMAN_EMAIL\", \"password\": \"$HUMAN_DEFAULT_PASSWORD\", \"role\": \"admin\"}" > /dev/null
    
    echo "   ✅ Admin Humain prêt."
fi

# --- FONCTIONS UTILITAIRES ---

api_upsert() {
    local endpoint_base="$1"
    local id="$2"
    local payload="$3"
    local type_desc="$4"

    # CREATION
    RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint_base/create" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload")
    
    HTTP_CODE=$(echo "$RESPONSE" | tail -n1 | cut -d: -f2)
    BODY=$(echo "$RESPONSE" | sed '$d')

    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 201 ]; then
        echo "   ✅ $type_desc créé."
    elif echo "$BODY" | grep -q "already registered" || [ "$HTTP_CODE" -eq 409 ]; then
        # UPDATE
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
        # Refresh Cache (OFF -> ON)
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

# --- 3. PARAMETRES GLOBAUX ---
echo "⚙️ [SETTINGS] Configuration..."
curl -s -X POST "$OWUI_URL/api/v1/admin/settings/update" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
    "ui": { 
        "banner": "ECHO v6 Infrastructure", 
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
# Note: enable_signup = false pour sécuriser après création de l'admin

# --- 4. IMPORT OUTILS ---
echo "🛠️ [TOOLS] Traitement des Outils..."
if [ -d "$TOOLS_DIR" ]; then
    for file in $TOOLS_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        TOOL_ID="${FILENAME%.*}"
        echo "   -> Traitement de $TOOL_ID..."
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$TOOL_ID" --arg name "$TOOL_ID" --arg content "$CONTENT" \
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
        echo "   -> Traitement de $FILTER_ID..."
        
        # --- RESTORED LOGIC FROM v5.18 ---
        # Cas spécifique pour Bypass RAG
        if [ "$FILTER_ID" == "bypass_rag" ]; then
            echo "      ⚠️  Filtre Critique détecté : Bypass RAG (Audit Aligned)"
        fi
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$FILTER_ID" --arg name "$FILTER_ID" --arg content "$CONTENT" \
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
        echo "   -> Traitement de $FUNC_ID..."
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$FUNC_ID" --arg name "$FUNC_ID" --arg content "$CONTENT" \
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
        echo "   -> Traitement de $ACTION_ID..."
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n --arg id "$ACTION_ID" --arg name "$ACTION_ID" --arg content "$CONTENT" \
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
else
    echo "⚠️  Fichiers config modèle manquants."
fi

echo "✅ [Config] Terminé avec succès."