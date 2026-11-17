#!/usr/bin/env bash
# Regenerate VENUAI/requirements.lock (universal, hash-pinned) from requirements.in, then audit it.
# Review the diff and run the full test suite before committing.
set -euo pipefail
cd "$(dirname "$0")/../../VENUAI"
pip install -q uv
uv pip compile requirements.in --universal --generate-hashes --python-version 3.12 --no-header -o requirements.lock "$@"
cd ..
bash scripts/security/audit_python.sh
