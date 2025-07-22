#!/bin/bash

# Elasticsearch URL (no auth)
# Elasticsearch URL (no auth) - built from env vars or defaults
ES_SERVICE_NAME="${ES_SERVICE_NAME:-elasticsearch-external}"
ES_PORT="${ES_PORT:-9200}"
ES_URL="http://${ES_SERVICE_NAME}:${ES_PORT}"

function cluster_health() {
  echo "=== Elasticsearch Cluster Health ==="
  curl -s "$ES_URL/_cluster/health?pretty"
}

function nodes_info() {
  echo "=== Elasticsearch Nodes Info ==="
  curl -s "$ES_URL/_nodes?pretty"
}

function indices_stats() {
  echo "=== Elasticsearch Indices Stats ==="
  curl -s "$ES_URL/_stats?pretty"
}

function indices_list() {
  echo "=== Elasticsearch Indices List ==="
  curl -s "$ES_URL/_cat/indices?v"
}

function cluster_settings() {
  echo "=== Elasticsearch Cluster Settings ==="
  curl -s "$ES_URL/_cluster/settings?pretty"
}

function pending_tasks() {
  echo "=== Elasticsearch Pending Tasks ==="
  curl -s "$ES_URL/_cluster/pending_tasks?pretty"
}

function shard_allocation() {
  echo "=== Elasticsearch Shard Allocation ==="
  curl -s "$ES_URL/_cat/shards?v"
}

function tasks_list() {
  echo "=== Elasticsearch Tasks List ==="
  curl -s "$ES_URL/_tasks?pretty"
}

function usage() {
  echo "Usage: $0 <command>"
  echo "Commands:"
  echo "  health        - Cluster health"
  echo "  nodes         - Nodes info"
  echo "  stats         - Indices stats"
  echo "  indices       - List indices"
  echo "  settings      - Cluster settings"
  echo "  pending       - Pending cluster tasks"
  echo "  shards        - Shard allocation"
  echo "  tasks         - Running tasks"
  echo "  all           - Run all commands (default)"
  exit 1
}

COMMAND="${1:-all}"

case "$COMMAND" in
  health) cluster_health ;;
  nodes) nodes_info ;;
  stats) indices_stats ;;
  indices) indices_list ;;
  settings) cluster_settings ;;
  pending) pending_tasks ;;
  shards) shard_allocation ;;
  tasks) tasks_list ;;
  all)
    cluster_health
    echo
    nodes_info
    echo
    indices_stats
    echo
    indices_list
    echo
    cluster_settings
    echo
    pending_tasks
    echo
    shard_allocation
    echo
    tasks_list
    ;;
  *)
    usage
    ;;
esac
