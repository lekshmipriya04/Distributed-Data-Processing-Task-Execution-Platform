FROM python:3.11-slim
WORKDIR /app
COPY services/worker-registry/pyproject.toml .
RUN pip install .
COPY shared/ /app/shared/
COPY services/worker-registry/ /app/
EXPOSE 8008
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8008", "--workers", "2"]
