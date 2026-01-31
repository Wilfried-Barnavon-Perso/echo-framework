#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (MODE ASSEMBLAGE) (retour à la 7.26)
# VERSION : 7.40
# ==============================================================================

# --- CONFIGURATION ---
DEBUG_MODE="false"  # Mettre à "true" pour afficher les payloads JSON
OWUI_URL="http://localhost:3000"
SECRET_FILE="/opt/.owui-setting-secret"
ADMIN_SECRET_FILE="/opt/.owui-admin-secret"

# Fichiers de Configuration
CONFIG_DIR="/opt/config"
MODEL_CONFIG_FILE="$CONFIG_DIR/model-config.json"
SYSTEM_PROMPT_FILE="$CONFIG_DIR/system-prompt.json"
IMAGE_BASE_DIR="/opt/echo-images"

# Comptes
SERVICE_EMAIL="install-stack@echo.local"
SERVICE_NAME="Install Stack Service"
HUMAN_EMAIL="admin@echo.local"
HUMAN_NAME="ECHO Architect"

# Dossiers Ressources
TOOLS_DIR="/opt/owui-tools"
FILTERS_DIR="/opt/owui-filters"
PIPES_DIR="/opt/owui-pipes"
ACTIONS_DIR="/opt/owui-actions"

echo "🔧 [Config] Démarrage initialisation ECHO..."

# --- 1. ATTENTE API ---
# Timeout fixé 
SLEEP_TIME=2
WAIT_LIMIT=600
COUNT=0
echo -n "⏳ [Config] Attente API open-webui (Max $(($WAIT_LIMIT*$SLEEP_TIME/60)) min)."

until curl -s -f "$OWUI_URL/health" > /dev/null; do
    if [ "$COUNT" -ge "$WAIT_LIMIT" ]; then
        echo "❌ [FATAL] Timeout : L'API n'est pas disponible après $(($WAIT_LIMIT*$SLEEP_TIME/60)) minutes."
        exit 1
    fi
    echo -n "."
    sleep $SLEEP_TIME
    ((COUNT++))
done
echo " OK après $(($COUNT*2)) secondes."

# --- 2. AUTHENTIFICATION ---
TOKEN=""

# A. Authentification Service (Automate)
if [ -f "$SECRET_FILE" ]; then
    SERVICE_PWD=$(cat "$SECRET_FILE" | tr -d '[:space:]')
    LOGIN_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signin" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"$SERVICE_EMAIL\", \"password\": \"$SERVICE_PWD\"}")
    TOKEN=$(echo "$LOGIN_RESP" | jq -r '.token // empty')
fi

if [ -z "$TOKEN" ] || [ "$TOKEN" == "null" ]; then
    echo "🆕 [AUTH] Création compte service..."
    SERVICE_PWD=$(LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*()_+=-' </dev/urandom | head -c 64)
    SIGNUP_RESP=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signup" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$SERVICE_NAME\", \"email\": \"$SERVICE_EMAIL\", \"password\": \"$SERVICE_PWD\"}")
    TOKEN=$(echo "$SIGNUP_RESP" | jq -r '.token // empty')
    
    if [ -n "$TOKEN" ] && [ "$TOKEN" != "null" ]; then
        touch "$SECRET_FILE"
        chmod 600 "$SECRET_FILE"
        echo "$SERVICE_PWD" > "$SECRET_FILE"
        chmod 400 "$SECRET_FILE"
        echo "   ✅ Compte service créé et authentifié."
    else
        echo "❌ [FATAL] Echec création/authentification du compte service."
        echo "   🔍 Debug Info:"
        echo "   - URL: $OWUI_URL/api/v1/auths/signup"
        echo "   - Réponse API: $SIGNUP_RESP"
        exit 1
    fi
fi

