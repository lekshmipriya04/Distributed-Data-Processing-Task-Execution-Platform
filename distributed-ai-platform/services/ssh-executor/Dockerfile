FROM python:3.11-slim
WORKDIR /app
COPY shared/ /app/shared/
COPY services/ssh-executor/pyproject.toml .
RUN pip install .
COPY services/ssh-executor/ /app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "main:create_app", "--host", "0.0.0.0", "--port", "8011", "--factory"]
