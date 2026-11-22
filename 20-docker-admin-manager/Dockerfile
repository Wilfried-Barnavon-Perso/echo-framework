# syntax=docker/dockerfile:1
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
FROM echo-python-base:latest

WORKDIR /app

# Installation des dépendances système (rclone uniquement, les autres sont dans la base)
RUN apt-get update && apt-get install -y --no-install-recommends \
    rclone \
    && rm -rf /var/lib/apt/lists/*

# Installation des packages Python
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    CMAKE_BUILD_PARALLEL_LEVEL=2 pip install -r requirements.txt

# Copie de l'application modulaire
COPY core /app/core
COPY services /app/services
COPY routes /app/routes
COPY templates /app/templates
COPY server.py /app/server.py

ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1

LABEL maintainer="Wilfried BARNAVON"
LABEL version="6.18.5.1"
LABEL description="ECHO Admin Manager (System & Orchestration)"

EXPOSE 3001

CMD ["python", "/app/server.py"]
