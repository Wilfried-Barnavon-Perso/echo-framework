#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (MODE ASSEMBLAGE)
# VERSION : 7.8
# ==============================================================================

# --- CONFIGURATION ---
OWUI_URL="http://localhost:3000"
SECRET_FILE="/opt/config/.owui-setting-secret"
ADMIN_SECRET_FILE="/opt/config/.owui-admin-secret"

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
            CONTENT=$(jq -sR . "$file")
            
            if [[ "$API_ENDPOINT" == "tools" ]]; then
                PAYLOAD=$(jq -n --arg id "$ID" --arg content "$CONTENT" \
                    '{id: $id, name: $id, content: ($content|fromjson), meta: {}}')
            else
                TYPE_VAL=$(echo "$DESC" | tr '[:upper:]' '[:lower:]')
                PAYLOAD=$(jq -n --arg id "$ID" --arg content "$CONTENT" --arg type "$TYPE_VAL" \
                    '{id: $id, name: $id, content: ($content|fromjson), type: $type, is_active: true, meta: {}}')
            fi
            api_upsert "$API_ENDPOINT" "$ID" "$PAYLOAD" "$DESC"
        done
    fi
done

# --- 4. CONFIGURATION MODELE (Assemblage) ---
if [ -f "$MODEL_CONFIG_FILE" ]; then
    echo "🧠 [MODEL] Assemblage et déploiement du modèle..."
    
    # Lecture Config (Gère si c'est un tableau [] ou un objet {})
    RAW_CONFIG=$(cat "$MODEL_CONFIG_FILE")
    IS_ARRAY=$(echo "$RAW_CONFIG" | jq 'if type=="array" then "yes" else "no" end')
    
    if [[ $IS_ARRAY == '"yes"' ]]; then
        FINAL_PAYLOAD=$(echo "$RAW_CONFIG" | jq '.[0]')
    else
        FINAL_PAYLOAD="$RAW_CONFIG"
    fi

    # Nettoyage des champs système auto-générés pour éviter les conflits
    FINAL_PAYLOAD=$(echo "$FINAL_PAYLOAD" | jq 'del(.user_id, .created, .updated_at, .created_at, .access_control) | .is_active = true')

    MODEL_ID=$(echo "$FINAL_PAYLOAD" | jq -r '.id')
    
    # A. Injection du System Prompt JSON
    if [ -f "$SYSTEM_PROMPT_FILE" ]; then
        echo "   📄 Injection du System Prompt JSON..."
        # Utilisation de --rawfile pour injecter le JSON entier comme une string dans params.system
        FINAL_PAYLOAD=$(echo "$FINAL_PAYLOAD" | jq --rawfile prompt "$SYSTEM_PROMPT_FILE" '.params.system = $prompt')
    fi
    
    # B. Injection de l'Image Locale
    IMG_NAME=$(echo "$FINAL_PAYLOAD" | jq -r '.local_image_filename // empty')
    if [ -n "$IMG_NAME" ] && [ "$IMG_NAME" != "null" ]; then
        IMG_PATH="$IMAGE_BASE_DIR/$IMG_NAME"
        if [ -f "$IMG_PATH" ]; then
            echo "   🖼️  Encodage de l'image : $IMG_NAME"
            MIME="image/png"
            [[ "$IMG_PATH" == *.jpg || "$IMG_PATH" == *.jpeg ]] && MIME="image/jpeg"
            [[ "$IMG_PATH" == *.webp ]] && MIME="image/webp"
            
            # Encodage Base64 (-w 0 pour linux/busybox)
            B64_DATA=$(base64 -w 0 "$IMG_PATH")
            FULL_B64="data:$MIME;base64,$B64_DATA"
            
            # Remplacement dans le Payload
            FINAL_PAYLOAD=$(echo "$FINAL_PAYLOAD" | jq --arg img "$FULL_B64" '.meta.profile_image_url = $img | del(.local_image_filename)')
        else
            echo "   ⚠️ Image introuvable : $IMG_PATH"
        fi
    fi
    
    # C. Envoi API
    CHECK=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$OWUI_URL/api/v1/models/$MODEL_ID" -H "Authorization: Bearer $TOKEN")
    
    # Note: On force l'update pour s'assurer que l'image/prompt sont rafraichis
    if [ "$CHECK" -eq 200 ]; then
        curl -s -X POST "$OWUI_URL/api/v1/models/model/update" \
            -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$FINAL_PAYLOAD" > /dev/null
        echo "   ✅ Modèle mis à jour."
    else
        curl -s -X POST "$OWUI_URL/api/v1/models/add" \
            -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$FINAL_PAYLOAD" > /dev/null
        echo "   ✅ Modèle créé."
    fi
fi

echo "✅ [Config] Terminé avec succès."

# --- 5. AFFICHAGE ADMIN ---
# Lancement du script d'affichage sécurisé (si présent)
if [ -f "/opt/echo-scripts/show-echo-admin.sh" ]; then
    bash "/opt/echo-scripts/show-echo-admin.sh"
fi
