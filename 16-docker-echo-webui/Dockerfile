# syntax=docker/dockerfile:1
# Initialize device type args
# use build args in the docker build command with --build-arg="BUILDARG=true"
ARG USE_CUDA=false
ARG USE_OLLAMA=false
ARG USE_SLIM=false
ARG USE_PERMISSION_HARDENING=false
ARG USE_EMBEDDING_MODEL=""
ARG USE_RERANKING_MODEL=""
ARG USE_AUXILIARY_EMBEDDING_MODEL=""

# Tiktoken encoding name; models to use can be found at https://huggingface.co/models?library=tiktoken
ARG USE_TIKTOKEN_ENCODING_NAME="cl100k_base"

ARG BUILD_HASH=dev-build
ARG BUILD_CHANNEL=unknown
# Override at your own risk - non-root configurations are untested
ARG UID=0
ARG GID=0

######## WebUI frontend ########
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build
ARG BUILD_HASH
ARG BUILD_CHANNEL
ARG USE_SLIM
ARG UID
ARG GID

# Set Node.js options (heap limit Allocation failed - JavaScript heap out of memory)
ENV NODE_OPTIONS="--max-old-space-size=6144"

WORKDIR /app

# to store git revision in build
RUN apk add --no-cache git

COPY package.json package-lock.json ./
# the frontend build never uses onnxruntime-node's CUDA binaries
RUN ONNXRUNTIME_NODE_INSTALL_CUDA=skip npm ci --force

COPY . .
ENV APP_BUILD_HASH=${BUILD_HASH}
ENV APP_BUILD_CHANNEL=${BUILD_CHANNEL}
RUN npm run build && \
    if [ "$USE_SLIM" = "true" ]; then find build -type f -name '*.map' -delete; fi

# Prepare backend ownership before the final copy so static assets occupy one layer.
# Group 0 write access lets arbitrary OpenShift UIDs update these assets at startup.
RUN chown -R $UID:$GID /app/backend && \
    chgrp -R 0 /app/backend/open_webui/static && \
    chmod -R g=u /app/backend/open_webui/static

######## WebUI backend ########
FROM python:3.12-slim-trixie AS base

# Use args
ARG USE_CUDA
ARG USE_OLLAMA
ARG USE_SLIM
ARG USE_PERMISSION_HARDENING
ARG USE_EMBEDDING_MODEL
ARG USE_RERANKING_MODEL
ARG USE_AUXILIARY_EMBEDDING_MODEL
ARG UID
ARG GID

# Python settings
ENV PYTHONUNBUFFERED=1

## Basis ##
ENV ENV=prod \
    PORT=8080 \
    # pass build args to the build
    USE_OLLAMA_DOCKER=${USE_OLLAMA} \
    USE_CUDA_DOCKER=${USE_CUDA} \
    USE_SLIM_DOCKER=${USE_SLIM} \
    USE_EMBEDDING_MODEL_DOCKER=${USE_EMBEDDING_MODEL} \
    USE_RERANKING_MODEL_DOCKER=${USE_RERANKING_MODEL} \
    USE_AUXILIARY_EMBEDDING_MODEL_DOCKER=${USE_AUXILIARY_EMBEDDING_MODEL} \
    VECTOR_DB="qdrant" \
    ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS="false"

## Basis URL Config ##
ENV OLLAMA_BASE_URL="/ollama" \
    OPENAI_API_BASE_URL=""

## API Key and Security Config ##
ENV OPENAI_API_KEY="" \
    WEBUI_SECRET_KEY="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false

#### Other models #########################################################
## whisper TTS model settings ##
ENV WHISPER_MODEL="base" \
    WHISPER_MODEL_DIR="/app/backend/data/cache/whisper/models"

