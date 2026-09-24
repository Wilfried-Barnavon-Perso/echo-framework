# syntax=docker/dockerfile:1
# ECHO Auth Server Dockerfile
FROM echo-python-base:latest

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Paris

WORKDIR /app

# Installation des dépendances de sécurité et web
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
    pyotp \
    passlib[argon2] \
    qrcode[pil] \
    jinja2 \
    user-agents

# Création du point de montage pour la base de données
RUN mkdir -p /data

# Exposition du port
EXPOSE 8000

# Commande de lancement
CMD ["uvicorn", "auth_server:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/app/logging.json"]
