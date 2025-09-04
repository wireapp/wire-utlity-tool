# Variables
REGISTRY ?= sukisuk
UTILITY_IMAGE ?= $(REGISTRY)/wire-utility-tool
PG_MANAGER_IMAGE ?= $(REGISTRY)/postgres-endpoint-manager
TAG ?= latest

# Platform targets
PLATFORMS = linux/amd64,linux/arm64

.PHONY: help build-utility build-pg-manager build-all push-utility push-pg-manager push-all test-utility test-pg-manager clean setup-buildx

# Default target
help:
	@echo "Available targets:"
	@echo ""
	@echo "Wire Utility Tool:"
	@echo "  build-utility       - Build wire-utility-tool image"
	@echo "  build-utility-multi - Build wire-utility-tool for multiple platforms"
	@echo "  push-utility        - Push wire-utility-tool image"
	@echo "  test-utility        - Test wire-utility-tool image"
	@echo ""
	@echo "PostgreSQL Endpoint Manager:"
	@echo "  build-pg-manager       - Build postgres-endpoint-manager image"
	@echo "  build-pg-manager-multi - Build postgres-endpoint-manager for multiple platforms"
	@echo "  push-pg-manager        - Push postgres-endpoint-manager image"
	@echo "  test-pg-manager        - Test postgres-endpoint-manager image"
	@echo "  test-pg-manager-custom - Test with custom node configuration"
	@echo "  test-pg-manager-interactive - Run interactive test mode"
	@echo ""
	@echo "Combined:"
	@echo "  build-all     - Build both images"
	@echo "  push-all      - Push both images"
	@echo "  test-all      - Test both images"
	@echo "  clean         - Clean local images"
	@echo ""
	@echo "Variables:"
	@echo "  REGISTRY      - Registry namespace (default: $(REGISTRY))"
	@echo "  TAG           - Image tag (default: $(TAG))"

# ============================================================================
# Wire Utility Tool Targets
# ============================================================================

# Build wire-utility-tool for current platform
build-utility:
	docker build -f Dockerfile.utility -t $(UTILITY_IMAGE):$(TAG) .

# Build wire-utility-tool for multiple platforms
build-utility-multi:
	docker buildx build --platform $(PLATFORMS) -f Dockerfile.utility -t $(UTILITY_IMAGE):$(TAG) .

# Push wire-utility-tool image
push-utility: build-utility
	docker push $(UTILITY_IMAGE):$(TAG)

# Push wire-utility-tool multi-platform
push-utility-multi:
	docker buildx build --platform $(PLATFORMS) -f Dockerfile.utility -t $(UTILITY_IMAGE):$(TAG) --push .

# Test wire-utility-tool image
test-utility:
	@echo "Testing wire-utility-tool image..."
	docker run --rm $(UTILITY_IMAGE):$(TAG) bash -c "echo 'Testing tools...' && python3 --version && python2 --version && psql --version && cqlsh --version && mc --version"

# ============================================================================
# PostgreSQL Endpoint Manager Targets
# ============================================================================

# Build postgres-endpoint-manager for current platform
build-pg-manager:
	docker build -f Dockerfile.postgres-endpoint-manager -t $(PG_MANAGER_IMAGE):$(TAG) .

# Build a test image (includes postgresql-client and dev tools) from the builder stage
build-pg-manager-test:
	docker build -f Dockerfile.postgres-endpoint-manager -t $(PG_MANAGER_IMAGE)-test:$(TAG) --target builder .

# Build postgres-endpoint-manager for multiple platforms
build-pg-manager-multi:
	docker buildx build --platform $(PLATFORMS) -f Dockerfile.postgres-endpoint-manager -t $(PG_MANAGER_IMAGE):$(TAG) .

# Push postgres-endpoint-manager image
push-pg-manager: build-pg-manager
	docker push $(PG_MANAGER_IMAGE):$(TAG)

# Push postgres-endpoint-manager multi-platform
push-pg-manager-multi:
	docker buildx build --platform $(PLATFORMS) -f Dockerfile.postgres-endpoint-manager -t $(PG_MANAGER_IMAGE):$(TAG) --push .

