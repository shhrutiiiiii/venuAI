"""
DRDO VenuAI — Authentication & Authorization
=============================================
Real, server-side authentication replacing the previous frontend-only mock.

Design:
  - Users persisted in a local SQLite database (VENUAI/data/users.db).
  - Passwords hashed with bcrypt via passlib (never stored in plaintext).
  - Access tokens: short-lived JWTs (HS256), signed with JWT_SECRET_KEY.
  - Refresh tokens: opaque random strings, hashed at rest, stored with an
    expiry and a revoked flag so logout / rotation actually invalidates them
    (a JWT alone cannot be revoked before expiry; the refresh token can).
  - FastAPI dependencies (`get_current_user`, `require_admin`) enforce auth
    on routes — see rag_server.py for where these are applied.

This module intentionally has zero dependency on the RAG pipeline so it can
be tested and reasoned about in isolation.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

from VENUAI import security_audit

logger = logging.getLogger("drdo-rag")

# ── Configuration ──────────────────────────────────────────────────────────
# Loaded independently of rag_server.py so this module is self-contained and
# testable/importable on its own (rag_server.py's load_dotenv() is a no-op
# once this has already run in the same process). Explicit path so it works
# regardless of the process's current working directory.
load_dotenv(Path(__file__).parent / ".env")

_WEAK_SECRET_MARKERS = ("changeme", "generate", "your-secret", "your_secret", "placeholder", "example")
_WEAK_SECRETS = {"secret", "password", "default", "jwt_secret", "jwtsecret", "test", "dev"}


def validate_jwt_secret(secret: Optional[str]) -> str:
    """Return the stripped secret or raise RuntimeError. The server refuses to
    boot with a missing, short (<32 bytes), placeholder or low-entropy secret."""
    s = (secret or "").strip()
    if not s:
        raise RuntimeError(
            "JWT_SECRET_KEY not set. Generate one with `python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"` and put it in your .env — the "
            "server refuses to start with an insecure/default secret."
        )
    low = s.lower()
    if (
        len(s.encode()) < 32
        or low.startswith("<")
        or low in _WEAK_SECRETS
        or any(m in low for m in _WEAK_SECRET_MARKERS)
        or len(set(s)) < 8
    ):
        raise RuntimeError(
            "JWT_SECRET_KEY is weak or a placeholder (need >= 32 bytes of real randomness). "
            "Generate one with `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )
    return s


JWT_SECRET_KEY = validate_jwt_secret(os.getenv("JWT_SECRET_KEY"))
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "venuai"
JWT_AUDIENCE = "venuai-api"
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "1800"))       # 30 min
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", "1209600"))  # 14 days
# A second use of a just-rotated refresh token within this window is treated as a
# benign concurrent refresh (two tabs): rejected, but the family is NOT revoked.
REFRESH_REUSE_GRACE_SECONDS = float(os.getenv("REFRESH_REUSE_GRACE_SECONDS", "5"))


def _derive_key(purpose: str) -> bytes:
    """Per-purpose signing key: a token minted for one purpose can never verify for another."""
    return hmac.new(JWT_SECRET_KEY.encode(), f"venuai:{purpose}".encode(), hashlib.sha256).digest()


def is_production() -> bool:
    return (os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower() in ("production", "prod")


BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "users.db"

BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))
if is_production() and BCRYPT_ROUNDS < 12:
    raise RuntimeError("BCRYPT_ROUNDS must be >= 12 in production.")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=BCRYPT_ROUNDS)
_bearer = HTTPBearer(auto_error=False)

VALID_ROLES = {"user", "admin"}
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # first lockout; doubles per consecutive lockout, capped below
LOGIN_LOCKOUT_MAX_SECONDS = 3600
GENERIC_LOGIN_ERROR = "Invalid email or password."


# ── Schema ──────────────────────────────────────────────────────────────────
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        # Phase 4 — Multi-Tenancy. One tenant per organization; every user
        # belongs to exactly one. Existing single-tenant deployments are
        # unaffected: every pre-Phase-4 user/resource is assigned to the
        # "default" tenant (seeded below), so current behavior is preserved
        # exactly unless a second tenant is actually created. See
        # Documents/Backend/multi-tenancy.md.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id       TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                created_at      REAL NOT NULL,
                max_users       INTEGER NOT NULL DEFAULT 20,
                max_documents   INTEGER NOT NULL DEFAULT 100,
                max_datasets    INTEGER NOT NULL DEFAULT 50
            )
            """
        )
        # Pre-quota tenants.db files won't have these columns yet.
        for _col, _default in (("max_users", 20), ("max_documents", 100), ("max_datasets", 50)):
            try:
                conn.execute(f"ALTER TABLE tenants ADD COLUMN {_col} INTEGER NOT NULL DEFAULT {_default}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id              TEXT PRIMARY KEY,
                email           TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                role            TEXT NOT NULL DEFAULT 'user',
                tenant_id       TEXT NOT NULL DEFAULT 'default',
                created_at      REAL NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until    REAL,
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
            )
            """
        )
        # Pre-Phase-4 database files won't have tenant_id — add it if missing
        # (SQLite ALTER TABLE ADD COLUMN is safe/idempotent to attempt-and-catch).
        try:
            conn.execute("ALTER TABLE users ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                token_hash  TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                expires_at  REAL NOT NULL,
                revoked     INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        # S1 hardening migrations (idempotent ALTER-and-catch).
        for _stmt in (
            "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN lockout_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE refresh_tokens ADD COLUMN family_id TEXT",
            "ALTER TABLE refresh_tokens ADD COLUMN parent_hash TEXT",
            "ALTER TABLE refresh_tokens ADD COLUMN used_at REAL",
        ):
            try:
                conn.execute(_stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_family ON refresh_tokens(family_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token_hash     TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                pw_fingerprint TEXT NOT NULL,
                expires_at     REAL NOT NULL,
                used_at        REAL,
                created_at     REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_identities (
                provider   TEXT NOT NULL,
                sub        TEXT NOT NULL,
                user_id    TEXT NOT NULL,
                email      TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY (provider, sub)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state_hash    TEXT PRIMARY KEY,
                provider      TEXT NOT NULL,
                binding_hash  TEXT NOT NULL,
                code_verifier TEXT NOT NULL,
                redirect_uri  TEXT NOT NULL,
                expires_at    REAL NOT NULL,
                used_at       REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenant_invites (
                token_hash TEXT PRIMARY KEY,
                tenant_id  TEXT NOT NULL,
                role       TEXT NOT NULL DEFAULT 'user',
                email      TEXT,
                created_by TEXT,
                expires_at REAL NOT NULL,
                used_at    REAL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at) VALUES ('default', 'Default Organization', ?)",
            (time.time(),),
        )
        conn.commit()


init_db()




# ── Models ──────────────────────────────────────────────────────────────────
class UserOut(BaseModel):
    id: str
    email: EmailStr
    role: str
    tenant_id: str = "default"


class TenantOut(BaseModel):
    tenant_id: str
    name: str
    max_users: int = 20
    max_documents: int = 100
    max_datasets: int = 50


class TenantUsageOut(BaseModel):
    tenant_id: str
    name: str
    user_count: int
    max_users: int
    document_count: int
    max_documents: int
    dataset_count: int
    max_datasets: int


class UpdateTenantRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    max_users: Optional[int] = Field(default=None, ge=1, le=10_000)
    max_documents: Optional[int] = Field(default=None, ge=1, le=100_000)
    max_datasets: Optional[int] = Field(default=None, ge=1, le=100_000)


class AddTenantMemberRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = "user"


class RegisterRequest(BaseModel):
    email: EmailStr
    # Length/strength are enforced by enforce_password_policy() for a clear message.
    password: str = Field(min_length=1, max_length=128)
    # If omitted, a brand-new tenant is created for this user (the "first
    # user creates an org" pattern most B2B SaaS products use). If provided,
    # the user joins that existing tenant — see create_user()'s docstring
    # for what's deliberately NOT built here (invite/approval flow).
    organization_name: Optional[str] = Field(default=None, max_length=200)
    # Joining an existing tenant requires an invite token (see create_invite);
    # a bare tenant_id is never sufficient.
    tenant_id: Optional[str] = Field(default=None, max_length=64)
    invite_token: Optional[str] = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    # Login keeps the legacy min length so pre-policy accounts can still sign in.
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=128)


@dataclass
class AuthenticatedUser:
    id: str
    email: str
    role: str
    tenant_id: str = "default"


# ── Password hashing ─────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(password, hashed)
    except ValueError:  # e.g. >72-byte input rejected by newer bcrypt builds
        return False


_DUMMY_HASH: Optional[str] = None


def _dummy_verify(password: str) -> None:
    """Burn a bcrypt verify so unknown/locked accounts cost the same as real ones."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = pwd_context.hash("venuai-timing-equaliser-" + secrets.token_hex(8))
    verify_password(password, _DUMMY_HASH)


