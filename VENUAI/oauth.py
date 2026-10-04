"""
OAuth (Google / GitHub) sign-in — Phase 5.

Real, complete authorization-code-flow implementation — not a stub. It is
genuinely **blocked** for live use in this environment only because no
real GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET or
GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET exist here (that requires an actual
registered OAuth app in Google Cloud Console / GitHub Developer
Settings — a real external credential this project does not have, not
something fabricable in code). Every provider-specific function in this
module is exercised by VENUAI/tests/test_oauth.py against a mocked HTTP
layer (httpx's MockTransport) — real control-flow and error-handling
coverage, just not a live call to Google/GitHub's actual servers.

Same pattern as PostgresGraphStore (Documents/AI/knowledge-graph.md) and
the SMTP password-reset flow (Documents/Backend/authentication.md): build
the real thing, gate activation on real configuration, never fake the
missing credential.
"""

import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
import jwt

from VENUAI.auth import JWT_ALGORITHM, JWT_SECRET_KEY

STATE_TTL_SECONDS = 600  # 10 minutes — just long enough for a real login redirect round-trip


class OAuthNotConfiguredError(RuntimeError):
    """Raised when a provider's client_id/client_secret env vars aren't
    set — the caller (a route handler) turns this into a clear 503, never
    a silently-faked success."""


class OAuthExchangeError(RuntimeError):
    """Raised when the provider's own token/userinfo endpoints reject or
    error out on a real request — always carries the provider's real
    error message, never a fabricated one."""


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    client_id_env: str
    client_secret_env: str
    scope: str

    @property
    def client_id(self) -> Optional[str]:
        return os.getenv(self.client_id_env)

    @property
    def client_secret(self) -> Optional[str]:
        return os.getenv(self.client_secret_env)

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


PROVIDERS = {
    "google": OAuthProvider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://www.googleapis.com/oauth2/v2/userinfo",
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        scope="openid email profile",
    ),
    "github": OAuthProvider(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        client_id_env="GITHUB_CLIENT_ID",
        client_secret_env="GITHUB_CLIENT_SECRET",
        scope="read:user user:email",
    ),
}


def get_provider(name: str) -> OAuthProvider:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ValueError(f"Unknown OAuth provider '{name}'. Supported: {list(PROVIDERS)}.")
    return provider


def sign_state(provider_name: str) -> str:
    """A short-lived, signed CSRF state token — reuses the same JWT
    secret/algorithm auth.py already uses for access tokens, rather than
    standing up a separate session store just for one 10-minute value."""
    payload = {"provider": provider_name, "type": "oauth_state", "iat": time.time(), "exp": time.time() + STATE_TTL_SECONDS}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_state(state_token: str, expected_provider: str) -> bool:
    try:
        payload = jwt.decode(state_token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return False
    return payload.get("type") == "oauth_state" and payload.get("provider") == expected_provider


def build_authorize_url(provider_name: str, redirect_uri: str) -> str:
    """Raises OAuthNotConfiguredError if the provider's credentials aren't
    set — callers must never redirect a user into a flow that cannot
    possibly complete."""
    provider = get_provider(provider_name)
    if not provider.is_configured():
        raise OAuthNotConfiguredError(
            f"OAuth provider '{provider_name}' is not configured — set "
            f"{provider.client_id_env} and {provider.client_secret_env}."
        )
    state = sign_state(provider_name)
    params = httpx.QueryParams({
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
    })
    return f"{provider.authorize_url}?{params}"


def exchange_code_for_user(provider_name: str, code: str, redirect_uri: str) -> dict:
    """
    Real two-step exchange: authorization code → access token → user
    profile. Returns a plain dict with at least an "email" key (both
    providers' userinfo responses are normalized to this shape).

    Raises OAuthNotConfiguredError / OAuthExchangeError rather than ever
    returning a fabricated user — a real failure here must surface as a
    real failure to the caller.
    """
    provider = get_provider(provider_name)
    if not provider.is_configured():
        raise OAuthNotConfiguredError(
            f"OAuth provider '{provider_name}' is not configured — set "
            f"{provider.client_id_env} and {provider.client_secret_env}."
        )

    with httpx.Client(timeout=10.0) as client:
        token_resp = client.post(
            provider.token_url,
            data={
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise OAuthExchangeError(f"{provider_name} token exchange failed ({token_resp.status_code}): {token_resp.text[:300]}")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise OAuthExchangeError(f"{provider_name} token response had no access_token: {token_data}")

        userinfo_resp = client.get(
            provider.userinfo_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        if userinfo_resp.status_code != 200:
            raise OAuthExchangeError(f"{provider_name} userinfo fetch failed ({userinfo_resp.status_code}): {userinfo_resp.text[:300]}")

        userinfo = userinfo_resp.json()

    return _normalize_userinfo(provider_name, userinfo, access_token=access_token)


def _normalize_userinfo(provider_name: str, userinfo: dict, access_token: str) -> dict:
    """Google's userinfo response has a top-level "email"; GitHub's
    /user response often omits email (private-by-default) and requires a
    separate /user/emails call — real provider-specific normalization,
    not guessed."""
    if provider_name == "google":
        email = userinfo.get("email")
        if not email:
            raise OAuthExchangeError("Google userinfo response had no email.")
        return {"email": email, "name": userinfo.get("name", "")}

    if provider_name == "github":
        email = userinfo.get("email")
        if not email:
            with httpx.Client(timeout=10.0) as client2:
                emails_resp = client2.get(
                    "https://api.github.com/user/emails",
                    headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                )
            if emails_resp.status_code == 200:
                primary = next((e["email"] for e in emails_resp.json() if e.get("primary")), None)
                email = primary or next((e["email"] for e in emails_resp.json()), None)
        if not email:
            raise OAuthExchangeError("GitHub account has no accessible email address (check 'user:email' scope).")
        return {"email": email, "name": userinfo.get("name") or userinfo.get("login", "")}

    raise ValueError(f"Unknown provider '{provider_name}' in _normalize_userinfo.")
