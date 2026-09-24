# syntax=docker/dockerfile:1
# ECHO STT WORKER (Faster-Whisper CPU)
FROM echo-python-base:latest

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO STT Worker (Faster-Whisper CPU)"

WORKDIR /app

# Installation optimisée pour CPU
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_BREAK_SYSTEM_PACKAGES=1 CMAKE_BUILD_PARALLEL_LEVEL=2 pip install \
    faster-whisper

COPY stt_api.py /app/

EXPOSE 7995

# Entrypoint : Lancement de l'API avec Uvicorn
CMD ["uvicorn", "stt_api:app", "--host", "0.0.0.0", "--port", "7995", "--log-config", "/app/logging.json"]
