#!/usr/bin/env bash
# pip-audit over the hash-pinned lock. Fails on any known vulnerability except IDs listed
# (with owner/expiry) in VENUAI/audit-ignore.txt. Expired exceptions fail the run.
set -euo pipefail
cd "$(dirname "$0")/../.."
args=()
today=$(date +%Y-%m-%d)
while IFS= read -r line; do
  [[ "$line" =~ ^[[:space:]]*# || -z "${line// }" ]] && continue
  id=${line%%[[:space:]]*}
  exp=$(sed -n 's/.*expires=\([0-9-]*\).*/\1/p' <<<"$line")
  if [[ -z "$exp" || "$exp" < "$today" ]]; then
    echo "ERROR: audit exception $id has no or an expired 'expires=' date ($exp)"; exit 2
  fi
  args+=(--ignore-vuln "$id")
done < VENUAI/audit-ignore.txt
pip-audit -r VENUAI/requirements.lock --no-deps --disable-pip --require-hashes "${args[@]}"
