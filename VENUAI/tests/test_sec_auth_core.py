"""
S1 abuse / edge-case tests for authentication: JWT hardening, token_version
revocation, refresh-token families, password-reset single use, lockout,
enumeration resistance, registration/tenant-join, demo seeding, audit log,
trusted client IP. See Documents/Backend/authentication.md.
"""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from VENUAI import auth, security_audit
from VENUAI.client_ip import trusted_client_ip

STRONG = "Correct-Horse-Battery-42"
DEMO = {"email": "demo@chat.com", "password": "demo12345"}


@pytest.fixture(autouse=True)
def _no_grace(monkeypatch):
    monkeypatch.setattr(auth, "REFRESH_REUSE_GRACE_SECONDS", 0.0)


def _login(client, creds=DEMO):
    r = client.post("/auth/login", json=creds)
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# ── JWT secret ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    None, "", "short", "x" * 31, "<GENERATE_A_SECRET_WITH_TOKEN_URLSAFE_48>", "changeme" * 6,
    "a" * 64, "your-secret-key-" + "q1w2e3r4" * 3, "secret",
])
def test_weak_or_placeholder_jwt_secret_refuses_to_boot(bad):
    with pytest.raises(RuntimeError):
        auth.validate_jwt_secret(bad)


def test_strong_jwt_secret_accepted():
    s = "Zk3-9vQpL2mXw8RtYb6NcHd4JfGs1UaE7oPi0KxMq5T"
    assert auth.validate_jwt_secret(f"  {s}  ") == s


# ── access token hardening ───────────────────────────────────────────────────

def _forge(payload, key=None, alg="HS256"):
    return jwt.encode(payload, key or auth._derive_key("access"), algorithm=alg)


def _claims(user_id="x", **over):
    now = int(time.time())
    base = {"sub": user_id, "role": "admin", "tv": 0, "iat": now, "exp": now + 600, "iss": auth.JWT_ISSUER,
            "aud": auth.JWT_AUDIENCE, "jti": "j", "type": "access"}
    base.update(over)
    return base


def test_alg_none_token_rejected(client):
    tok = jwt.encode(_claims(), key=None, algorithm="none")
    assert client.get("/auth/me", headers=_bearer(tok)).status_code == 401


def test_hs512_token_rejected(client):
    assert client.get("/auth/me", headers=_bearer(_forge(_claims(), alg="HS512"))).status_code == 401


def test_token_signed_with_raw_secret_instead_of_derived_key_rejected(client):
    uid = _login(client)["user"]["id"]
    tok = _forge(_claims(uid), key=auth.JWT_SECRET_KEY)
    assert client.get("/auth/me", headers=_bearer(tok)).status_code == 401


@pytest.mark.parametrize("over", [
    {"type": "password_reset"}, {"aud": "other"}, {"iss": "evil"},
    {"exp": int(time.time()) - 5},
])
def test_wrong_type_aud_iss_or_expired_token_rejected(client, over):
    uid = _login(client)["user"]["id"]
    assert client.get("/auth/me", headers=_bearer(_forge(_claims(uid, **over)))).status_code == 401


def test_token_missing_required_claims_rejected(client):
    uid = _login(client)["user"]["id"]
    c = _claims(uid)
    del c["jti"]
    assert client.get("/auth/me", headers=_bearer(_forge(c))).status_code == 401


def test_valid_token_works_and_carries_claims(client):
    body = _login(client)
    p = jwt.decode(body["access_token"], auth._derive_key("access"), algorithms=["HS256"], audience=auth.JWT_AUDIENCE)
    assert p["iss"] == auth.JWT_ISSUER and p["jti"] and p["tv"] == 0
    assert client.get("/auth/me", headers=_bearer(body["access_token"])).status_code == 200


# ── token_version revocation ─────────────────────────────────────────────────

def test_access_token_dies_on_logout(client):
    body = _login(client)
    assert client.post("/auth/logout", json={"refresh_token": body["refresh_token"]}).status_code == 204
    assert client.get("/auth/me", headers=_bearer(body["access_token"])).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 401


def test_access_token_dies_on_password_reset(client):
    body = _login(client)
    uid = body["user"]["id"]
    auth.update_password(uid, STRONG)
    assert client.get("/auth/me", headers=_bearer(body["access_token"])).status_code == 401


def test_access_token_dies_on_role_change(client):
    body = _login(client)
    uid = body["user"]["id"]
    auth.set_user_role(uid, "admin", actor_id="someone")
    assert client.get("/auth/me", headers=_bearer(body["access_token"])).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 401
    assert any(e["event"] == "role_change" for e in security_audit.recent())


