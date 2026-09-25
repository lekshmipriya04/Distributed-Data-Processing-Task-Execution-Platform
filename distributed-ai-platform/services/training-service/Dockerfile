FROM python:3.11-slim

WORKDIR /app

COPY services/training-service/pyproject.toml .
RUN pip install .

COPY shared/ /app/shared/
COPY services/training-service/ /app/

EXPOSE 8003

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "2"]
