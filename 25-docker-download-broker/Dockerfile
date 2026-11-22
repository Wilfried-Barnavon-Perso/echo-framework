# syntax=docker/dockerfile:1
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
FROM echo-python-base:latest

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

LABEL maintainer="Wilfried BARNAVON"
LABEL version="6.18.6.1"
LABEL description="ECHO Download Broker (Secure Streaming & Ingestion)"

CMD ["python", "-u", "/app/download_broker.py"]
