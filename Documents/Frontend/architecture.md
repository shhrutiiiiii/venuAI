> Entry point for the `Frontend/` documentation folder.

# Frontend Architecture — VenuAI

## Purpose
Describes the Next.js chat application's structure: pages, components, contexts, and how it talks to the backend.

## Responsibilities
- Render the chat UI, document upload, verification panel, analytics dashboard, and auth screens.
- Proxy all backend calls through Next.js API routes rather than calling FastAPI directly from the browser.

## Scope
Covers `chat-app/` structure and client-side state only. Backend contract lives in [`../Backend/api.md`](../Backend/api.md).

## High-Level Overview
Built on **Next.js 16 (App Router)**, **React 19**, **TypeScript**, **Tailwind CSS v4**.

```
src/app/
  page.tsx                 landing
  login/page.tsx           login (real auth, via authClient.ts)
  forgot-password/page.tsx forgot-password (still a cosmetic mock — not wired to real auth)
  chat/page.tsx            main chat interface
  analytics/page.tsx       admin analytics dashboard
  api/auth/
    login/route.ts         proxies POST /auth/login, sets httpOnly cookies
    logout/route.ts        proxies POST /auth/logout, clears cookies
    me/route.ts            proxies GET /auth/me, transparently refreshes an expired access token
    register/route.ts      proxies POST /auth/register
  api/rag/
    query/route.ts         proxies POST /query (forwards Authorization from cookie)
    stream/route.ts        proxies POST /query/stream (SSE, forwards Authorization)
    upload/route.ts        proxies POST /upload (forwards Authorization)
    documents/route.ts     proxies GET/DELETE /documents (forwards Authorization)
    health/route.ts        proxies GET /health
    analytics/route.ts     proxies GET /analytics/dashboard (forwards Authorization; backend enforces admin-only)
src/components/
  StreamingMessage.tsx      renders incrementally-streamed answer tokens
  VerificationPanel.tsx     shows grounding/hallucination scores + flags
  ConfidenceBadge.tsx       small confidence indicator
  AgentModeToggle.tsx       switch between standard / ReAct / multi-agent mode
  FormattedMarkdown.tsx     renders answer markdown (tables, headings, bold)
  ThemeToggle.tsx           light/dark switch
  login/                    LoginForm, DemoCredentialsBadge, ParticleBackground
src/contexts/
  AuthContext.tsx           wraps the real session (hydrated via /api/auth/me)
  ThemeContext.tsx           light/dark theme state
src/lib/
  authClient.ts             real auth calls — see ../Backend/authentication.md
  serverAuthHeader.ts        reads the access-token cookie for proxy routes to forward
```

## Design Principles
1. **Browser never talks to FastAPI directly.** Every backend call goes through a Next.js API route under `src/app/api/rag/*`, which proxies to `rag_server.py`. This keeps the backend URL out of client bundles and is the single-entry-point principle from [`../index.md`](../index.md).
2. **Mode toggles are explicit UI state**, not hidden behavior — `AgentModeToggle` sets `agent_mode`/`multi_agent_mode` booleans sent straight through on `QueryRequest`.
3. **Verification is surfaced, not hidden** — `VerificationPanel` exists specifically to show the user the grounding/hallucination scores the backend computed, rather than presenting answers as unconditionally trustworthy.

## Workflow
```
User types question in chat/page.tsx
   │
   ▼
fetch('/api/rag/query' or '/api/rag/stream')  [Next.js API route]
   │
   ▼
proxies to FastAPI backend /query or /query/stream
   │
   ▼
response rendered via FormattedMarkdown + VerificationPanel + ConfidenceBadge
```

## Inputs
User text input, PDF file uploads, mode toggles, login form submissions.

## Outputs
Rendered chat messages, document list, analytics charts, auth session state (client-side only — see [`../Backend/authentication.md`](../Backend/authentication.md)).

## Dependencies
- [`api.md`](api.md) — the Next.js proxy route contract.
- [`routing.md`](routing.md) — page-by-page routing detail.
- [`state-management.md`](state-management.md) — Context + storage detail.
- [`../Backend/api.md`](../Backend/api.md) — what's being proxied to.

## Interactions with Other Services
Frontend (browser) → Next.js API routes (same origin) → FastAPI backend (`localhost:8000` in dev). No other external services called from the frontend directly.

## Security Considerations
Real auth now flows end-to-end: every `api/rag/*` proxy route attaches the access token (read from the httpOnly cookie via `serverAuthHeader.ts`) to its backend call, so the backend's auth enforcement applies all the way through. See [`../Backend/authentication.md`](../Backend/authentication.md).

## Performance Considerations
`/api/rag/stream` must pass through SSE without buffering the whole response — if a hosting platform buffers responses by default, streaming will appear to hang until the full answer is ready. Verify this explicitly before any non-local deployment.

## Future Improvements
- Wire `/forgot-password` to a real backend reset flow — it is still a cosmetic mock, not touched when auth was made real.
- Add loading/error states consistently across all `api/rag/*` proxy failures (currently varies by route).

## Notes for Developers
If you add a new backend route, add its Next.js proxy route under `src/app/api/rag/` in the same change, and add a row to [`api.md`](api.md) — don't let the frontend call FastAPI directly as a shortcut.
