# ==============================================================================
# STAGE 1 : Récupération du gestionnaire de paquets statique (apk.static)
# ==============================================================================
FROM alpine:latest AS apk-builder
RUN apk add --no-cache wget tar
# Téléchargement dynamique de l'exécutable statique apk-tools
RUN ARCH=$(uname -m) && \
    wget -qO- "http://dl-cdn.alpinelinux.org/alpine/latest-stable/main/${ARCH}/" | \
    grep -o 'href="apk-tools-static-[^"]*\.apk"' | head -1 | cut -d'"' -f2 | \
    xargs -I {} wget -q "http://dl-cdn.alpinelinux.org/alpine/latest-stable/main/${ARCH}/{}" && \
    tar -xzf apk-tools-static-*.apk

# ==============================================================================
# STAGE 2 : Restauration d'Alpine et installation de Python sur N8N officiel
# ==============================================================================
FROM n8nio/n8n:latest

# Élévation temporaire pour modifier le système distroless
USER root

# 1. Injection du binaire statique depuis le builder
COPY --from=apk-builder /sbin/apk.static /sbin/apk.static

# 2. Restauration native du gestionnaire de paquets apk
RUN /sbin/apk.static -X http://dl-cdn.alpinelinux.org/alpine/latest-stable/main -U --allow-untrusted --initdb add apk-tools && \
    rm -f /sbin/apk.static

# 3. L'image n'est plus distroless : installation de Python et FastAPI
RUN apk add --no-cache python3 py3-pip bash && \
    python3 -m pip install --no-cache-dir --break-system-packages fastapi uvicorn pydantic httpx bcrypt

# 4. Préparation de l'environnement API ECHO
WORKDIR /app
COPY n8n_api.py .
COPY n8n_architecture.md .
COPY start.sh .

RUN chmod +x start.sh && chown -R node:node /app

# 5. Retour au contexte de sécurité standard
USER node

EXPOSE 5003

# Lancement conjoint de FastAPI et N8N
CMD ["/bin/bash", "./start.sh"]
