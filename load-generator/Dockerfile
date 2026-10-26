FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/hrushikeshkuwlekar/lightweight-monitoring-stack"
LABEL org.opencontainers.image.description="Async load generator for the lightweight monitoring stack"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py engine.py scenarios.py ./
COPY static/ ./static/

RUN adduser --disabled-password --gecos "" --uid 10001 appuser
USER 10001

EXPOSE 8089

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8089"]
