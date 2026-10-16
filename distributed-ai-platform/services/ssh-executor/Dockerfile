FROM python:3.11-slim
WORKDIR /app
# Training is pure Python on the master (no Spark/Ray), so no JVM is needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
	procps \
	&& rm -rf /var/lib/apt/lists/*
COPY shared/ /app/shared/
COPY services/ssh-executor/pyproject.toml .
RUN pip install --timeout 300 --retries 10 .
COPY services/ssh-executor/ /app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1"]
