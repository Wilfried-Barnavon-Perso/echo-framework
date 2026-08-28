# ECHO Auth Server Dockerfile
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Paris

WORKDIR /app

# Installation des dépendances de sécurité et web
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    pyotp \
    passlib[argon2] \
    qrcode[pil] \
    jinja2 \
    python-multipart \
    user-agents \
    orjson

# Création du point de montage pour la base de données
RUN mkdir -p /data

# Exposition du port
EXPOSE 8000

# Commande de lancement
CMD ["uvicorn", "auth_server:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/app/logging.json"]