# B. Création Compte Admin Humain (si nécessaire)
if [ ! -s "$ADMIN_SECRET_FILE" ]; then
    echo "🆕 [AUTH] Le fichier secret admin est manquant ou vide. Création d'un nouveau compte admin..."
    ADMIN_PWD=$(LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*()_+=-' </dev/urandom | head -c 16)
    
    # Sauvegarde sécurisée AVANT création
    touch "$ADMIN_SECRET_FILE"
    chmod 600 "$ADMIN_SECRET_FILE"
    echo "$ADMIN_PWD" > "$ADMIN_SECRET_FILE"
    chmod 400 "$ADMIN_SECRET_FILE"
    
    curl -s -X POST "$OWUI_URL/api/v1/auths/add" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$HUMAN_NAME\", \"email\": \"$HUMAN_EMAIL\", \"password\": \"$ADMIN_PWD\", \"role\": \"admin\"}" > /dev/null
        
    echo "   ✅ Admin créé. Credentials stockés dans $ADMIN_SECRET_FILE"
else
    echo "👍 [AUTH] Le compte admin existe déjà, pas de création nécessaire."
fi

# --- 3. IMPORT RESSOURCES (Legacy) ---
api_upsert() {
    local endpoint="$1"
    local id="$2"
    local payload="$3"
    local desc="$4"
    RESPONSE=$(curl -s -w "%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint/create" \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$payload")
    HTTP_CODE=${RESPONSE: -3}
    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 201 ]; then
        echo "   ✅ $desc : $id créé."
    elif [ "$HTTP_CODE" -eq 409 ]; then
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint/id/$id/update" \
            -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$payload" > /dev/null
        echo "   🔄 $desc : $id mis à jour."
    fi
}

# Fonction pour vérifier et basculer l'état (Active/Global) si nécessaire
toggle_state() {
    local id="$1"
    
    # Récupération de l'état actuel
    CURRENT_STATE=$(curl -s -X GET "$OWUI_URL/api/v1/functions/id/$id" \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
    
    IS_ACTIVE=$(echo "$CURRENT_STATE" | jq -r '.is_active')
    IS_GLOBAL=$(echo "$CURRENT_STATE" | jq -r '.is_global')
    
    # Bascule Global si nécessaire (on veut Global = true)
    if [ "$IS_GLOBAL" != "true" ]; then
        curl -s -X POST "$OWUI_URL/api/v1/functions/id/$id/toggle/global" \
            -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" > /dev/null
        echo "      🌐 $id passé en Global."
    fi
    
    # Bascule Active si nécessaire (on veut Active = true)
    if [ "$IS_ACTIVE" != "true" ]; then
        curl -s -X POST "$OWUI_URL/api/v1/functions/id/$id/toggle" \
            -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" > /dev/null
        echo "      ⚡ $id activé."
    fi
}

# Boucle générique de traitement des ressources
# Format: "API_ENDPOINT:API_ENDPOINT:DESCRIPTION_HUMAINE"
# $DESC est utilisé pour :
#  1. Déterminer le dossier source via un case/esac
#  2. Afficher des logs lisibles ("Traitement Outil...")
#  3. Construire le champ 'type' du JSON pour les Pipes/Filtres/Actions
for DIR_TYPE in "tools:tools:Outil" "functions:functions:Filtre" "functions:functions:Pipe" "functions:functions:Action"; do
    IFS=":" read -r DIR_NAME API_ENDPOINT DESC <<< "$DIR_TYPE"
    TARGET_DIR=""
    case "$DESC" in
        "Outil") TARGET_DIR="$TOOLS_DIR";;
        "Filtre") TARGET_DIR="$FILTERS_DIR";;
        "Pipe") TARGET_DIR="$PIPES_DIR";;
        "Action") TARGET_DIR="$ACTIONS_DIR";;
    esac

    if [ -d "$TARGET_DIR" ]; then
        echo "📂 Traitement $DESC..."
        for file in "$TARGET_DIR"/*.py;
 do
            [ -e "$file" ] || continue
            ID=$(basename "$file" | cut -d. -f1)
            echo "   👉 Découverte : $ID"
            
            # Extraction du Nom (Title) pour affichage propre
            # On récupère la première occurrence de "title:", on nettoie le préfixe et les espaces
            TITLE_LINE=$(grep -m 1 "title:" "$file")
            CLEAN_NAME=$(echo "$TITLE_LINE" | sed 's/.*title:[[:space:]]*//' | tr -d '\r')
            
            # Si pas de titre trouvé, on fallback sur l'ID
            if [ -n "$CLEAN_NAME" ]; then
                NAME="$CLEAN_NAME"
            else
                NAME="$ID"
            fi

            CONTENT=$(jq -sR . "$file")
            
            if [[ "$API_ENDPOINT" == "tools" ]]; then
                # Tools: Payload strict sans is_active/is_global
                PAYLOAD=$(jq -n --arg id "$ID" --arg name "$NAME" --arg content "$CONTENT" \
                    '{id: $id, name: $name, content: ($content|fromjson), meta: {}}')
                api_upsert "$API_ENDPOINT" "$ID" "$PAYLOAD" "$DESC"
                
            else
                # Functions: Payload strict, puis toggle
                TYPE_VAL=$(echo "$DESC" | tr '[:upper:]' '[:lower:]')
                PAYLOAD=$(jq -n --arg id "$ID" --arg name "$NAME" --arg content "$CONTENT" --arg type "$TYPE_VAL" \
                    '{id: $id, name: $name, content: ($content|fromjson), type: $type, meta: {}}')
                
                api_upsert "$API_ENDPOINT" "$ID" "$PAYLOAD" "$DESC"
                
                # Vérification et Forçage de l'état (Active + Global)
                toggle_state "$ID"
            fi
        done
    fi
