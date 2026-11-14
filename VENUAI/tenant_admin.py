"""
Tenant administration routes (security stream S2): invites, member role
changes and member removal. Auto-included by the rag_server router hook.

All routes are tenant-admin only and always act on the CALLER'S tenant; a
user/invite id of another tenant is indistinguishable from a missing one (404).

Invite flow (consumed by S1's /auth/register):
    token = create_invite(...)            # admin, shown once
    info  = consume_invite(token, email)  # registration; returns {"tenant_id","role","invite_id"} or None
Only a SHA-256 of the token is stored; tokens are single use and expire.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import time
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from VENUAI import auth, authz
from VENUAI.auth import AuthenticatedUser

logger = logging.getLogger("drdo-rag")
router = APIRouter(tags=["tenants"])

DEFAULT_INVITE_TTL_HOURS = 72
MAX_INVITE_TTL_HOURS = 24 * 30
_ready_for: Optional[str] = None


def _conn() -> sqlite3.Connection:
    global _ready_for
    c = auth._get_conn()
    if _ready_for != str(auth.DB_PATH):
        c.execute("""
            CREATE TABLE IF NOT EXISTS tenant_invites (
                id          TEXT PRIMARY KEY,
                tenant_id   TEXT NOT NULL,
                token_hash  TEXT NOT NULL UNIQUE,
                role        TEXT NOT NULL DEFAULT 'user',
                email       TEXT,
                created_by  TEXT NOT NULL,
                created_at  REAL NOT NULL,
                expires_at  REAL NOT NULL,
                used_at     REAL,
                used_by     TEXT,
                revoked     INTEGER NOT NULL DEFAULT 0
            )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_invites_tenant ON tenant_invites(tenant_id)")
        c.commit()
        _ready_for = str(auth.DB_PATH)
    return c


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── invite primitives (also the interface S1 consumes) ───────────────────────

def create_invite(tenant_id: str, created_by: str, role: str = "user", email: Optional[str] = None,
                  ttl_hours: int = DEFAULT_INVITE_TTL_HOURS) -> dict:
    if role not in auth.VALID_ROLES:
        raise ValueError("invalid role")
    ttl_hours = max(1, min(int(ttl_hours), MAX_INVITE_TTL_HOURS))
    token = secrets.token_urlsafe(32)
    invite_id = secrets.token_hex(8)
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO tenant_invites (id, tenant_id, token_hash, role, email, created_by, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (invite_id, tenant_id, _hash(token), role, email.lower() if email else None, created_by,
             now, now + ttl_hours * 3600))
        c.commit()
    return {"id": invite_id, "token": token, "tenant_id": tenant_id, "role": role,
            "email": email.lower() if email else None, "expires_at": now + ttl_hours * 3600}


def consume_invite(token: str, email: Optional[str] = None) -> Optional[dict]:
    """Atomically redeem an invite. Returns {"invite_id","tenant_id","role"} or
    None when the token is unknown / expired / used / revoked / bound to a
    different email. Single use: the UPDATE only matches an unused row."""
    if not token or not isinstance(token, str) or len(token) > 256:
        return None
    th = _hash(token)
    now = time.time()
    with _conn() as c:
        row = c.execute("SELECT * FROM tenant_invites WHERE token_hash = ?", (th,)).fetchone()
        if row is None or row["revoked"] or row["used_at"] is not None or row["expires_at"] < now:
            return None
        if row["email"] and (not email or row["email"] != email.lower()):
            return None
        cur = c.execute(
            "UPDATE tenant_invites SET used_at = ?, used_by = ? WHERE id = ? AND used_at IS NULL AND revoked = 0",
            (now, (email or "").lower() or None, row["id"]))
        c.commit()
        if cur.rowcount != 1:
            return None
    return {"invite_id": row["id"], "tenant_id": row["tenant_id"], "role": row["role"]}