## RAG Embedding model settings ##
ENV RAG_EMBEDDING_MODEL="$USE_EMBEDDING_MODEL_DOCKER" \
    RAG_RERANKING_MODEL="$USE_RERANKING_MODEL_DOCKER" \
    AUXILIARY_EMBEDDING_MODEL="$USE_AUXILIARY_EMBEDDING_MODEL_DOCKER" \
    SENTENCE_TRANSFORMERS_HOME="/app/backend/data/cache/embedding/models"

## Tiktoken model settings ##
ENV TIKTOKEN_ENCODING_NAME="cl100k_base" \
    TIKTOKEN_CACHE_DIR="/app/backend/data/cache/tiktoken"

## Hugging Face download cache ##
ENV HF_HOME="/app/backend/data/cache/embedding/models"

## Torch Extensions ##
# ENV TORCH_EXTENSIONS_DIR="/.cache/torch_extensions"

#### Other models ##########################################################

WORKDIR /app/backend

ENV HOME=/root
# Create user and group if not root
RUN if [ $UID -ne 0 ]; then \
    if [ $GID -ne 0 ]; then \
    addgroup --gid $GID app; \
    fi; \
    adduser --uid $UID --gid $GID --home $HOME --disabled-password --no-create-home app; \
    fi

RUN mkdir -p $HOME/.cache/chroma
RUN echo -n 00000000-0000-0000-0000-000000000000 > $HOME/.cache/chroma/telemetry_user_id

# Make sure the user has access to the app and root directory
RUN chown -R $UID:$GID /app $HOME

# Slim cannot bundle a local model server or GPU runtime.
RUN if [ "$USE_SLIM" = "true" ] && { [ "$USE_CUDA" = "true" ] || [ "$USE_OLLAMA" = "true" ]; }; then \
    echo "USE_SLIM cannot be combined with USE_CUDA or USE_OLLAMA" >&2; exit 1; fi

# Keep the slim runtime free of local document/audio processing tools.
# Git-based tool requirements require the standard image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl jq ca-certificates git ffmpeg pandoc \
    && rm -rf /var/lib/apt/lists/*

# install python dependencies
COPY --chown=$UID:$GID ./backend/requirements*.txt ./

# Set UV_LINK_MODE to copy to prevent 0-byte file corruption in QEMU arm64 cross-builds
ENV UV_LINK_MODE=copy

RUN --mount=from=ghcr.io/astral-sh/uv:0.12.10,source=/uv,target=/bin/uv \
    set -e; \
    uv pip install --system -r requirements.txt --no-cache-dir; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    mkdir -p /app/backend/data; chown -R $UID:$GID /app/backend/data/; \
    if [ -d /app/backend/data/cache ]; then chmod -R a+rX /app/backend/data/cache; fi; \
    rm -rf /var/lib/apt/lists/*;

# copy embedding weight from build
# RUN mkdir -p /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2
# COPY --from=build /app/onnx /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx

# copy built frontend files
COPY --chown=$UID:$GID --from=build /app/build /app/build
COPY --chown=$UID:$GID --from=build /app/CHANGELOG.md /app/CHANGELOG.md
COPY --chown=$UID:$GID --from=build /app/package.json /app/package.json

# copy backend files with the ownership and static permissions prepared above
COPY --from=build /app/backend .

EXPOSE 8080

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-8080}/health | jq -ne 'input.status == true' || exit 1

# Minimal, atomic permission hardening for OpenShift (arbitrary UID):
# - Group 0 owns /app and /root
# - Directories are group-writable and have SGID so new files inherit GID 0
RUN if [ "$USE_PERMISSION_HARDENING" = "true" ]; then \
    set -eux; \
    chgrp -R 0 /app /root || true; \
    chmod -R g+rwX /app /root || true; \
    find /app -type d -exec chmod g+s {} + || true; \
    find /root -type d -exec chmod g+s {} + || true; \
    fi

USER $UID:$GID

ARG BUILD_HASH
ENV WEBUI_BUILD_HASH=${BUILD_HASH}
ENV DOCKER=true

CMD [ "bash", "start.sh"]
