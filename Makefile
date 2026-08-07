# Variables
REGISTRY ?= sukisuk
UTILITY_IMAGE ?= $(REGISTRY)/wire-utility-tool
TAG ?= latest

# Platform targets
PLATFORMS = linux/amd64,linux/arm64

# ============================================================================
# Wire Utility Tool Targets
# ============================================================================

.PHONY: build
# Build wire-utility-tool for current platform
build:
	docker buildx build -f Dockerfile.utility -t $(UTILITY_IMAGE):$(TAG) --progress=plain .

.PHONY: build-multi
# Build wire-utility-tool for multiple platforms
build-multi:
	docker buildx build --platform $(PLATFORMS) -f Dockerfile.utility -t $(UTILITY_IMAGE):$(TAG) .

.PHONY: push
# Push wire-utility-tool image
push: build
	docker push $(UTILITY_IMAGE):$(TAG)

.PHONY: push-multi
# Push wire-utility-tool multi-platform
push-multi:
	docker buildx build --platform $(PLATFORMS) -f Dockerfile.utility -t $(UTILITY_IMAGE):$(TAG) --push .

.PHONY: tests test
tests: test

# Test wire-utility-tool image
test:
	@echo "Testing wire-utility-tool image..."
	docker run --rm --entrypoint="" $(UTILITY_IMAGE):$(TAG) bash -c "echo 'Testing tools...' && python3 --version && python2 --version && psql --version && cqlsh --version && mc --version && rabbitmqadmin --version && echo 'Testing es command...' && es usages && echo 'Testing PATH...' && which bash && echo 'All tests passed!'"

# ============================================================================
# Utility Targets
# ============================================================================

.PHONY: clean
# Clean local images
clean:
	docker rmi $(UTILITY_IMAGE):$(TAG) || true
	@echo "Cleaned local images"

.PHONY: setup-buildx
# Setup buildx for multi-platform builds
setup-buildx:
	docker buildx create --use --name multiarch || true
	docker buildx inspect --bootstrap
	@echo "Buildx setup complete"

.PHONY: clean-buildx
# Remove buildx builder
cleanup-buildx:
	docker buildx rm multiarch || true

.PHONY: show-images
# Show current images
show-images:
	@echo "Current images:"
	@docker images | grep -E "($(REGISTRY)|REPOSITORY)" || echo "No matching images found"

.PHONY: login
# Login to Docker Hub (interactive)
login:
	docker login

.PHONY: dev
# Quick development workflow
dev: build test
	@echo "Development build complete for wire-utility-tool"

.PHONY: release
# Quick release workflow (build + test + push)
release: build test push
	@echo "Released $(UTILITY_IMAGE):$(TAG)"

.PHONY: tar
# serialise the image as a tarball.
tar: build
	@echo -n "Serializing image..."
	@docker image save $(UTILITY_IMAGE):latest -o wire-utility-tool-`date +%s`.tar
	@echo done.
