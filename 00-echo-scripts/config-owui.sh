#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (MODE ASSEMBLAGE)
# VERSION : 7.65
# ==============================================================================

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

# --- CONFIGURATION ---
DEBUG_MODE="false"
OWUI_URL="http://localhost:3000"
SECRET_FILE="$ECHO_SECRETS/.owui-setting-secret"
ADMIN_SECRET_FILE="$ECHO_SECRETS/.owui-admin-secret"

# Fichiers de Configuration
CONFIG_DIR="$ECHO_CONFIG"
MODEL_CONFIG_FILE="$CONFIG_DIR/model-config.json"
SYSTEM_PROMPT_FILE="$CONFIG_DIR/system-prompt.md"
SETTINGS_FILE="$CONFIG_DIR/webui-settings.json"
IMAGE_BASE_DIR="$ECHO_IMAGES"

# Comptes
if [ -f "$ECHO_ENV_FILE" ]; then 
    ECHO_DOMAIN=$(grep "^ECHO_DOMAIN=" "$ECHO_ENV_FILE" | cut -d '=' -f2)
fi
SERVICE_EMAIL="${ECHO_SERVICE_ACCOUNT:-install-stack@echo.local}"
SERVICE_NAME="Install Stack Service"
HUMAN_EMAIL="admin@echo.local"
HUMAN_NAME="ECHO Architect"

# Dossiers Ressources
TOOLS_DIR="$ECHO_OWUI_TOOLS"
FILTERS_DIR="$ECHO_OWUI_FILTERS"
PIPES_DIR="$ECHO_OWUI_PIPES"
ACTIONS_DIR="$ECHO_OWUI_ACTIONS"

echo "🔧 [Config] Démarrage initialisation ECHO dans $ECHO_ROOT..."

# --- FONCTION AUTH REFRESH ---
refresh_token() {
    if [ -f "$SECRET_FILE" ]; then
        SERVICE_PWD=$(cat "$SECRET_FILE" | tr -d '[:space:]')
        LOGIN_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signin" \
            -H "Content-Type: application/json" $EXTRA_HEADER \
            -d "{\"email\": \"$SERVICE_EMAIL\", \"password\": \"$SERVICE_PWD\"}")
        NEW_TOKEN=$(echo "$LOGIN_RESP" | jq -r '.token // empty')
        
        if [ -n "$NEW_TOKEN" ] && [ "$NEW_TOKEN" != "null" ]; then
            TOKEN="$NEW_TOKEN"
            return 0
        fi
    fi
    echo "   ⚠️  Echec refresh token."
    return 1
}

# --- 1. ATTENTE API ---
SLEEP_TIME=2; WAIT_LIMIT=600; COUNT=0
echo -n "⏳ [Config] Attente API open-webui (Max 20 min)."
until curl -s -f "$OWUI_URL/health" > /dev/null; do
    if [ "$COUNT" -ge "$WAIT_LIMIT" ]; then exit 1; fi
    echo -n "."; sleep $SLEEP_TIME; ((COUNT++))
done
echo " OK apres $(($COUNT*$SLEEP_TIME)) secondes."

# --- 2. AUTHENTIFICATION INITIALE ---
TOKEN=""
EXTRA_HEADER=""
AUTH_HEADER=""

if [ -n "$ECHO_DOMAIN" ]; then
    EXTRA_HEADER="-H X-Webui-User:$SERVICE_EMAIL"
    echo "   [DEBUG] Mode SSO détecté. Injection de l'entête : $EXTRA_HEADER"
fi

