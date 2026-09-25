FROM python:3.11-slim

WORKDIR /app

# For pandas/mlflow dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY services/serving-service/pyproject.toml .
RUN pip install .

COPY shared/ /app/shared/
COPY services/serving-service/ /app/

EXPOSE 8006

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8006", "--workers", "4"]
