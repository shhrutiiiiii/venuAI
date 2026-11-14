"""
Real tests for Phase 5's password-reset flow: VENUAI/email_service.py,
auth.py's token sign/verify + update_password, and the
/auth/forgot-password + /auth/reset-password routes. SMTP send itself is
mocked at the smtplib.SMTP level (no real mail server in this
environment — see Documents/Backend/password-reset.md), but every other
piece of control flow (token validity, email-enumeration resistance,
account lookup, password update, refresh-token revocation) is real.
"""

import pytest

from VENUAI import auth, email_service


@pytest.fixture(autouse=True)
def _unconfigured_smtp(monkeypatch):
    for v in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL"):
        monkeypatch.delenv(v, raising=False)


# ── VENUAI/email_service.py ──────────────────────────────────────────────────

def test_send_email_fails_without_smtp_configured():
    with pytest.raises(email_service.EmailNotConfiguredError):
        email_service.send_email("someone@example.com", "subject", "body")


def test_send_email_succeeds_with_mocked_smtp_server(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "fake-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@example.com")

    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=15):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, msg):
            sent["to"] = msg["To"]
            sent["subject"] = msg["Subject"]

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    email_service.send_email("recipient@example.com", "Test Subject", "Test body")
    assert sent["to"] == "recipient@example.com"
    assert sent["subject"] == "Test Subject"
    assert sent["login"] == ("bot@example.com", "fake-password")
    assert sent["starttls"] is True


def test_send_email_raises_on_real_smtp_error(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "fake-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@example.com")

    import smtplib

    class _FailingSMTP:
        def __init__(self, host, port, timeout=15):
            raise OSError("Connection refused")

    monkeypatch.setattr(smtplib, "SMTP", _FailingSMTP)

    with pytest.raises(email_service.EmailSendError):
        email_service.send_email("recipient@example.com", "subject", "body")


# ── auth.py token sign/verify + update_password ─────────────────────────────

def test_password_reset_token_round_trips(isolated_auth_db):
    user = auth.create_user("resettest@example.com", "oldpassword123")
    token = auth.create_password_reset_token(user.id)
    assert auth.verify_password_reset_token(token) == user.id


def test_garbage_token_returns_none():
    assert auth.verify_password_reset_token("not-a-real-token") is None


def test_update_password_actually_changes_hash_and_revokes_sessions(isolated_auth_db):
    user = auth.create_user("resettest2@example.com", "oldpassword123")
    old_row = auth.get_user_by_email(user.email)
    refresh = auth.issue_refresh_token(user.id)

    auth.update_password(user.id, "newpassword456")

    new_row = auth.get_user_by_email(user.email)
    assert new_row["hashed_password"] != old_row["hashed_password"]
    assert auth.verify_password("newpassword456", new_row["hashed_password"])
    assert not auth.verify_password("oldpassword123", new_row["hashed_password"])

    # The old refresh token must now be revoked.
    with pytest.raises(Exception):
        auth.rotate_refresh_token(refresh)


# ── HTTP routes ──────────────────────────────────────────────────────────────

def test_forgot_password_for_unknown_email_returns_202_without_revealing(client):
    r = client.post("/auth/forgot-password", json={"email": "nobody-real@example.com"})
    assert r.status_code == 202


def test_forgot_password_without_smtp_is_still_a_uniform_202(client):
    # No account-existence (or config) oracle: same 202 as an unknown email;
    # the misconfiguration is logged server-side only.
    r = client.post("/auth/forgot-password", json={"email": "demo@chat.com"})
    assert r.status_code == 202
    assert r.json() == client.post("/auth/forgot-password", json={"email": "ghost@example.com"}).json()


def test_forgot_password_sends_real_looking_email_when_configured(client, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "fake-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@example.com")

    captured = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=15):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, username, password):
            pass

        def send_message(self, msg):
            captured["body"] = msg.get_content()
            captured["to"] = msg["To"]

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    r = client.post("/auth/forgot-password", json={"email": "demo@chat.com"})
    assert r.status_code == 202
    assert captured["to"] == "demo@chat.com"
    assert "reset-password?token=" in captured["body"]


def test_reset_password_end_to_end(client, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "fake-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@example.com")

    captured = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=15):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, username, password):
            pass

        def send_message(self, msg):
            body = msg.get_content()
            captured["token"] = body.split("token=")[1].split("\n")[0].strip()

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    client.post("/auth/forgot-password", json={"email": "demo@chat.com"})
    token = captured["token"]

    r = client.post("/auth/reset-password", json={"token": token, "new_password": "brandnewpassword789"})
    assert r.status_code == 200

    # Old password no longer works; new one does.
    old_login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    assert old_login.status_code == 401
    new_login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "brandnewpassword789"})
    assert new_login.status_code == 200


def test_reset_password_rejects_invalid_token(client):
    r = client.post("/auth/reset-password", json={"token": "garbage", "new_password": "newpassword123"})
    assert r.status_code == 400
