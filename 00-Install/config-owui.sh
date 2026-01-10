#!/bin/bash
# ==============================================================================
# CONFIGURATION AUTOMATIQUE OPEN WEBUI (API-BASED v5.13 - ECHO ENGINE)
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# - Gestion UTF-8 BOM (Nettoyage)
# - Gestion Create/Update (Idempotence) avec FIX URL (/id/)
# - Configuration Native Pipe "ECHO Engine" & Activation Tools/Filters
# - FIX v5.4 : Smart Toggle (Vérification d'état avant bascule)
# - FIX v5.6 : HARD RESET ECHO Engine (Delete/Create) pour purger User Valves
# - UPDATE v5.13 : Activation globale systématique des filtres (Bypass RAG etc.)
# ==============================================================================

OWUI_URL="http://localhost:8080"
ADMIN_EMAIL="admin@echo.local"
ADMIN_PASSWORD="password" 
ADMIN_NAME="ECHO Architect"

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
    local endpoint_base="$1" # ex: "tools" ou "functions"
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

    if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 201 ]; then
        echo "   ✅ $type_desc créé."
    elif echo "$BODY" | grep -q "already registered"; then
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
        echo "      🚀 Activation Forcée (ON)..."
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

# --- 6. IMPORT FONCTIONS (PIPES) - ECHO ENGINE ---
echo "🧩 [FUNCTIONS] Traitement du Pipe Engine..."
FUNCS_DIR="/opt/owui-functions"
if [ -d "$FUNCS_DIR" ]; then
    for file in $FUNCS_DIR/*.py; do
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
        
        # --- FIX V5.4 : Smart Toggle ---
        if [ "$FUNC_ID" == "pipe_engine" ]; then
             ensure_active "functions" "$FUNC_ID"
        fi
    done
fi

echo "✅ [Config] Terminé."