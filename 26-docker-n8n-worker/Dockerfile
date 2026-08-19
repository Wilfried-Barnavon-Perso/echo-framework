# ==============================================================================
# STAGE 1 : L'extraction (Le vol de N8N pré-compilé)
# ==============================================================================
# L'image officielle est distroless (pas de shell, pas de pip, pas d'apk),
# on ne peut donc pas y installer FastAPI. Mais on peut lui voler N8N !
FROM n8nio/n8n:latest AS n8n_official

# ==============================================================================
# STAGE 2 : Notre environnement de travail Alpine (musl compatible)
# ==============================================================================
# IMPORTANT : On utilise Node 24 (même version que l'image officielle N8N)
# pour garantir la compatibilité ABI avec les binaires C++ natifs (isolated-vm, sqlite3).
FROM node:24-alpine

# On copie N8N bit pour bit de l'image officielle vers notre Alpine
COPY --from=n8n_official /usr/local/lib/node_modules/n8n /usr/local/lib/node_modules/n8n
# On recrée le symlink manuellement (si on le copie, Docker copie le fichier cible et casse les imports relatifs de Node.js)
RUN mkdir -p /usr/local/bin && ln -s /usr/local/lib/node_modules/n8n/bin/n8n /usr/local/bin/n8n

# Installation de Python 3, de pip et bash
RUN apk add --no-cache python3 py3-pip bash libstdc++

# Installation de FastAPI (sans casser le système)
RUN python3 -m pip install --no-cache-dir --break-system-packages fastapi uvicorn pydantic httpx bcrypt

# Préparation de l'environnement de travail
WORKDIR /app
COPY n8n_api.py .
COPY n8n_architecture.md .
COPY start.sh .
RUN chmod +x start.sh

# Préparation du répertoire de données N8N avec les bons droits
RUN mkdir -p /home/node/.n8n && chown -R node:node /home/node/.n8n

# Sécurité : exécution en tant que node (pas root)
USER node

EXPOSE 5003

# Lancement de FastAPI et N8N
CMD ["/bin/bash", "./start.sh"]

