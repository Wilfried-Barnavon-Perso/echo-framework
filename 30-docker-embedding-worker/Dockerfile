FROM python:3.11-slim

LABEL maintainer="Wilfried BARNAVON"
LABEL version="2.0"
LABEL description="ECHO Custom Embedding Worker for Harrier-OSS GGUF (CPU Optimized)"

WORKDIR /app

# Installation des dépendances système minimales (inclus outils de compilation pour llama.cpp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Autres dépendances Python
COPY requirements.txt .
RUN CMAKE_BUILD_PARALLEL_LEVEL=$(nproc --ignore=1) pip install --no-cache-dir -r requirements.txt

# Copie de l'application
COPY app.py .

# Configuration de l'environnement
ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1
# Désactiver le téléchargement automatique de télémétrie si applicable
ENV DO_NOT_TRACK=1

EXPOSE 7997

# Lancement de l'API avec Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7997", "--workers", "1", "--log-config", "/app/logging.json"]