# ── Password policy (signup + reset only; login keeps the legacy minimum) ────
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_BYTES = 72  # bcrypt silently truncates beyond this -> reject instead
_COMMON_PASSWORDS = {
    "password", "password1", "password12", "password123", "password1234", "passw0rd1234",
    "123456789012", "1234567890123", "qwertyuiop12", "qwerty123456", "qwertyuiopas",
    "iloveyou1234", "letmein12345", "welcome12345", "administrator", "admin1234567",
    "admin12345678", "changeme1234", "abcdefghijkl", "abc123456789", "111111111111",
    "000000000000", "123123123123", "demo12345678", "p@ssw0rd1234", "monkey123456",
    "dragon123456", "football1234", "baseball1234", "trustno1trustno1",
}


def password_policy_problems(password: str, email: Optional[str] = None) -> List[str]:
    problems: List[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        problems.append(f"at least {PASSWORD_MIN_LENGTH} characters")
    if len(password.encode()) > PASSWORD_MAX_BYTES:
        problems.append(f"at most {PASSWORD_MAX_BYTES} bytes")
    low = password.lower()
    if low in _COMMON_PASSWORDS or len(set(password)) < 5:
        problems.append("not a common or repetitive password")
    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 4 and local in low:
            problems.append("must not contain your email name")
    return problems


def enforce_password_policy(password: str, email: Optional[str] = None) -> None:
    problems = password_policy_problems(password, email)
    if problems:
        raise HTTPException(status_code=422, detail="Password must be " + "; ".join(problems) + ".")


# ── Tenant store ──────────────────────────────────────────────────────────
def create_tenant(name: str, tenant_id: Optional[str] = None) -> TenantOut:
    tid = tenant_id or secrets.token_hex(12)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO tenants (tenant_id, name, created_at) VALUES (?, ?, ?)",
            (tid, name, time.time()),
        )
        conn.commit()
    return TenantOut(tenant_id=tid, name=name)


