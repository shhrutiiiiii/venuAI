#!/usr/bin/env bash
# Verify images run as a non-root user and define a HEALTHCHECK. Usage: check_images.sh image...
set -euo pipefail
for img in "$@"; do
  user=$(docker inspect -f '{{.Config.User}}' "$img")
  hc=$(docker inspect -f '{{if .Config.Healthcheck}}yes{{end}}' "$img")
  [[ -n "$user" && "$user" != "0" && "$user" != "root" ]] || { echo "FAIL $img runs as root"; exit 1; }
  [[ "$hc" == "yes" ]] || { echo "FAIL $img has no HEALTHCHECK"; exit 1; }
  echo "OK $img user=$user healthcheck=yes"
done
