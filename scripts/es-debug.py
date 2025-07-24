#!/usr/bin/env python3

import os
import sys
import argparse
import requests

ES_SERVICE_NAME = os.getenv("ES_SERVICE_NAME", "elasticsearch-external")
ES_PORT = os.getenv("ES_PORT", "9200")
ES_URL = f"http://{ES_SERVICE_NAME}:{ES_PORT}"

def cluster_health():
    print("=== Elasticsearch Cluster Health ===")
    r = requests.get(f"{ES_URL}/_cluster/health?pretty")
    print(r.text)

def nodes_info():
    print("=== Elasticsearch Nodes Info ===")
    r = requests.get(f"{ES_URL}/_nodes?pretty")
    print(r.text)

def indices_stats():
    print("=== Elasticsearch Indices Stats ===")
    r = requests.get(f"{ES_URL}/_stats?pretty")
    print(r.text)

def indices_list():
    print("=== Elasticsearch Indices List ===")
    r = requests.get(f"{ES_URL}/_cat/indices?v")
    print(r.text)

def cluster_settings():
    print("=== Elasticsearch Cluster Settings ===")
    r = requests.get(f"{ES_URL}/_cluster/settings?pretty")
    print(r.text)

def pending_tasks():
    print("=== Elasticsearch Pending Tasks ===")
    r = requests.get(f"{ES_URL}/_cluster/pending_tasks?pretty")
    print(r.text)

def shard_allocation():
    print("=== Elasticsearch Shard Allocation ===")
    r = requests.get(f"{ES_URL}/_cat/shards?v")
    print(r.text)

def tasks_list():
    print("=== Elasticsearch Tasks List ===")
    r = requests.get(f"{ES_URL}/_tasks?pretty")
    print(r.text)

def usage():
    print("Usage: es_debug.py <command>")
    print("Commands:")
    print("  health        - Cluster health")
    print("  nodes         - Nodes info")
    print("  stats         - Indices stats")
    print("  indices       - List indices")
    print("  settings      - Cluster settings")
    print("  pending       - Pending cluster tasks")
    print("  shards        - Shard allocation")
    print("  tasks         - Running tasks")
    print("  all           - Run all commands (default)")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?", default="all")
    args = parser.parse_args()

    commands = {
        "health": cluster_health,
        "nodes": nodes_info,
        "stats": indices_stats,
        "indices": indices_list,
        "settings": cluster_settings,
        "pending": pending_tasks,
        "shards": shard_allocation,
        "tasks": tasks_list,
        "all": lambda: [
            cluster_health(), print(),
            nodes_info(), print(),
            indices_stats(), print(),
            indices_list(), print(),
            cluster_settings(), print(),
            pending_tasks(), print(),
            shard_allocation(), print(),
            tasks_list()
        ]
    }

    if args.command in commands:
        commands[args.command]()
    else:
        usage()

if __name__ == "__main__":
    main()