# syntax=docker/dockerfile:1
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
FROM echo-python-base:latest

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    CMAKE_BUILD_PARALLEL_LEVEL=2 pip install -r requirements.txt

COPY core /app/core
COPY services /app/services
COPY routes /app/routes
COPY templates /app/templates
COPY server.py /app/server.py

ENV TZ=Europe/Paris
ENV PYTHONUNBUFFERED=1

LABEL maintainer="Wilfried BARNAVON"
LABEL version="6.18.6.1"
LABEL description="ECHO User Manager (Self-Service Profile, Sessions & Data Export)"

EXPOSE 3002

CMD ["python", "/app/server.py"]
