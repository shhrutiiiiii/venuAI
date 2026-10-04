# Frontend Routing — VenuAI

## Purpose
Documents every page route in the Next.js App Router.

## Responsibilities
List each route, its file, and its purpose.

## Scope
Page routes only — API routes are in [`api.md`](api.md).

## High-Level Overview
| Route | File | Purpose | Auth gate (current) |
|---|---|---|---|
| `/` | `src/app/page.tsx` | Landing page | None |
| `/login` | `src/app/login/page.tsx` | Login form (real auth via `authClient.ts`) | N/A |
| `/forgot-password` | `src/app/forgot-password/page.tsx` | Password reset request — real backend call (Phase 5), blocked on live delivery only by missing SMTP credentials | N/A |
| `/reset-password` | `src/app/reset-password/page.tsx` | Completes a password reset from an emailed `?token=` link (Phase 5) | N/A |
| `/chat` | `src/app/chat/page.tsx` | Main chat interface | Page itself has no Next.js middleware gate, but every data call it makes now requires a valid auth cookie server-side (401 without one) |
| `/analytics` | `src/app/analytics/page.tsx` | Admin analytics dashboard | Page itself has no middleware gate, but the data it fetches requires the `admin` role server-side (403 for a non-admin, 401 for anonymous) |
| `/signup` | `src/app/signup/page.tsx` | Registration — create an account, optionally a brand-new tenant via "Organization name" (Phase 4 multi-tenancy, see [`../Backend/multi-tenancy.md`](../Backend/multi-tenancy.md)) | N/A |
| `/workspace` | `src/app/workspace/page.tsx` | Research Workspace — tenant overview, document/dataset management, knowledge-graph search/extraction, agent/model registry (Phase 4) | Data fetches require a valid auth cookie server-side |
| `/security` | `src/app/security/page.tsx` | Security Center — guardrail audit log, tenant-scoped (Phase 4) | Page redirects to an access-denied state for non-admins; the data it fetches requires the `admin` role server-side, same pattern as `/analytics` |
| `/evidence` | `src/app/evidence/page.tsx` | Evidence Explorer — past `/query`/`/research` answers' claim-by-claim verification breakdown (Phase 4) | Data fetches require a valid auth cookie server-side; a non-admin sees only their own records, enforced server-side |

## Design Principles
1. Page-level route protection is still **client-side only** (a redirect if `AuthContext` has no user) — that remains UX, not the security boundary. The actual security boundary is now the backend: every data fetch these pages make is authenticated/authorized server-side regardless of whether the page itself redirected. See [`../Backend/authentication.md`](../Backend/authentication.md).

## Workflow
N/A — static route table above is the complete picture.

## Inputs / Outputs
N/A.

## Dependencies
- [`state-management.md`](state-management.md) — `AuthContext` behavior.
- [`../Backend/authentication.md`](../Backend/authentication.md).

## Interactions with Other Services
N/A.

## Security Considerations
Both pages are still reachable by URL without logging in — only their *data* is protected. An anonymous visitor to `/analytics` sees the page shell but every fetch it makes returns 401. This is a UX gap (an unauthenticated user briefly sees an empty/error dashboard instead of a redirect), not a data-exposure gap.

## Performance Considerations
N/A.

## Future Improvements
Add Next.js middleware-based route protection so an anonymous visitor is redirected before the page renders, rather than rendering and then failing every data fetch.

## Notes for Developers
Add a row here for any new page route in the same change that adds it.
