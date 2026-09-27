# syntax=docker/dockerfile:1
FROM echo-python-base:latest

# Eviter l'écriture de bytecode et forcer le flush stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installation de NodeJS (le reste est dans la base partagée)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# Copie du code source
COPY core /app/core
COPY modules /app/modules
COPY server.py /app/server.py

# Exposition du port FastMCP
EXPOSE 8000

# Démarrage du serveur Uvicorn (FastMCP)
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/app/logging.json"]
