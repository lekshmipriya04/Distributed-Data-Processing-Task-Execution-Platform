FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install .

COPY . /app/

ENV CONTROL_PLANE_URL=http://api-gateway:8000
ENV WORKER_REGISTRY_URL=http://worker-registry:8008
ENV RESOURCE_MANAGER_URL=http://resource-manager:8009
ENV WORKER_NAME=worker-01
ENV MAX_CPU=4
ENV MAX_MEMORY_GB=8.0
ENV ALLOW_GPU=false
ENV REQUIRE_APPROVAL=true

CMD ["python", "main.py"]
