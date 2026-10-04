# Authentication — VenuAI

## Purpose
Documents the real, server-side authentication system (`VENUAI/auth.py`) that replaced the previous frontend-only mock, and what's still open.

## Responsibilities
- Describe the current implementation precisely: password hashing, JWT issuance, refresh-token rotation, account lockout.
- Enumerate which routes require which access level.
- Track what is intentionally deferred (see Future Improvements).

## Scope
Covers identity and access control only. Content-safety checks (prompt injection, PII) are unrelated to *who* is making the request — see [`architecture.md`](architecture.md) Security Considerations for those.

## High-Level Overview
Authentication is now real and enforced server-side:
- **User store**: `VENUAI/data/users.db` (SQLite) — `users` table (id, email, bcrypt-hashed password, role, failed-attempt counter, lockout timestamp) and a `refresh_tokens` table (hashed token, expiry, revoked flag).
- **Passwords**: hashed with bcrypt via `passlib`, never stored or logged in plaintext.
- **Access tokens**: short-lived JWTs (HS256, 30-minute default `ACCESS_TOKEN_TTL_SECONDS`), signed with `JWT_SECRET_KEY`. The server refuses to start if this secret is unset — no insecure default is permitted.
- **Refresh tokens**: opaque random strings (not JWTs), hashed at rest, stored with an expiry and a `revoked` flag — this is what makes logout and rotation actually effective, since a bare JWT cannot be revoked before it expires. Reusing an already-rotated refresh token is rejected (replay protection).
- **Account lockout**: 5 failed login attempts locks the account for 5 minutes (`MAX_FAILED_LOGIN_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`).
- **Roles**: `user` and `admin`. `require_admin` gates admin-only routes.
- **FastAPI routes**: `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me` — all in `rag_server.py`, backed by `VENUAI/auth.py`.
- **Enforced on every previously-open route**: `/upload`, `/query`, `/query/stream`, `/documents` (`GET`/`DELETE`) now require `Depends(get_current_user)`; `/analytics/dashboard` requires `Depends(require_admin)`.
- **Frontend**: `chat-app/src/lib/authClient.ts` (replacing the deleted `mockAuth.ts`) calls Next.js proxy routes under `src/app/api/auth/*`, which set the access/refresh tokens as **httpOnly cookies** — never `localStorage`, which is readable by any script on the page. `AuthContext` hydrates by asking `/api/auth/me`, which transparently rotates an expired access token using the refresh cookie.
- **Demo accounts**: `demo@chat.com` / `demo12345` (role `user`) and `admin@chat.com` / `admin12345` (role `admin`) are seeded once on first run (`seed_demo_users()`) so the frontend's existing "demo credentials" UI keeps working — but now against real hashed credentials and real JWTs, not a hardcoded client-side check.

