# syntax=docker/dockerfile:1
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
FROM python:3.14

WORKDIR /app

# L'image de base comprend déjà les build-essentials (gcc, python3-dev, etc.)

# Installation des packages système et Node.js (22.x LTS - patch sécurité CVE-2026-111497 Brotli)
RUN apt-get update && apt-get install -y curl bubblewrap openssh-client sshpass jq less nano iputils-ping dnsutils && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g ws express axios cheerio js-yaml lodash papaparse mathjs zod dotenv moment && \
    rm -rf /var/lib/apt/lists/*

# Configurer le NODE_PATH global
ENV NODE_PATH=/usr/lib/node_modules

# Installation des packages Python
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    CMAKE_BUILD_PARALLEL_LEVEL=2 pip install -r requirements.txt

# Copie de l'application
COPY worker_api.py /app/app.py

ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1

LABEL maintainer="Wilfried BARNAVON"
LABEL version="6.18.6.1"
LABEL description="ECHO Python Worker (Data Science & Logic)"

EXPOSE 5000

CMD ["python", "/app/app.py"]