def test_logout_all_kills_every_session(client):
    a, b = _login(client), _login(client)
    assert client.post("/auth/logout-all", headers=_bearer(a["access_token"])).status_code == 204
    for s in (a, b):
        assert client.get("/auth/me", headers=_bearer(s["access_token"])).status_code == 401
        assert client.post("/auth/refresh", json={"refresh_token": s["refresh_token"]}).status_code == 401


def test_logout_of_one_session_keeps_other_session_refreshable(client):
    a, b = _login(client), _login(client)
    client.post("/auth/logout", json={"refresh_token": a["refresh_token"]})
    r = client.post("/auth/refresh", json={"refresh_token": b["refresh_token"]})
    assert r.status_code == 200
    assert client.get("/auth/me", headers=_bearer(r.json()["access_token"])).status_code == 200


# ── refresh families ─────────────────────────────────────────────────────────

def test_refresh_reuse_revokes_whole_family(client):
    first = _login(client)["refresh_token"]
    r1 = client.post("/auth/refresh", json={"refresh_token": first})
    child = r1.json()["refresh_token"]
    # attacker replays the already-rotated token
    assert client.post("/auth/refresh", json={"refresh_token": first}).status_code == 401
    # ...and now the legitimate newest token from the same family is dead too
    assert client.post("/auth/refresh", json={"refresh_token": child}).status_code == 401
    assert any(e["event"] == "refresh_reuse_detected" for e in security_audit.recent())


def test_reuse_only_kills_its_own_family(client):
    mine = _login(client)["refresh_token"]
    other = _login(client)["refresh_token"]
    client.post("/auth/refresh", json={"refresh_token": mine})
    client.post("/auth/refresh", json={"refresh_token": mine})  # replay
    assert client.post("/auth/refresh", json={"refresh_token": other}).status_code == 200


def test_concurrent_rotations_exactly_one_wins(isolated_auth_db):
    user = auth.get_user_by_email("demo@chat.com")
    raw = auth.issue_refresh_token(user["id"])

    def go(_):
        try:
            return auth.rotate_refresh_token(raw)
        except HTTPException as e:
            return e.status_code

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(go, range(8)))
    winners = [r for r in results if isinstance(r, tuple)]
    assert len(winners) == 1
    assert results.count(401) == 7


def test_benign_double_refresh_within_grace_keeps_family(client, monkeypatch):
    monkeypatch.setattr(auth, "REFRESH_REUSE_GRACE_SECONDS", 60.0)
    first = _login(client)["refresh_token"]
    child = client.post("/auth/refresh", json={"refresh_token": first}).json()["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": first}).status_code == 401  # loser of the race
    assert client.post("/auth/refresh", json={"refresh_token": child}).status_code == 200  # winner unaffected


def test_expired_refresh_token_rejected(client, isolated_auth_db):
    raw = _login(client)["refresh_token"]
    with auth._get_conn() as conn:
        conn.execute("UPDATE refresh_tokens SET expires_at = 1")
        conn.commit()
    assert client.post("/auth/refresh", json={"refresh_token": raw}).status_code == 401


def test_refresh_token_is_hashed_at_rest(client, isolated_auth_db):
    raw = _login(client)["refresh_token"]
    with auth._get_conn() as conn:
        hashes = [r[0] for r in conn.execute("SELECT token_hash FROM refresh_tokens")]
    assert raw not in hashes and auth._hash_token(raw) in hashes


# ── password reset ───────────────────────────────────────────────────────────

def _uid():
    return auth.get_user_by_email("demo@chat.com")["id"]


def test_reset_token_is_single_use(client):
    tok = auth.create_password_reset_token(_uid())
    assert client.post("/auth/reset-password", json={"token": tok, "new_password": STRONG}).status_code == 200
    # replay with a different password: must not work
    r = client.post("/auth/reset-password", json={"token": tok, "new_password": "Attacker-Chosen-Pw-77"})
    assert r.status_code == 400
    assert client.post("/auth/login", json={"email": "demo@chat.com", "password": STRONG}).status_code == 200


def test_new_reset_token_invalidates_older_ones(client):
    old = auth.create_password_reset_token(_uid())
    new = auth.create_password_reset_token(_uid())
    assert client.post("/auth/reset-password", json={"token": old, "new_password": STRONG}).status_code == 400
    assert client.post("/auth/reset-password", json={"token": new, "new_password": STRONG}).status_code == 200


def test_expired_reset_token_rejected(client):
    tok = auth.create_password_reset_token(_uid())
    with auth._get_conn() as conn:
        conn.execute("UPDATE password_reset_tokens SET expires_at = 1")
        conn.commit()
    assert client.post("/auth/reset-password", json={"token": tok, "new_password": STRONG}).status_code == 400


