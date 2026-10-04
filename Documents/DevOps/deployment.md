> Entry point for the `DevOps/` documentation folder.

# Deployment — VenuAI

## Purpose
States how the system actually runs today (manually, locally) versus what's needed for any real deployment.

## Responsibilities
Describe the current run model and the concrete gaps blocking containerized/hosted deployment.

## Scope
Covers run/deploy topology only. Monitoring detail is in [`monitoring.md`](monitoring.md); container detail in [`docker.md`](docker.md).

## High-Level Overview
**Current state: containerized locally, not deployed anywhere.** Two run modes now exist:

Manual (no Docker):
```
Terminal 1:  uvicorn rag_server:app --host 0.0.0.0 --port 8000   (or `python rag_server.py`)
Terminal 2:  npm run dev   (Next.js dev server, chat-app/, port 3000)
```

Containerized (verified to build and pass health checks — see [`docker.md`](docker.md)):
```
docker compose up   # builds + runs backend and frontend together, with persistent volumes
```
There is still no CI/CD deployment step and no hosting configuration (cloud provider, DNS, TLS termination) anywhere in this repository — `docker-compose.yml` runs locally only.

## Design Principles
1. Backend and frontend are independently buildable/deployable containers — done, see [`docker.md`](docker.md).
2. Secrets (`GROQ_API_KEY`, `JWT_SECRET_KEY`) must come from the hosting platform's secret store in any real deployment, never a committed `.env` — local `docker-compose.yml` still reads `VENUAI/.env` via `env_file`, which is fine for local/dev use only.
3. Local-disk stores (ChromaDB, SQLite, uploaded PDFs, JSON metadata) are mounted to named volumes in `docker-compose.yml` — done, see [`../Database/architecture.md`](../Database/architecture.md).

## Workflow
N/A — no deployment pipeline exists to diagram.

## Inputs / Outputs
N/A.

## Dependencies
- [`docker.md`](docker.md) — the containerization itself.
- [`monitoring.md`](monitoring.md) — proposed observability.
- [`../Database/architecture.md`](../Database/architecture.md) — what must be persisted across deploys.

## Interactions with Other Services
N/A today.

## Security Considerations
Running with `--host 0.0.0.0` on the backend exposes it on every network interface — fine for local dev, must be firewalled or placed behind a reverse proxy before any shared/cloud environment.

## Performance Considerations
N/A until a real deployment target is chosen.

## Future Improvements
1. Choose an actual hosting target (cloud VM, managed container service, PaaS) and wire a real deploy step — nothing here deploys anywhere yet, it only builds and runs locally.
2. Push built images to a registry as part of CI (`.github/workflows/ci.yml` currently tests/builds but does not push or deploy).
3. Pin base images to digests, not just tags, for production reproducibility — see [`docker.md`](docker.md) Security Considerations.
4. Move secrets to the hosting platform's secret manager once a real target is chosen.

## Notes for Developers
The local-disk-store gap is now closed for the containerized path (named volumes persist across `docker compose` restarts) — but still applies if anyone runs the manual (non-Docker) mode without separately backing up `VENUAI/chroma_db/`, `VENUAI/data/`, and `VENUAI/uploaded_docs/`.
