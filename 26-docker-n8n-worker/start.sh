#!/bin/bash
set -e

# Injection dynamique : Hashage du mot de passe propriétaire
if [ -n "$N8N_INSTANCE_OWNER_PASSWORD" ]; then
    export N8N_INSTANCE_OWNER_PASSWORD_HASH=$(python3 -c "import os, bcrypt; print(bcrypt.hashpw(os.getenv('N8N_INSTANCE_OWNER_PASSWORD').encode(), bcrypt.gensalt(12)).decode())")
fi

echo "[ECHO Worker] Démarrage du moteur N8N local en arrière-plan..."
n8n start &

echo "[ECHO Worker] Initialisation de la surcouche FastAPI d'orchestration (Port 5003)..."
exec uvicorn n8n_api:app --host 0.0.0.0 --port 5003