def get_tenant(tenant_id: str) -> Optional[sqlite3.Row]:
    with _get_conn() as conn:
        return conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()


def update_tenant(
    tenant_id: str, name: Optional[str] = None, max_users: Optional[int] = None,
    max_documents: Optional[int] = None, max_datasets: Optional[int] = None,
) -> TenantOut:
    """Admin-only rename/quota update (Phase 4 tenant management — see
    Documents/Backend/multi-tenancy.md). Only the fields actually passed
    are changed; omitted fields keep their current value. Raises 404 if
    the tenant doesn't exist — should not happen for a real admin's own
    tenant, but this is cheap insurance against calling it with a stale id."""
    current = get_tenant(tenant_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    with _get_conn() as conn:
        conn.execute(
            "UPDATE tenants SET name = ?, max_users = ?, max_documents = ?, max_datasets = ? WHERE tenant_id = ?",
            (
                name if name is not None else current["name"],
                max_users if max_users is not None else current["max_users"],
                max_documents if max_documents is not None else current["max_documents"],
                max_datasets if max_datasets is not None else current["max_datasets"],
                tenant_id,
            ),
        )
        conn.commit()
    row = get_tenant(tenant_id)
    return TenantOut(
        tenant_id=row["tenant_id"], name=row["name"], max_users=row["max_users"],
        max_documents=row["max_documents"], max_datasets=row["max_datasets"],
    )


def get_tenant_user_count(tenant_id: str) -> int:
    return len(get_user_ids_in_tenant(tenant_id))


# ── User store ────────────────────────────────────────────────────────────
def create_user(
    email: str, password: str, role: str = "user",
    tenant_id: str = "default", organization_name: Optional[str] = None,
) -> UserOut:
    """
    `organization_name` set (and `tenant_id` left as the caller's default)
    creates a brand-new tenant for this user and assigns them to it — the
    "first user creates an org" signup pattern. Passing an explicit
    `tenant_id` instead joins that existing tenant directly, with no
    invite/approval step — a real, deliberate simplification; see
    Documents/Backend/multi-tenancy.md "Not Yet Implemented" for what a
    production invite flow would add on top of this.
    """
    if role not in VALID_ROLES:
        role = "user"
    if organization_name:
        new_tenant = create_tenant(organization_name)
        tenant_id = new_tenant.tenant_id
    else:
        existing_tenant = get_tenant(tenant_id)
        if tenant_id != "default" and existing_tenant is None:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' does not exist.")
        # Quota check only applies when *joining* an existing tenant — a
        # brand-new org (the `organization_name` branch above) trivially
        # has room for its first member. See
        # Documents/Backend/multi-tenancy.md "Quotas".
        existing_tenant = existing_tenant or get_tenant(tenant_id)
        if existing_tenant is not None:
            max_users = existing_tenant["max_users"]
            if get_tenant_user_count(tenant_id) >= max_users:
                raise HTTPException(
                    status_code=403,
                    detail=f"Tenant '{tenant_id}' has reached its member limit ({max_users}).",
                )

    user_id = secrets.token_hex(16)
    hashed = hash_password(password)
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO users (id, email, hashed_password, role, tenant_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, email.lower(), hashed, role, tenant_id, time.time()),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return UserOut(id=user_id, email=email, role=role, tenant_id=tenant_id)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with _get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower(),)
        ).fetchone()


