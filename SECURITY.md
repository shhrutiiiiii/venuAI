# Security Policy

## Supported versions

VenuAI is developed on a single trunk. Security fixes land on `main` and are
released from there; only the latest commit on `main` is supported.

| Version | Supported |
|---|---|
| `main` (latest) | yes |
| anything older | no |

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

1. Use GitHub's private vulnerability reporting ("Security" tab, "Report a
   vulnerability") on this repository, or email the maintainers at the address
   listed on the repository profile.
2. Include: affected component and version/commit, reproduction steps or a
   proof of concept, impact, and any suggested fix. Do not include real user data.
3. Do not access, modify or delete data that is not yours, and do not run
   denial-of-service or social-engineering tests against deployed instances.

### What to expect

| Step | Target |
|---|---|
| Acknowledgement | within 3 business days |
| Initial assessment and severity | within 7 days |
| Fix or mitigation for critical/high | within 30 days |
| Public disclosure | after a fix is available, coordinated with the reporter |

We credit reporters in the release notes unless they prefer to remain anonymous.
Good-faith research that follows this policy will not be pursued legally.

## Scope

In scope: the FastAPI backend (`VENUAI/`), the Next.js frontend (`chat-app/`),
the container images and compose files, and the CI configuration.
Out of scope: vulnerabilities in third-party services (Groq, Google/GitHub
OAuth) and findings that require a compromised host or physical access.

## Secrets

Never commit credentials. `.env` files are gitignored; `gitleaks` runs in CI over
the full history and as a pre-commit hook. If a secret is exposed: revoke it at
the provider first, then rotate (`python scripts/security/gen_secrets.py`) and
redeploy. See [Documents/DevOps/security-checklist.md](Documents/DevOps/security-checklist.md).

## Automated controls

Dependency audits (pip-audit, npm audit), Bandit, CodeQL, Trivy (filesystem and
image), gitleaks and Dependabot run from `.github/workflows/security.yml` and
`.github/dependabot.yml`.
