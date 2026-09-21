#!/usr/bin/env bash
# Build and push the helios runtime image.
# Usage: ./build.sh <dockerhub-user> [version]
# Workbench nodes are x86_64: always build for linux/amd64 (matters on Apple Silicon).
set -euo pipefail
USER_NAME="${1:?dockerhub user required}"
VERSION="${2:-0.1.1}"
IMAGE="docker.io/${USER_NAME}/helios-runtime:${VERSION}"

docker buildx build \
  --platform linux/amd64 \
  --build-arg HELIOS_VERSION="${VERSION}" \
  -t "${IMAGE}" \
  --push \
  "$(dirname "$0")"

echo "pushed ${IMAGE}"
echo "register this URL in Cloudera AI: Site Administration -> Runtime Catalog -> Add Runtime"
./build