def get_user_by_id(user_id: str) -> Optional[sqlite3.Row]:
    with _get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_ids_in_tenant(tenant_id: str) -> List[str]:
    """Used by the Security Center (GET /security/violations) to scope a
    cross-database join: guardrail_logs (VENUAI/guardrails.py) has no
    tenant_id of its own, only user_id, so the caller filters by this list
    instead. See Documents/Backend/multi-tenancy.md."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT id FROM users WHERE tenant_id = ?", (tenant_id,)).fetchall()
    return [r["id"] for r in rows]


def list_tenant_members(tenant_id: str) -> List[UserOut]:
    """Backs GET /tenants/members (Phase 4 tenant management UI)."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM users WHERE tenant_id = ?", (tenant_id,)).fetchall()
    return [UserOut(id=r["id"], email=r["email"], role=r["role"], tenant_id=r["tenant_id"]) for r in rows]


def _lockout_duration(lockout_count: int) -> int:
    """Exponential backoff: 5 min, 10 min, 20 min ... capped at 1 h."""
    return min(LOGIN_LOCKOUT_SECONDS * (2 ** max(lockout_count - 1, 0)), LOGIN_LOCKOUT_MAX_SECONDS)


def record_failed_login(email: str, ip: Optional[str] = None) -> None:
    locked_until = None
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, tenant_id, failed_attempts, locked_until, lockout_count FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
        if row is None:
            return
        # Failures while already locked must not extend or escalate the lock.
        if row["locked_until"] and time.time() < row["locked_until"]:
            return
        attempts = row["failed_attempts"] + 1
        lockout_count = row["lockout_count"]
        if attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            lockout_count += 1
            locked_until = time.time() + _lockout_duration(lockout_count)
            attempts = 0
        conn.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ?, lockout_count = ? WHERE email = ?",
            (attempts, locked_until, lockout_count, email.lower()),
        )
        conn.commit()
    if locked_until:
        security_audit.log("account_locked", row["id"], row["tenant_id"], ip, {"seconds": int(locked_until - time.time())})


