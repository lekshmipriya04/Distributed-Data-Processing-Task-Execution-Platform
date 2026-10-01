# Build context is the repo root (see docker-compose.yml: context: .), so all
# COPY paths below are relative to the repo root.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies. Kept in sync with worker/pyproject.toml.
RUN pip install --no-cache-dir \
    "httpx==0.27.0" \
    "websockets==12.0" \
    "psutil==5.9.8" \
    "structlog==24.2.0" \
    "pydantic==2.8.2" \
    "pydantic-settings==2.3.4" \
    "pyjwt==2.8.0"

# The worker imports `from shared.common...`, so the shared package must be on
# PYTHONPATH alongside the worker code.
COPY shared /app/shared
COPY worker /app/worker

ENV PYTHONPATH=/app
ENV CONTROL_PLANE_URL=http://api-gateway:8000
ENV WORKER_REGISTRY_URL=http://worker-registry:8008
ENV RESOURCE_MANAGER_URL=http://resource-manager:8009
ENV WORKER_NAME=worker-01
ENV MAX_CPU=4
ENV MAX_MEMORY_GB=8.0
ENV ALLOW_GPU=false
ENV REQUIRE_APPROVAL=true

WORKDIR /app/worker
CMD ["python", "main.py"]
