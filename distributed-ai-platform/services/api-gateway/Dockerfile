FROM python:3.11-slim

WORKDIR /app

COPY services/api-gateway/pyproject.toml .
RUN pip install .

COPY shared/ /app/shared/
COPY services/api-gateway/ /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
