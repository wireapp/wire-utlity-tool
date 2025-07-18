# Variables
IMAGE_NAME ?= sukisuk/wire-utility-tool
TAG ?= latest
DOCKERFILE ?= Dockerfile.utility

# Platform targets
PLATFORMS = linux/amd64,linux/arm64

.PHONY: help build build-amd64 build-arm64 build-multi push push-multi clean

# Default target
help:
	@echo "Available targets:"
	@echo "  build-amd64    - Build for AMD64 platform"
	@echo "  build-arm64    - Build for ARM64 platform"
	@echo "  build-multi    - Build for multiple platforms (amd64,arm64)"
	@echo "  push           - Push image to registry"
	@echo "  push-multi     - Build and push multi-platform image"
	@echo "  clean          - Clean local images"
	@echo ""
	@echo "Variables:"
	@echo "  IMAGE_NAME     - Image name (default: $(IMAGE_NAME))"
	@echo "  TAG            - Image tag (default: $(TAG))"
	@echo "  DOCKERFILE     - Dockerfile to use (default: $(DOCKERFILE))"

# Build for AMD64 only
build-amd64:
	docker build --platform linux/amd64 -f $(DOCKERFILE) -t $(IMAGE_NAME):$(TAG) .

# Build for ARM64 only
build-arm64:
	docker build --platform linux/arm64 -f $(DOCKERFILE) -t $(IMAGE_NAME):$(TAG) .

# Build for current platform (default)
build:
	docker build -f $(DOCKERFILE) -t $(IMAGE_NAME):$(TAG) .

# Build for multiple platforms using buildx
build-multi:
	docker buildx build --platform $(PLATFORMS) -f $(DOCKERFILE) -t $(IMAGE_NAME):$(TAG) .

# Push single platform image
push: build
	docker push $(IMAGE_NAME):$(TAG)

# Build and push multi-platform image
push-multi:
	docker buildx build --platform $(PLATFORMS) -f $(DOCKERFILE) -t $(IMAGE_NAME):$(TAG) --push .

# Test the image locally
test:
	docker run -it --rm $(IMAGE_NAME):$(TAG) bash -c "echo 'Testing tools...' && python3 --version && rabbitmqadmin --version && mc --version && cqlsh --version && psql --version"

# Clean local images
clean:
	docker rmi $(IMAGE_NAME):$(TAG) || true

# Setup buildx for multi-platform builds
setup-buildx:
	docker buildx create --use --name multiarch || true
	docker buildx inspect --bootstrap

# Remove buildx builder
cleanup-buildx:
	docker buildx rm multiarch || true

# Build with custom tag
build-tag:
	@read -p "Enter tag: " tag; \
	make build TAG=$$tag

# Push with custom tag
push-tag:
	@read -p "Enter tag: " tag; \
	make push TAG=$$tag
