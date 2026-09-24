# syntax=docker/dockerfile:1
# ECHO PYTHON BASE IMAGE
FROM python:3.14-slim

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO Shared Python Base Image"

# Dépendances systèmes communes (Compilation C, Réseau, Médias, Compression)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    ffmpeg \
    wget \
    curl \
    xz-utils \
    libxml2-dev \
    libxslt-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python communes (socle API + utilitaires transverses)
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_BREAK_SYSTEM_PACKAGES=1 CMAKE_BUILD_PARALLEL_LEVEL=2 pip install \
    fastapi \
    uvicorn \
    httpx \
    orjson \
    pybase64 \
    mgzip \
    pydantic \
    python-multipart