def test_reset_token_dies_when_password_changes_by_other_route(client):
    tok = auth.create_password_reset_token(_uid())
    with auth._get_conn() as conn:  # password changed some other way (no reset-row bookkeeping)
        conn.execute("UPDATE users SET hashed_password = ? WHERE id = ?", (auth.hash_password("Another-Pw-Entirely-1"), _uid()))
        conn.commit()
    assert auth.verify_password_reset_token(tok) is None
    assert client.post("/auth/reset-password", json={"token": tok, "new_password": STRONG}).status_code == 400


def test_reset_token_stored_hashed_and_random(isolated_auth_db):
    tok = auth.create_password_reset_token(_uid())
    assert len(tok) >= 40
    with auth._get_conn() as conn:
        assert tok not in [r[0] for r in conn.execute("SELECT token_hash FROM password_reset_tokens")]


def test_weak_new_password_does_not_burn_the_token(client):
    tok = auth.create_password_reset_token(_uid())
    assert client.post("/auth/reset-password", json={"token": tok, "new_password": "password1234"}).status_code == 422
    assert client.post("/auth/reset-password", json={"token": tok, "new_password": STRONG}).status_code == 200


def test_reset_kills_sessions_and_clears_lockout(client):
    body = _login(client)
    for _ in range(5):
        client.post("/auth/login", json={**DEMO, "password": "wrong-password-x"})
    tok = auth.create_password_reset_token(_uid())
    client.post("/auth/reset-password", json={"token": tok, "new_password": STRONG})
    assert client.get("/auth/me", headers=_bearer(body["access_token"])).status_code == 401
    assert client.post("/auth/login", json={"email": "demo@chat.com", "password": STRONG}).status_code == 200


def test_forgot_password_identical_for_known_unknown_and_smtp_failure(client, monkeypatch):
    from VENUAI import email_service

    monkeypatch.setattr(email_service, "is_configured", lambda: True)

    def _boom(**kw):
        raise email_service.EmailSendError("smtp down")

    monkeypatch.setattr(email_service, "send_email", _boom)
    known = client.post("/auth/forgot-password", json={"email": "demo@chat.com"})
    unknown = client.post("/auth/forgot-password", json={"email": "nobody-here@example.com"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json()) == (202, {"status": "accepted"})
    assert known.headers.get("content-length") == unknown.headers.get("content-length")


def test_forgot_password_email_contains_working_link(client, monkeypatch):
    from VENUAI import email_service

    sent = {}
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setattr(email_service, "send_email", lambda to, subject, body_text: sent.update(to=to, body=body_text))
    client.post("/auth/forgot-password", json={"email": "demo@chat.com"})
    token = sent["body"].split("token=")[1].split()[0]
    assert auth.verify_password_reset_token(token) == _uid()


# ── login: lockout, uniform errors ───────────────────────────────────────────

def _bad(client, email="demo@chat.com", pw="wrong-password-x"):
    from VENUAI.rag_server import limiter
    limiter.reset()  # these tests exercise account lockout, not the per-IP limiter
    return client.post("/auth/login", json={"email": email, "password": pw})


def test_login_failures_are_uniform_for_unknown_wrong_and_locked(client):
    unknown = _bad(client, "ghost@example.com")
    wrong = _bad(client)
    for _ in range(5):
        _bad(client)
    locked = client.post("/auth/login", json=DEMO)  # CORRECT password while locked
    assert {r.status_code for r in (unknown, wrong, locked)} == {401}
    strip = lambda r: {k: v for k, v in r.json().items() if k != "request_id"}  # request_id is per-request
    assert strip(unknown) == strip(wrong) == strip(locked)


def test_lockout_is_not_extended_while_locked_and_cannot_be_bypassed(client, isolated_auth_db):
    for _ in range(5):
        _bad(client)
    first = auth.get_user_by_email("demo@chat.com")["locked_until"]
    assert first
    for _ in range(10):
        assert _bad(client).status_code == 401
    assert auth.get_user_by_email("demo@chat.com")["locked_until"] == first
    assert client.post("/auth/login", json=DEMO).status_code == 401  # correct pw still refused


def test_lockout_backoff_escalates(isolated_auth_db):
    assert auth._lockout_duration(1) == 300
    assert auth._lockout_duration(2) == 600
    assert auth._lockout_duration(3) == 1200
    assert auth._lockout_duration(20) == auth.LOGIN_LOCKOUT_MAX_SECONDS


def test_lockout_expires_then_escalates_on_next_round(client, isolated_auth_db):
    for _ in range(5):
        _bad(client)
    with auth._get_conn() as conn:
        conn.execute("UPDATE users SET locked_until = ? WHERE email = 'demo@chat.com'", (time.time() - 1,))
        conn.commit()
    for _ in range(5):
        _bad(client)
    row = auth.get_user_by_email("demo@chat.com")
    assert row["lockout_count"] == 2
    assert row["locked_until"] - time.time() > 500  # ~10 min, not 5


def test_successful_login_resets_counters(client, isolated_auth_db):
    for _ in range(3):
        _bad(client)
    _login(client)
    row = auth.get_user_by_email("demo@chat.com")
    assert row["failed_attempts"] == 0 and row["lockout_count"] == 0


def test_login_rehashes_outdated_hash(client, isolated_auth_db, monkeypatch):
    weak = auth.CryptContext(schemes=["bcrypt"], bcrypt__rounds=4).hash("demo12345")
    monkeypatch.setattr(auth, "pwd_context", auth.CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=5))
    with auth._get_conn() as conn:
        conn.execute("UPDATE users SET hashed_password = ? WHERE email = 'demo@chat.com'", (weak,))
        conn.commit()
    _login(client)
    new = auth.get_user_by_email("demo@chat.com")["hashed_password"]
    assert new != weak and "$05$" in new


