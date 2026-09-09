FROM python:3.14

LABEL maintainer="Wilfried BARNAVON"
LABEL version="2.0"
LABEL description="ECHO Python Worker (Data Science & Logic)"

WORKDIR /app

# L'image de base comprend déjà les build-essentials (gcc, python3-dev, etc.)

# Installation des packages Python
RUN apt-get update && apt-get install -y bubblewrap && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN CMAKE_BUILD_PARALLEL_LEVEL=$(nproc --ignore=1) pip install --no-cache-dir -r requirements.txt

# Copie de l'application
COPY worker_api.py /app/app.py

ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["python", "/app/app.py"]
