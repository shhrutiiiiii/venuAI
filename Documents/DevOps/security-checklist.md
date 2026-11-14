# Security Checklist — VenuAI

## Purpose
One place that maps controls to the standards the project aims at (OWASP ASVS L2, API Top 10 2023, LLM Top 10 2025), the automated checks that guard them, and the operational runbooks (secret rotation, dependency exceptions).

## Scope
Supply chain, CI scanning, containers, secrets, and the shared security test harness (stream S5). Application-level controls are owned by streams S1-S4 (auth, authorization, LLM safety, HTTP hardening); their rows are marked "owner" and the final status table is filled in after those streams merge.

## Dependency state at S5 merge
| Area | Result |
|---|---|
| npm (production) | `next` 16.3.5 -> 16.4.6 (fixes the critical next/og advisory, range 16.0.0-16.4.0); `sharp`, `source-map-js`, `brace-expansion`, `zod` fixed via `npm audit fix` / `zod ^4.6.6`. `npm audit` high/critical: 0. |
| npm (dev only) | 8 moderate advisories remain (`@typescript-eslint/*` via `eslint-config-next`); npm's only offered fix is a downgrade to `eslint-config-next@15`, which is incompatible with Next 16. Dev tooling only, not shipped. Revisit when `eslint-config-next` ships a patched `typescript-eslint`. |
| Python | `requirements.lock` (161 packages, hashes) audited with pip-audit: 4 findings, all ChromaDB *server*-mode advisories with no fixed release (PYSEC-2026-311, -3813, -3814, -3815); VenuAI uses the embedded `PersistentClient` only. Time-boxed exceptions in `VENUAI/audit-ignore.txt` (expire 2027-02-28). |

## Secret scan result (repository and history)
Tracked tree and all branches' history scanned with `scripts/security/scan_secrets.py --history` (gitleaks was not installable on the dev machine; CI runs real gitleaks over full history): no API keys, private keys or tokens found. No `.env`, `*.db`, uploads or dataset JSON have ever been committed. Test files contain synthetic fake keys (allowlisted in `.gitleaks.toml`). `.env.example` files are placeholder-only.

## Runbooks
### Rotating secrets
1. Revoke the exposed value at the provider (Groq console, OAuth app, SMTP).
2. `python scripts/security/gen_secrets.py` for `JWT_SECRET_KEY`, `MCP_API_KEY`, `POSTGRES_PASSWORD`; update the env file or secret store.
3. Redeploy. Rotating `JWT_SECRET_KEY` logs everyone out.
4. If a secret reached git history: rotate first, then rewrite history only if required; rotation is what removes the risk.

### Dependency exceptions
Add the advisory id to `VENUAI/audit-ignore.txt` (or `.trivyignore`) with `owner=` and `expires=`; the audit script fails once an exception expires. Prefer upgrading.

### Updating the Python lock
`bash scripts/security/update_lock.sh`, review the diff, run the full test suite.

## Automated checks
| Check | Where | Fails on |
|---|---|---|
| pip-audit | `security.yml` / `scripts/security/audit_python.sh` | any vuln not in `audit-ignore.txt`, expired exceptions |
| npm audit | `security.yml` | high/critical in production deps |
| Bandit | `security.yml`, `.bandit` | medium+ findings |
| gitleaks (history) | `security.yml`, pre-commit | any secret |
| CodeQL (python, js/ts) | `security.yml` | alerts surface in the Security tab |
| Trivy fs + images, SBOM | `security.yml` | fixed HIGH/CRITICAL |
| Compose policy | `scripts/security/check_compose.sh` | published 8000/5432, docker socket, default DB password |
| Image policy | `scripts/security/check_images.sh` | root user, no HEALTHCHECK |
| Repo hygiene tests | `VENUAI/tests/test_sec_supply_chain.py` (`pytest -m security`) | tracked secrets/DBs, unpinned deps, Docker/compose policy, unpinned actions |
| Header smoke | `scripts/security/smoke_headers.sh` | missing X-Content-Type-Options / X-Frame-Options / Referrer-Policy / HSTS (https) |
| ZAP baseline | `scripts/security/zap-baseline.yml` | non-blocking initially |

## Test harness for other streams
`from VENUAI.tests.security.fixtures import *` gives `two_tenants` (alice and carol in tenant A, bob in tenant B, an admin; each with `.headers`), `sec_client`, `make_token`, `forged_tokens` (alg=none, wrong secret, expired, wrong type, garbage) and `evil_origin`. Mark tests with `pytest.mark.security`; CI runs `pytest -m security`.

## Standards map (status updated after S1-S4 merge)
| Standard item | Control | Owner | Status |
|---|---|---|---|
| ASVS V14 Config / API8 Misconfiguration | Hardened images, prod compose, no published DB/API ports, prod config validation | S5 / S4 | containers done; app config S4 |
| ASVS V14.2 Dependencies / LLM03 Supply chain | Hash-pinned lock, audits, Dependabot, SHA-pinned actions, SBOM | S5 | done |
| ASVS V2/V3 Auth, sessions / API2 | JWT, refresh, rate limits | S1 | see S1 |
| ASVS V4 Access control / API1, API5 | Tenant isolation, admin routes | S2 | see S2 |
| LLM01, LLM05, LLM06 | Prompt injection, output handling, agency | S3 | see S3 |
| ASVS V7 Logging / LLM02 | Log redaction | S4 | see S4 |
| ASVS V8 Data protection | Secrets in env only, gitignored data, `.dockerignore` | S5 | done |

## Notes
- Docker images were not built on the dev machine (no daemon); CI builds and scans them.
- Base images are tag-pinned; run `scripts/security/pin_images.sh` to digest-pin before a production rollout.