refresh_token
if [ -z "$TOKEN" ] || [ "$TOKEN" == "null" ]; then
    echo "🆕 [AUTH] Création compte service..."
    SERVICE_PWD=$(LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*()_+=-' </dev/urandom | head -c 64)
    SIGNUP_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signup" \
        -H "Content-Type: application/json" $EXTRA_HEADER \
        -d "{\"name\": \"$SERVICE_NAME\", \"email\": \"$SERVICE_EMAIL\", \"password\": \"$SERVICE_PWD\"}")
    TOKEN=$(echo "$SIGNUP_RESP" | jq -r '.token // empty')
    
    if [ -n "$TOKEN" ] && [ "$TOKEN" != "null" ]; then
        mkdir -p "$ECHO_SECRETS"
        touch "$SECRET_FILE"; chmod 600 "$SECRET_FILE"; echo "$SERVICE_PWD" > "$SECRET_FILE"; chmod 400 "$SECRET_FILE"
        echo "   ✅ Compte service créé."
    else
        echo "❌ [FATAL] Echec création service."
        echo "   [DEBUG] Reponse: $SIGNUP_RESP"
        exit 1
    fi
fi

if [ ! -s "$ADMIN_SECRET_FILE" ]; then
    echo "🆕 [AUTH] Création/Mise à jour compte admin humain avec mot de passe de secours..."
    ADMIN_PWD=$(LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*()_+=-' </dev/urandom | head -c 16)
    touch "$ADMIN_SECRET_FILE"; echo "$ADMIN_PWD" > "$ADMIN_SECRET_FILE"; chmod 400 "$ADMIN_SECRET_FILE"
    
    # La création/mise à jour fonctionne indifféremment avec JWT ou SSO grâce à $AUTH_HEADER
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$OWUI_URL/api/v1/auths/add" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "{\"name\": \"$HUMAN_NAME\", \"email\": \"$HUMAN_EMAIL\", \"password\": \"$ADMIN_PWD\", \"role\": \"admin\"}")
    [ "$HTTP_CODE" -eq 401 ] && refresh_token && curl -s -X POST "$OWUI_URL/api/v1/auths/add" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "{\"name\": \"$HUMAN_NAME\", \"email\": \"$HUMAN_EMAIL\", \"password\": \"$ADMIN_PWD\", \"role\": \"admin\"}" > /dev/null
    echo "   ✅ Admin créé avec clé de secours."
fi

# --- 3. IMPORT RESSOURCES (STRATÉGIE DISQUE) ---
api_upsert() {
    local endpoint="$1"; local id="$2"; local payload_file="$3"; local desc="$4"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint/create" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$payload_file")
    
    if [ "$HTTP_CODE" -eq 401 ] && refresh_token; then
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint/create" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$payload_file")
    fi

    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 201 ]; then
        echo "   ✅ $desc : $id créé."
    elif [ "$HTTP_CODE" -eq 409 ] || [ "$HTTP_CODE" -eq 400 ]; then
        UPDATE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint/id/$id/update" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$payload_file")
        [ "$UPDATE_CODE" -eq 401 ] && refresh_token && UPDATE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint/id/$id/update" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$payload_file")
        if [ "$UPDATE_CODE" -eq 200 ]; then echo "   🔄 $desc : $id mis à jour."; else echo "   ❌ Echec $desc $id (HTTP $UPDATE_CODE)."; fi
    fi
}

toggle_state() {
    local id="$1"
    RESP=$(curl -s -X GET "$OWUI_URL/api/v1/functions/id/$id" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER)
    [[ "$RESP" == *"expired"* ]] && refresh_token && RESP=$(curl -s -X GET "$OWUI_URL/api/v1/functions/id/$id" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER)
    IS_ACTIVE=$(echo "$RESP" | jq -r '.is_active // empty'); IS_GLOBAL=$(echo "$RESP" | jq -r '.is_global // empty')
    [ "$IS_GLOBAL" != "true" ] && curl -s -X POST "$OWUI_URL/api/v1/functions/id/$id/toggle/global" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER > /dev/null
    [ "$IS_ACTIVE" != "true" ] && curl -s -X POST "$OWUI_URL/api/v1/functions/id/$id/toggle" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER > /dev/null
}

