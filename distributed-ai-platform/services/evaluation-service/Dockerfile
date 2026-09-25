FROM python:3.11-slim

WORKDIR /app

COPY services/evaluation-service/pyproject.toml .
RUN pip install .

COPY shared/ /app/shared/
COPY services/evaluation-service/ /app/

EXPOSE 8004

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004", "--workers", "2"]
