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

import os
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

# ── Configuration ──────────────────────────────────────────────────────────
# Loaded independently of rag_server.py so this module is self-contained and
# testable/importable on its own (rag_server.py's load_dotenv() is a no-op
# once this has already run in the same process). Explicit path so it works
# regardless of the process's current working directory.
load_dotenv(Path(__file__).parent / ".env")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY not set. Generate one with `python -c \"import secrets; "
        "print(secrets.token_urlsafe(48))\"` and put it in your .env — the "
        "server refuses to start with an insecure/default secret."
    )
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "1800"))       # 30 min
REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", "1209600"))  # 14 days

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "users.db"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)

VALID_ROLES = {"user", "admin"}
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes


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
    password: str = Field(min_length=8, max_length=128)
    # If omitted, a brand-new tenant is created for this user (the "first
    # user creates an org" pattern most B2B SaaS products use). If provided,
    # the user joins that existing tenant — see create_user()'s docstring
    # for what's deliberately NOT built here (invite/approval flow).
    organization_name: Optional[str] = None
    tenant_id: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


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
    return pwd_context.verify(password, hashed)


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


def record_failed_login(email: str) -> None:
    with _get_conn() as conn:
        row = conn.execute("SELECT failed_attempts FROM users WHERE email = ?", (email.lower(),)).fetchone()
        if row is None:
            return
        attempts = row["failed_attempts"] + 1
        locked_until = time.time() + LOGIN_LOCKOUT_SECONDS if attempts >= MAX_FAILED_LOGIN_ATTEMPTS else None
        conn.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE email = ?",
            (attempts, locked_until, email.lower()),
        )
        conn.commit()


def reset_failed_login(email: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE email = ?",
            (email.lower(),),
        )
        conn.commit()


def is_locked(user_row: sqlite3.Row) -> bool:
    locked_until = user_row["locked_until"]
    return bool(locked_until) and time.time() < locked_until


# ── JWT access tokens ─────────────────────────────────────────────────────────
def create_access_token(user_id: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid access token.")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type.")
    return payload


# ── Password reset (Phase 5) ─────────────────────────────────────────────────
PASSWORD_RESET_TTL_SECONDS = 30 * 60  # 30 minutes — real email delivery can lag; see email_service.py


def create_password_reset_token(user_id: str) -> str:
    """A signed, short-lived JWT — same reasoning as oauth.py's state
    token: a one-time value with its own built-in expiry doesn't need a
    dedicated DB table to track it."""
    now = int(time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + PASSWORD_RESET_TTL_SECONDS, "type": "password_reset"}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_password_reset_token(token: str) -> Optional[str]:
    """Returns the user_id if the token is a valid, unexpired
    password_reset token; None otherwise — never raises, since an
    invalid/expired reset link is an expected, routine case for the
    caller to turn into a 400, not an exceptional one."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "password_reset":
        return None
    return payload.get("sub")


def update_password(user_id: str, new_password: str) -> None:
    """Resets a user's password and revokes every existing refresh token
    for them — a password reset is a real security event; any session
    issued under the old password should not silently continue."""
    hashed = hash_password(new_password)
    with _get_conn() as conn:
        conn.execute("UPDATE users SET hashed_password = ? WHERE id = ?", (hashed, user_id))
        conn.commit()
    revoke_all_refresh_tokens(user_id)


# ── Refresh tokens (opaque, hashed at rest, revocable) ─────────────────────────
def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def issue_refresh_token(user_id: str) -> str:
    raw = secrets.token_urlsafe(48)
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, user_id, expires_at, created_at) "
            "VALUES (?, ?, ?, ?)",
            (_hash_token(raw), user_id, time.time() + REFRESH_TOKEN_TTL_SECONDS, time.time()),
        )
        conn.commit()
    return raw


def rotate_refresh_token(raw_token: str) -> tuple[str, str]:
    """Validate + revoke the old refresh token, issue a new (access, refresh) pair."""
    token_hash = _hash_token(raw_token)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None or row["revoked"] or row["expires_at"] < time.time():
            raise HTTPException(status_code=401, detail="Refresh token invalid or expired.")
        conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,))
        conn.commit()

    user_row = get_user_by_id(row["user_id"])
    if user_row is None:
        raise HTTPException(status_code=401, detail="User no longer exists.")

    new_access = create_access_token(user_row["id"], user_row["role"])
    new_refresh = issue_refresh_token(user_row["id"])
    return new_access, new_refresh


def revoke_refresh_token(raw_token: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
            (_hash_token(raw_token),),
        )
        conn.commit()


def revoke_all_refresh_tokens(user_id: str) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
        conn.commit()


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
    return AuthenticatedUser(
        id=user_row["id"], email=user_row["email"], role=user_row["role"],
        tenant_id=user_row["tenant_id"],
    )


def require_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


# ── Demo account seeding ──────────────────────────────────────────────────────
def seed_demo_users() -> None:
    """
    Seed two demo accounts on first run only, so the frontend's existing
    'demo credentials' UI (clearly labeled as a demo, not a security feature)
    keeps working end-to-end against *real* auth — hashed passwords, real
    JWTs — rather than being backed by a client-side mock.
    """
    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if count > 0:
        return
    try:
        create_user("demo@chat.com", "demo12345", role="user")
        create_user("admin@chat.com", "admin12345", role="admin")
    except HTTPException:
        pass  # already seeded by a concurrent worker


seed_demo_users()
