#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (API-BASED)
# VERSION : 5.15.0
# AUTEUR  : Wilfried BARNAVON
# DATE    : 2026-01-21
# ==============================================================================

OWUI_URL="http://localhost:8080"
ADMIN_EMAIL="admin@echo.local"
ADMIN_PASSWORD="password" 
ADMIN_NAME="ECHO Architect"

# Répertoire du script courant (pour localiser les fichiers JSON config)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🔧 [Config] Démarrage de l'initialisation ECHO..."

# --- 1. ATTENTE DISPONIBILITE API ---
echo "⏳ [Config] Attente API..."
until curl -s -f "$OWUI_URL/health" > /dev/null; do
    sleep 5
    echo -n "."
done
echo " OK."

# --- 2. AUTHENTIFICATION ---
echo "🔑 [AUTH] Login Admin..."
TOKEN=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signin" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$ADMIN_EMAIL\", \"password\": \"$ADMIN_PASSWORD\"}" | jq -r '.token')

if [ "$TOKEN" == "null" ] || [ -z "$TOKEN" ]; then
    echo "🆕 Création compte Admin..."
    TOKEN=$(curl -s -X POST "$OWUI_URL/api/v1/auths/signup" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$ADMIN_NAME\", \"email\": \"$ADMIN_EMAIL\", \"password\": \"$ADMIN_PASSWORD\"}" | jq -r '.token')
fi

if [ -z "$TOKEN" ] || [ "$TOKEN" == "null" ]; then
    echo "❌ [FATAL] ECHEC AUTH. Impossible de configurer."
    exit 1
fi

# Fonction intelligente Create ou Update
api_upsert() {
    local endpoint_base="$1" # ex: "tools", "functions"
    local id="$2"
    local payload="$3"
    local type_desc="$4"

    # 1. Tentative de CREATION
    RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint_base/create" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload")
    
    HTTP_CODE=$(echo "$RESPONSE" | tail -n1 | cut -d: -f2)
    BODY=$(echo "$RESPONSE" | sed '$d')

    # FIX 5.13.1 : Gestion de l'erreur 409 (Conflict/Already registered) pour déclencher l'update
    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 201 ]; then
        echo "   ✅ $type_desc créé."
    elif echo "$BODY" | grep -q "already registered" || [ "$HTTP_CODE" -eq 409 ]; then
        # 2. Si existe déjà -> MISE A JOUR (Update)
        echo "   🔄 $type_desc existe déjà. Mise à jour..."
        
        RESPONSE_UPD=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/update" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$payload")
            
        HTTP_UPD=$(echo "$RESPONSE_UPD" | tail -n1 | cut -d: -f2)
        if [ "$HTTP_UPD" -ge 200 ] && [ "$HTTP_UPD" -lt 300 ]; then
             echo "   ✅ $type_desc mis à jour."
        else
             BODY_UPD=$(echo "$RESPONSE_UPD" | sed '$d')
             echo "   ❌ ECHEC UPDATE ($HTTP_UPD): $BODY_UPD"
        fi
    else
        echo "   ❌ ERREUR ($HTTP_CODE): $BODY"
    fi
}

# --- FIX V5.4 : Fonction Smart Toggle ---
ensure_active() {
    local endpoint_base="$1"
    local id="$2"
    
    # 1. Vérifier l'état actuel
    STATE_RESP=$(curl -s -X GET "$OWUI_URL/api/v1/$endpoint_base/id/$id" \
        -H "Authorization: Bearer $TOKEN")
    
    IS_ACTIVE=$(echo "$STATE_RESP" | jq -r '.is_active')
    
    echo "      🔎 État actuel de $id : $IS_ACTIVE"

    if [ "$IS_ACTIVE" == "true" ]; then
        # Si déjà actif -> ON/OFF/ON pour rafraîchir le cache UI et Valves
        echo "      🔄 Rafraîchissement Cache (OFF -> ON)..."
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/toggle" -H "Authorization: Bearer $TOKEN" > /dev/null
        sleep 1
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/toggle" -H "Authorization: Bearer $TOKEN" > /dev/null
    else
        # Si inactif -> ON une seule fois
        echo "      🚀 Activation Forcée (ON)..."
        curl -s -X POST "$OWUI_URL/api/v1/$endpoint_base/id/$id/toggle" -H "Authorization: Bearer $TOKEN" > /dev/null
    fi
    
    echo "      ✅ $id opérationnel (Global)."
}


# --- 3. PARAMETRES GLOBAUX ---
echo "⚙️ [SETTINGS] Update..."
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
        "enable_memory": true 
    }
}' > /dev/null

