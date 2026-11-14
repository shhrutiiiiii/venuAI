# Containerization — VenuAI

## Purpose
Documents the containerization that now exists for both apps.

## Responsibilities
Describe each Dockerfile's shape and how `docker-compose.yml` wires them together.

## Scope
Container definitions only — orchestration/CI is [`deployment.md`](deployment.md) and the root `.github/workflows/ci.yml`.

## High-Level Overview
Implemented:

```
VENUAI/Dockerfile       → python:3.12-slim, pip install -r VENUAI/requirements.txt,
                           non-root user, healthcheck against /health,
                           CMD uvicorn VENUAI.rag_server:app
chat-app/Dockerfile     → node:20-alpine, 3-stage build (deps → build → runner),
                           non-root user, healthcheck, CMD npm run start
docker-compose.yml      → backend + frontend services, named volumes
                           (chroma_data, uploaded_docs, app_data) mounted into the
                           backend's chroma_db/uploaded_docs/data paths, frontend
                           depends_on backend's healthcheck, RAG_SERVER_URL wired
                           to the backend service name for container-to-container calls

                           Phase 5: a real postgres:16-alpine service, gated behind
                           the "postgres" Compose profile (`docker compose --profile
                           postgres up`) — opt-in, since SQLite stays the zero-config
                           default for the knowledge graph (Documents/AI/knowledge-graph.md).
                           Verified end-to-end during development: started via this
                           exact compose file, reached with psycopg2, and used as a
                           real PostgresGraphStore backend (entity write + read-back)
                           — not just syntax-validated.
```

## Design Principles
1. **Multi-stage frontend build** (separate `deps`/`builder`/`runner` stages) keeps the final image free of devDependencies and build-only files.
2. **Named volumes for every local-disk store** identified in [`../Database/architecture.md`](../Database/architecture.md) — `chroma_data`, `uploaded_docs`, and `app_data` (which holds `users.db` and the guardrails audit DB) all survive a container recreate.
3. **No secrets baked into the image** — the backend service reads `VENUAI/.env` via `env_file` in `docker-compose.yml`; `.env` itself is gitignored and never `COPY`'d into the image.
4. **Both images run as non-root users** and define a `HEALTHCHECK` so compose's `depends_on: condition: service_healthy` has something real to wait on.

## Workflow
```
docker compose build
docker compose up
   │
   ├─ backend  — builds from VENUAI/Dockerfile, waits on its own /health check
   └─ frontend — builds from chat-app/Dockerfile, waits for backend to report healthy,
                 then starts with RAG_SERVER_URL=http://backend:8000
```

## Inputs / Outputs
N/A.

## Dependencies
- [`deployment.md`](deployment.md)
- [`../Database/architecture.md`](../Database/architecture.md) — which paths need volumes.

## Interactions with Other Services
Backend and frontend containers communicate over the default compose network by service name (`backend`, `frontend`); neither talks to anything outside the compose network except the backend's outbound calls to Groq.

## Security Considerations
Base images (`python:3.12-slim`, `node:20-alpine`) are tag-pinned but not digest-pinned — consider pinning to a specific digest for full reproducibility before a real production rollout.

## Performance Considerations
Not yet measured under load — these images have been verified to build and pass health checks (see `Documents/future-additions.md`), not load-tested.

## Future Improvements
- Pin base images to digests, not just tags.
- Push built images to a registry and wire that into CI once a real deployment target is chosen.
- ~~Deploy Postgres for real~~ — **done (Phase 5)**, see above. Still not CI-wired (the CI workflow doesn't start the `postgres` profile, so Postgres-backend tests remain skip-when-unreachable there, same as a developer machine without Docker running).

## Notes for Developers
This document previously described a proposed, unbuilt shape — it was updated the same session the Dockerfiles were written, per the rule in `Agent.md` against letting documents drift from reality.


## Hardening update (S5, supply chain and containers)
- `VENUAI/Dockerfile` is multi-stage: a `builder` (with build-essential) installs `VENUAI/requirements.lock` into `/opt/venv` using `pip install --require-hashes`; the `runtime` stage copies only the venv, has no compiler, runs as UID 10001 under `tini`, and starts uvicorn with `--proxy-headers --forwarded-allow-ips ${FORWARDED_ALLOW_IPS}` and `--workers ${UVICORN_WORKERS}`. Writable state lives only under `data/`, `chroma_db/`, `uploaded_docs/` and `uploaded_datasets/` (volumes); `doc_metadata.json` and `datasets.json` are symlinked into `data/`, so the root filesystem can be read-only.
- `chat-app/Dockerfile` uses Node 22, UID 10001, `tini`, `NEXT_TELEMETRY_DISABLED=1`, and a healthcheck on `/api/rag/health`. It uses `.next/standalone/server.js` when `output: "standalone"` is configured (S4 owns `next.config.ts`) and falls back to `next start` otherwise.
- `docker-compose.yml` is the **dev** file: ports bound to `127.0.0.1`, backend on `:8000` for debugging, postgres opt-in with no default password (`POSTGRES_PASSWORD` is required for it to start).
- `docker-compose.prod.yml` is the **prod** file: only the Caddy TLS proxy (`deploy/Caddyfile`, HSTS, body limit, SSE-friendly) publishes 80/443; backend, frontend and postgres are on an `internal` network with no host ports; `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`, `mem_limit`, `cpus`; secrets come from `.env.prod` / `BACKEND_ENV_FILE` (see `.env.prod.example`); `ENABLE_CODE_EXECUTION=false` and the container runtime socket is never mounted. `scripts/security/check_compose.sh` enforces this policy in CI.
- `.dockerignore` (root and `chat-app/`) keeps `.env*`, databases, uploads and `.git` out of build contexts.
- Base images are tag-pinned; `scripts/security/pin_images.sh` prints digest-pinned references to paste in. The torch wheel from PyPI is large (CUDA); switch to the CPU index in a separate lock if image size matters.
- The voice prototype packages (pyaudio, pyttsx3, SpeechRecognition, pywin32) moved to `VENUAI/requirements-voice.txt`: they are only used by `main.py` and cannot build in a slim image.
- Not verified here: the Docker daemon was not available, so images were not built or run; `docker compose config` was validated and CI (`security.yml`) builds and scans them.
