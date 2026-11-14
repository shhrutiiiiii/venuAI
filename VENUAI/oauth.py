"""
OAuth (Google / GitHub) sign-in — Phase 5, hardened in S1.

Real authorization-code flow. Genuinely blocked for live use here only because
no real client credentials exist (GOOGLE_CLIENT_ID/SECRET, GITHUB_CLIENT_ID/
SECRET); every provider call is exercised against a mocked HTTP layer in
VENUAI/tests/test_oauth*.py. Never fake a missing credential.

Hardening (see Documents/Backend/oauth.md, ADR-S1.4):
  - state is random, stored hashed server-side, single-use, bound to the
    browser via a binding cookie (set by the Next login route), PKCE S256.
  - redirect_uri must match an allow-list derived from FRONTEND_URL /
    ALLOWED_ORIGINS / OAUTH_ALLOWED_ORIGINS.
  - only a provider-verified email counts; accounts are keyed on (provider, sub).
  - an existing password account is never auto-linked by email.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

import httpx

from VENUAI import auth, security_audit

STATE_TTL_SECONDS = 600  # 10 minutes — just long enough for a real login redirect round-trip


class OAuthNotConfiguredError(RuntimeError):
    """Raised when a provider's client_id/client_secret env vars aren't
    set — the caller (a route handler) turns this into a clear 503, never
    a silently-faked success."""


class OAuthExchangeError(RuntimeError):
    """Raised when the provider's own token/userinfo endpoints reject or
    error out on a real request — always carries the provider's real
    error message, never a fabricated one."""


class OAuthLoginError(RuntimeError):
    """A deliberate, user-presentable refusal (state/binding/redirect problem,
    unverified email, account-link refusal). Carries the HTTP status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


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


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _allowed_origins() -> set:
    origins = set()
    for raw in (
        os.getenv("OAUTH_ALLOWED_ORIGINS", ""),
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        os.getenv("ALLOWED_ORIGINS", ""),
    ):
        for item in raw.split(","):
            item = item.strip().rstrip("/")
            if item and item != "*":
                parts = urlsplit(item)
                if parts.scheme and parts.netloc:
                    origins.add(f"{parts.scheme}://{parts.netloc}")
    return origins


def is_redirect_uri_allowed(provider_name: str, redirect_uri: str) -> bool:
    """redirect_uri must be exactly <allowed frontend origin>/api/auth/oauth/<provider>/callback."""
    parts = urlsplit(redirect_uri)
    if parts.query or parts.fragment or not parts.scheme or not parts.netloc:
        return False
    return (
        f"{parts.scheme}://{parts.netloc}" in _allowed_origins()
        and parts.path == f"/api/auth/oauth/{provider_name}/callback"
    )


