# syntax=docker/dockerfile:1
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ECHO BROWSER WORKER
FROM echo-python-base:latest

WORKDIR /app

# Installation de Playwright et Chromium dans la couche Docker
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_BREAK_SYSTEM_PACKAGES=1 CMAKE_BUILD_PARALLEL_LEVEL=2 pip install \
    playwright \
    html2text \
    Pillow \
    && playwright install --with-deps chromium

LABEL maintainer="Wilfried BARNAVON"
LABEL version="6.18.6.1"
LABEL description="ECHO Browser Worker (Playwright)"

EXPOSE 5002

# Entrypoint : Lancement de l'API FastAPI
CMD ["uvicorn", "browser_api:app", "--host", "0.0.0.0", "--port", "5002", "--log-config", "/app/logging.json"]
