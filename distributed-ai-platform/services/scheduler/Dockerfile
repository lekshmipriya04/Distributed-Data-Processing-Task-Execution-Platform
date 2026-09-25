FROM python:3.11-slim
WORKDIR /app
COPY services/scheduler/pyproject.toml .
RUN pip install .
COPY shared/ /app/shared/
COPY services/scheduler/ /app/
EXPOSE 8010
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "2"]
