# ssh-executor (the master). Build context is distributed-ai-platform/.
#
# Spark + Ray are optional and live only here, never on worker nodes:
#   docker compose build --build-arg WITH_SPARK_RAY=true ssh-executor
# or `make enable-spark-ray`. Adds Java 17, PySpark and Ray (roughly 1 GB).
FROM python:3.11-slim-bookworm
ARG WITH_SPARK_RAY=false
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends procps \
	&& if [ "$WITH_SPARK_RAY" = "true" ]; then \
		apt-get install -y --no-install-recommends openjdk-17-jre-headless; \
	fi \
	&& rm -rf /var/lib/apt/lists/*
COPY shared/ /app/shared/
COPY services/ssh-executor/pyproject.toml .
RUN pip install --no-cache-dir --timeout 300 --retries 10 . \
	&& if [ "$WITH_SPARK_RAY" = "true" ]; then \
		pip install --no-cache-dir --timeout 300 --retries 10 ".[spark_ray]"; \
	fi
COPY services/ssh-executor/ /app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1"]
