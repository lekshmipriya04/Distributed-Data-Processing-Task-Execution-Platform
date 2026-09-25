FROM python:3.11-slim

WORKDIR /app

COPY services/preprocessing-service/pyproject.toml .
RUN pip install .

COPY shared/ /app/shared/
COPY services/preprocessing-service/ /app/

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
