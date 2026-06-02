# ECHO STT WORKER (Faster-Whisper CPU)
FROM python:3.14-slim

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO STT Worker (Faster-Whisper CPU)"

# Installation des dépendances systèmes minimales (ffmpeg requis pour l'audio)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installation optimisée pour CPU
RUN PIP_BREAK_SYSTEM_PACKAGES=1 CMAKE_BUILD_PARALLEL_LEVEL=$(nproc) pip install --no-cache-dir \
    faster-whisper \
    fastapi \
    uvicorn \
    python-multipart

COPY stt_api.py /app/

EXPOSE 7995

# Entrypoint : Lancement de l'API avec Uvicorn
CMD ["uvicorn", "stt_api:app", "--host", "0.0.0.0", "--port", "7995"]
