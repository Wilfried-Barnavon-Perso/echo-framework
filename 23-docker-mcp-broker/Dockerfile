# syntax=docker/dockerfile:1
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
FROM echo-python-base:latest

# Eviter l'écriture de bytecode et forcer le flush stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installation de NodeJS 22.x LTS (le reste est dans la base partagée) et Git pour les paquets Python
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# Copie du code source
COPY core /app/core
COPY modules /app/modules
COPY server.py /app/server.py

LABEL maintainer="Wilfried BARNAVON"
LABEL version="6.18.6.1"
LABEL description="ECHO MCP Broker (Search, Corporate, Academic & Providers)"

# Exposition du port FastMCP
EXPOSE 8000

# Démarrage du serveur Uvicorn (FastMCP)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/app/logging.json"]
