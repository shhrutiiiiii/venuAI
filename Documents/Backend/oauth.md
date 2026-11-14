# OAuth Sign-In (Google / GitHub) — VenuAI

## Purpose
Documents Phase 5's real Google/GitHub OAuth implementation, and states plainly what is genuinely blocked (missing real provider credentials) versus what is real, working, and tested.

## Responsibilities
- Describe the real authorization-code flow this implements.
- State exactly which env vars activate each provider, and what happens when they're absent.
- Explain why the login page's existing "Sign in with Google/GitHub" buttons were deliberately **not** rewired to this flow in this pass.

## Scope
Covers `VENUAI/oauth.py`, the `/auth/oauth/{provider}/login` and `/auth/oauth/{provider}/callback` routes in `VENUAI/rag_server.py`, and the Next.js proxy routes under `chat-app/src/app/api/auth/oauth/[provider]/`.

## High-Level Overview
```
Browser → GET /api/auth/oauth/google/login
             │  Next.js asks backend for the real authorize URL
             ▼
         GET /auth/oauth/google/login?redirect_uri=.../api/auth/oauth/google/callback
             │  503 if GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET unset — never a URL that can't complete
             ▼
         {authorize_url: "https://accounts.google.com/o/oauth2/v2/auth?...&state=<signed-jwt>"}
             │  Next.js redirects the browser here
             ▼
Google's real consent screen → user approves → Google redirects to
         GET /api/auth/oauth/google/callback?code=...&state=...
             │  Next.js POSTs {code, state, redirect_uri} to the backend
             ▼
         POST /auth/oauth/google/callback
             │  1. verify_state() — the state JWT must be unexpired, type "oauth_state", for this provider
             │  2. exchange_code_for_user() — real token exchange + userinfo fetch (or 502 with the
             │     provider's actual error on failure)
             │  3. find-or-create local user by email, issue the same access/refresh JWT pair /auth/login does
             ▼
         {access_token, refresh_token, user}
             │  Next.js sets the same httpOnly cookies /api/auth/login sets
             ▼
Browser redirected to /chat, signed in
```

## Design Principles
1. **CSRF state is a signed JWT, not a server-side session.** `sign_state()`/`verify_state()` reuse `auth.py`'s existing `JWT_SECRET_KEY`/`JWT_ALGORITHM` rather than standing up a session store just to hold one 10-minute nonce — the state token itself carries its own expiry and the provider name it was issued for, and `jwt.decode` rejects anything tampered with or expired.
2. **A real failure is never swallowed into a fake success.** `build_authorize_url()` raises `OAuthNotConfiguredError` rather than returning a URL that will dead-end; `exchange_code_for_user()` raises `OAuthExchangeError` carrying the provider's actual error text on any non-200 response from Google/GitHub's real endpoints.
3. **Provider-specific userinfo normalization is real, not guessed.** Google's userinfo always has a top-level `email`. GitHub's `/user` endpoint often has `email: null` (private-by-default) and needs a separate authenticated call to `/user/emails`, preferring the entry marked `"primary": true` — both branches are exercised in `VENUAI/tests/test_oauth.py` against mocked responses shaped exactly like each provider's real API.
4. **OAuth sign-in produces the exact same session as password sign-in.** The callback route issues the same `auth.create_access_token`/`auth.issue_refresh_token` pair `/auth/login` does, and the frontend callback route sets the same httpOnly cookies `/api/auth/login` does — there is no second, parallel session mechanism to keep in sync.
5. **The login page's existing buttons were deliberately left untouched.** They already honestly document themselves as a demo-credential shortcut, not real OAuth (see `Documents/Backend/authentication.md`). Rewiring them to `/api/auth/oauth/*` without being able to test against a real Google/GitHub app (no real credentials exist in this environment) would mean shipping a UI claim ("Sign in with Google") this session cannot actually verify end-to-end — see Not Yet Implemented.

