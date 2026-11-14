#!/usr/bin/env bash
# Policy: the prod compose may publish only 80/443; backend (8000) and postgres (5432) must not be published.
set -euo pipefail
cd "$(dirname "$0")/../.."
out=$(SITE_ADDRESS=ci.example.com docker compose -f docker-compose.prod.yml --profile postgres config)
if grep -Eq 'published: "?(8000|5432)"?' <<<"$out"; then echo "FAIL: prod compose publishes 8000 or 5432"; exit 1; fi
pub=$(grep -Eo 'published: "?[0-9]+' <<<"$out" | grep -Eo '[0-9]+' | sort -u | tr '\n' ' ')
[[ "$pub" == "443 80 " || "$pub" == "80 443 " ]] || { echo "FAIL: unexpected published ports: $pub"; exit 1; }
if grep -q "docker.sock" <<<"$out"; then echo "FAIL: docker.sock mounted"; exit 1; fi
if grep -Eq 'POSTGRES_PASSWORD: (postgres|password|admin)$' <<<"$out"; then echo "FAIL: default postgres password"; exit 1; fi
echo "OK: prod compose publishes only: $pub"
