# HTTP security (headers, CORS, limits, errors, config)

Implemented by `VENUAI/http_security.py`, `VENUAI/config.py`, `VENUAI/observability.py` (log redaction), and on the frontend `chat-app/next.config.ts`, `chat-app/src/proxy.ts`, `chat-app/src/lib/proxyErrors.ts`. See ADR-S4.1 and ADR-S4.2.

## Production configuration (`ENV=production` or `APP_ENV=production`)
Start-up aborts (`config.ConfigError`, all problems listed) when: `JWT_SECRET_KEY` missing/short/placeholder; `ALLOWED_ORIGINS` has `*`, a local origin or a non-https origin; `FRONTEND_URL` missing or non-https; `DEBUG`/`FASTAPI_DEBUG`/`RELOAD`/`UVICORN_RELOAD` set; `SEED_DEMO_USER` or `NEXT_PUBLIC_SHOW_DEMO` true; default demo/admin password env vars. Warnings: `ENABLE_DOCS=1`, missing `SMTP_HOST`, `TRUSTED_PROXY=*`. In dev only the JWT/debug findings are logged as warnings.

| Env var | Default | Meaning |
|---|---|---|
| `ENV` / `APP_ENV` | dev | `production` enables validation, HSTS, disables docs |
| `ENABLE_DOCS` | off in production, on in dev | exposes /docs, /redoc, /openapi.json |
| `ALLOWED_ORIGINS` | localhost:3000 | strict CORS allow-list (GET/POST/PATCH/DELETE/OPTIONS; headers Authorization, Content-Type, X-Requested-With; max-age 600) |
| `TRUSTED_PROXY` (or `FORWARDED_ALLOW_IPS`) | none | peers (IPs/CIDRs) whose `X-Forwarded-For` is trusted; the right-most untrusted hop is the client IP |
| `MAX_BODY_BYTES` / `MAX_UPLOAD_BYTES` | 1 MB / 30 MB | 413 on Content-Length or streamed overflow; upload paths: `/upload*`, `/datasets/upload`, `/voice/*` |
| `RATE_LIMIT_DEFAULT` | 120/minute | global per-user (else per-IP) bucket |
| `RATE_LIMIT_LLM_IP` / `RATE_LIMIT_LLM_DAILY` | 90/minute / 1000/day | extra per-IP and per-user-daily buckets on expensive routes |
| `RATE_LIMIT_STORAGE_URI` | memory:// | e.g. `redis://...` for multi-worker deployments |
| `HOST`, `PORT`, `LIMIT_CONCURRENCY` | 127.0.0.1, 8000, 200 | used by `python rag_server.py` |

## Rate limiting
Key: JWT subject (signature and expiry verified, no DB) else client IP. Without `TRUSTED_PROXY` the IP is the TCP peer, so behind the Next proxy all anonymous users share one bucket: set `TRUSTED_PROXY` to the proxy's address and the Next proxies' forwarded client IP is used. Expensive routes (`/query*` cost 5, `/research*` 10, `/graph/extract`, `/datasets/*/analyze`, region ask, `/a2a` 5, uploads and voice 3) consume that cost from the per-user bucket. 429 responses carry `Retry-After`. `/health` and `OPTIONS` are exempt. The existing per-route `@limiter.limit` decorators remain and now key per user. Not done: per-tenant daily budget (the access token has no tenant claim) and login keyed by (IP+email), which belongs to the auth stream.

## Errors
Every error body is `{"detail": ..., "request_id": ...}`; the id equals the `X-Request-ID` header and appears in JSON logs. Unhandled exceptions return a generic 500; 5xx details are never echoed; validation errors list `loc`/`msg`/`type` only (no input). Use `raise http_security.safe_error(e, "Public message", status)` in handlers. Frontend proxies normalise upstream errors with `lib/proxyErrors.ts`.

## Headers
API: nosniff, X-Frame-Options DENY, Referrer-Policy no-referrer, Permissions-Policy, COOP/CORP same-origin, CSP `default-src 'none'; frame-ancestors 'none'` (not on /docs), `Cache-Control: no-store`, HSTS in production or when `X-Forwarded-Proto: https`. Next: baseline headers in `next.config.ts` (microphone allowed for same-origin; COOP same-origin-allow-popups), CSP-Report-Only in `src/proxy.ts`; flip with `CSP_ENFORCE=1` after the report log is clean. Streaming (SSE) is untouched: the middleware is pure ASGI and the Next proxy sends `no-transform`.

## Capabilities
`GET /capabilities` (auth required) returns which optional routers are mounted; `/api/capabilities` uses it and falls back to `/openapi.json` for old backends.

## Log redaction
`observability.redact()` masks bearer tokens, JWTs, API keys, `password=`/`token=` style pairs, sensitive query parameters and partially masks e-mails (`a***@host`) in text and JSON log output (including tracebacks) and on `uvicorn.*` loggers.
