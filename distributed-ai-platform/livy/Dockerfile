FROM eclipse-temurin:11-jre-focal

ENV LIVY_VERSION=0.8.0-incubating
ENV LIVY_SCALA_VERSION=2.12
ENV SPARK_VERSION=3.3.0
ENV LIVY_HOME=/opt/livy
ENV SPARK_HOME=/opt/spark
ENV PYSPARK_PYTHON=python3

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget procps python3 unzip && \
    rm -rf /var/lib/apt/lists/*

# ── Download Spark ──────────────────────────────────────────────────────────
RUN wget -q "https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3.tgz" \
        -O /tmp/spark.tgz && \
    tar -xzf /tmp/spark.tgz -C /opt && \
    mv /opt/spark-${SPARK_VERSION}-bin-hadoop3 ${SPARK_HOME} && \
    rm /tmp/spark.tgz

# ── Download Apache Livy ────────────────────────────────────────────────────
RUN wget -q "https://archive.apache.org/dist/incubator/livy/${LIVY_VERSION}/apache-livy-${LIVY_VERSION}_${LIVY_SCALA_VERSION}-bin.zip" \
        -O /tmp/livy.zip && \
    unzip -q /tmp/livy.zip -d /opt && \
    mv /opt/apache-livy-${LIVY_VERSION}_${LIVY_SCALA_VERSION}-bin ${LIVY_HOME} && \
    rm /tmp/livy.zip

RUN mkdir -p ${LIVY_HOME}/logs ${LIVY_HOME}/work-dir

ENV PATH="${LIVY_HOME}/bin:${SPARK_HOME}/bin:${PATH}"

EXPOSE 8998

# Write livy.conf from env vars at runtime, then start server in foreground
CMD ["/bin/bash", "-c", "\
    mkdir -p ${LIVY_HOME}/conf && \
    echo \"livy.spark.master = ${LIVY_SPARK_MASTER:-spark://spark-master:7077}\" > ${LIVY_HOME}/conf/livy.conf && \
    echo \"livy.spark.deployMode = ${LIVY_SPARK_DEPLOY_MODE:-client}\"              >> ${LIVY_HOME}/conf/livy.conf && \
    echo \"livy.server.port = 8998\"                                                 >> ${LIVY_HOME}/conf/livy.conf && \
    echo \"livy.repl.enable-hive-context = false\"                                   >> ${LIVY_HOME}/conf/livy.conf && \
    echo \"livy.file.local-dir-whitelist = /opt/spark/jobs\"                         >> ${LIVY_HOME}/conf/livy.conf && \
    exec ${LIVY_HOME}/bin/livy-server"]
