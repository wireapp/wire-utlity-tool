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

### Build the Image Locally to test

```bash
# Build for current platform
make build

# Build for specific platform
make build-amd64
make build-arm64

# Build for multiple platforms
make build-multi
```


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

### Run the Container with the upstream image from quay.io

```bash
# Interactive shell
docker run -it --rm quay.io/wire/wire-utility-tool:latest

# With specific version
docker run -it --rm quay.io/wire/wire-utility-tool:1.0.0
```

### Deploy with Helm (Kubernetes)

This image is designed to work with the [Wire Utility Helm Chart](https://github.com/wireapp/helm-charts/tree/add-utility-helm/charts/wire-utility) for Kubernetes deployments:

## Versioning and Releases

### Adding New Tools and Creating Releases

When adding new tools or making significant changes to the image:

1. **Update the Dockerfile** with the new tool installation
2. **Update this README** to document the new tool
3. **Test the changes locally**:
   ```bash
   make build
   make test
   ```

4. **Commit your changes**:
   ```bash
   git add .
   git commit -m "Add new tool: example-tool"
   ```

5. **Create a version tag** following semantic versioning:
   ```bash
   # For new tools/features (minor version)
   git tag -a v1.1.0 -m "Release version 1.1.0 - Add example-tool"
   
   # For bug fixes (patch version)
   git tag -a v1.0.1 -m "Release version 1.0.1 - Fix tool configuration"
   
   # For breaking changes (major version)
   git tag -a v2.0.0 -m "Release version 2.0.0 - Breaking changes"
   ```

6. **Push the tag to trigger automated build**:
   ```bash
   git push origin v1.1.0
   ```

### Version Tag Results

When you push a version tag, the CI/CD pipeline automatically builds and pushes:

- `quay.io/wire/wire-utility-tool:v1.1.0` - Exact version
- `quay.io/wire/wire-utility-tool:1.1` - Major.minor version
- `quay.io/wire/wire-utility-tool:latest` - Latest stable release

### Semantic Versioning Guidelines

- **Patch** (`v1.0.1`): Bug fixes, security updates, tool configuration changes
- **Minor** (`v1.1.0`): New tools added, new features, backward-compatible changes
- **Major** (`v2.0.0`): Breaking changes, tool removals, base image changes

## Architecture Support

This image supports both AMD64 and ARM64 architectures, making it suitable for:
- Intel/AMD x86_64 systems
- Apple Silicon (M1/M2) Macs
- ARM64 servers

## Security

- Runs as non-root user (`nonroot` with UID 65532)
- Minimal attack surface with only essential tools
- Based on Debian Bullseye Slim to support legacy versions

## Related Projects

- **[Wire Utility Helm Chart](https://github.com/wireapp/helm-charts/tree/add-utility-helm/charts/wire-utility)** - Kubernetes deployment chart for this utility image

## Contributing

Feel free to submit issues and enhancement requests!

## License

This project is open source and available under standard terms.
