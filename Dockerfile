# Build the manager binary
FROM golang:1.24 AS builder
ARG TARGETOS
ARG TARGETARCH

WORKDIR /workspace

# Copy go mod and sum files
COPY go.mod go.mod
COPY go.sum go.sum

# Download dependencies
RUN go mod download

# Copy source code
COPY cmd/ cmd/
COPY controllers/ controllers/

# Build the binary
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} go build -a -o manager cmd/main.go

# Use Alpine as base image for smaller size and utilities
FROM alpine:3.19

# Install essential utilities
RUN apk add --no-cache \
    bash \
    curl \
    ca-certificates \
    wget \
    netcat-openbsd \
    bind-tools \
    jq

# Install MinIO client
RUN wget https://dl.min.io/client/mc/release/linux-amd64/mc -O /usr/local/bin/mc && \
    chmod +x /usr/local/bin/mc

# Create non-root user
RUN adduser -D -s /bin/bash -u 65532 nonroot

# Create service directories matching the controller's mount paths
RUN mkdir -p /etc/wire-services/minio \
             /etc/wire-services/rabbitmq \
             /etc/wire-services/cassandra \
             /etc/wire-services/postgres \
             /etc/wire-services/redis \
             /etc/wire-services/mysql \
             /etc/wire-services/endpoints && \
    chown -R 65532:65532 /etc/wire-services

# Create scripts directory
RUN mkdir -p /usr/local/bin/wire-utils && \
    chown -R 65532:65532 /usr/local/bin/wire-utils

# Copy and setup scripts
COPY scripts/ /usr/local/bin/wire-utils/
RUN find /usr/local/bin/wire-utils -name "*.sh" -exec chmod +x {} \; && \
    chown -R 65532:65532 /usr/local/bin/wire-utils

# Create convenient symlinks
RUN ln -s /usr/local/bin/wire-utils/connect-minio.sh /usr/local/bin/connect-minio.sh

# Copy the manager binary
WORKDIR /
COPY --from=builder /workspace/manager .
RUN chmod +x /manager

# Switch to non-root user
USER nonroot

ENTRYPOINT ["/manager"]