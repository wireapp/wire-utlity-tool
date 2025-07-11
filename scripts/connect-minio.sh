#!/bin/bash
set -e

# Read credentials
ACCESS_KEY=$(cat /etc/wire-services/minio/minio-credentials/access-key 2>/dev/null || echo "")
SECRET_KEY=$(cat /etc/wire-services/minio/minio-credentials/secret-key 2>/dev/null || echo "")

if [ -z "$ACCESS_KEY" ] || [ -z "$SECRET_KEY" ]; then
    echo "❌ MinIO credentials not found"
    exit 1
fi

# Get endpoint from service discovery ConfigMap
ENDPOINT=""
if [ -f "/etc/wire-services/endpoints/minio-endpoint" ]; then
    ENDPOINT=$(cat /etc/wire-services/endpoints/minio-endpoint)
fi

if [ -z "$ENDPOINT" ]; then
    echo "❌ MinIO service endpoint not found in service discovery"
    echo "Expected: /etc/wire-services/endpoints/minio-endpoint"
    echo "Available endpoints:"
    ls -la /etc/wire-services/endpoints/ 2>/dev/null || echo "No endpoints directory found"
    exit 1
fi

echo "🗄️ Connecting to MinIO at $ENDPOINT..."

# Configure MinIO client - Use /tmp for config (writable by nonroot user)
export MC_CONFIG_DIR="/tmp/.mc"
mkdir -p $MC_CONFIG_DIR

# Clear any existing config to avoid conflicts
rm -rf $MC_CONFIG_DIR/*

# Set up the alias
mc alias set minio "$ENDPOINT" "$ACCESS_KEY" "$SECRET_KEY"

if [ $? -eq 0 ]; then
    echo "✅ Connected! Try: mc ls minio"
    echo "📦 Available buckets:"
    mc ls minio 2>/dev/null || echo "No buckets found"
    
    echo ""
    echo "💡 To use mc commands, run:"
    echo "   export MC_CONFIG_DIR=\"/tmp/.mc\""
    echo "   mc ls minio"
    echo "   mc mb minio/my-bucket"
    echo "   mc cp file.txt minio/my-bucket/"
else
    echo "❌ Connection failed"
    exit 1
fi