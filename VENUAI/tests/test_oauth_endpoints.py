"""
Real end-to-end TestClient tests for the /auth/oauth/* routes
(VENUAI/rag_server.py). See VENUAI/tests/test_oauth.py for the underlying
VENUAI/oauth.py module tests (mocked HTTP transport); these test the
HTTP-layer wiring on top: the route exists, state is actually enforced,
missing credentials surface as a real 503.
"""

import httpx
import pytest

from VENUAI import oauth

_RealClient = httpx.Client


@pytest.fixture(autouse=True)
def _unconfigured_providers(monkeypatch):
    for env_var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        monkeypatch.delenv(env_var, raising=False)


def test_oauth_login_returns_503_when_not_configured(client):
    r = client.get("/auth/oauth/google/login", params={"redirect_uri": "http://localhost:3000/cb"})
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


def test_oauth_login_returns_400_for_unknown_provider(client):
    r = client.get("/auth/oauth/facebook/login", params={"redirect_uri": "http://localhost:3000/cb"})
    assert r.status_code == 400


def test_oauth_login_returns_real_authorize_url_when_configured(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")

    r = client.get("/auth/oauth/google/login", params={"redirect_uri": "http://localhost:3000/cb"})
    assert r.status_code == 200
    assert r.json()["authorize_url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_oauth_callback_rejects_invalid_state(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")

    r = client.post("/auth/oauth/google/callback", json={
        "code": "irrelevant", "state": "garbage-not-a-real-state", "redirect_uri": "http://localhost:3000/cb",
    })
    assert r.status_code == 400


def test_oauth_callback_creates_and_logs_in_a_new_user(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")

    state = oauth.sign_state("google")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "real-looking-token"})
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"email": "new-oauth-user@drdo.gov.in", "name": "New User"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))

    r = client.post("/auth/oauth/google/callback", json={
        "code": "real-code", "state": state, "redirect_uri": "http://localhost:3000/cb",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "new-oauth-user@drdo.gov.in"
    assert body["access_token"]
    assert body["refresh_token"]

    # A second callback for the same email must log into the SAME
    # account, not create a duplicate.
    state2 = oauth.sign_state("google")
    r2 = client.post("/auth/oauth/google/callback", json={
        "code": "real-code-2", "state": state2, "redirect_uri": "http://localhost:3000/cb",
    })
    assert r2.status_code == 200
    assert r2.json()["user"]["id"] == body["user"]["id"]
