# ==============================================================================
# ECHO ADMIN MANAGER - DOCKERFILE
# ==============================================================================
# Image optimisée avec dépendances pré-installées pour un démarrage instantané.
# ==============================================================================

FROM python:3.11-slim

# Définition du fuseau horaire
ENV TZ=Europe/Paris

# Installation des dépendances système (si nécessaire pour psutil/docker)
# build-essential peut être requis pour compiler certaines libs
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

# Installation des dépendances Python
# On regroupe tout ici pour profiter du cache Docker
RUN pip install --no-cache-dir flask flask-cors docker psutil paramiko APScheduler schedule httpx

# Copie du code source
WORKDIR /app
COPY server.py /app/server.py

# Point d'entrée
CMD ["python", "/app/server.py"]