# --- 4. IMPORT OUTILS ---
echo "🛠️ [TOOLS] Traitement des Outils..."
TOOLS_DIR="/opt/owui-tools"
if [ -d "$TOOLS_DIR" ]; then
    for file in $TOOLS_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        TOOL_ID="${FILENAME%.*}"
        echo "   -> Traitement de $TOOL_ID..."
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        PAYLOAD=$(jq -n \
                  --arg id "$TOOL_ID" \
                  --arg name "$TOOL_ID" \
                  --arg content "$CONTENT" \
                  '{id: $id, name: $name, content: ($content | fromjson), meta: {description: "ECHO Tool", manifest: {}}}')
        
        api_upsert "tools" "$TOOL_ID" "$PAYLOAD" "Outil"
    done
fi

# --- 5. IMPORT FILTRES (GLOBAL VALVES) ---
echo "🛡️ [FILTERS] Traitement des Filtres..."
FILTERS_DIR="/opt/owui-filters"
if [ -d "$FILTERS_DIR" ]; then
    for file in $FILTERS_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        FILTER_ID="${FILENAME%.*}"
        echo "   -> Traitement de $FILTER_ID..."
        
        # Cas spécifique pour Bypass RAG si besoin de log
        if [ "$FILTER_ID" == "bypass_rag" ]; then
            echo "      ⚠️  Filtre Critique détecté : Bypass RAG (Audit Aligned)"
        fi
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        # NOTE: is_active: true force l'activation globale de la fonction (Type Filter)
        PAYLOAD=$(jq -n \
                  --arg id "$FILTER_ID" \
                  --arg name "$FILTER_ID" \
                  --arg content "$CONTENT" \
                  '{ 
                    id: $id, 
                    name: $name, 
                    content: ($content | fromjson), 
                    type: "filter", 
                    meta: {
                        description: "ECHO Filter", 
                        manifest: {}
                    }, 
                    is_active: true,
                    is_global: true
                  }')
        
        api_upsert "functions" "$FILTER_ID" "$PAYLOAD" "Filtre"
        ensure_active "functions" "$FILTER_ID"
    done
fi

# --- 6. IMPORT PIPES (ECHO ENGINE) ---
echo "🧩 [PIPES] Traitement du Pipe Engine..."
PIPES_DIR="/opt/owui-pipes"
if [ -d "$PIPES_DIR" ]; then
    for file in $PIPES_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        FUNC_ID="${FILENAME%.*}"
        echo "   -> Traitement de $FUNC_ID..."
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        
        if [ "$FUNC_ID" == "pipe_engine" ]; then
            # --- FIX V5.6 : HARD RESET ---
            echo "      ♻️  Reset ECHO Engine (Suppression préalable pour purger User Valves)..."
            curl -s -X DELETE "$OWUI_URL/api/v1/functions/id/$FUNC_ID" \
                -H "Authorization: Bearer $TOKEN" > /dev/null
            
            echo "      🚀 Configuration Spéciale ECHO Engine (Native Mode)..."
            # Payload complet avec function_calling: native et toolIds explicites
            PAYLOAD=$(jq -n \
                --arg id "$FUNC_ID" \
                --arg content "$CONTENT" \
                '{ 
                  id: $id,
                  name: "ECHO Engine",
                  content: ($content | fromjson),
                  type: "pipe",
                  is_active: true,
                  params: {
                    function_calling: "native"
                  },
                  meta: {
                    profile_image_url: "/static/favicon.png",
                    description: "ECHO v5 Kernel - Agentic Infrastructure",
                    manifest: {},
                    capabilities: {
                      vision: true,
                      file_upload: true,
                      web_search: false,
                      image_generation: false,
                      code_interpreter: false,
                      citations: true,
                      status_updates: true
                    },
                    toolIds: [
                      "api_client",
                      "context_gauge",
                      "gemini_internal_web_search",
                      "python_code_executor",
                      "web_browser_advanced"
                    ]
                  }
                }')
        else
            PAYLOAD=$(jq -n \
                      --arg id "$FUNC_ID" \
                      --arg name "$FUNC_ID" \
                      --arg content "$CONTENT" \
                      '{id: $id, name: $name, content: ($content | fromjson), type: "pipe", meta: {description: "ECHO Helper", manifest: {}}, is_active: true}')
        fi

        api_upsert "functions" "$FUNC_ID" "$PAYLOAD" "Fonction (Pipe)"
        
        if [ "$FUNC_ID" == "pipe_engine" ]; then
             ensure_active "functions" "$FUNC_ID"
        fi
    done
fi

