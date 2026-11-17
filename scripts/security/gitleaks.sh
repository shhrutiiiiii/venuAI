#!/usr/bin/env bash
# Run gitleaks over the full git history (CI) using the repo config. Downloads the pinned CLI if absent.
set -euo pipefail
cd "$(dirname "$0")/../.."
VER=8.30.1
if ! command -v gitleaks >/dev/null; then
  curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${VER}/gitleaks_${VER}_linux_x64.tar.gz" -o /tmp/gl.tgz
  tar -xzf /tmp/gl.tgz -C /tmp gitleaks
  export PATH="/tmp:$PATH"
fi
gitleaks detect --source . --config .gitleaks.toml --redact --verbose
