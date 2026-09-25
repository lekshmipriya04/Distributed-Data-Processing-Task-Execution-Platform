#!/usr/bin/env bash
set -e
KAFKA_CONTAINER=kafka

topics=("inference_requests:3" "predictions:3" "pipeline_events:3" "model_lifecycle:1" "resource_requests:3" "resource_approvals:3" "worker_events:3" "job_events:3")

for entry in "${topics[@]}"; do
  IFS=':' read -r topic partitions <<< "$entry"
  docker exec $KAFKA_CONTAINER kafka-topics.sh \
    --bootstrap-server localhost:9092 \
    --create --if-not-exists \
    --topic "$topic" \
    --partitions "$partitions" \
    --replication-factor 1
  echo "Created topic: $topic"
done
