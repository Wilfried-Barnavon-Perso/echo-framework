# ECHO TTS WORKER (Kokoro ONNX CPU)
FROM python:3.12-slim

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO TTS Worker (Kokoro ONNX CPU)"

# Installation de espeak-ng requis pour le phonemizer de Kokoro et wget
RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng \
    wget \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installation optimisée pour CPU
RUN PIP_BREAK_SYSTEM_PACKAGES=1 CMAKE_BUILD_PARALLEL_LEVEL=$(nproc --ignore=1) pip install --no-cache-dir \
    kokoro-onnx \
    soundfile \
    fastapi \
    uvicorn \
    pydantic \
    langid \
    pydub \
    pyspellchecker

# Téléchargement du modèle v1.0 et du profil vocal bin
RUN wget -q https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx && \
    wget -q https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

COPY tts_api.py /app/

EXPOSE 7996

# Entrypoint : Lancement de l'API avec Uvicorn
CMD ["uvicorn", "tts_api:app", "--host", "0.0.0.0", "--port", "7996", "--log-config", "/app/logging.json"]
