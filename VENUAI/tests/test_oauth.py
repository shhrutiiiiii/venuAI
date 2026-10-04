"""
Real tests for VENUAI/oauth.py — Phase 5's Google/GitHub OAuth flow.
Exercises the actual control flow (state signing/verification, token
exchange, userinfo normalization, error handling) against a mocked HTTP
transport (httpx.MockTransport) — no network call to Google/GitHub,
since this project has no real client_id/client_secret for either
provider (see Documents/Backend/oauth.md). The mocking is of the
transport layer only; none of VENUAI.oauth's own logic is mocked.
"""

import httpx
import pytest

from VENUAI import oauth

# Captured before any test monkeypatches httpx.Client, so the replacement
# lambdas below can still construct a real httpx.Client (just pointed at
# a MockTransport) instead of recursing into themselves.
_RealClient = httpx.Client


@pytest.fixture(autouse=True)
def _unconfigured_providers(monkeypatch):
    """Default: no provider has real credentials — matches this
    environment's actual state. Individual tests opt into "configured"
    by setting the env vars themselves."""
    for env_var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        monkeypatch.delenv(env_var, raising=False)


def test_build_authorize_url_fails_without_credentials():
    with pytest.raises(oauth.OAuthNotConfiguredError):
        oauth.build_authorize_url("google", "http://localhost:3000/callback")


def test_build_authorize_url_succeeds_when_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-client-secret")

    url = oauth.build_authorize_url("google", "http://localhost:3000/callback")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=fake-client-id" in url
    assert "state=" in url


def test_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        oauth.build_authorize_url("facebook", "http://localhost/callback")


def test_state_round_trips_for_correct_provider(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    state = oauth.sign_state("google")
    assert oauth.verify_state(state, expected_provider="google") is True


def test_state_rejected_for_wrong_provider():
    state = oauth.sign_state("google")
    assert oauth.verify_state(state, expected_provider="github") is False


def test_state_rejected_when_garbage():
    assert oauth.verify_state("not-a-real-token", expected_provider="google") is False


def test_exchange_code_for_user_fails_without_credentials():
    with pytest.raises(oauth.OAuthNotConfiguredError):
        oauth.exchange_code_for_user("google", "fake-code", "http://localhost/callback")


def test_exchange_code_for_user_google_success(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "real-looking-access-token"})
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"email": "scientist@drdo.gov.in", "name": "Dr. Scientist"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))

    profile = oauth.exchange_code_for_user("google", "real-code", "http://localhost/callback")
    assert profile["email"] == "scientist@drdo.gov.in"
    assert profile["name"] == "Dr. Scientist"


def test_exchange_code_for_user_google_missing_access_token_raises(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "invalid_grant"})

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))

    with pytest.raises(oauth.OAuthExchangeError, match="no access_token"):
        oauth.exchange_code_for_user("google", "bad-code", "http://localhost/callback")


def test_exchange_code_for_user_token_endpoint_error_raises(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-secret")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid_request")

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))

    with pytest.raises(oauth.OAuthExchangeError, match="token exchange failed"):
        oauth.exchange_code_for_user("google", "bad-code", "http://localhost/callback")


def test_exchange_code_for_user_github_falls_back_to_emails_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "fake-secret")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, json={"access_token": "gh-token"})
        if str(request.url).endswith("/user/emails"):
            return httpx.Response(200, json=[
                {"email": "secondary@example.com", "primary": False},
                {"email": "primary@example.com", "primary": True},
            ])
        if request.url.host == "api.github.com":
            # /user — no public email, triggers the /user/emails fallback
            return httpx.Response(200, json={"login": "drdo-scientist", "email": None})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))

    profile = oauth.exchange_code_for_user("github", "real-code", "http://localhost/callback")
    assert profile["email"] == "primary@example.com"
    assert profile["name"] == "drdo-scientist"


def test_exchange_code_for_user_github_no_email_anywhere_raises(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "fake-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "fake-secret")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, json={"access_token": "gh-token"})
        if str(request.url).endswith("/user/emails"):
            return httpx.Response(200, json=[])
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"login": "no-email-user", "email": None})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))

    with pytest.raises(oauth.OAuthExchangeError, match="no accessible email"):
        oauth.exchange_code_for_user("github", "real-code", "http://localhost/callback")
