# ==============================================================================
# STAGE UNIQUE : Compilation native depuis une image Node Alpine standard
# ==============================================================================
# On s'aligne sur la version de Node (26) utilisée par l'équipe officielle N8N
FROM node:26-alpine

# 1. Installation des dépendances système requises pour Python, FastAPI
#    ET pour la compilation native de `isolated-vm` (make, g++).
RUN apk add --no-cache python3 py3-pip bash libstdc++ make g++

# 2. Installation de N8N globalement via NPM.
#    Cela force node-gyp à compiler `isolated-vm` spécifiquement pour
#    le moteur V8 de cette version de Node et pour `musl` (Alpine).
#    Garantit zéro mismatch d'ABI et zéro erreur de pointeur/IsolatePool.
RUN npm install -g n8n

# 3. Installation de l'API ECHO (FastAPI)
RUN python3 -m pip install --no-cache-dir --break-system-packages fastapi uvicorn pydantic httpx bcrypt

# 4. Préparation de l'environnement de travail
WORKDIR /app
COPY n8n_api.py .
COPY n8n_architecture.md .
COPY start.sh .

# 5. Préparation du répertoire N8N et droits
RUN chmod +x start.sh && \
    mkdir -p /home/node/.n8n && \
    chown -R node:node /home/node/.n8n && \
    chown -R node:node /app

# 6. Sécurité : exécution sans privilèges root
USER node
EXPOSE 5003

# Lancement conjoint de FastAPI et N8N
CMD ["/bin/bash", "./start.sh"]
