FROM python:3.11-slim
WORKDIR /app
COPY services/resource-manager/pyproject.toml .
RUN pip install .
COPY shared/ /app/shared/
COPY services/resource-manager/ /app/
EXPOSE 8009
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8009", "--workers", "2"]
