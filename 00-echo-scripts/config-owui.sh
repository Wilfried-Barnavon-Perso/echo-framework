#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (MODE ASSEMBLAGE) (retour à la 7.26)
# VERSION : 7.35
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

# --- 4. CONFIGURATION MODELE (Merge & Update) ---
# Fonction d'affichage d'erreur API détaillée
check_http_error() {
    local http_code="$1"
    local response_file="$2"
    local context="$3"
    
    if [ "$http_code" -ne 200 ]; then
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

    # 2. Récupération du Modèle Distant (Retry 3x)
    MAX_RETRIES=3
    COUNT=0
    REMOTE_MODEL=""
    
    echo "   📥 Tentative de récupération de la configuration existante..."
    TMP_RESP="/tmp/owui_get_model_$$.json"
    
    until [ "$COUNT" -ge "$MAX_RETRIES" ]; do
        HTTP_CODE=$(curl -s -w "%{http_code}" -o "$TMP_RESP" -X GET "$OWUI_URL/api/v1/models/$MODEL_ID" \
            -H "Authorization: Bearer $TOKEN")
            
        if [ "$HTTP_CODE" -eq 200 ]; then
            REMOTE_MODEL=$(cat "$TMP_RESP")
            echo "      ✅ Modèle trouvé (Tentative $(($COUNT+1))/$MAX_RETRIES)."
            break
        else
            echo "      ⚠️  Modèle non trouvé ou non prêt (HTTP $HTTP_CODE). Nouvelle tentative dans 3s..."
            sleep 3
            ((COUNT++))
        fi
    done
    rm -f "$TMP_RESP"

    # 3. Arrêt si échec (Critique)
    if [ -z "$REMOTE_MODEL" ]; then
        echo "❌ [FATAL] Impossible de récupérer le modèle '$MODEL_ID' après $MAX_RETRIES tentatives."
        echo "   💡 Le Pipe a été enregistré, mais le modèle associé n'est pas accessible."
        echo "   💡 Vérifiez que le fichier 'pipe_engine.py' définit bien un modèle valide."
        exit 1
    fi
    
    # 4. Fusion & Injection (Merge Strategy)
    # On prend le REMOTE comme base, et on applique le LOCAL par dessus.
    # Cela permet de conserver les IDs internes ou champs cachés d'Open WebUI.
    
    echo "   🔨 Fusion de la configuration locale sur la configuration distante..."
    
    # Découverte Dynamique (inchangé)
    TOOL_IDS="[]"; FILTER_IDS="[]"; ACTION_IDS="[]"
    [ -d "$TOOLS_DIR" ] && TOOL_IDS=$(find "$TOOLS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s .)
    [ -d "$FILTERS_DIR" ] && FILTER_IDS=$(find "$FILTERS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s .)
    [ -d "$ACTIONS_DIR" ] && ACTION_IDS=$(find "$ACTIONS_DIR" -name "*.py" -exec basename {} .py \; | jq -R . | jq -s .)

    echo "   🔗 Injection Dynamique :"
    echo "$TOOL_IDS" | jq -r '.[]' | while read id; do echo "      + Tool   : $id"; done
    echo "$FILTER_IDS" | jq -r '.[]' | while read id; do echo "      + Filter : $id"; done
    echo "$ACTION_IDS" | jq -r '.[]' | while read id; do echo "      + Action : $id"; done

    # Construction du Payload Final (Merge)
    # 1. Merge Remote + Local
    # 2. Injection IDs
    # 3. Nettoyage champs protégés (id, created, etc. ne doivent pas être écrasés par le local si null)
    MERGED_PAYLOAD=$(jq -n \
        --argjson remote "$REMOTE_MODEL" \
        --argjson local "$LOCAL_PAYLOAD" \
        --argjson tools "$TOOL_IDS" \
        --argjson filters "$FILTER_IDS" \
        --argjson actions "$ACTION_IDS" \
        '$remote * $local | .meta.toolIds = $tools | .meta.filterIds = $filters | .meta.defaultFilterIds = $filters | .meta.actionIds = $actions')

    # A. Injection System Prompt
    if [ -f "$SYSTEM_PROMPT_FILE" ]; then
       echo "   📄 Injection du System Prompt JSON..."
       MERGED_PAYLOAD=$(echo "$MERGED_PAYLOAD" | jq --rawfile prompt "$SYSTEM_PROMPT_FILE" '.params.system = $prompt')
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
            
            # Injection via fichier tmp pour éviter "Argument list too long"
            TMP_IMG="/tmp/owui_img_$$.txt"
            echo -n "$FULL_B64" > "$TMP_IMG"
            MERGED_PAYLOAD=$(echo "$MERGED_PAYLOAD" | jq --rawfile img "$TMP_IMG" '.meta.profile_image_url = $img | del(.local_image_filename)')
            rm -f "$TMP_IMG"
            echo "   ✅ Image injectée."
        fi
    fi

    # 5. Envoi de la Mise à Jour
    PAYLOAD_FILE="/tmp/owui_payload_$$.json"
    RESP_FILE="/tmp/owui_resp_$$.json"
    echo "$MERGED_PAYLOAD" > "$PAYLOAD_FILE"
    
    if [ "$DEBUG_MODE" == "true" ]; then
        echo "📤 DEBUG: Payload fusionné :"
        cat "$PAYLOAD_FILE"
    fi

    echo "   🚀 Envoi de la mise à jour (POST /update)..."
    HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESP_FILE" -X POST "$OWUI_URL/api/v1/models/model/update" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "@$PAYLOAD_FILE")
    
    # Vérification d'erreur stricte
    check_http_error "$HTTP_CODE" "$RESP_FILE" "Mise à jour du modèle"
    
    echo "   ✅ Modèle mis à jour avec succès."
    
    rm -f "$PAYLOAD_FILE" "$RESP_FILE"
    
    # 6. Vérification Finale (Statistiques)
    # On réutilise REMOTE_MODEL pour la comparaison ? Non, il faut re-fetcher le NOUVEAU
    NEW_REMOTE=$(curl -s -X GET "$OWUI_URL/api/v1/models/$MODEL_ID" -H "Authorization: Bearer $TOKEN")
    
    R_TOOLS=$(echo "$NEW_REMOTE" | jq '.info.meta.toolIds | length // .meta.toolIds | length // 0')
    L_TOOLS=$(echo "$TOOL_IDS" | jq length)
    
    if [ "$R_TOOLS" -ne "$L_TOOLS" ]; then
         echo "   ⚠️  [WARNING] Tools attendus: $L_TOOLS, reçus: $R_TOOLS. La mise à jour a réussi mais les liens semblent incomplets."
    else
         echo "   ✨ Vérification Configuration : OK ($L_TOOLS outils synchronisés)"
    fi
fi

echo "✅ [Config] Terminé avec succès."

# --- 5. AFFICHAGE ADMIN ---
# Lancement du script d'affichage sécurisé (si présent)
if [ -f "/opt/echo-scripts/show-echo-admin.sh" ]; then
    bash "/opt/echo-scripts/show-echo-admin.sh"
fi
