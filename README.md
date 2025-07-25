# Wire Utility Tool

A comprehensive Docker image containing essential debugging and utility tools for working with various backend services and databases.

## What's Included

### Database Tools
- **PostgreSQL Client** (`psql`) - Connect to PostgreSQL databases
- **Redis CLI** (`redis-cli`) - Interact with Redis instances
- **Cassandra CQL Shell** (`cqlsh`) - Query Cassandra databases (v3.11 compatible)

### Message Queue Tools
- **RabbitMQ Admin** (`rabbitmqadmin`) - Manage RabbitMQ instances

### Storage Tools
- **MinIO Client** (`mc`) - Interact with S3-compatible storage

### Search & Analytics
- **Elasticsearch Debug Script** (`es-debug.sh`) - Debug Elasticsearch clusters

### Network & System Tools
- **Network**: `curl`, `wget`, `nc`, `nmap`, `tcpdump`, `dig`, `ping`, `traceroute`
- **Text Processing**: `jq`, `vim`, `nano`, `less`, `tree`
- **System**: `bash`, `find`, `file`, and other core utilities

### Programming Languages
- **Python 2** and **Python 3** with pip

## Usage

### Build the Image

```bash
# Build for current platform
make build

# Build for specific platform
make build-amd64
make build-arm64

# Build for multiple platforms
make build-multi
```

### Run the Container

```bash
# Interactive shell
docker run -it --rm quay.io/wire/wire-utility-tool:tag
```

### Deploy with Helm (Kubernetes)

This image is designed to work with the [Wire Utility Helm Chart](https://github.com/wireapp/helm-charts/tree/add-utility-helm/charts/wire-utility) for Kubernetes deployments:


## Available Make Targets

```bash
make help           # Show all available targets
make build          # Build for current platform
make build-amd64    # Build for AMD64 platform
make build-arm64    # Build for ARM64 platform
make build-multi    # Build for multiple platforms
make push           # Push single platform image
make push-multi     # Build and push multi-platform image
make test           # Test the image locally
make clean          # Clean local images
```

## Architecture Support

This image supports both AMD64 and ARM64 architectures, making it suitable for:
- Intel/AMD x86_64 systems
- Apple Silicon (M1/M2) Macs
- ARM64 servers

## Security

- Runs as non-root user (`nonroot` with UID 65532)
- Minimal attack surface with only essential tools
- Based on Debian Bullseye Slim for stability

## Related Projects

- **[Wire Utility Helm Chart](https://github.com/wireapp/helm-charts/tree/add-utility-helm/charts/wire-utility)** - Kubernetes deployment chart for this utility image

## Contributing

Feel free to submit issues and enhancement requests!

## License

This project is open source and available under standard terms.