for DIR_TYPE in "tools:tools:Outil" "functions:functions:Filtre" "functions:functions:Pipe" "functions:functions:Action"; do
    IFS=":" read -r DIR_NAME API_ENDPOINT DESC <<< "$DIR_TYPE"
    case "$DESC" in "Outil") T="$TOOLS_DIR";; "Filtre") T="$FILTERS_DIR";; "Pipe") T="$PIPES_DIR";; "Action") T="$ACTIONS_DIR";; esac
    if [ -d "$T" ]; then
        echo "📂 Traitement $DESC..."
        for file in "$T"/*.py; do
            [ -e "$file" ] || continue
            ID=$(basename "$file" | cut -d. -f1)
            NAME=$(grep -m 1 "^title:" "$file" | sed 's/^title:[ \t]*//' | tr -d '\r' | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//")
            if [ -z "$NAME" ]; then
                NAME="$ID"
            fi
            
            DESC_VAL=$(grep -m 1 "^description:" "$file" | sed 's/^description:[ \t]*//' | tr -d '\r' | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//")
            
            TMP_JSON="/tmp/owui_payload_$$.json"
            if [ "$API_ENDPOINT" == "tools" ]; then
                jq -n --arg id "$ID" --arg name "$NAME" --arg desc "$DESC_VAL" --rawfile content "$file" '{id: $id, name: $name, content: $content, meta: {description: $desc}}' > "$TMP_JSON"
            else
                TYPE_VAL=$(echo "$DESC" | tr '[:upper:]' '[:lower:]')
                jq -n --arg id "$ID" --arg name "$NAME" --arg desc "$DESC_VAL" --rawfile content "$file" --arg type "$TYPE_VAL" '{id: $id, name: $name, content: $content, type: $type, meta: {description: $desc}}' > "$TMP_JSON"
            fi
            
            api_upsert "$API_ENDPOINT" "$ID" "$TMP_JSON" "$DESC"
            if [ "$API_ENDPOINT" != "tools" ]; then
                toggle_state "$ID"
            else
                TMP_ACCESS="/tmp/owui_access_payload_$$.json"
                echo '{"access_grants":[{"principal_type":"user","principal_id":"*","permission":"read"}]}' > "$TMP_ACCESS"
                curl -s -X POST "$OWUI_URL/api/v1/tools/id/$ID/access/update" \
                    -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER \
                    -H "Content-Type: application/json" -d "@$TMP_ACCESS" > /dev/null
                echo "   🌍 $DESC : $ID configuré en accès public."
                rm -f "$TMP_ACCESS"
            fi
            rm -f "$TMP_JSON"
        done
    fi
done

# --- 4. CONFIGURATION MODELE (RE-ALIGNEMENT v5.29.0 + DISK-ONLY) ---
if [ -f "$MODEL_CONFIG_FILE" ]; then
    sleep 2
    # Extraction de l'ID via fichier pour eviter ARG_MAX
    MODEL_ID=$(jq -r 'if type=="array" then .[0].id else .id end' "$MODEL_CONFIG_FILE")
    if [ -z "$MODEL_ID" ] || [ "$MODEL_ID" == "null" ]; then echo "❌ [FATAL] Impossible de déterminer MODEL_ID."; exit 1; fi
    
    echo "🧠 [MODEL] Configuration : $MODEL_ID"
    
    # 1. Discovery vers fichiers
    TMP_TOOLS="/tmp/owui_tool_ids_$$.json"; TMP_FILTERS="/tmp/owui_filter_ids_$$.json"; TMP_ACTIONS="/tmp/owui_action_ids_$$.json"
    find "$TOOLS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s . > "$TMP_TOOLS"
    find "$FILTERS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s . > "$TMP_FILTERS"
    find "$ACTIONS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s . > "$TMP_ACTIONS"

    # 2. Construction du Payload de base (DISK -> DISK)
    TMP_FINAL="/tmp/owui_model_final_$$.json"
    jq --slurpfile tools "$TMP_TOOLS" --slurpfile filters "$TMP_FILTERS" --slurpfile actions "$TMP_ACTIONS" \
       'if type=="array" then .[0] else . end | del(.user_id, .created, .updated_at, .created_at, .is_global) | .is_active = true | .meta.toolIds = $tools[0] | .meta.filterIds = $filters[0] | .meta.defaultFilterIds = $filters[0] | .meta.actionIds = $actions[0]' \
       "$MODEL_CONFIG_FILE" > "$TMP_FINAL"
    
    # 3. Injection System Prompt (RAWFILE)
    if [ -f "$SYSTEM_PROMPT_FILE" ]; then
        TMP_STEP="/tmp/owui_model_step_$$.json"
        jq --rawfile p "$SYSTEM_PROMPT_FILE" '.params.system = $p' "$TMP_FINAL" > "$TMP_STEP"
        mv "$TMP_STEP" "$TMP_FINAL"
    fi
    
    # 4. Injection Image (STRATÉGIE ANTI-TRONCATION)
    IMG_NAME=$(jq -r 'if type=="array" then .[0].local_image_filename // empty else .local_image_filename // empty end' "$MODEL_CONFIG_FILE")
    if [ -n "$IMG_NAME" ] && [ -f "$IMAGE_BASE_DIR/$IMG_NAME" ]; then
        TMP_B64="/tmp/owui_img_b64_$$.txt"
        echo -n "data:image/png;base64," > "$TMP_B64"
        base64 -w 0 "$IMAGE_BASE_DIR/$IMG_NAME" >> "$TMP_B64"
        TMP_STEP="/tmp/owui_model_step_$$.json"
        jq --rawfile img "$TMP_B64" '.meta.profile_image_url = $img | del(.local_image_filename)' "$TMP_FINAL" > "$TMP_STEP"
        mv "$TMP_STEP" "$TMP_FINAL"
        rm -f "$TMP_B64"
    fi

    # 5. Déploiement (ENDPOINTS STABLES v5.29.0)
    CHECK_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$OWUI_URL/api/v1/models/model?id=$MODEL_ID" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER)
    [ "$CHECK_CODE" -eq 401 ] && refresh_token && CHECK_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$OWUI_URL/api/v1/models/model?id=$MODEL_ID" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER)

    if [ "$CHECK_CODE" -eq 200 ]; then
        echo "   🚀 Mise à jour du modèle existant (POST /update)..."
        TARGET_URL="$OWUI_URL/api/v1/models/model/update"
    else
        echo "   🚀 Création du nouveau modèle (POST /create)..."
        TARGET_URL="$OWUI_URL/api/v1/models/create"
    fi
    
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$TARGET_URL" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$TMP_FINAL")
    
    if [ "$HTTP_CODE" -eq 401 ] && refresh_token; then
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$TARGET_URL" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$TMP_FINAL")
    fi
    echo "   ✅ Modèlle $MODEL_ID déployé (HTTP $HTTP_CODE)."
    
    rm -f "$TMP_TOOLS" "$TMP_FILTERS" "$TMP_ACTIONS" "$TMP_FINAL"