# Test postgres-endpoint-manager image
test-pg-manager:
	@echo "Testing postgres-endpoint-manager image (runtime) and functionality via test image..."
	# Check minimal runtime image (no psql expected)
	docker run --rm --entrypoint sh $(PG_MANAGER_IMAGE):$(TAG) -c "python --version && echo 'psql:' $(which psql 2>/dev/null || echo 'absent') && curl --version >/dev/null 2>&1 || true || true"
	@echo "Running functional tests using the fuller test image (includes psql)..."
	# Build or ensure the test image exists and run the comprehensive test harness from it
	$(MAKE) build-pg-manager-test
	docker run --rm \
		-e PG_NODES="192.168.122.31,192.168.122.32,192.168.122.33" \
		-v $(PWD)/scripts:/app/scripts \
		--entrypoint python3 \
		$(PG_MANAGER_IMAGE)-test:$(TAG) /app/scripts/test-postgres-endpoint-manager.py --comprehensive

# Test postgres-endpoint-manager with custom nodes
test-pg-manager-custom:
	@echo "Testing postgres-endpoint-manager with custom node configuration..."
	docker run --rm \
		-e PG_NODES="10.0.0.1,10.0.0.2,10.0.0.3" \
		-e RW_SERVICE="my-postgres-rw" \
		-e RO_SERVICE="my-postgres-ro" \
		-e PGUSER="testuser" \
		-e PGDATABASE="testdb" \
		-v $(PWD)/scripts:/app/scripts \
		--entrypoint python3 \
		$(PG_MANAGER_IMAGE):$(TAG) /app/scripts/test-postgres-endpoint-manager.py --scenario healthy_cluster

# Test postgres-endpoint-manager interactively
test-pg-manager-interactive:
	@echo "Running interactive test mode..."
	docker run --rm -it \
		-e PG_NODES="192.168.122.31,192.168.122.32,192.168.122.33" \
		-v $(PWD)/scripts:/app/scripts \
		--entrypoint python3 \
		$(PG_MANAGER_IMAGE):$(TAG) /app/scripts/test-postgres-endpoint-manager.py --interactive

# ============================================================================
# Combined Targets
# ============================================================================

# Build both images
build-all: build-utility build-pg-manager

# Build both images for multiple platforms
build-all-multi: build-utility-multi build-pg-manager-multi

# Push both images
push-all: push-utility push-pg-manager

# Push both images multi-platform
push-all-multi: push-utility-multi push-pg-manager-multi

# Test both images
test-all: test-utility test-pg-manager

# ============================================================================
# Utility Targets
# ============================================================================

# Clean local images
clean:
	docker rmi $(UTILITY_IMAGE):$(TAG) || true
	docker rmi $(PG_MANAGER_IMAGE):$(TAG) || true
	@echo "Cleaned local images"

# Setup buildx for multi-platform builds
setup-buildx:
	docker buildx create --use --name multiarch || true
	docker buildx inspect --bootstrap
	@echo "Buildx setup complete"

# Remove buildx builder
cleanup-buildx:
	docker buildx rm multiarch || true

# Show current images
show-images:
	@echo "Current images:"
	@docker images | grep -E "($(REGISTRY)|REPOSITORY)" || echo "No matching images found"

# Login to Docker Hub (interactive)
login:
	docker login

# Quick development workflow
dev-utility: build-utility test-utility
	@echo "Development build complete for wire-utility-tool"

dev-pg-manager: build-pg-manager test-pg-manager
	@echo "Development build complete for postgres-endpoint-manager"

# Quick release workflow (build + test + push)
release-utility: build-utility test-utility push-utility
	@echo "Released $(UTILITY_IMAGE):$(TAG)"

release-pg-manager: build-pg-manager test-pg-manager push-pg-manager
	@echo "Released $(PG_MANAGER_IMAGE):$(TAG)"

release-all: build-all test-all push-all
	@echo "Released both images with tag $(TAG)"