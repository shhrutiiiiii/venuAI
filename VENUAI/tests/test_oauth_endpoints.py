"""
Real end-to-end TestClient tests for the /auth/oauth/* routes
(VENUAI/rag_server.py). See VENUAI/tests/test_oauth.py for the underlying
VENUAI/oauth.py module tests (mocked HTTP transport); these test the
HTTP-layer wiring on top: state + browser binding are enforced, redirect URIs
are allow-listed, missing credentials surface as a real 503.
"""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

_RealClient = httpx.Client
CB = "http://localhost:3000/api/auth/oauth/google/callback"


@pytest.fixture(autouse=True)
def _unconfigured_providers(monkeypatch):
    for env_var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")


def _configure(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")


def _mock_google(monkeypatch, userinfo):
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "real-looking-token"})
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json=userinfo)
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))


def _start(client):
    r = client.get("/auth/oauth/google/login", params={"redirect_uri": CB})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["binding"], parse_qs(urlsplit(body["authorize_url"]).query)["state"][0]


def test_oauth_login_returns_503_when_not_configured(client):
    r = client.get("/auth/oauth/google/login", params={"redirect_uri": CB})
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


def test_oauth_login_returns_400_for_unknown_provider(client):
    r = client.get("/auth/oauth/facebook/login", params={"redirect_uri": "http://localhost:3000/api/auth/oauth/facebook/callback"})
    assert r.status_code == 400


def test_oauth_login_rejects_foreign_redirect_uri(client, monkeypatch):
    _configure(monkeypatch)
    r = client.get("/auth/oauth/google/login", params={"redirect_uri": "https://evil.example/api/auth/oauth/google/callback"})
    assert r.status_code == 400


def test_oauth_login_returns_authorize_url_with_pkce_and_binding(client, monkeypatch):
    _configure(monkeypatch)
    r = client.get("/auth/oauth/google/login", params={"redirect_uri": CB})
    assert r.status_code == 200
    assert r.json()["authorize_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "code_challenge_method=S256" in r.json()["authorize_url"]
    assert r.json()["binding"]


def test_oauth_callback_rejects_invalid_state(client, monkeypatch):
    _configure(monkeypatch)
    r = client.post("/auth/oauth/google/callback", json={
        "code": "irrelevant", "state": "garbage-not-a-real-state", "redirect_uri": CB, "binding": "x",
    })
    assert r.status_code == 400


def test_oauth_callback_without_binding_cookie_value_is_rejected(client, monkeypatch):
    _configure(monkeypatch)
    _, state = _start(client)
    r = client.post("/auth/oauth/google/callback", json={"code": "c", "state": state, "redirect_uri": CB})
    assert r.status_code == 400


def test_oauth_callback_creates_logs_in_and_state_is_single_use(client, monkeypatch):
    _configure(monkeypatch)
    _mock_google(monkeypatch, {"id": "sub-1", "email": "new-oauth-user@drdo.gov.in", "name": "New User", "verified_email": True})

    binding, state = _start(client)
    payload = {"code": "real-code", "state": state, "redirect_uri": CB, "binding": binding}
    r = client.post("/auth/oauth/google/callback", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == "new-oauth-user@drdo.gov.in"
    assert body["access_token"] and body["refresh_token"]

    # replaying the same state/binding must fail
    assert client.post("/auth/oauth/google/callback", json=payload).status_code == 400

    # a second sign-in for the same provider identity reaches the SAME account
    binding2, state2 = _start(client)
    r2 = client.post("/auth/oauth/google/callback", json={**payload, "state": state2, "binding": binding2})
    assert r2.status_code == 200
    assert r2.json()["user"]["id"] == body["user"]["id"]


def test_oauth_callback_unverified_email_is_403(client, monkeypatch):
    _configure(monkeypatch)
    _mock_google(monkeypatch, {"id": "sub-2", "email": "unverified@x.com", "verified_email": False})
    binding, state = _start(client)
    r = client.post("/auth/oauth/google/callback", json={"code": "c", "state": state, "redirect_uri": CB, "binding": binding})
    assert r.status_code == 403


def test_oauth_callback_does_not_take_over_password_account(client, monkeypatch):
    _configure(monkeypatch)
    _mock_google(monkeypatch, {"id": "attacker", "email": "demo@chat.com", "verified_email": True})
    binding, state = _start(client)
    r = client.post("/auth/oauth/google/callback", json={"code": "c", "state": state, "redirect_uri": CB, "binding": binding})
    assert r.status_code == 409