def reset_failed_login(email: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL, lockout_count = 0 WHERE email = ?",
            (email.lower(),),
        )
        conn.commit()


def is_locked(user_row: sqlite3.Row) -> bool:
    locked_until = user_row["locked_until"]
    return bool(locked_until) and time.time() < locked_until


def authenticate(email: str, password: str, ip: Optional[str] = None) -> sqlite3.Row:
    """Verify credentials. Every failure (unknown email, wrong password, locked
    account) raises the identical 401 and costs one bcrypt verify, so neither
    the message nor the timing reveals which case it was. Lockout is checked
    BEFORE the password, so the correct password cannot bypass a lock."""
    row = get_user_by_email(email)
    if row is None:
        _dummy_verify(password)
        security_audit.log("login_failed", None, None, ip, {"reason": "unknown_email"})
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    if is_locked(row):
        _dummy_verify(password)
        security_audit.log("login_blocked_locked", row["id"], row["tenant_id"], ip)
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    if not verify_password(password, row["hashed_password"]):
        record_failed_login(email, ip)
        security_audit.log("login_failed", row["id"], row["tenant_id"], ip, {"reason": "bad_password"})
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    reset_failed_login(email)
    if pwd_context.needs_update(row["hashed_password"]):
        with _get_conn() as conn:  # rehash-on-login: upgrade old cost factors transparently
            conn.execute("UPDATE users SET hashed_password = ? WHERE id = ?", (hash_password(password), row["id"]))
            conn.commit()
    security_audit.log("login_ok", row["id"], row["tenant_id"], ip)
    return row


# ── Registration / invites ───────────────────────────────────────────────────
INVITE_TTL_SECONDS = 7 * 24 * 3600


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(
    tenant_id: str, created_by: Optional[str] = None, role: str = "user",
    email: Optional[str] = None, ttl_seconds: int = INVITE_TTL_SECONDS,
) -> str:
    """Mint a single-use invite for `tenant_id` and return the raw token (shown
    once; only its hash is stored). Called by the admin invite route (S2)."""
    if role not in VALID_ROLES:
        role = "user"
    raw = secrets.token_urlsafe(32)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO tenant_invites (token_hash, tenant_id, role, email, created_by, expires_at, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_hash_token(raw), tenant_id, role, email.lower() if email else None, created_by,
             time.time() + ttl_seconds, time.time()),
        )
        conn.commit()
    return raw


def _consume_invite(raw: str, email: str) -> Optional[sqlite3.Row]:
    """Atomically mark an invite used. None if unknown/expired/used/email-mismatch."""
    h = _hash_token(raw)
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM tenant_invites WHERE token_hash = ?", (h,)).fetchone()
        if row is None or row["used_at"] is not None or row["expires_at"] < time.time():
            return None
        if row["email"] and row["email"] != email.lower():
            return None
        cur = conn.execute(
            "UPDATE tenant_invites SET used_at = ? WHERE token_hash = ? AND used_at IS NULL", (time.time(), h)
        )
        conn.commit()
        return row if cur.rowcount == 1 else None


def _release_invite(raw: str) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE tenant_invites SET used_at = NULL WHERE token_hash = ?", (_hash_token(raw),))
        conn.commit()


