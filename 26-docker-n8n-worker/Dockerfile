# ==============================================================================
# STAGE UNIQUE : Extension native de l'image officielle N8N (Alpine-based)
# ==============================================================================
FROM n8nio/n8n:latest

# Élévation temporaire des privilèges pour déverrouiller Alpine
USER root

# Installation de Python 3 et FastAPI dans l'environnement natif
# (Ceci préserve la compatibilité stricte de isolated-vm et V8)
RUN apk add --no-cache python3 py3-pip bash && \
    python3 -m pip install --no-cache-dir --break-system-packages fastapi uvicorn pydantic httpx bcrypt

# Préparation de l'environnement API ECHO
WORKDIR /app
COPY n8n_api.py .
COPY n8n_architecture.md .
COPY start.sh .

# Définition des permissions correctes
RUN chmod +x start.sh && \
    chown -R node:node /app

# Retour au contexte de sécurité standard de N8N
USER node

EXPOSE 5003

# Lancement conjoint de FastAPI et N8N
CMD ["/bin/bash", "./start.sh"]
