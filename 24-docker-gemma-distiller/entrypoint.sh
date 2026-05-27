#!/bin/bash
# ==============================================================================
# ECHO GEMMA DISTILLER — Entrypoint
# VERSION : 1.0
# ==============================================================================
# Auto-provisioning du modèle GGUF au premier démarrage.
# Le modèle est stocké dans /models (volume monté depuis l'hôte).
# aria2c (16 connexions) si disponible, sinon wget -c (reprise).
# ==============================================================================

MODEL_PATH="${MODEL_PATH:-/models/distiller.gguf}"
MODEL_URL="${MODEL_URL:-https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q5_K_M.gguf}"
MODEL_DIR=$(dirname "$MODEL_PATH")
MODEL_FILE=$(basename "$MODEL_PATH")

mkdir -p "$MODEL_DIR"

# Vérification : modèle présent, complet (pas de .aria2 résiduel) et > 5 Go (Q5_K_M = 5.48 Go)
if [ -f "$MODEL_PATH" ] && [ ! -f "${MODEL_PATH}.aria2" ] && [ $(stat -c%s "$MODEL_PATH" 2>/dev/null || echo 0) -gt 5000000000 ]; then
    echo "✅ Modèle vérifié : $MODEL_PATH ($(du -h "$MODEL_PATH" | cut -f1))"
else
    echo "🧠 Téléchargement du modèle Gemma 4 E4B (Q5_K_M, ~3 Go)..."

    if command -v aria2c > /dev/null 2>&1; then
        echo "   ⚡ Mode accéléré (aria2c, 16 connexions parallèles)..."
        aria2c -x 16 -s 16 -k 1M --continue=true \
            -d "$MODEL_DIR" -o "$MODEL_FILE" \
            "$MODEL_URL"
    else
        echo "   📦 Téléchargement wget (reprise automatique si interrompu)..."
        wget -c -q --show-progress -O "$MODEL_PATH" "$MODEL_URL"
    fi

    # Validation
    if [ -f "$MODEL_PATH" ] && [ ! -f "${MODEL_PATH}.aria2" ] && [ $(stat -c%s "$MODEL_PATH" 2>/dev/null || echo 0) -gt 5000000000 ]; then
        echo "   ✅ Modèle provisionné avec succès."
    else
        echo "❌ Téléchargement incomplet ou échoué. Le service va redémarrer (restart: always)."
        exit 1
    fi
fi

# Lancement du serveur
exec python app.py
