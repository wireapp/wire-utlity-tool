# Wire Utility Tool

A comprehensive Docker toolkit providing essential debugging utilities and automated PostgreSQL cluster management for Wire's backend infrastructure.

## 🚀 Quick Start

```bash
# Wire Utility Tool (debugging & utilities)
docker run -it --rm quay.io/wire/wire-utility-tool:latest

# PostgreSQL Endpoint Manager (cluster automation)
docker run --rm quay.io/wire/wire-utility-tool:pg-manager-latest --test
```

## 📦 What's Included

### 🔧 Wire Utility Tool

A debugging container with comprehensive tooling:

**Database Clients**
- **PostgreSQL** (`psql`) - Connect and query PostgreSQL databases
- **Redis** (`redis-cli`) - Interact with Redis instances
- **Cassandra** (`cqlsh`) - Query Cassandra clusters (v3.11 compatible)

**Message Queue & Storage**
- **RabbitMQ** (`rabbitmqadmin`) - Manage RabbitMQ instances
- **MinIO Client** (`mc`) - Interact with S3-compatible storage

**Network & System Tools**
- **Network**: `curl`, `wget`, `nc`, `nmap`, `tcpdump`, `dig`, `ping`, `traceroute`
- **Text Processing**: `jq`, `vim`, `nano`, `less`, `tree`
- **Programming**: Python 2 & 3 with pip

**Search & Analytics**
- **Elasticsearch Debug** (`es-debug.sh`) - Debug Elasticsearch clusters

### 🗄️ PostgreSQL Endpoint Manager

Automated PostgreSQL cluster management:

- **🔍 Discovers** cluster topology from Kubernetes service annotations
- **🔬 Verifies** node roles (primary/standby) via live database connections
- **📡 Updates** Kubernetes endpoints automatically during topology changes
- **📊 Provides** structured JSON logging for observability

## 🛠️ Usage

### Development

```bash
# Build and test locally
make build && make test

# PostgreSQL manager
make dev-pg-manager && make test-pg-manager
```

### Production

```bash
# Interactive debugging session
docker run -it --rm quay.io/wire/wire-utility-tool:latest

# Test PostgreSQL manager
docker run --rm quay.io/wire/wire-utility-tool:pg-manager-latest --test
```

## 🔖 Versioning

### Creating Releases

1. **Make changes and test locally**
2. **Create version tag**:
   ```bash
   # Utility tool releases
   git tag -a v1.3.0 -m "Add new debugging tools"

   # PostgreSQL manager releases
   git tag -a pg-v1.1.0 -m "Improve cluster discovery"
   ```
3. **Push to trigger automated build**:
   ```bash
   git push origin v1.3.0     # → quay.io/wire/wire-utility-tool:v1.3.0
   git push origin pg-v1.1.0  # → quay.io/wire/wire-utility-tool:pg-manager-v1.1.0
   ```

### Available Tags

| Component | Latest | Versioned |
|-----------|--------|-----------|
| **Utility Tool** | `latest` | `v1.2.0`, `1.2` |
| **PostgreSQL Manager** | `pg-manager-latest` | `pg-manager-v1.0.0` |

## 🏗️ Architecture

- **Multi-platform**: AMD64 & ARM64 support
- **Security**: Non-root user (UID 65532), minimal attack surface
- **Base**: Debian Bullseye Slim for stability

## 🤝 Contributing

1. **Add new tools** to Dockerfile
2. **Update README** with tool documentation
3. **Test changes** locally
4. **Submit PR** with version tag

---

**Repository Purpose**: Provides standardized debugging utilities and automated PostgreSQL cluster management for Wire's infrastructure operations.