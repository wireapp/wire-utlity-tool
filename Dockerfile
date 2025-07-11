# Build the manager binary
FROM golang:1.24 AS builder
ARG TARGETOS
ARG TARGETARCH

WORKDIR /workspace

# Copy the Go Modules manifests
COPY go.mod go.mod
COPY go.sum go.sum

# Download dependencies
RUN go mod download

# Copy the go source
COPY cmd/ cmd/
COPY controllers/ controllers/

# Build
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} go build -a -o manager cmd/main.go

# Use Alpine as base image for utilities
FROM alpine:3.19

# Install useful utilities and create everything as root
RUN apk add --no-cache bash curl ca-certificates && \
    adduser -D -s /bin/bash -u 65532 nonroot && \
    mkdir -p /etc/wire-services/minio \
             /etc/wire-services/rabbitmq \
             /etc/wire-services/cassandra \
             /etc/wire-services/postgres \
             /etc/wire-services/redis \
             /etc/wire-services/mysql && \
    chown -R 65532:65532 /etc/wire-services

WORKDIR /
COPY --from=builder /workspace/manager .
RUN chmod +x /manager

USER nonroot

ENTRYPOINT ["/manager"]