def _public(row: sqlite3.Row) -> dict:
    status = ("revoked" if row["revoked"] else "used" if row["used_at"] is not None
              else "expired" if row["expires_at"] < time.time() else "pending")
    return {"id": row["id"], "role": row["role"], "email": row["email"], "created_by": row["created_by"],
            "created_at": row["created_at"], "expires_at": row["expires_at"], "status": status}


# ── request models (extra fields rejected: no mass assignment) ───────────────

class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "admin"] = "user"
    email: Optional[EmailStr] = None
    ttl_hours: int = Field(default=DEFAULT_INVITE_TTL_HOURS, ge=1, le=MAX_INVITE_TTL_HOURS)


class RoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "admin"]


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("/tenants/invites", status_code=201)
def post_invite(body: InviteRequest, admin: AuthenticatedUser = Depends(authz.require_tenant_admin)) -> dict:
    tenant = auth.get_tenant(admin.tenant_id)
    if tenant is None:
        raise authz.not_found("Tenant")
    inv = create_invite(admin.tenant_id, admin.id, body.role, body.email, body.ttl_hours)
    authz.audit("tenant.invite_created", admin, detail=f"invite={inv['id']} role={body.role}")
    return inv


@router.get("/tenants/invites")
def list_invites(admin: AuthenticatedUser = Depends(authz.require_tenant_admin)) -> List[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM tenant_invites WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 200",
                         (admin.tenant_id,)).fetchall()
    return [_public(r) for r in rows]


@router.delete("/tenants/invites/{invite_id}")
def revoke_invite(invite_id: str, admin: AuthenticatedUser = Depends(authz.require_tenant_admin)) -> dict:
    with _conn() as c:
        cur = c.execute("UPDATE tenant_invites SET revoked = 1 WHERE id = ? AND tenant_id = ?",
                        (invite_id, admin.tenant_id))
        c.commit()
    if cur.rowcount == 0:
        raise authz.not_found("Invite")
    authz.audit("tenant.invite_revoked", admin, detail=f"invite={invite_id}")
    return {"status": "revoked", "id": invite_id}


def _member(tenant_id: str, user_id: str) -> sqlite3.Row:
    row = auth.get_user_by_id(user_id) if isinstance(user_id, str) else None
    if row is None or row["tenant_id"] != tenant_id:
        raise authz.not_found("Member")
    return row


def _admin_count(tenant_id: str) -> int:
    with auth._get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE tenant_id = ? AND role = 'admin'",
                         (tenant_id,)).fetchone()[0]


@router.patch("/tenants/members/{user_id}")
def change_member_role(user_id: str, body: RoleChange,
                       admin: AuthenticatedUser = Depends(authz.require_tenant_admin)) -> dict:
    target = _member(admin.tenant_id, user_id)
    if target["role"] == "admin" and body.role != "admin" and _admin_count(admin.tenant_id) <= 1:
        raise HTTPException(status_code=409, detail="A tenant must keep at least one admin.")
    with auth._get_conn() as c:
        # tenant_id in the WHERE: a role change can never cross tenants.
        c.execute("UPDATE users SET role = ? WHERE id = ? AND tenant_id = ?", (body.role, user_id, admin.tenant_id))
        c.commit()
    authz.audit("tenant.role_changed", admin, detail=f"target={user_id} {target['role']}->{body.role}")
    return {"id": user_id, "role": body.role, "tenant_id": admin.tenant_id}


@router.delete("/tenants/members/{user_id}")
def remove_member(user_id: str, admin: AuthenticatedUser = Depends(authz.require_tenant_admin)) -> dict:
    target = _member(admin.tenant_id, user_id)
    if target["role"] == "admin" and _admin_count(admin.tenant_id) <= 1:
        raise HTTPException(status_code=409, detail="A tenant must keep at least one admin.")
    auth.revoke_all_refresh_tokens(user_id)
    with auth._get_conn() as c:
        c.execute("DELETE FROM users WHERE id = ? AND tenant_id = ?", (user_id, admin.tenant_id))
        c.commit()
    authz.audit("tenant.member_removed", admin, detail=f"target={user_id}")
    return {"status": "removed", "id": user_id}
