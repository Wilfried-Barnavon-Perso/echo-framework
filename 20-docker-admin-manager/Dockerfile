FROM python:3.14-slim

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO Admin Manager (System & Orchestration)"

WORKDIR /app

# Installation des dépendances système nécessaires
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Installation des packages Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie de l'application
COPY server.py /app/server.py

ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1

EXPOSE 3001

CMD ["python", "/app/server.py"]