done

# --- 4. CONFIGURATION MODELE (Assemblage & Déploiement) ---
# Fonction d'affichage d'erreur API détaillée
check_http_error() {
    local http_code="$1"
    local response_file="$2"
    local context="$3"
    
    if [ "$http_code" -ne 200 ] && [ "$http_code" -ne 201 ]; then
        echo "❌ [API ERROR] $context (HTTP $http_code)"
        if [ -s "$response_file" ]; then
            echo "   🔍 Réponse API :"
            cat "$response_file" | jq . 2>/dev/null || cat "$response_file"
        fi
        rm -f "$response_file"
        exit 1
    fi
}

if [ -f "$MODEL_CONFIG_FILE" ]; then
    echo "⏳ [MODEL] Attente de 2s pour stabilisation des index..."
    sleep 2
    
    # 1. Préparation de la Config Locale
    RAW_LOCAL_CONFIG=$(cat "$MODEL_CONFIG_FILE")
    # Si c'est un tableau, on prend le premier élément
    LOCAL_PAYLOAD=$(echo "$RAW_LOCAL_CONFIG" | jq 'if type=="array" then .[0] else . end')
    MODEL_ID=$(echo "$LOCAL_PAYLOAD" | jq -r '.id')
    
    echo "🧠 [MODEL] Configuration du modèle : $MODEL_ID"

    # 2. Découverte Dynamique des Ressources
    # On garantit que les variables sont des tableaux JSON valides "[]"
    
    TOOL_IDS="[]"
    if [ -d "$TOOLS_DIR" ]; then
        FOUND=$(find "$TOOLS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s .)
        # Vérification si JSON valide et non vide
        if echo "$FOUND" | jq empty >/dev/null 2>&1; then
             TOOL_IDS="$FOUND"
        fi
    fi
    
    FILTER_IDS="[]"
    if [ -d "$FILTERS_DIR" ]; then
        FOUND=$(find "$FILTERS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s .)
        if echo "$FOUND" | jq empty >/dev/null 2>&1; then
             FILTER_IDS="$FOUND"
        fi
    fi
    
    ACTION_IDS="[]"
    if [ -d "$ACTIONS_DIR" ]; then
        FOUND=$(find "$ACTIONS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s .)
        if echo "$FOUND" | jq empty >/dev/null 2>&1; then
             ACTION_IDS="$FOUND"
        fi
    fi

    echo "   🔗 Injection Dynamique :"
    echo "$TOOL_IDS" | jq -r '.[]' | while read id; do echo "      + Tool   : $id"; done
    echo "$FILTER_IDS" | jq -r '.[]' | while read id; do echo "      + Filter : $id"; done
    echo "$ACTION_IDS" | jq -r '.[]' | while read id; do echo "      + Action : $id"; done

    # 3. Construction du Payload (Base Locale + Injections)
    # On utilise des fichiers temporaires pour jq afin d'éviter les erreurs d'arguments shell
    TMP_LOCAL="/tmp/owui_local_$$.json"
    TMP_TOOLS="/tmp/owui_tools_$$.json"
    TMP_FILTERS="/tmp/owui_filters_$$.json"
    TMP_ACTIONS="/tmp/owui_actions_$$.json"
    
    echo "$LOCAL_PAYLOAD" > "$TMP_LOCAL"
    echo "$TOOL_IDS" > "$TMP_TOOLS"
    echo "$FILTER_IDS" > "$TMP_FILTERS"
    echo "$ACTION_IDS" > "$TMP_ACTIONS"
    
    # Construction et Nettoyage
    # On supprime les champs système qui pourraient gêner l'update (user_id, created...)
    # On force is_active = true
    FINAL_PAYLOAD=$(jq -n \
        --argjson local "$(cat $TMP_LOCAL)" \
        --argjson tools "$(cat $TMP_TOOLS)" \
        --argjson filters "$(cat $TMP_FILTERS)" \
        --argjson actions "$(cat $TMP_ACTIONS)" \
        '$local | del(.user_id, .created, .updated_at, .created_at, .is_global) | .is_active = true | .meta.toolIds = $tools | .meta.filterIds = $filters | .meta.defaultFilterIds = $filters | .meta.actionIds = $actions')

    rm -f "$TMP_LOCAL" "$TMP_TOOLS" "$TMP_FILTERS" "$TMP_ACTIONS"

    if [ -z "$FINAL_PAYLOAD" ]; then
        echo "❌ [FATAL] Erreur lors de la construction du payload JSON."
        exit 1
    fi

    # A. Injection System Prompt
    if [ -f "$SYSTEM_PROMPT_FILE" ]; then
       echo "   📄 Injection du System Prompt JSON..."
       FINAL_PAYLOAD=$(echo "$FINAL_PAYLOAD" | jq --rawfile prompt "$SYSTEM_PROMPT_FILE" '.params.system = $prompt')
    fi
    
    # B. Injection Image
    IMG_NAME=$(echo "$LOCAL_PAYLOAD" | jq -r '.local_image_filename // empty')
    if [ -n "$IMG_NAME" ] && [ "$IMG_NAME" != "null" ]; then
        IMG_PATH="$IMAGE_BASE_DIR/$IMG_NAME"
        if [ -f "$IMG_PATH" ]; then
            echo "   🖼️  Encodage de l'image : $IMG_NAME"
            MIME="image/png"
            [[ "$IMG_PATH" == *.jpg || "$IMG_PATH" == *.jpeg ]] && MIME="image/jpeg"
            [[ "$IMG_PATH" == *.webp ]] && MIME="image/webp"
            B64_DATA=$(base64 -w 0 "$IMG_PATH")
            FULL_B64="data:$MIME;base64,$B64_DATA"
            
            TMP_IMG="/tmp/owui_img_$$.txt"
            echo -n "$FULL_B64" > "$TMP_IMG"
            FINAL_PAYLOAD=$(echo "$FINAL_PAYLOAD" | jq --rawfile img "$TMP_IMG" '.meta.profile_image_url = $img | del(.local_image_filename)')
            rm -f "$TMP_IMG"
            echo "   ✅ Image injectée."
        fi
    fi

    # 4. Envoi de la Mise à Jour
    # On vérifie d'abord si le modèle existe pour choisir entre ADD et UPDATE
    # CORRECTIF: Utilisation du bon endpoint GET avec paramètre ?id=
    CHECK_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$OWUI_URL/api/v1/models/model?id=$MODEL_ID" -H "Authorization: Bearer $TOKEN")
    
    PAYLOAD_FILE="/tmp/owui_payload_$$.json"
    RESP_FILE="/tmp/owui_resp_$$.json"
    echo "$FINAL_PAYLOAD" > "$PAYLOAD_FILE"
    
    if [ "$DEBUG_MODE" == "true" ]; then
        echo "📤 DEBUG: Payload final :"
        cat "$PAYLOAD_FILE"
    fi

    if [ "$CHECK_CODE" -eq 200 ]; then
        echo "   🚀 Mise à jour du modèle existant (POST /update)..."
        # Endpoint VALIDE : /api/v1/models/model/update
        HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESP_FILE" -X POST "$OWUI_URL/api/v1/models/model/update" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "@$PAYLOAD_FILE")
    else
        echo "   🚀 Création du nouveau modèle (POST /create)..."
        # CORRECTIF: Utilisation de /create au lieu de /add
        HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESP_FILE" -X POST "$OWUI_URL/api/v1/models/create" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "@$PAYLOAD_FILE")
    fi
    
    # Vérification d'erreur stricte
    check_http_error "$HTTP_CODE" "$RESP_FILE" "Déploiement du modèle"
    
    echo "   ✅ Modèle déployé avec succès."
    
    rm -f "$PAYLOAD_FILE" "$RESP_FILE"
    
    # 5. Vérification Finale
    NEW_REMOTE=$(curl -s -X GET "$OWUI_URL/api/v1/models/model?id=$MODEL_ID" -H "Authorization: Bearer $TOKEN")
    R_TOOLS=$(echo "$NEW_REMOTE" | jq '.info.meta.toolIds | length // .meta.toolIds | length // 0')
    L_TOOLS=$(echo "$TOOL_IDS" | jq length)
    
    if [ "$R_TOOLS" -ne "$L_TOOLS" ]; then
         echo "   ⚠️  [WARNING] Discrépance Tools (Reçu: $R_TOOLS / Attendu: $L_TOOLS)."
         echo "   🔍 DEBUG STRUCTURE :"
         echo "$NEW_REMOTE" | jq .
    else
         echo "   ✨ Vérification : OK ($L_TOOLS outils synchronisés)"
    fi
fi

echo "✅ [Config] Terminé avec succès."

# --- 5. AFFICHAGE ADMIN ---
# Lancement du script d'affichage sécurisé (si présent)
if [ -f "/opt/echo-scripts/show-echo-admin.sh" ]; then
    bash "/opt/echo-scripts/show-echo-admin.sh"
fi
