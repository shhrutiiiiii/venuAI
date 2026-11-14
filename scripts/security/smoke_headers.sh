#!/usr/bin/env bash
# Check security headers on a running stack. Usage: smoke_headers.sh [base_url]  (default http://127.0.0.1:3000)
# Exits non-zero when a required header is missing. HSTS is only required on https URLs.
set -uo pipefail
BASE=${1:-http://127.0.0.1:3000}
fail=0
check() { # url header
  val=$(curl -s -o /dev/null -D - "$1" | tr -d '\r' | grep -i "^$2:" || true)
  if [[ -z "$val" ]]; then echo "MISSING  $2  ($1)"; fail=1; else echo "ok       $val"; fi
}
for path in / /login /api/rag/health; do
  for h in X-Content-Type-Options X-Frame-Options Referrer-Policy; do check "$BASE$path" "$h"; done
done
[[ "$BASE" == https://* ]] && check "$BASE/" Strict-Transport-Security
leak=$(curl -s -o /dev/null -D - "$BASE/" | tr -d '\r' | grep -i '^x-powered-by:' || true)
[[ -n "$leak" ]] && { echo "LEAK     $leak"; fail=1; }
exit $fail
