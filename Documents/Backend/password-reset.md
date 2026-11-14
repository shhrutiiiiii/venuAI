# Password Reset — VenuAI

## Purpose
Documents Phase 5's real SMTP-backed password-reset flow, replacing the previous cosmetic mock (`/forgot-password` with a `setTimeout` and no backend call).

## Responsibilities
- Describe the real token sign/verify mechanism and why it needs no new database table.
- State what activates real email delivery and what happens without it.
- Document the email-enumeration-resistance design explicitly.

## Scope
Covers `VENUAI/email_service.py`, the password-reset additions to `VENUAI/auth.py` (`create_password_reset_token`, `verify_password_reset_token`, `update_password`), the `/auth/forgot-password`+`/auth/reset-password` routes, and the `chat-app/src/app/forgot-password` + new `chat-app/src/app/reset-password` pages.

## High-Level Overview
```
POST /auth/forgot-password {email}
   │  1. Look up the account — unknown email → 202 immediately (no enumeration signal)
   │  2. SMTP not configured → 503 (a real server-side problem, not hidden)
   │  3. Otherwise: sign a 30-minute JWT reset token, send a real email containing
   │     {FRONTEND_URL}/reset-password?token=<token>
   ▼
202 Accepted (always, for a real account whose email actually got sent)

User clicks the emailed link → /reset-password?token=... (real frontend page)
   │
   ▼
POST /auth/reset-password {token, new_password}
   │  1. verify_password_reset_token() — must be unexpired, type "password_reset"
   │  2. update_password() — real bcrypt hash update + revoke_all_refresh_tokens()
   ▼
200 OK — old password and every existing session are now dead; new password works immediately
```

## Design Principles
1. **No new database table for reset tokens.** `create_password_reset_token()`/`verify_password_reset_token()` reuse the same `JWT_SECRET_KEY`/`JWT_ALGORITHM` `auth.py` already signs access tokens with — a self-contained, self-expiring (30 minutes) token needs no server-side row to track, the same reasoning `VENUAI/oauth.py`'s CSRF state token uses.
2. **Email-enumeration resistance is a real design constraint, not an afterthought.** `POST /auth/forgot-password` returns `202 Accepted` for an unknown email immediately — the response for "no such account" and "account exists, email sent" must look the same from the outside. The one place this isn't perfectly symmetric: a *real* account with SMTP unconfigured gets a 503, which is still never a "yes this account exists" confirmation (an attacker gets the same 503-vs-202 signal regardless of which real/fake email they tried, since SMTP configuration is server-global, not per-account).
3. **A password reset is a real security event.** `update_password()` doesn't just rehash — it calls `auth.revoke_all_refresh_tokens(user_id)`, so every session issued under the old password dies immediately. Verified end-to-end in `test_reset_password_end_to_end`: after reset, the old password fails login and the new one succeeds.
4. **A real failure is never swallowed into a fake success.** `email_service.send_email()` raises `EmailNotConfiguredError`/`EmailSendError` on any real failure (missing config, SMTP connection refused, auth rejected, etc.) — the route handler turns these into 503/502, never a silent 202 that implies an email was sent when it wasn't.

## Workflow
See the diagram above.

## Inputs
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL` — all absent by default in this environment; `FRONTEND_URL` (default `http://localhost:3000`) for building the reset link.

## Outputs
`202 Accepted` (forgot-password, always for a real send-or-nonexistent-account outcome), `200 OK` with `{"status": "password_reset"}` (reset-password).

## Dependencies
- `VENUAI/auth.py` — `hash_password`, `revoke_all_refresh_tokens`, the JWT constants reused for token signing.
- `smtplib`/`email.message` (Python standard library) — no new third-party dependency for email delivery itself.

## Interactions with Other Services
A real SMTP server, once configured — Gmail (with an app password), SendGrid, Mailgun, AWS SES, or any other STARTTLS-capable SMTP provider. None is configured in this environment.

## Security Considerations
- Reset tokens expire in 30 minutes (`PASSWORD_RESET_TTL_SECONDS`) — long enough for real email delivery lag, short enough to bound a leaked-link window.
- A reset token's `type: "password_reset"` claim is checked explicitly — an access token or OAuth state token (both JWTs signed with the same secret) cannot be replayed as a password-reset token.
- Successfully resetting a password revokes every refresh token for that account (Design Principle 3) — a stolen session cannot outlive a legitimate password reset.

## Performance Considerations
One real SMTP round-trip per `/auth/forgot-password` call — bounded by the 5/minute rate limit already applied to the route, same pattern as `/auth/login`/`/auth/register`.

## Not Yet Implemented (genuinely blocked by missing external credentials)
1. **Live delivery through a real mail provider.** `VENUAI/tests/test_password_reset.py` mocks `smtplib.SMTP` itself — real control flow (STARTTLS, login, message construction, error propagation), but never a connection to an actual mail server, because no real SMTP account exists in this environment (a Gmail app password, a SendGrid/Mailgun/SES API key — genuine external credentials this codebase cannot fabricate).
2. **Rate-limiting reset *attempts* per account** (today's `5/minute` limit on `/auth/forgot-password` is per-client-IP, the same mechanism every other rate-limited route in this project uses — not a separate per-account counter).

## Notes for Developers
If you add a second kind of short-lived, self-expiring token (anything with the shape "sign it, email/link it, verify it once within N minutes"), follow this same pattern — a `type` claim in a JWT signed with the existing secret — before reaching for a new database table. `VENUAI/oauth.py`'s CSRF state token and this module's reset token are now two real examples of it.

## S1 hardening (supersedes the stateless-JWT token described above)

- Token: `secrets.token_urlsafe(32)`, stored as SHA-256 in `password_reset_tokens`, 15 min, single use (atomic `used_at`), bound to a fingerprint of the current password hash (dies if the password changes any other way). Issuing a new token voids older ones.
- `POST /auth/forgot-password` always returns `202 {"status":"accepted"}` for known, unknown and SMTP-failing cases; the lookup/send runs in a background task and failures are logged server-side only.
- `POST /auth/reset-password` checks the password policy before spending the token (a weak password does not burn it), then spends it, updates the password, bumps `token_version`, revokes all refresh tokens and clears lockout.
- The reset page removes `?token=` from the URL immediately and sets `<meta name="referrer" content="no-referrer">`.

