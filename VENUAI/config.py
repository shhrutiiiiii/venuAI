"""
Startup configuration and production-hardening checks (security stream S4).

`validate_config()` is a pure function over an env mapping so it is trivially
testable; `enforce_config()` is what the server calls at start-up. In
production (ENV=production or APP_ENV=production) any *error* aborts start-up
with a ConfigError listing every problem; in dev the same findings are logged
as warnings so a laptop still boots.

S1 (auth) consumes `is_weak_secret()` / `validate_config()` rather than
re-implementing the secret-strength rule.
See Documents/Backend/http-security.md and ADR-S4.1.
"""
from __future__ import annotations

import logging
import os
from typing import List, Mapping, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("drdo-rag")

_TRUTHY = {"1", "true", "yes", "on"}
_PROD_NAMES = {"production", "prod"}

# Secrets that appear in tutorials / .env.example files; never acceptable in prod.
_WEAK_SECRET_MARKERS = (
    "changeme", "change-me", "change_me", "your-secret", "your_secret", "yoursecret",
    "secret-key", "example", "default", "password", "test-only", "not-for-prod",
    "replace", "placeholder", "dev-secret", "insecure",
)
_MIN_SECRET_LEN = 32

# Credentials that must never be configured in production.
_DEFAULT_CRED_ENV = {
    "ADMIN_PASSWORD": {"admin", "admin123", "password", "changeme", "venuai"},
    "DEMO_PASSWORD": None,   # any value is a demo credential
    "DEMO_ADMIN_PASSWORD": None,
    "DEFAULT_ADMIN_PASSWORD": None,
}


class ConfigError(RuntimeError):
    """Raised at start-up when the production configuration is unsafe."""


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in _TRUTHY


def app_env(env: Optional[Mapping[str, str]] = None) -> str:
    e = _env(env)
    v = (e.get("APP_ENV") or e.get("ENV") or "dev").strip().lower()
    return "production" if v in _PROD_NAMES else v


def is_production(env: Optional[Mapping[str, str]] = None) -> bool:
    return app_env(env) == "production"


def is_weak_secret(secret: Optional[str]) -> bool:
    """True for a missing, short, low-entropy or placeholder secret (policy: secret_policy, shared with auth)."""
    from VENUAI.secret_policy import validate_jwt_secret
    try:
        validate_jwt_secret(secret)
        return False
    except RuntimeError:
        return True


def allowed_origins(env: Optional[Mapping[str, str]] = None) -> List[str]:
    raw = _env(env).get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def docs_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """/docs, /redoc, /openapi.json: on in dev, off in production unless ENABLE_DOCS=1."""
    e = _env(env)
    flag = e.get("ENABLE_DOCS")
    if flag is not None and flag.strip() != "":
        return _truthy(flag)
    return not is_production(e)


def trusted_proxies(env: Optional[Mapping[str, str]] = None) -> List[str]:
    """IPs whose X-Forwarded-For we trust (comma list; `*` = any peer). Empty = trust none."""
    raw = _env(env).get("TRUSTED_PROXY") or _env(env).get("FORWARDED_ALLOW_IPS") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _int_env(name: str, default: int, env: Optional[Mapping[str, str]] = None) -> int:
    try:
        return max(1, int(_env(env).get(name, "")))
    except (TypeError, ValueError):
        return default


def max_body_bytes(env: Optional[Mapping[str, str]] = None) -> int:
    return _int_env("MAX_BODY_BYTES", 1 * 1024 * 1024, env)


def max_upload_bytes(env: Optional[Mapping[str, str]] = None) -> int:
    return _int_env("MAX_UPLOAD_BYTES", 30 * 1024 * 1024, env)


def docs_kwargs(env: Optional[Mapping[str, str]] = None) -> dict:
    if docs_enabled(env):
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


def validate_config(env: Optional[Mapping[str, str]] = None) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). Errors only apply in production; dev returns warnings."""
    e = _env(env)
    prod = is_production(e)
    problems: List[str] = []
    warnings: List[str] = []

    secret = e.get("JWT_SECRET_KEY")
    if not secret:
        problems.append("JWT_SECRET_KEY is not set.")
    elif is_weak_secret(secret):
        problems.append(f"JWT_SECRET_KEY is weak (need >= {_MIN_SECRET_LEN} chars of real randomness, no placeholder words).")

    origins = allowed_origins(e)
    if not origins:
        problems.append("ALLOWED_ORIGINS is empty.")
    for o in origins:
        host = (urlparse(o).hostname or o).lower()
        if o == "*" or "*" in o:
            problems.append("ALLOWED_ORIGINS must not contain a wildcard.")
        elif host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            problems.append(f"ALLOWED_ORIGINS contains a local origin ({o}).")
        elif urlparse(o).scheme != "https":
            problems.append(f"ALLOWED_ORIGINS entry {o} is not https.")

    fe = (e.get("FRONTEND_URL") or "").strip()
    if not fe:
        problems.append("FRONTEND_URL is not set.")
    elif urlparse(fe).scheme != "https":
        problems.append("FRONTEND_URL must be https.")

    for flag in ("DEBUG", "FASTAPI_DEBUG", "UVICORN_RELOAD", "RELOAD"):
        if _truthy(e.get(flag)):
            problems.append(f"Debug flag {flag} is enabled.")

    if _truthy(e.get("SEED_DEMO_USER")):
        problems.append("SEED_DEMO_USER is enabled (demo accounts must not exist in production).")
    if _truthy(e.get("NEXT_PUBLIC_SHOW_DEMO")):
        problems.append("NEXT_PUBLIC_SHOW_DEMO is enabled.")

    for name, bad in _DEFAULT_CRED_ENV.items():
        val = e.get(name)
        if val and (bad is None or val.strip().lower() in bad):
            problems.append(f"{name} is set to a default/demo credential.")

    if _truthy(e.get("ENABLE_DOCS")):
        warnings.append("ENABLE_DOCS=1: /docs, /redoc and /openapi.json are exposed.")
    if not (e.get("SMTP_HOST") or "").strip():
        warnings.append("SMTP_HOST is not set: password reset e-mail cannot be delivered.")
    if "*" in trusted_proxies(e):
        warnings.append("TRUSTED_PROXY=* trusts X-Forwarded-For from any peer; list the proxy IPs instead.")

    if prod:
        return problems, warnings
    return [], [f"(dev) {p}" for p in problems if p.startswith(("JWT", "Debug"))] + warnings


def enforce_config(env: Optional[Mapping[str, str]] = None) -> List[str]:
    """Called at start-up. Raises ConfigError in production; logs warnings otherwise."""
    errors, warnings = validate_config(env)
    for w in warnings:
        logger.warning("config: %s", w)
    if errors:
        raise ConfigError(
            "Refusing to start with an unsafe production configuration:\n  - " + "\n  - ".join(errors)
        )
    return warnings

