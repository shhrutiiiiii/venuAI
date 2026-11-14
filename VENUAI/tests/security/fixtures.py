"""
Shared fixtures for security tests (S1-S4 and beyond).

Usage in a test module (conftest.py is intentionally not edited):

    from VENUAI.tests.security.fixtures import *  # noqa: F401,F403
    pytestmark = pytest.mark.security

    def test_x(two_tenants, client):
        r = client.get("/auth/me", headers=two_tenants.alice.headers)

Fixtures provided
-----------------
sec_auth          : auth module pointed at a throwaway SQLite DB (no demo seeding side effects relied on)
two_tenants       : TwoTenants(alice, bob, admin, carol_same_tenant)
                    alice / carol are in tenant A, bob in tenant B, admin is role=admin
sec_client        : FastAPI TestClient bound to rag_server.app using sec_auth's DB
evil_origin       : an origin that must never be echoed in CORS headers
make_token        : factory (user_id, role, **claim_overrides, ttl) -> signed JWT, incl. expired/forged variants
forged_tokens     : dict of malformed / alg=none / wrong-secret / expired tokens that must all be rejected
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Optional

import pytest

TEST_PASSWORD = "Sec-Test#Passw0rd-1"


@dataclass
class SecUser:
    id: str
    email: str
    password: str
    role: str
    tenant_id: str
    token: str = field(repr=False, default="")

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class TwoTenants:
    alice: SecUser          # tenant A, role user
    carol: SecUser          # tenant A, role user (same tenant as alice)
    bob: SecUser            # tenant B, role user
    admin: SecUser          # role admin
    tenant_a: str
    tenant_b: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _mk(auth, email: str, role: str, **kw) -> SecUser:
    u = auth.create_user(email=email, password=TEST_PASSWORD, role=role, **kw)
    return SecUser(
        id=u.id, email=email, password=TEST_PASSWORD, role=u.role,
        tenant_id=u.tenant_id, token=auth.create_access_token(u.id, u.role),
    )


@pytest.fixture()
def sec_auth(monkeypatch, tmp_path):
    from VENUAI import auth

    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "sec_users.db")
    auth.init_db()
    yield auth


@pytest.fixture()
def two_tenants(sec_auth) -> TwoTenants:
    alice = _mk(sec_auth, "alice@tenant-a.example.com", "user", organization_name="Tenant A")
    carol = _mk(sec_auth, "carol@tenant-a.example.com", "user", tenant_id=alice.tenant_id)
    bob = _mk(sec_auth, "bob@tenant-b.example.com", "user", organization_name="Tenant B")
    admin = _mk(sec_auth, "admin@sec.example.com", "admin")
    return TwoTenants(alice, carol, bob, admin, alice.tenant_id, bob.tenant_id)


@pytest.fixture()
def sec_client(sec_auth):
    from fastapi.testclient import TestClient
    from VENUAI import rag_server

    return TestClient(rag_server.app)


@pytest.fixture()
def evil_origin() -> str:
    return "https://evil.example"


@pytest.fixture()
def make_token(sec_auth):
    import jwt

    def _make(user_id: str = "x", role: str = "user", ttl: int = 900,
              secret: Optional[str] = None, algorithm: str = "HS256", **overrides) -> str:
        now = int(time.time())
        payload = {"sub": user_id, "role": role, "iat": now, "exp": now + ttl, "type": "access"}
        payload.update(overrides)
        payload = {k: v for k, v in payload.items() if v is not None}
        return jwt.encode(payload, secret or sec_auth.JWT_SECRET_KEY, algorithm=algorithm)

    return _make


@pytest.fixture()
def forged_tokens(make_token, two_tenants) -> dict:
    """Tokens that every authenticated endpoint must reject with 401."""
    uid = two_tenants.alice.id
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"sub": uid, "role": "admin", "type": "access",
                            "exp": int(time.time()) + 900}).encode())
    return {
        "empty": "",
        "garbage": "not.a.jwt",
        "alg_none": f"{header}.{body}.",
        "wrong_secret": make_token(uid, secret="a-completely-different-secret-value-0123456789"),
        "expired": make_token(uid, ttl=-60),
        "refresh_type": make_token(uid, type="refresh"),
    }
