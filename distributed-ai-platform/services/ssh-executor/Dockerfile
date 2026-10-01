FROM python:3.11-slim
WORKDIR /app
COPY shared/ /app/shared/
COPY services/ssh-executor/pyproject.toml .
RUN pip install --timeout 300 --retries 10 .
COPY services/ssh-executor/ /app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1"]