def begin_authorization(provider_name: str, redirect_uri: str) -> tuple:
    """Returns (authorize_url, binding). The caller (the Next route) must store
    `binding` in a short-lived httpOnly cookie and send it back on callback —
    that cookie ties the flow to THIS browser (login-CSRF defence). State and
    the PKCE verifier live server-side, single-use."""
    provider = get_provider(provider_name)
    if not provider.is_configured():
        raise OAuthNotConfiguredError(
            f"OAuth provider '{provider_name}' is not configured — set "
            f"{provider.client_id_env} and {provider.client_secret_env}."
        )
    if not is_redirect_uri_allowed(provider_name, redirect_uri):
        raise ValueError("redirect_uri is not allowed.")
    state = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with auth._get_conn() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (time.time(),))
        conn.execute(
            "INSERT INTO oauth_states (state_hash, provider, binding_hash, code_verifier, redirect_uri, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (_h(state), provider_name, _h(binding), verifier, redirect_uri, time.time() + STATE_TTL_SECONDS),
        )
        conn.commit()
    params = httpx.QueryParams({
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{provider.authorize_url}?{params}", binding


def consume_state(provider_name: str, state: str, binding: Optional[str], redirect_uri: str) -> str:
    """Atomically spend the state (single use) and return its PKCE verifier.
    Raises OAuthLoginError unless provider, browser binding and redirect_uri all match."""
    bad = OAuthLoginError(400, "Invalid or expired OAuth state — please try signing in again.")
    if not state or not binding:
        raise bad
    sh = _h(state)
    with auth._get_conn() as conn:
        cur = conn.execute(
            "UPDATE oauth_states SET used_at = ? WHERE state_hash = ? AND used_at IS NULL AND expires_at > ?",
            (time.time(), sh, time.time()),
        )
        conn.commit()
        if cur.rowcount != 1:
            raise bad
        row = conn.execute("SELECT * FROM oauth_states WHERE state_hash = ?", (sh,)).fetchone()
    if (
        row["provider"] != provider_name
        or not hmac.compare_digest(row["binding_hash"], _h(binding))
        or row["redirect_uri"] != redirect_uri
    ):
        raise bad
    return row["code_verifier"]


def exchange_code_for_user(
    provider_name: str, code: str, redirect_uri: str, code_verifier: Optional[str] = None
) -> dict:
    """
    Real two-step exchange: authorization code (+ PKCE verifier) -> access
    token -> user profile, normalized to {email, name, sub, email_verified}.

    Raises OAuthNotConfiguredError / OAuthExchangeError rather than ever
    returning a fabricated user.
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
                **({"code_verifier": code_verifier} if code_verifier else {}),
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise OAuthExchangeError(f"{provider_name} token exchange failed ({token_resp.status_code}).")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise OAuthExchangeError(f"{provider_name} token response had no access_token.")

        userinfo_resp = client.get(
            provider.userinfo_url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        if userinfo_resp.status_code != 200:
            raise OAuthExchangeError(f"{provider_name} userinfo fetch failed ({userinfo_resp.status_code}).")

        userinfo = userinfo_resp.json()

    return _normalize_userinfo(provider_name, userinfo, access_token=access_token)


def _normalize_userinfo(provider_name: str, userinfo: dict, access_token: str) -> dict:
    """Normalize to {email, name, sub, email_verified}. Only a provider-VERIFIED
    email counts (Google verified_email/email_verified; GitHub /user/emails entry
    that is both primary and verified — the public /user email is not trusted)."""
    if provider_name == "google":
        email = userinfo.get("email")
        if not email:
            raise OAuthExchangeError("Google userinfo response had no email.")
        sub = userinfo.get("id") or userinfo.get("sub")
        if not sub:
            raise OAuthExchangeError("Google userinfo response had no subject id.")
        verified = userinfo.get("verified_email", userinfo.get("email_verified")) is True
        return {"email": email, "name": userinfo.get("name", ""), "sub": str(sub), "email_verified": verified}

    if provider_name == "github":
        sub = userinfo.get("id")
        if not sub:
            raise OAuthExchangeError("GitHub user response had no id.")
        email = None
        with httpx.Client(timeout=10.0) as client2:
            emails_resp = client2.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
        if emails_resp.status_code == 200:
            email = next(
                (e["email"] for e in emails_resp.json() if e.get("primary") and e.get("verified") is True), None
            )
        if not email:
            raise OAuthExchangeError(
                "GitHub account has no primary verified email address (check the 'user:email' scope)."
            )
        return {
            "email": email, "name": userinfo.get("name") or userinfo.get("login", ""),
            "sub": str(sub), "email_verified": True,
        }

    raise ValueError(f"Unknown provider '{provider_name}' in _normalize_userinfo.")


def complete_login(
    provider_name: str, code: str, state: str, binding: Optional[str], redirect_uri: str,
    ip: Optional[str] = None,
):
    """Whole callback: redirect/state/binding check -> PKCE code exchange ->
    identity resolution. Returns the local user row. Accounts are keyed on the
    provider's stable `sub`, never on email; an existing password account is
    NEVER silently linked by email (pre-account-takeover defence)."""
    get_provider(provider_name)
    if not is_redirect_uri_allowed(provider_name, redirect_uri):
        security_audit.log("oauth_rejected", None, None, ip, {"reason": "redirect_uri", "provider": provider_name})
        raise OAuthLoginError(400, "redirect_uri is not allowed.")
    try:
        verifier = consume_state(provider_name, state, binding, redirect_uri)
    except OAuthLoginError:
        security_audit.log("oauth_rejected", None, None, ip, {"reason": "state", "provider": provider_name})
        raise
    profile = exchange_code_for_user(provider_name, code, redirect_uri, code_verifier=verifier)
    if not profile.get("email_verified"):
        security_audit.log("oauth_rejected", None, None, ip, {"reason": "email_unverified", "provider": provider_name})
        raise OAuthLoginError(403, "Your provider account's email address is not verified.")

    with auth._get_conn() as conn:
        ident = conn.execute(
            "SELECT user_id FROM oauth_identities WHERE provider = ? AND sub = ?", (provider_name, profile["sub"])
        ).fetchone()
    if ident is not None:
        user_row = auth.get_user_by_id(ident["user_id"])
        if user_row is not None:
            security_audit.log("oauth_login", user_row["id"], user_row["tenant_id"], ip, {"provider": provider_name})
            return user_row

    if auth.get_user_by_email(profile["email"]) is not None:
        security_audit.log(
            "oauth_link_refused", None, None, ip, {"provider": provider_name, "email": profile["email"].lower()}
        )
        raise OAuthLoginError(409, "An account with this email already exists. Sign in with your password instead.")

    display = profile.get("name") or profile["email"].split("@")[0]
    created = auth.create_user(
        profile["email"], secrets.token_urlsafe(32), organization_name=f"{display}'s workspace",
    )
    with auth._get_conn() as conn:
        conn.execute(
            "INSERT INTO oauth_identities (provider, sub, user_id, email, created_at) VALUES (?,?,?,?,?)",
            (provider_name, profile["sub"], created.id, profile["email"].lower(), time.time()),
        )
        conn.commit()
    security_audit.log("oauth_register", created.id, created.tenant_id, ip, {"provider": provider_name})
    return auth.get_user_by_id(created.id)
