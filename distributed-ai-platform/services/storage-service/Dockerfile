FROM python:3.11-slim-bookworm

# Install libhdfs native library dependencies
RUN apt-get update && apt-get install -y \
    openjdk-17-jdk-headless \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV HADOOP_HOME=/opt/hadoop
ENV PATH=$PATH:$HADOOP_HOME/bin

WORKDIR /app

# Using pip to install the package dependencies from pyproject.toml
COPY services/storage-service/pyproject.toml .
RUN pip install .

# Copy shared libraries first (layer caching)
COPY shared/ /app/shared/

# Copy service code
COPY services/storage-service/ /app/

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