# --- 7. IMPORT ACTIONS ---
echo "🎬 [ACTIONS] Traitement des Actions UI..."
ACTIONS_DIR="/opt/owui-actions"
if [ -d "$ACTIONS_DIR" ]; then
    for file in $ACTIONS_DIR/*.py; do
        [ -e "$file" ] || continue
        FILENAME=$(basename "$file")
        ACTION_ID="${FILENAME%.*}"
        echo "   -> Traitement de $ACTION_ID..."
        
        CONTENT=$(sed '1s/^\xEF\xBB\xBF//' "$file" | jq -sR .)
        
        PAYLOAD=$(jq -n \
                  --arg id "$ACTION_ID" \
                  --arg name "$ACTION_ID" \
                  --arg content "$CONTENT" \
                  '{ 
                    id: $id, 
                    name: $name, 
                    content: ($content | fromjson), 
                    type: "action", 
                    is_active: true,
                    meta: {
                        description: "ECHO Action", 
                        manifest: {}
                    }
                  }')
        
        api_upsert "functions" "$ACTION_ID" "$PAYLOAD" "Action"
        ensure_active "functions" "$ACTION_ID"
    done
fi

# --- 8. CONFIGURATION DU MODELE (SYSTEM PROMPT & META) ---
echo "🧠 [MODEL] Configuration du Prompt Système et des Métadonnées..."

MODEL_CONFIG_FILE="$SCRIPT_DIR/model-config.json"
SYSTEM_PROMPT_FILE="$SCRIPT_DIR/system-prompt.json"
MODEL_ID="pipe_engine"

# Vérification Stricte des Fichiers de Config
if [ ! -f "$MODEL_CONFIG_FILE" ]; then
    echo "❌ [FATAL] Fichier de configuration manquant : $MODEL_CONFIG_FILE"
    echo "   Veuillez placer 'model-config.json' dans $SCRIPT_DIR"
    exit 1
fi

if [ ! -f "$SYSTEM_PROMPT_FILE" ]; then
    echo "❌ [FATAL] Fichier de prompt système manquant : $SYSTEM_PROMPT_FILE"
    echo "   Veuillez placer 'system-prompt.json' dans $SCRIPT_DIR"
    exit 1
fi

# 1. Lecture du Prompt Système (Extraction Intelligente ou Brute)
echo "   📄 Lecture Prompt : $SYSTEM_PROMPT_FILE"
EXTRACTED_PROMPT=$(jq -r '.content // .system_prompt // empty' "$SYSTEM_PROMPT_FILE" 2>/dev/null)

if [ -n "$EXTRACTED_PROMPT" ]; then
    SYSTEM_PROMPT="$EXTRACTED_PROMPT"
else
    # Fallback : Lecture intégrale du fichier comme chaîne de caractères
    SYSTEM_PROMPT=$(cat "$SYSTEM_PROMPT_FILE")
fi

# 2. Lecture de la Config Modèle et Fusion
echo "   📄 Lecture Config : $MODEL_CONFIG_FILE"
MODEL_PAYLOAD=$(jq --arg system "$SYSTEM_PROMPT" '
    .[0] |
    del(.user_id, .created, .updated_at, .created_at, .access_control) |
    .params.system = $system |
    .is_active = true
' "$MODEL_CONFIG_FILE")

# 3. Logique DELETE-then-ADD (Adapté à la doc technique: /api/models/{id} sans /id/ et /add)
echo "   -> Vérification existence modèle $MODEL_ID..."
CHECK_MODEL=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$OWUI_URL/api/v1/models/$MODEL_ID" -H "Authorization: Bearer $TOKEN")

if [ "$CHECK_MODEL" -eq 200 ]; then
    echo "   ♻️  Modèle existant détecté. Suppression préalable (Clean Slate)..."
    # Note: L'endpoint de suppression est /api/v1/models/{id} (sans /id/ intermédiaire)
    curl -s -X DELETE "$OWUI_URL/api/v1/models/$MODEL_ID" -H "Authorization: Bearer $TOKEN" > /dev/null
fi

echo "   🆕 Création du modèle $MODEL_ID (via /add)..."
# Note: L'endpoint de création est /api/v1/models/add (et non /create)
RESPONSE_MODEL=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$OWUI_URL/api/v1/models/add" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$MODEL_PAYLOAD")

HTTP_MODEL=$(echo "$RESPONSE_MODEL" | tail -n1 | cut -d: -f2)
if [ "$HTTP_MODEL" -ge 200 ] && [ "$HTTP_MODEL" -lt 300 ]; then
    echo "   ✅ Modèle configuré avec succès."
else
    BODY_MODEL=$(echo "$RESPONSE_MODEL" | sed '$d')
    echo "   ❌ ECHEC CONFIG MODELE ($HTTP_MODEL): $BODY_MODEL"
fi

echo "✅ [Config] Terminé."