#!/usr/bin/env bash
set -e
NAMENODE=namenode

echo "Waiting for NameNode..."
until docker exec $NAMENODE hdfs dfsadmin -report > /dev/null 2>&1; do
  sleep 5
done

echo "Creating HDFS directory structure..."
docker exec $NAMENODE hdfs dfs -mkdir -p /platform/raw
docker exec $NAMENODE hdfs dfs -mkdir -p /platform/processed
docker exec $NAMENODE hdfs dfs -mkdir -p /platform/models
docker exec $NAMENODE hdfs dfs -mkdir -p /platform/logs
docker exec $NAMENODE hdfs dfs -chmod -R 777 /platform
echo "HDFS directories created."
