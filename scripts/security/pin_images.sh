#!/usr/bin/env bash
# Print digest-pinned references for the base images, to paste into the Dockerfiles / compose files.
set -euo pipefail
for img in python:3.12-slim node:22-alpine caddy:2-alpine postgres:16-alpine; do
  docker pull -q "$img" >/dev/null
  echo "$(docker inspect --format '{{index .RepoDigests 0}}' "$img")   # $img"
done
