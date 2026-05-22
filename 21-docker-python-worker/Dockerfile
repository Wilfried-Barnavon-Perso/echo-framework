FROM python:3.14-slim

LABEL maintainer="Wilfried BARNAVON"
LABEL version="1.0"
LABEL description="ECHO Python Worker (Data Science & Logic)"

WORKDIR /app

# Installation des dépendances système nécessaires à la compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Installation des packages Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie de l'application
COPY worker_api.py /app/app.py

ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "/app/app.py"]
