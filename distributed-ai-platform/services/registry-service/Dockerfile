FROM python:3.11-slim

WORKDIR /app

COPY services/registry-service/pyproject.toml .
RUN pip install .

COPY shared/ /app/shared/
COPY services/registry-service/ /app/

EXPOSE 8005

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005", "--workers", "2"]