def register_user(
    email: str, password: str, organization_name: Optional[str] = None,
    tenant_id: Optional[str] = None, invite_token: Optional[str] = None, ip: Optional[str] = None,
) -> UserOut:
    """Self-service signup. Applies the password policy; creates a personal
    tenant unless a valid invite is presented. A bare `tenant_id` never joins
    an existing tenant."""
    enforce_password_policy(password, email)
    if get_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    if invite_token or tenant_id:
        invite = _consume_invite(invite_token, email) if invite_token else None
        if invite is None or (tenant_id and tenant_id != invite["tenant_id"]):
            security_audit.log("register_denied_tenant_join", None, tenant_id, ip, {"email": email.lower()})
            raise HTTPException(status_code=403, detail="Joining an existing organization requires a valid invite.")
        try:
            user = create_user(email, password, role=invite["role"], tenant_id=invite["tenant_id"])
        except HTTPException:
            _release_invite(invite_token)
            raise
        security_audit.log("register_with_invite", user.id, user.tenant_id, ip)
        return user

    name = organization_name or f"{email.split('@')[0]}'s workspace"
    user = create_user(email, password, role="user", organization_name=name)
    security_audit.log("register", user.id, user.tenant_id, ip)
    return user


# ── JWT access tokens ─────────────────────────────────────────────────────────
def create_access_token(user_id: str, role: str, token_version: Optional[int] = None) -> str:
    if token_version is None:
        row = get_user_by_id(user_id)
        token_version = row["token_version"] if row is not None else 0
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "tv": token_version,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "jti": secrets.token_hex(8),
        "type": "access",
    }
    return jwt.encode(payload, _derive_key("access"), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token, _derive_key("access"), algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "jti", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid access token.")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type.")
    return payload


def bump_token_version(user_id: str) -> None:
    """Instantly invalidates every outstanding access token for the user."""
    with _get_conn() as conn:
        conn.execute("UPDATE users SET token_version = token_version + 1 WHERE id = ?", (user_id,))
        conn.commit()