## Workflow
See the diagram above.

## Inputs
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` — all absent by default in this environment.

## Outputs
Same `TokenResponse` shape (`access_token`, `refresh_token`, `user`) as `POST /auth/login`.

## Dependencies
- `VENUAI/auth.py` — `JWT_SECRET_KEY`/`JWT_ALGORITHM` (state signing), `create_access_token`/`issue_refresh_token`/`create_user`/`get_user_by_email` (session issuance and account lookup/creation).
- `httpx` — the real HTTP client used for the token-exchange and userinfo calls.

## Interactions with Other Services
Real, live calls to `accounts.google.com`/`oauth2.googleapis.com`/`www.googleapis.com` or `github.com`/`api.github.com` — but only once real credentials are configured; see Security Considerations.

## Security Considerations
- State tokens expire in `STATE_TTL_SECONDS=600` (10 minutes) — long enough for a real consent-screen round trip, short enough to bound a replay window.
- `verify_state()` checks both the token's signature/expiry *and* that it was issued for the specific provider being called back into — a state token signed for `google` cannot be replayed against the `github` callback.
- An auto-created OAuth account gets a random, never-surfaced password (`secrets.token_urlsafe(32)`) via the existing `auth.create_user()` path — there is no "set your password" step for an OAuth-only account today (a real, if minor, follow-up: such a user currently has no way to also sign in with a password, which is arguably correct, but isn't an explicit design decision documented elsewhere yet).

## Performance Considerations
Two sequential real HTTP calls per callback (token exchange, then userinfo — three for a GitHub account with no public email) — this is the real, unavoidable cost of the actual OAuth protocol, not an optimization target.

## Not Yet Implemented (genuinely blocked by missing external credentials)
1. **Live verification against real Google/GitHub.** Every code path in `VENUAI/oauth.py` is exercised against a mocked HTTP transport (`VENUAI/tests/test_oauth.py`, `test_oauth_endpoints.py`) — real control flow, real error handling, but never a live call to Google's or GitHub's actual servers, because this project has no registered OAuth application (no `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, no `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`) and registering one requires a real Google Cloud Console / GitHub Developer Settings account action outside this codebase.
2. **Rewiring the login page's "Sign in with Google/GitHub" buttons** to actually call `/api/auth/oauth/{provider}/login` — a two-line change (`<a href="/api/auth/oauth/google/login">` in place of the current demo-credential-autofill `onClick`) once real credentials exist and the flow has been verified against them live. Deliberately not done in this pass — see Design Principle 5.

## Notes for Developers
When real credentials are eventually added, do the rewiring in #2 above as its own small, separately-verifiable change — don't bundle it with unrelated work, since "does this actually sign a real user in through a real browser" is the one thing that can only be confirmed by someone who has the real `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (or GitHub's equivalent) and a browser to click through it.

## S1 hardening (supersedes the signed-JWT state described above)

- `GET /auth/oauth/{provider}/login` returns `{authorize_url, binding}`. State is random, hashed in `oauth_states`, single use (10 min); PKCE S256, verifier stored server-side. The Next login route puts `binding` in an httpOnly cookie (`venuai_oauth_binding`, path `/api/auth/oauth`); the callback sends it back and the backend requires it to match, so a state planted into a victim's browser fails.
- `redirect_uri` must be `<FRONTEND_URL | ALLOWED_ORIGINS | OAUTH_ALLOWED_ORIGINS origin>/api/auth/oauth/<provider>/callback`.
- Only verified emails: Google `verified_email`/`email_verified`; GitHub primary + verified entry of `/user/emails` (the public profile email is ignored).
- Accounts key on `oauth_identities(provider, sub)`. An existing password account with the same email is NOT linked (409, audited as `oauth_link_refused`); a signed-in "connect provider" action is future work. New users get a personal tenant.
- Provider error bodies are logged server-side, not returned to the browser.

