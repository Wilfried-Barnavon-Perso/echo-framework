FROM python:3.14-slim

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO Custom Embedding Worker for SigLIP-2 (CPU Optimized)"

WORKDIR /app

# Installation des dépendances système minimales
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# PyTorch CPU-only (zéro dépendance CUDA — économise ~400 Mo)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Autres dépendances Python
COPY requirements.txt .
RUN CMAKE_BUILD_PARALLEL_LEVEL=$(nproc) pip install --no-cache-dir -r requirements.txt

# Copie de l'application
COPY app.py .

# Configuration de l'environnement
ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1
# Désactiver le téléchargement automatique de télémétrie si applicable
ENV DO_NOT_TRACK=1

EXPOSE 7997

# Lancement de l'API avec Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7997", "--workers", "1"]
