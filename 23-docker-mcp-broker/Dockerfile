FROM python:3.14-slim

# Eviter l'écriture de bytecode et forcer le flush stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installation des dépendances systèmes nécessaires
RUN apt-get update && apt-get install -y \
    build-essential \
    libxml2-dev \
    libxslt-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source
COPY core /app/core
COPY modules /app/modules
COPY server.py /app/server.py

# Exposition du port FastMCP
EXPOSE 8000

# Démarrage du serveur Uvicorn (FastMCP)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
