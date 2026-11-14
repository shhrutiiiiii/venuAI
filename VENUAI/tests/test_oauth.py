"""
Real tests for VENUAI/oauth.py — Google/GitHub OAuth flow (S1-hardened).
Exercises the actual control flow (authorize URL + PKCE, browser-bound
single-use state, token exchange, userinfo normalization, verified-email and
account-link rules) against a mocked HTTP transport (httpx.MockTransport) —
no network call, since this project has no real client credentials (see
Documents/Backend/oauth.md). Only the transport is mocked.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from VENUAI import auth, oauth

_RealClient = httpx.Client
REDIRECT = "http://localhost:3000/api/auth/oauth/google/callback"
GH_REDIRECT = "http://localhost:3000/api/auth/oauth/github/callback"


@pytest.fixture(autouse=True)
def _env(monkeypatch, isolated_auth_db):
    for env_var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")


def _configure(monkeypatch, provider="google"):
    monkeypatch.setenv(f"{provider.upper()}_CLIENT_ID", "fake-id")
    monkeypatch.setenv(f"{provider.upper()}_CLIENT_SECRET", "fake-secret")


def _mock_google(monkeypatch, userinfo, seen=None):
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            if seen is not None:
                seen["token_body"] = request.content.decode()
            return httpx.Response(200, json={"access_token": "real-looking-access-token"})
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json=userinfo)
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))


def _begin(monkeypatch, provider="google", redirect=REDIRECT):
    _configure(monkeypatch, provider)
    url, binding = oauth.begin_authorization(provider, redirect)
    state = parse_qs(urlsplit(url).query)["state"][0]
    return url, binding, state


GOOD = {"id": "g-123", "email": "scientist@drdo.gov.in", "name": "Dr. Scientist", "verified_email": True}


# ── authorize URL ────────────────────────────────────────────────────────────

def test_authorize_fails_without_credentials():
    with pytest.raises(oauth.OAuthNotConfiguredError):
        oauth.begin_authorization("google", REDIRECT)


def test_authorize_url_has_state_and_pkce_s256(monkeypatch):
    url, binding, state = _begin(monkeypatch)
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["code_challenge_method"] == ["S256"]
    assert q["client_id"] == ["fake-id"]
    assert state and binding and state != binding
    # the verifier is never exposed in the URL
    with auth._get_conn() as conn:
        verifier = conn.execute("SELECT code_verifier FROM oauth_states").fetchone()[0]
    assert verifier not in url
    expect = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [expect]


def test_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        oauth.begin_authorization("facebook", "http://localhost:3000/api/auth/oauth/facebook/callback")


@pytest.mark.parametrize("bad", [
    "https://evil.example/api/auth/oauth/google/callback",
    "http://localhost:3000/somewhere/else",
    "http://localhost:3000/api/auth/oauth/github/callback",       # wrong provider path
    "http://localhost:3000/api/auth/oauth/google/callback?x=1",
    "http://localhost:3000.evil.example/api/auth/oauth/google/callback",
])
def test_redirect_uri_outside_allowlist_is_rejected(monkeypatch, bad):
    _configure(monkeypatch)
    with pytest.raises(ValueError):
        oauth.begin_authorization("google", bad)


# ── state / binding ──────────────────────────────────────────────────────────

def test_state_is_single_use(monkeypatch):
    _, binding, state = _begin(monkeypatch)
    assert oauth.consume_state("google", state, binding, REDIRECT)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.consume_state("google", state, binding, REDIRECT)  # replay


def test_state_without_browser_binding_is_rejected(monkeypatch):
    _, _binding, state = _begin(monkeypatch)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.consume_state("google", state, None, REDIRECT)


def test_state_with_someone_elses_binding_is_rejected(monkeypatch):
    _, _binding, state = _begin(monkeypatch)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.consume_state("google", state, "attacker-binding", REDIRECT)


def test_state_for_other_provider_or_redirect_is_rejected(monkeypatch):
    _, binding, state = _begin(monkeypatch)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.consume_state("github", state, binding, REDIRECT)
    _, binding2, state2 = _begin(monkeypatch)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.consume_state("google", state2, binding2, "http://localhost:3000/api/auth/oauth/google/callback2")


def test_garbage_and_expired_state_rejected(monkeypatch):
    _, binding, state = _begin(monkeypatch)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.consume_state("google", "garbage", binding, REDIRECT)
    with auth._get_conn() as conn:
        conn.execute("UPDATE oauth_states SET expires_at = 1")
        conn.commit()
    with pytest.raises(oauth.OAuthLoginError):
        oauth.consume_state("google", state, binding, REDIRECT)


# ── code exchange / normalization ────────────────────────────────────────────

def test_exchange_fails_without_credentials():
    with pytest.raises(oauth.OAuthNotConfiguredError):
        oauth.exchange_code_for_user("google", "fake-code", REDIRECT)


def test_exchange_google_success_sends_pkce_verifier(monkeypatch):
    _configure(monkeypatch)
    seen = {}
    _mock_google(monkeypatch, GOOD, seen)
    profile = oauth.exchange_code_for_user("google", "real-code", REDIRECT, code_verifier="ver-123")
    assert profile == {"email": "scientist@drdo.gov.in", "name": "Dr. Scientist", "sub": "g-123", "email_verified": True}
    assert "code_verifier=ver-123" in seen["token_body"]


def test_exchange_google_unverified_email_flagged(monkeypatch):
    _configure(monkeypatch)
    _mock_google(monkeypatch, {**GOOD, "verified_email": False})
    assert oauth.exchange_code_for_user("google", "c", REDIRECT)["email_verified"] is False


def test_exchange_google_missing_access_token_raises(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": "invalid_grant"}))))
    with pytest.raises(oauth.OAuthExchangeError, match="no access_token"):
        oauth.exchange_code_for_user("google", "bad-code", REDIRECT)


def test_exchange_token_endpoint_error_raises(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, text="invalid_request"))))
    with pytest.raises(oauth.OAuthExchangeError, match="token exchange failed"):
        oauth.exchange_code_for_user("google", "bad-code", REDIRECT)


def _mock_github(monkeypatch, emails):
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, json={"access_token": "gh-token"})
        if str(request.url).endswith("/user/emails"):
            return httpx.Response(200, json=emails)
        if request.url.host == "api.github.com":
            # public profile email is deliberately set: it must NOT be trusted
            return httpx.Response(200, json={"id": 77, "login": "drdo-scientist", "email": "public@unverified.example"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda timeout=10.0: _RealClient(transport=httpx.MockTransport(_handler)))


def test_github_uses_primary_verified_email_only(monkeypatch):
    _configure(monkeypatch, "github")
    _mock_github(monkeypatch, [
        {"email": "secondary@example.com", "primary": False, "verified": True},
        {"email": "unverified-primary@example.com", "primary": True, "verified": False},
    ])
    with pytest.raises(oauth.OAuthExchangeError, match="verified"):
        oauth.exchange_code_for_user("github", "c", GH_REDIRECT)

    _mock_github(monkeypatch, [{"email": "primary@example.com", "primary": True, "verified": True}])
    profile = oauth.exchange_code_for_user("github", "c", GH_REDIRECT)
    assert profile["email"] == "primary@example.com"
    assert profile["sub"] == "77"


# ── complete_login: takeover attempts ────────────────────────────────────────

def _login(monkeypatch, userinfo):
    _, binding, state = _begin(monkeypatch)
    _mock_google(monkeypatch, userinfo)
    return oauth.complete_login("google", "code", state, binding, REDIRECT)


def test_new_verified_user_gets_personal_tenant_and_identity(monkeypatch):
    row = _login(monkeypatch, GOOD)
    assert row["email"] == "scientist@drdo.gov.in"
    assert row["tenant_id"] != "default"
    with auth._get_conn() as conn:
        assert conn.execute("SELECT user_id FROM oauth_identities WHERE provider='google' AND sub='g-123'").fetchone()


def test_second_login_resolves_by_sub_even_if_email_changes(monkeypatch):
    first = _login(monkeypatch, GOOD)
    second = _login(monkeypatch, {**GOOD, "email": "renamed@drdo.gov.in"})
    assert second["id"] == first["id"]


def test_unverified_email_cannot_log_in_or_register(monkeypatch):
    with pytest.raises(oauth.OAuthLoginError) as e:
        _login(monkeypatch, {**GOOD, "verified_email": False})
    assert e.value.status_code == 403
    assert auth.get_user_by_email(GOOD["email"]) is None


def test_oauth_takeover_of_existing_password_account_is_refused(monkeypatch):
    victim = auth.create_user("victim@corp.com", "Victim-Pass-12345", organization_name="Victim Co")
    with pytest.raises(oauth.OAuthLoginError) as e:
        _login(monkeypatch, {"id": "attacker-sub", "email": "victim@corp.com", "verified_email": True})
    assert e.value.status_code == 409
    with auth._get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM oauth_identities WHERE user_id = ?", (victim.id,)).fetchone()[0] == 0


def test_callback_with_wrong_binding_never_reaches_provider(monkeypatch):
    _, binding, state = _begin(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("provider must not be called when state binding fails")

    monkeypatch.setattr(httpx, "Client", _boom)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.complete_login("google", "code", state, "attacker", REDIRECT)


def test_callback_replay_of_same_state_fails(monkeypatch):
    _, binding, state = _begin(monkeypatch)
    _mock_google(monkeypatch, GOOD)
    oauth.complete_login("google", "code", state, binding, REDIRECT)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.complete_login("google", "code", state, binding, REDIRECT)


def test_callback_with_disallowed_redirect_uri_rejected(monkeypatch):
    _, binding, state = _begin(monkeypatch)
    with pytest.raises(oauth.OAuthLoginError):
        oauth.complete_login("google", "code", state, binding, "https://evil.example/api/auth/oauth/google/callback")
