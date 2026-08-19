#!/bin/bash

# Hash dynamique du mot de passe en clair fourni par l'environnement
if [ -n "$N8N_INSTANCE_OWNER_PASSWORD" ]; then
    export N8N_INSTANCE_OWNER_PASSWORD_HASH=$(python3 -c "import os, bcrypt; print(bcrypt.hashpw(os.getenv('N8N_INSTANCE_OWNER_PASSWORD').encode(), bcrypt.gensalt(12)).decode())")
fi

# Démarrage du démon N8N en arrière-plan
n8n start &
# Démarrage de l'API FastAPI au premier plan
exec uvicorn n8n_api:app --host 0.0.0.0 --port 5003
