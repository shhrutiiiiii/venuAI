# Frontend State Management — VenuAI

## Purpose
Documents how client-side state (auth, theme) is held and persisted.

## Responsibilities
Describe each React Context and its backing storage.

## Scope
Client-side state only. Server-side state (memory/analytics) is in [`../Backend/architecture.md`](../Backend/architecture.md).

## High-Level Overview
| Context | File | Backing storage | Holds |
|---|---|---|---|
| `AuthContext` | `src/contexts/AuthContext.tsx` | httpOnly cookies, set by `/api/auth/*` route handlers (via `src/lib/authClient.ts`) | Current real user (id, email, role), login/logout actions |
| `ThemeContext` | `src/contexts/ThemeContext.tsx` | likely `localStorage` + `prefers-color-scheme` | Light/dark theme |

No global state library (Redux/Zustand/Jotai) is used — React Context + component-local `useState` covers the current UI's needs.

## Design Principles
1. **No state library beyond Context** — appropriate at this app's current size; revisit only if cross-component state sharing becomes unwieldy.
2. **Auth tokens are never held in client-readable storage.** `/api/auth/login` sets the access and refresh tokens as httpOnly, `SameSite=lax` cookies server-side; client-side JS (and `AuthContext`) never sees the raw token, only the derived user object returned by `/api/auth/me`. This replaced the original mock's `localStorage`-stored fake token, which was readable by any script on the page (an XSS exposure that was moot only because the token was fake).

## Workflow
`authClient.login()` → `POST /api/auth/login` → backend verifies credentials, issues access+refresh tokens → Next.js route sets them as httpOnly cookies → `AuthContext` hydrates via `getSessionUser()` → `GET /api/auth/me`, which transparently rotates an expired access token using the refresh cookie before returning the user.

## Inputs / Outputs
N/A.

## Dependencies
- [`../Backend/authentication.md`](../Backend/authentication.md) — the real auth system this now wraps.

## Interactions with Other Services
`AuthContext` → `/api/auth/*` Next.js route handlers → FastAPI `/auth/*` routes. No direct browser-to-backend auth calls.

## Security Considerations
Cookies are `httpOnly` (unreadable by JS) and `SameSite=lax`; `secure` is enabled when `NODE_ENV=production`. CSRF is a residual consideration for any future state-changing GET-adjacent endpoint — current mutating routes are POST/DELETE with JSON bodies, which is a reasonable baseline but not a substitute for CSRF tokens if this is exposed cross-site in the future.

## Performance Considerations
N/A — negligible state size.

## Future Improvements
Add explicit CSRF tokens if any cross-site POST flow is ever introduced. Consider `SameSite=strict` if no cross-site navigation flow needs `lax`.

## Notes for Developers
Don't reach for a state management library before Context + local state actually becomes insufficient — no evidence of that yet in this codebase. Don't reintroduce token storage in `localStorage`/`sessionStorage` — that was the exact pattern this was changed away from.
