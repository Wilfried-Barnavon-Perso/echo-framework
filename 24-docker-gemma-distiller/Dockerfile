# ==============================================================================
# ECHO GEMMA DISTILLER — Dockerfile
# VERSION : 1.0
# ==============================================================================
# Image runtime. Le modèle GGUF est auto-provisionné au premier démarrage
# par l'entrypoint (aria2c 16 connexions || wget -c).
# Stocké sur le volume hôte /opt/ECHO/models/ (persistant, hors image).
#
# STRATÉGIE GPU :
#   - CPU par défaut (python:3.14-slim)
#   - Pour CUDA : passer CMAKE_ARGS="-DGGML_CUDA=on" au build
#   - Alternative : Dockerfile.gpu séparé pour les déploiements WSL2
# ==============================================================================

FROM python:3.14-slim

# Dépendances système : build C++ (llama-cpp-python) + téléchargement modèle
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ cmake wget ca-certificates aria2 && \
    rm -rf /var/lib/apt/lists/*

# Dépendances Python
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Nettoyage des outils de build, préservation runtime OpenMP (libgomp = parallélisme CPU)
RUN apt-get purge -y gcc g++ cmake && \
    apt-get autoremove -y && \
    apt-get update && apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Code applicatif + entrypoint
COPY app.py /app/app.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
WORKDIR /app

EXPOSE 7998

ENTRYPOINT ["/app/entrypoint.sh"]