## Design Principles
1. **Enforcement is server-side, always.** Every protected route depends on `get_current_user` or `require_admin` — a frontend check is UX, never the actual gate.
2. **The authenticated user's ID is the only trustworthy identity.** `/query` and `/query/stream` overwrite `req.user_id` with `current_user.id` before doing anything else — a client-supplied `user_id` is never trusted as an identity claim.
3. **Tokens are verifiable and revocable.** JWTs are signed and expire quickly; the separate, revocable refresh token is what allows real logout and detects refresh-token replay.
4. **Document ownership follows from authentication.** Uploaded documents record `owner_id` (the uploader's user ID) and a `classification` (from `DocumentIntelligence`); `GET /documents` filters to the caller's own documents plus anything classified `PUBLIC`, and `DELETE /documents/{id}` is restricted to the owner or an admin. Admins see everything.

## Workflow
```
POST /auth/login {email, password}
   │  verify bcrypt hash, check lockout
   ▼
access_token (JWT, 30 min) + refresh_token (opaque, 14 days, hashed at rest)
   │
   ▼
Client calls protected routes with Authorization: Bearer <access_token>
   │  access token expires
   ▼
POST /auth/refresh {refresh_token}
   │  old refresh token revoked, new (access, refresh) pair issued
   ▼
POST /auth/logout {refresh_token}  ──► refresh token revoked; access token simply expires naturally
```
Frontend-side, the Next.js API routes under `/api/auth/*` hold these tokens in httpOnly cookies and perform the refresh step transparently from `/api/auth/me` — see [`../Frontend/state-management.md`](../Frontend/state-management.md).

## Inputs
Email/password (login, register); a refresh token (refresh, logout); a bearer access token (every protected route).

## Outputs
`TokenResponse` (access + refresh tokens + user info) on login/refresh; `UserOut` on register/me; 401/403/423 on auth/authorization failures.

## Dependencies
- [`architecture.md`](architecture.md) — the routes this document says are now protected.
- `VENUAI/auth.py`, `VENUAI/upload_security.py` — the implementation.
- [`../Frontend/state-management.md`](../Frontend/state-management.md) — the httpOnly-cookie session on the frontend.

## Interactions with Other Services
No external identity provider — this is a self-contained username/password system. No SSO/OAuth integration exists (the frontend's "Sign in with Google/GitHub" buttons are demo-credential-autofill shortcuts, not real OAuth — see Notes for Developers).

## Security Considerations
- `JWT_SECRET_KEY` must be a real generated secret (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) — the server raises `RuntimeError` at startup if it's missing. Never commit a real value; `.env.example` holds only a placeholder.
- Refresh tokens are stored **hashed** (SHA-256) in SQLite, not in plaintext — a database read alone does not yield a usable token.
- CORS origins are environment-driven (`ALLOWED_ORIGINS`), not hardcoded — see [`architecture.md`](architecture.md).
- Rate limiting (`slowapi`) is applied to `/auth/register` (5/min), `/auth/login` (10/min), `/auth/refresh` (20/min), `/upload` (10/min), and `/query`/`/query/stream` (30/min) per client IP, to blunt brute-force and abusive-flood attempts.
- **Per-chunk retrieval access control is now implemented for the standard RAG, streaming, and multi-agent paths.** `get_visible_doc_ids(current_user)` computes the set of `doc_id`s a caller may see (their own uploads + anything classified `PUBLIC`; `None`/unrestricted for admins). It's threaded into:
  - `retrieve_node` (via `RAGGraphState.allowed_doc_ids`) — standard RAG.
  - `/query/stream`'s direct retrieval call — streaming mode.
  - `BaseSpecialistAgent.query()`'s `allowed_doc_ids` parameter, via `OrchestratorAgent.route(..., allowed_doc_ids=...)` — multi-agent mode. (Specialist agents retrieve directly from the vector store themselves — they were not actually exempt from this gap, despite an earlier, incorrect note in this document to the contrary.)
  - `agent_tools.build_agent_tools(user_id, allowed_doc_ids, user_role)` — ReAct mode. Tools (`drdo_kb_search`, `document_summary`, `list_knowledge_base`, `memory_recall`) are built **fresh per request** inside `POST /query`'s `agent_mode=true` branch, closing over this specific caller's `current_user.id`/`get_visible_doc_ids(current_user)` by closure — not as shared module-level singletons. `memory_recall` additionally no longer exposes `user_id` as an LLM-controllable tool argument at all, closing a second, related bug (a confused/malicious agent turn could previously ask to recall a *different* user's memory by passing their id as the argument). See `VENUAI/tests/test_agent_tools_authorization.py`.

  In all four modes, both the Chroma similarity search (via a `{"doc_id": {"$in": [...]}}` filter, combined with any domain `metadata_filter` via `$and` for specialists) and the BM25 corpus (filtered before `BM25Retriever.from_documents(...)` is built, in `retrieve_node`) are restricted — a user with zero visible documents gets zero retrieved chunks, not an unfiltered fallback. See `VENUAI/tests/test_retrieval_authorization.py`, `VENUAI/tests/test_specialist_agent_retrieval.py`, and `VENUAI/tests/test_agent_tools_authorization.py`.

  **One narrower residual case, not web-reachable:** `VENUAI/agent_tools.py` still defines unbound, module-level versions of these same tools (`drdo_kb_search`, `document_summary`, `list_knowledge_base`, each hardcoding `allowed_doc_ids=None`) for `VENUAI/mcp_server.py`, a local stdio MCP server consumed by desktop tools (Claude Desktop/Cursor/Windsurf) that the server operator runs themselves — it has no HTTP endpoint and isn't part of the multi-tenant web surface `POST /query` exposes. It retrieves without per-caller filtering by design (there's exactly one "caller": whoever started the local process), the same way a developer running a one-off script against `rag_server.py`'s internals would. If this MCP server is ever exposed as a shared/remote service rather than run locally per-operator, it would need the same per-request identity binding `build_agent_tools()` already does.

## Performance Considerations
bcrypt hashing is deliberately slow (by design, to resist brute force) — this is a few hundred milliseconds per login/register call, not a RAG-pipeline bottleneck.

## Future Improvements (in priority order)
1. ~~Add real OAuth (Google/GitHub)~~ — **done (Phase 5), blocked only on real credentials.** A complete, tested authorization-code-flow implementation exists (`VENUAI/oauth.py`, `/auth/oauth/{provider}/login`+`/callback`) — see [`oauth.md`](oauth.md). The login page's buttons remain demo-credential shortcuts, deliberately not rewired until real `GOOGLE_CLIENT_ID`/`GITHUB_CLIENT_ID` credentials exist to verify the live flow against.
2. ~~Add a password-reset flow backed by real email delivery~~ — **done (Phase 5), blocked only on real SMTP credentials.** `/forgot-password` and the new `/reset-password` page now call real backend routes (`/auth/forgot-password`, `/auth/reset-password`) — see [`password-reset.md`](password-reset.md). Live email delivery itself is blocked until real SMTP credentials are configured.
3. Move `JWT_SECRET_KEY` and other secrets to a real secret manager before any shared/cloud deployment — `.env` is a local-dev-only mechanism.
4. If `VENUAI/mcp_server.py` is ever deployed as a shared/remote service (today it's local-stdio-only), thread per-caller identity into its tool calls the same way `build_agent_tools()` already does for the HTTP ReAct path.

## Notes for Developers
`mockAuth.ts` has been deleted — do not re-add client-side mock credential checks. Real auth logic lives entirely in `VENUAI/auth.py`; the frontend (`src/lib/authClient.ts`) only calls it. The "Sign in with Google/GitHub" buttons on the login page are pre-existing UI that call the same demo-credential autofill as the "DRDO Scientist"/"Lab Director" buttons — they are not real OAuth and should not be presented as such until item 2 above is done.
