#!/usr/bin/env bash
# Builds the Linux package from macOS, in one command:
#
#   ./scripts/build-linux.sh
#
# Docker Desktop has to be running. Nothing is installed on this Mac and
# nothing in the checkout is deleted — the container gets a copy of the source
# (minus .dockerignore) and hands back dist-linux/TiewTrade.
#
#   --skip-verify   skip the repository checks inside the container, when they
#                   were already run in this checkout
#   TIEWTRADE_LINUX_PLATFORM   the architecture to build for; see below
#
# See docs/linux-build.md.

set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE_NAME="${TIEWTRADE_LINUX_IMAGE:-tiewtrade-linux-build}"
OUTPUT_DIRECTORY="$PWD/dist-linux"

# An Apple Silicon Mac builds arm64 images unless told otherwise, and an arm64
# executable does not start on an x86_64 Ubuntu — it fails with "cannot
# execute binary file", which reads like a broken build rather than the wrong
# architecture. Naming the platform explicitly is what keeps that from
# happening by accident.
#
# amd64 is also the only target that builds today: PySide6 6.10, the version
# pyproject.toml asks for, publishes no Linux aarch64 wheel, so
# TIEWTRADE_LINUX_PLATFORM=linux/arm64 stops while installing dependencies.
# The knob stays because that is a version constraint, not a fact about this
# script.
TARGET_PLATFORM="${TIEWTRADE_LINUX_PLATFORM:-linux/amd64}"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running — start Docker Desktop and try again." >&2
  exit 1
fi

echo "==> Building the image for $TARGET_PLATFORM"
docker build \
  --platform "$TARGET_PLATFORM" \
  --tag "$IMAGE_NAME" \
  .

mkdir -p "$OUTPUT_DIRECTORY"

echo "==> Running the build"
docker run --rm \
  --platform "$TARGET_PLATFORM" \
  --volume "$OUTPUT_DIRECTORY:/app/dist" \
  "$IMAGE_NAME" "$@"

echo
echo "Package: dist-linux/TiewTrade/TiewTrade"
echo "Copy the whole dist-linux/TiewTrade folder to the Ubuntu machine."
