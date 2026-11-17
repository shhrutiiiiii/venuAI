# Security Checklist — VenuAI

## Purpose
One place that maps controls to the standards the project aims at (OWASP ASVS L2, API Top 10 2023, LLM Top 10 2025), the automated checks that guard them, and the operational runbooks (secret rotation, dependency exceptions).

## Scope
Supply chain, containers, secrets, and the shared security test harness (stream S5). Application-level controls are owned by streams S1-S4 (auth, authorization, LLM safety, HTTP hardening); their rows are marked "owner" and the final status table is filled in after those streams merge.

## Dependency state at S5 merge
| Area | Result |
|---|---|
| npm (production) | `next` 16.3.5 -> 16.4.6 (fixes the critical next/og advisory, range 16.0.0-16.4.0); `sharp`, `source-map-js`, `brace-expansion`, `zod` fixed via `npm audit fix` / `zod ^4.6.6`. `npm audit` high/critical: 0. |
| npm (dev only) | 8 moderate advisories remain (`@typescript-eslint/*` via `eslint-config-next`); npm's only offered fix is a downgrade to `eslint-config-next@15`, which is incompatible with Next 16. Dev tooling only, not shipped. Revisit when `eslint-config-next` ships a patched `typescript-eslint`. |
| Python | `requirements.lock` (161 packages, hashes) audited with pip-audit: 4 findings, all ChromaDB *server*-mode advisories with no fixed release (PYSEC-2026-311, -3813, -3814, -3815); VenuAI uses the embedded `PersistentClient` only. The exception list file was removed together with the CI scanners. |

## Secret scan result (repository and history)
Tracked tree and all branches' history scanned once with a local pattern scan (all branches' history): no API keys, private keys or tokens found. No `.env`, `*.db`, uploads or dataset JSON have ever been committed. Test files contain synthetic fake keys. `.env.example` files are placeholder-only.

## Runbooks
### Rotating secrets
1. Revoke the exposed value at the provider (Groq console, OAuth app, SMTP).
2. `python -c "import secrets; print(secrets.token_urlsafe(48))"` for `JWT_SECRET_KEY`, `MCP_API_KEY`, `POSTGRES_PASSWORD`; update the env file or secret store.
3. Redeploy. Rotating `JWT_SECRET_KEY` logs everyone out.
4. If a secret reached git history: rotate first, then rewrite history only if required; rotation is what removes the risk.

### Updating the Python lock
`uv pip compile --universal --generate-hashes VENUAI/requirements.in -o VENUAI/requirements.lock`, review the diff, run the full test suite.

## Automated checks
| Check | Where | Fails on |
|---|---|---|
| Repo hygiene tests | `VENUAI/tests/test_sec_supply_chain.py` (`pytest -m security`) | tracked secrets/DBs, unpinned deps, Docker/compose policy, unpinned actions |

> Note: GitHub-side scanning (the `security.yml` workflow with pip-audit, npm audit, Bandit, gitleaks, CodeQL and Trivy, plus Dependabot, `SECURITY.md` and the helper scripts under `scripts/security/`) was added in stream S5 and later **removed by owner decision**. Run dependency audits manually (`pip-audit`, `npm audit`) when needed.

## Test harness for other streams
`from VENUAI.tests.security.fixtures import *` gives `two_tenants` (alice and carol in tenant A, bob in tenant B, an admin; each with `.headers`), `sec_client`, `make_token`, `forged_tokens` (alg=none, wrong secret, expired, wrong type, garbage) and `evil_origin`. Mark tests with `pytest.mark.security`; CI runs `pytest -m security`.

## Standards map (status after S1-S5 merge)
| Standard item | Control | Owner | Status |
|---|---|---|---|
| ASVS V14 Config / API8 Misconfiguration | Hardened images, prod compose, no published DB/API ports; `config.validate_config` fails fast in production (JWT secret policy shared with auth via `secret_policy.py`, CORS, docs off, proxy trust) | S5 / S4 | done |
| ASVS V14.2 Dependencies / LLM03 Supply chain | Hash-pinned lock, SHA-pinned actions | S5 | done (lock not regenerated at merge: no new direct deps were introduced) |
| ASVS V2/V3 Auth, sessions / API2 | Refresh-token families + reuse detection, uniform login failures, lockout, password policy (signup, reset, tenant member creation), OAuth hardening, CSRF (`src/proxy.ts` + `lib/csrf.ts`), append-only audit log, trusted client IP | S1 | done |
| ASVS V4 Access control / API1, API5 | One `authz` module, 404 for non-visible resources, tenant in every memory/retrieval key, tenant-admin routes, one invite implementation (auth.tenant_invites) shared with registration, deny-by-default tool policy, `/health` minimal + `/health/details` admin | S2 | done |
| LLM01, LLM05, LLM06 | Bounded + structurally scanned uploads, decompression-bomb defence, SSRF gate, MCP command allowlist, hardened sandbox gated by `ENABLE_CODE_EXECUTION`, request bounds, output sanitising | S3 | done |
| ASVS V7 Logging / LLM02 | Log redaction, request ids, safe error bodies (no input echo; single 422 handler) | S4 | done |
| ASVS V8 Data protection | Secrets in env only, gitignored data, `.dockerignore` | S5 | done |
| API4 Resource consumption | Global/per-IP/per-user and daily LLM-cost rate limits, body limits (1 MB; uploads up to `MAX_UPLOAD_BYTES`, 30 MB) | S4 | done |
| Browser hardening | Baseline headers, CSP Report-Only (enforce with `CSP_ENFORCE=1`), HSTS over https | S4 | CSP not yet enforcing |

## Known follow-ups
- Flip CSP to enforcing after reviewing `/api/csp-report` logs.
- Regenerate `requirements.lock` (`uv pip compile --universal --generate-hashes VENUAI/requirements.in -o VENUAI/requirements.lock`) whenever `requirements.in` changes.
- No email delivery: invites and password resets are handed over out-of-band.