def test_overlong_login_password_rejected_cheaply(client):
    assert client.post("/auth/login", json={"email": "demo@chat.com", "password": "a" * 5000}).status_code == 422


# ── registration / tenant join ───────────────────────────────────────────────

def _reg(client, email, pw=STRONG, **extra):
    return client.post("/auth/register", json={"email": email, "password": pw, **extra})


@pytest.mark.parametrize("pw,fragment", [
    ("Short1!", "12"), ("password1234", "common"), ("aaaaaaaaaaaaaaaa", "repetitive"),
    ("x" * 80 + "Q1", "72"), ("janedoe-secret-99", "email name"),
])
def test_signup_password_policy(client, pw, fragment):
    r = _reg(client, "janedoe@example.com", pw)
    assert r.status_code == 422
    assert fragment in r.json()["detail"]


def test_register_creates_personal_tenant_and_user_role(client):
    r = _reg(client, "solo@example.com")
    assert r.status_code == 201
    assert r.json()["tenant_id"] != "default" and r.json()["role"] == "user"


def test_register_organization_name_still_works(client):
    r = _reg(client, "founder@example.com", organization_name="Acme")
    assert r.status_code == 201
    assert client.get("/auth/tenant", headers=_bearer(
        client.post("/auth/login", json={"email": "founder@example.com", "password": STRONG}).json()["access_token"]
    )).json()["name"] == "Acme"


def test_cannot_join_arbitrary_tenant_without_invite(client, isolated_auth_db):
    owner = _reg(client, "owner@acme.com").json()
    r = _reg(client, "intruder@evil.com", tenant_id=owner["tenant_id"])
    assert r.status_code == 403
    assert isolated_auth_db.get_user_by_email("intruder@evil.com") is None
    assert any(e["event"] == "register_denied_tenant_join" for e in security_audit.recent())


def test_garbage_invite_token_refused(client):
    owner = _reg(client, "owner@acme.com").json()
    assert _reg(client, "i@evil.com", tenant_id=owner["tenant_id"], invite_token="nope").status_code == 403
    assert _reg(client, "i2@evil.com", invite_token="nope").status_code == 403


def test_invite_joins_tenant_once_only(client):
    owner = _reg(client, "owner@acme.com").json()
    inv = auth.create_invite(owner["tenant_id"], created_by=owner["id"])
    r = _reg(client, "mate@acme.com", invite_token=inv)
    assert r.status_code == 201 and r.json()["tenant_id"] == owner["tenant_id"]
    assert _reg(client, "mate2@acme.com", invite_token=inv).status_code == 403  # single use


def test_invite_for_other_tenant_id_mismatch_refused(client):
    owner = _reg(client, "owner@acme.com").json()
    inv = auth.create_invite(owner["tenant_id"])
    assert _reg(client, "m@acme.com", tenant_id="default", invite_token=inv).status_code == 403


def test_invite_bound_to_email_and_expiry(client, isolated_auth_db):
    owner = _reg(client, "owner@acme.com").json()
    inv = auth.create_invite(owner["tenant_id"], email="only-this@acme.com")
    assert _reg(client, "someone-else@acme.com", invite_token=inv).status_code == 403
    assert _reg(client, "only-this@acme.com", invite_token=inv).status_code == 201
    old = auth.create_invite(owner["tenant_id"], ttl_seconds=-5)
    assert _reg(client, "late@acme.com", invite_token=old).status_code == 403


