# syntax=docker/dockerfile:1
# ECHO BROWSER WORKER
FROM echo-python-base:latest

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO Browser Worker (Playwright)"

WORKDIR /app

# Installation de Playwright et Chromium dans la couche Docker
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_BREAK_SYSTEM_PACKAGES=1 CMAKE_BUILD_PARALLEL_LEVEL=2 pip install \
    playwright \
    html2text \
    Pillow \
    && playwright install --with-deps chromium

EXPOSE 5002

# Entrypoint : Lancement de l'API FastAPI
CMD ["uvicorn", "browser_api:app", "--host", "0.0.0.0", "--port", "5002", "--log-config", "/app/logging.json"]
