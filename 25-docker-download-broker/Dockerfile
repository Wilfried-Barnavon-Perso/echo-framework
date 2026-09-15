# syntax=docker/dockerfile:1
FROM python:3.14-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

CMD ["python", "-u", "/app/download_broker.py"]
