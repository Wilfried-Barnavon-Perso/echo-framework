# syntax=docker/dockerfile:1
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
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
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install --break-system-packages fastapi uvicorn pydantic httpx bcrypt

# Préparation de l'environnement de travail
WORKDIR /app
COPY n8n_api.py .
COPY n8n_architecture.md .
COPY start.sh .

RUN chmod +x start.sh && \
    mkdir -p /home/node/.n8n && \
    chown -R node:node /home/node/.n8n && \
    chown -R node:node /app

LABEL maintainer="Wilfried BARNAVON"
LABEL version="6.18.6.1"
LABEL description="ECHO n8n Worker (Workflow Automation & FastAPI Orchestration)"

# Sécurité : exécution sans privilèges root
USER node
EXPOSE 5003

# Lancement conjoint de FastAPI et N8N
CMD ["/bin/bash", "./start.sh"]
