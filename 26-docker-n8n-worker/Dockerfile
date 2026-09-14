# ==============================================================================
# STAGE 1 : L'extraction (Le vol de N8N pré-compilé)
# ==============================================================================
FROM n8nio/n8n:latest AS n8n_official

# ==============================================================================
# STAGE 2 : Image d'exécution basée sur Node 26 standard (Alignement ABI)
# ==============================================================================
FROM node:26-alpine

# Installation de Python, FastAPI et des librairies requises par les addons natifs C++ de N8N
# (librdkafka-dev est crucial pour éviter le crash du module kafka-javascript)
RUN apk add --no-cache python3 py3-pip bash libstdc++ librdkafka-dev

# On copie N8N bit pour bit de l'image officielle vers notre Alpine
COPY --from=n8n_official /usr/local/lib/node_modules/n8n /usr/local/lib/node_modules/n8n

# On recrée le symlink manuellement
RUN mkdir -p /usr/local/bin && ln -s /usr/local/lib/node_modules/n8n/bin/n8n /usr/local/bin/n8n

# Installation de l'API ECHO (FastAPI)
RUN python3 -m pip install --no-cache-dir --break-system-packages fastapi uvicorn pydantic httpx bcrypt

# Préparation de l'environnement de travail
WORKDIR /app
COPY n8n_api.py .
COPY n8n_architecture.md .
COPY start.sh .

RUN chmod +x start.sh && \
    mkdir -p /home/node/.n8n && \
    chown -R node:node /home/node/.n8n && \
    chown -R node:node /app

# Sécurité : exécution sans privilèges root
USER node
EXPOSE 5003

# Lancement conjoint de FastAPI et N8N
CMD ["/bin/bash", "./start.sh"]