fi

# --- 5. IMPORT CONFIGURATION GLOBALE ---
if [ -f "$SETTINGS_FILE" ]; then
    echo "⚙️ [Config] Import des paramètres globaux..."
    TMP_PAYLOAD="/tmp/owui_config_import_$$.json"
    jq -n --slurpfile content "$SETTINGS_FILE" '{config: $content[0]}' > "$TMP_PAYLOAD"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$OWUI_URL/api/v1/configs/import" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$TMP_PAYLOAD")
    [ "$HTTP_CODE" -eq 401 ] && refresh_token && HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$OWUI_URL/api/v1/configs/import" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER -H "Content-Type: application/json" -d "@$TMP_PAYLOAD")
    rm -f "$TMP_PAYLOAD"
    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 201 ]; then
        echo "   ✅ Configuration importée."
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$OWUI_URL/api/v1/admin/config/reload" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER)
        [ "$HTTP_CODE" -eq 401 ] && refresh_token && curl -s -X GET "$OWUI_URL/api/v1/admin/config/reload" -H "Authorization: Bearer $TOKEN" $EXTRA_HEADER > /dev/null
        sleep 5
    fi
fi

echo "✅ [Config] Terminé."
[ -f "$ECHO_SCRIPTS/show-echo-admin.sh" ] && bash "$ECHO_SCRIPTS/show-echo-admin.sh"
