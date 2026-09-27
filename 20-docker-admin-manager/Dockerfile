# syntax=docker/dockerfile:1
FROM echo-python-base:latest

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO Admin Manager (System & Orchestration)"

WORKDIR /app

# Installation des dépendances système (rclone uniquement, les autres sont dans la base)
RUN apt-get update && apt-get install -y --no-install-recommends \
    rclone \
    && rm -rf /var/lib/apt/lists/*

# Installation des packages Python
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    CMAKE_BUILD_PARALLEL_LEVEL=2 pip install -r requirements.txt

# Copie de l'application
COPY server.py /app/server.py

ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1

EXPOSE 3001

CMD ["python", "/app/server.py"]
