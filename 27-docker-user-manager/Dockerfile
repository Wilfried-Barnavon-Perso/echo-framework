# syntax=docker/dockerfile:1
FROM echo-python-base:latest

LABEL maintainer="Wilfried BARNAVON"
LABEL version="5.237.0"
LABEL description="ECHO User Manager (Self-Service Profile, Sessions & Data Export)"

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

EXPOSE 3002

CMD ["python", "/app/server.py"]