def test_failed_signup_does_not_burn_invite(client, isolated_auth_db):
    owner = _reg(client, "owner@acme.com").json()
    isolated_auth_db.update_tenant(owner["tenant_id"], max_users=1)
    inv = auth.create_invite(owner["tenant_id"])
    assert _reg(client, "full@acme.com", invite_token=inv).status_code == 403  # quota
    isolated_auth_db.update_tenant(owner["tenant_id"], max_users=5)
    assert _reg(client, "full@acme.com", invite_token=inv).status_code == 201


def test_duplicate_email_registration_conflicts_without_creating_tenant(client, isolated_auth_db):
    with auth._get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
    assert _reg(client, "demo@chat.com").status_code == 409
    with auth._get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == before


# ── demo / admin seeding ─────────────────────────────────────────────────────

def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "seed_users.db")
    auth.init_db()


def test_demo_seed_on_by_default_in_dev_without_admin(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.delenv("SEED_DEMO_USER", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    auth.seed_demo_users()
    assert auth.get_user_by_email("demo@chat.com") is not None
    assert auth.get_user_by_email("admin@chat.com") is None
    with auth._get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 0


@pytest.mark.parametrize("var", ["APP_ENV", "ENV"])
def test_demo_seed_refused_in_production_even_if_forced(monkeypatch, tmp_path, var):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv(var, "production")
    monkeypatch.setenv("SEED_DEMO_USER", "true")
    auth.seed_demo_users()
    assert auth.get_user_by_email("demo@chat.com") is None


def test_demo_seed_can_be_switched_off(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("SEED_DEMO_USER", "false")
    auth.seed_demo_users()
    assert auth.get_user_by_email("demo@chat.com") is None


def test_bootstrap_admin_from_env_once_and_policy_enforced(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setenv("SEED_DEMO_USER", "false")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "root@corp.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "weak")
    auth.seed_demo_users()
    assert auth.get_user_by_email("root@corp.com") is None  # weak password refused
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", STRONG)
    auth.seed_demo_users()
    assert auth.get_user_by_email("root@corp.com")["role"] == "admin"
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "second@corp.com")
    auth.seed_demo_users()
    assert auth.get_user_by_email("second@corp.com") is None  # only when no admin exists


# ── audit log ────────────────────────────────────────────────────────────────

def test_audit_log_is_append_only():
    security_audit.log("unit_test", "u1", "t1", "1.2.3.4", {"k": "v"})
    with pytest.raises(sqlite3.DatabaseError):
        c = security_audit._conn()
        c.execute("UPDATE security_events SET event = 'tampered'")
    with pytest.raises(sqlite3.DatabaseError):
        c = security_audit._conn()
        c.execute("DELETE FROM security_events")
    assert security_audit.recent(event="unit_test")[0]["user_id"] == "u1"


def test_login_events_are_audited_without_secrets(client):
    _bad(client)
    _login(client)
    events = security_audit.recent()
    names = {e["event"] for e in events}
    assert {"login_failed", "login_ok"} <= names
    assert "wrong-password-x" not in str(events) and "demo12345" not in str(events)


def test_audit_log_never_raises(monkeypatch):
    monkeypatch.setattr(security_audit, "DB_PATH", "/nonexistent-dir-xyz/\0/bad.db")
    security_audit.log("x")  # must not raise


# ── trusted client IP ────────────────────────────────────────────────────────

def _req(headers, host="10.0.0.9"):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, client=SimpleNamespace(host=host))


def test_forwarded_ip_ignored_without_shared_secret(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_SECRET", "s3cr3t-proxy-value")
    assert trusted_client_ip(_req({"X-Forwarded-For": "6.6.6.6"})) == "10.0.0.9"
    assert trusted_client_ip(_req({"X-Forwarded-For": "6.6.6.6", "X-VenuAI-Proxy-Secret": "wrong"})) == "10.0.0.9"


def test_forwarded_ip_trusted_with_secret(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_SECRET", "s3cr3t-proxy-value")
    r = _req({"X-Forwarded-For": "203.0.113.5, 10.1.1.1", "X-VenuAI-Proxy-Secret": "s3cr3t-proxy-value"})
    assert trusted_client_ip(r) == "203.0.113.5"


def test_forwarded_ip_never_trusted_when_no_secret_configured(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_SECRET", raising=False)
    assert trusted_client_ip(_req({"X-Forwarded-For": "6.6.6.6", "X-VenuAI-Proxy-Secret": ""})) == "10.0.0.9"