def set_user_role(user_id: str, role: str, actor_id: Optional[str] = None, ip: Optional[str] = None) -> None:
    """Change a role; kills the user's access + refresh tokens so the old role
    cannot linger in a live JWT. (S2's member-role route should call this.)"""
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role.")
    row = get_user_by_id(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found.")
    with _get_conn() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()
    bump_token_version(user_id)
    revoke_all_refresh_tokens(user_id)
    security_audit.log("role_change", user_id, row["tenant_id"], ip, {"from": row["role"], "to": role, "actor": actor_id})


# ── Password reset (single-use, hashed at rest, bound to current password) ──
PASSWORD_RESET_TTL_SECONDS = 15 * 60


def _pw_fingerprint(hashed_password: str) -> str:
    return hashlib.sha256(hashed_password.encode()).hexdigest()


def create_password_reset_token(user_id: str) -> str:
    """Random 32-byte token; only its hash is stored. Issuing a new one voids
    every older unused token for the user. Bound to the CURRENT password hash,
    so it dies the moment the password changes by any route."""
    row = get_user_by_id(user_id)
    if row is None:
        raise ValueError("unknown user")
    raw = secrets.token_urlsafe(32)
    now = time.time()
    with _get_conn() as conn:
        conn.execute("UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now, user_id))
        conn.execute(
            "INSERT INTO password_reset_tokens (token_hash, user_id, pw_fingerprint, expires_at, created_at) VALUES (?,?,?,?,?)",
            (_hash_token(raw), user_id, _pw_fingerprint(row["hashed_password"]), now + PASSWORD_RESET_TTL_SECONDS, now),
        )
        conn.commit()
    return raw


def verify_password_reset_token(token: str) -> Optional[str]:
    """Non-consuming check: user_id if the token is currently valid, else None."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM password_reset_tokens WHERE token_hash = ?", (_hash_token(token),)).fetchone()
    if row is None or row["used_at"] is not None or row["expires_at"] < time.time():
        return None
    user = get_user_by_id(row["user_id"])
    if user is None or _pw_fingerprint(user["hashed_password"]) != row["pw_fingerprint"]:
        return None
    return row["user_id"]


def consume_password_reset_token(token: str) -> Optional[str]:
    """Atomically spend the token (single use). Returns user_id or None."""
    h = _hash_token(token)
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            (time.time(), h, time.time()),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        row = conn.execute("SELECT * FROM password_reset_tokens WHERE token_hash = ?", (h,)).fetchone()
    user = get_user_by_id(row["user_id"])
    if user is None or _pw_fingerprint(user["hashed_password"]) != row["pw_fingerprint"]:
        return None
    return row["user_id"]


def update_password(user_id: str, new_password: str) -> None:
    """Sets a new password; kills all access tokens (tv bump), all refresh
    tokens, pending reset tokens, and clears any lockout."""
    hashed = hash_password(new_password)
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET hashed_password = ?, token_version = token_version + 1, "
            "failed_attempts = 0, locked_until = NULL, lockout_count = 0 WHERE id = ?",
            (hashed, user_id),
        )
        conn.execute("UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (time.time(), user_id))
        conn.commit()
    revoke_all_refresh_tokens(user_id)


# ── Refresh tokens (opaque, hashed at rest, families + reuse detection) ──────
def issue_refresh_token(user_id: str, family_id: Optional[str] = None, parent_hash: Optional[str] = None) -> str:
    raw = secrets.token_urlsafe(48)
    h = _hash_token(raw)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, created_at, family_id, parent_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (h, user_id, time.time() + REFRESH_TOKEN_TTL_SECONDS, time.time(), family_id or secrets.token_hex(16), parent_hash),
        )
        conn.commit()
    return raw


def rotate_refresh_token(raw_token: str, ip: Optional[str] = None) -> tuple[str, str]:
    """Atomic rotation. Presenting an already-rotated token (replay) revokes the
    whole family — a thief and the victim both lose the session — and 401s.
    BEGIN IMMEDIATE serialises concurrent rotations: exactly one wins."""
    token_hash = _hash_token(raw_token)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    reuse = None
    user_row = None
    new_raw = ""
    invalid = HTTPException(status_code=401, detail="Refresh token invalid or expired.")
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise invalid
        now = time.time()
        if row["used_at"] is not None:
            if now - row["used_at"] <= REFRESH_REUSE_GRACE_SECONDS:
                conn.execute("ROLLBACK")  # benign concurrent refresh: reject, keep family
                raise invalid
            if row["family_id"]:
                conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?", (row["family_id"],))
            else:
                conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (row["user_id"],))
            conn.execute("COMMIT")
            reuse = row
        elif row["revoked"] or row["expires_at"] < now:
            conn.execute("ROLLBACK")
            raise invalid
        else:
            user_row = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
            if user_row is None:
                conn.execute("ROLLBACK")
                raise invalid
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked = 1, used_at = ? WHERE token_hash = ? AND revoked = 0 AND used_at IS NULL",
                (now, token_hash),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                raise invalid
            new_raw = secrets.token_urlsafe(48)
            conn.execute(
                "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, created_at, family_id, parent_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_hash_token(new_raw), row["user_id"], now + REFRESH_TOKEN_TTL_SECONDS, now,
                 row["family_id"] or secrets.token_hex(16), token_hash),
            )
            conn.execute("COMMIT")
    finally:
        conn.close()
    if reuse is not None:
        user = get_user_by_id(reuse["user_id"])
        security_audit.log("refresh_reuse_detected", reuse["user_id"], user["tenant_id"] if user else None, ip,
                           {"family_id": reuse["family_id"]})
        raise invalid
    new_access = create_access_token(user_row["id"], user_row["role"], user_row["token_version"])
    return new_access, new_raw


def revoke_refresh_token(raw_token: str) -> Optional[str]:
    """Logout: revoke the token's whole family and bump the user's token_version
    so outstanding access tokens die too. Returns the user_id (None if unknown)."""
    h = _hash_token(raw_token)
    with _get_conn() as conn:
        row = conn.execute("SELECT user_id, family_id FROM refresh_tokens WHERE token_hash = ?", (h,)).fetchone()
        if row is None:
            return None
        if row["family_id"]:
            conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE family_id = ?", (row["family_id"],))
        else:
            conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (h,))
        conn.commit()
    bump_token_version(row["user_id"])
    return row["user_id"]


def revoke_all_refresh_tokens(user_id: str) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
        conn.commit()


def logout_all(user_id: str) -> None:
    bump_token_version(user_id)
    revoke_all_refresh_tokens(user_id)


# ── FastAPI dependencies ─────────────────────────────────────────────────────
def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthenticatedUser:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(creds.credentials)
    user_row = get_user_by_id(payload["sub"])
    if user_row is None:
        raise HTTPException(status_code=401, detail="User no longer exists.")
    if payload.get("tv") != user_row["token_version"]:
        raise HTTPException(status_code=401, detail="Access token revoked.")
    return AuthenticatedUser(
        id=user_row["id"], email=user_row["email"], role=user_row["role"],
        tenant_id=user_row["tenant_id"],
    )


def require_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


# ── Demo / bootstrap account seeding ─────────────────────────────────────────
def seed_demo_users() -> None:
    """
    DEV convenience only. Seeds ONE demo user (demo@chat.com) into an empty
    database unless SEED_DEMO_USER is falsy; always refused when
    APP_ENV/ENV=production. No admin is ever seeded with a well-known password:
    create one with `python -m VENUAI.auth create-admin` or the one-time
    BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD environment variables.
    """
    wants_demo = os.getenv("SEED_DEMO_USER", "true").strip().lower() not in ("0", "false", "no", "off")
    if is_production():
        if wants_demo and os.getenv("SEED_DEMO_USER") is not None:
            logger.warning("[auth] SEED_DEMO_USER ignored: demo seeding is disabled in production.")
        wants_demo = False
    if wants_demo:
        with _get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if count == 0:
            try:
                create_user("demo@chat.com", "demo12345", role="user")
            except HTTPException:
                pass  # seeded by a concurrent worker
    bootstrap_admin_from_env()


def bootstrap_admin_from_env() -> None:
    email = (os.getenv("BOOTSTRAP_ADMIN_EMAIL") or "").strip()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or ""
    if not email or not password:
        return
    with _get_conn() as conn:
        has_admin = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if has_admin:
        return
    try:
        enforce_password_policy(password, email)
        user = create_user(email, password, role="admin", organization_name="Administration")
        security_audit.log("bootstrap_admin_created", user.id, user.tenant_id)
        logger.warning("[auth] Bootstrap admin created from environment; remove BOOTSTRAP_ADMIN_PASSWORD now.")
    except HTTPException as e:
        logger.error(f"[auth] BOOTSTRAP_ADMIN_* rejected: {e.detail}")


seed_demo_users()


if __name__ == "__main__":  # python -m VENUAI.auth create-admin
    import argparse
    import getpass

    parser = argparse.ArgumentParser(prog="python -m VENUAI.auth")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ca = sub.add_parser("create-admin", help="Create an admin user (password prompted, never echoed).")
    ca.add_argument("--email", required=True)
    args = parser.parse_args()
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    try:
        enforce_password_policy(pw, args.email)
        u = create_user(args.email, pw, role="admin", organization_name="Administration")
    except HTTPException as e:
        raise SystemExit(f"Error: {e.detail}")
    security_audit.log("admin_created_cli", u.id, u.tenant_id)
    print(f"Admin created: {u.email}")
