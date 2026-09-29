#!/usr/bin/env bash
# Build and push the helios-ds runtime images, then print their digests.
# Usage: ./build.sh <registry/namespace> [version]
#   e.g. ./build.sh docker.io/myuser 0.1.0
# Workbench nodes are x86_64: always build for linux/amd64 (matters on Apple Silicon).
set -euo pipefail
REPO="${1:?registry/namespace required}"
VERSION="${2:-0.1.0}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="${REPO}/helios-ds-runtime:${VERSION}"
MEDIA="${REPO}/helios-ds-media-runtime:${VERSION}"

docker buildx build --platform linux/amd64 -t "${BASE}" --push "${HERE}/helios-ds"
docker buildx build --platform linux/amd64 --build-arg BASE_IMAGE="${BASE}" \
  -t "${MEDIA}" --push "${HERE}/helios-ds-media"

for image in "${BASE}" "${MEDIA}"; do
  echo "${image} -> $(docker buildx imagetools inspect "${image}" --format '{{json .Manifest.Digest}}')"
done
echo "Register both in Cloudera AI: Site Administration -> Runtime Catalog -> Add Runtime,"
echo "then record the digests in docs/helios-ds-burndown.md (F-11)."
