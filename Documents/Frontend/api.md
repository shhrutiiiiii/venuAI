# Frontend API Routes — VenuAI

## Purpose
Documents the Next.js API routes under `chat-app/src/app/api/*` that proxy to the FastAPI backend — both the RAG routes and the auth routes.

## Responsibilities
List each proxy route, what it forwards to, and any transformation it applies.

## Scope
Covers only the proxy layer. The actual backend contract is [`../Backend/api.md`](../Backend/api.md).

## High-Level Overview

### Auth routes (`api/auth/*`)
| Next.js route | Proxies to | Notes |
|---|---|---|
| `POST /api/auth/login` | `POST {BACKEND_URL}/auth/login` | On success, sets `venuai_access_token` + `venuai_refresh_token` as httpOnly cookies; returns only `{user}` to the client |
| `POST /api/auth/logout` | `POST {BACKEND_URL}/auth/logout` | Reads refresh token from cookie, revokes it server-side, clears both cookies |
| `GET /api/auth/me` | `GET {BACKEND_URL}/auth/me` | Reads access token from cookie; on 401, transparently calls `/auth/refresh` with the refresh cookie and retries once before giving up |
| `POST /api/auth/register` | `POST {BACKEND_URL}/auth/register` | Pass-through — accepts optional `organization_name`/`tenant_id` (Phase 4 multi-tenancy) |
| `GET /api/auth/tenant` | `GET {BACKEND_URL}/auth/tenant` | Same auth forwarding; the caller's own organization/workspace |
| `GET /api/auth/oauth/[provider]/login` | `GET {BACKEND_URL}/auth/oauth/{provider}/login` | Redirects the browser to the real authorize URL; 503 passed through if the provider isn't configured |
| `GET /api/auth/oauth/[provider]/callback` | `POST {BACKEND_URL}/auth/oauth/{provider}/callback` | Where the provider redirects back to; exchanges code/state, then sets the same httpOnly cookies `/api/auth/login` does |
| `POST /api/auth/forgot-password` | `POST {BACKEND_URL}/auth/forgot-password` | Pass-through |
| `POST /api/auth/reset-password` | `POST {BACKEND_URL}/auth/reset-password` | Pass-through |

### RAG routes (`api/rag/*`)
| Next.js route | Proxies to | Notes |
|---|---|---|
| `POST /api/rag/query` | `POST {BACKEND_URL}/query` | Forwards `Authorization: Bearer <token>` read from the access-token cookie (via `serverAuthHeader.ts`) |
| `POST /api/rag/research` | `POST {BACKEND_URL}/research` | Deep Research. Same auth forwarding; `maxDuration = 180`, upstream timeout 170 s (backend can take ~120 s). Returns 504 on timeout, 503 if backend is down; backend 429 (5/min) is passed through |
| `POST /api/rag/stream` | `POST {BACKEND_URL}/query/stream` | Same auth forwarding; must preserve SSE streaming, not buffer |
| `POST /api/rag/upload` | `POST {BACKEND_URL}/upload` | Same auth forwarding; pass-through multipart form data |
| `GET /api/rag/documents`, `DELETE /api/rag/documents` | `{BACKEND_URL}/documents[/{id}]` | Same auth forwarding |
| `GET /api/rag/health` | `GET {BACKEND_URL}/health` | No auth required (backend route is public) |
| `GET /api/rag/analytics` | `GET {BACKEND_URL}/analytics/dashboard` | Same auth forwarding; backend returns 403 for a non-admin token |

### Dataset routes (`api/datasets/*`)
| Next.js route | Proxies to | Notes |
|---|---|---|
| `POST /api/datasets/upload` | `POST {BACKEND_URL}/datasets/upload` | Same auth forwarding; pass-through multipart form data |
| `GET /api/datasets` | `GET {BACKEND_URL}/datasets` | Same auth forwarding |
| `DELETE /api/datasets/[id]` | `DELETE {BACKEND_URL}/datasets/{id}` | Same auth forwarding |
| `POST /api/datasets/[id]/analyze` | `POST {BACKEND_URL}/datasets/{id}/analyze` | Same auth forwarding |

### Registry/security routes (Phase 4)
| Next.js route | Proxies to | Notes |
|---|---|---|
| `GET /api/agents` | `GET {BACKEND_URL}/agents` | Same auth forwarding; real Phase 1 agent registry |
| `GET /api/models` | `GET {BACKEND_URL}/models` | Same auth forwarding; real Phase 1 model catalog |
| `GET /api/security/violations` | `GET {BACKEND_URL}/security/violations` | Same auth forwarding; backend returns 403 for a non-admin token, and scopes results to the admin's own tenant |
| `PATCH /api/tenants` | `PATCH {BACKEND_URL}/tenants` | Same auth forwarding; rename/requota the caller's own tenant, admin-only |
| `GET /api/tenants/usage` | `GET {BACKEND_URL}/tenants/usage` | Same auth forwarding; member/document/dataset counts vs. quotas, any tenant member |
| `GET/POST /api/tenants/members` | `{BACKEND_URL}/tenants/members` | Same auth forwarding; list/add tenant members, admin-only |
| `GET /api/evidence`, `GET /api/evidence/[id]` | `{BACKEND_URL}/evidence[/{id}]` | Same auth forwarding; Evidence Explorer — past verification records |

### Knowledge graph routes (`api/graph/*`, Phase 3/4)
| Next.js route | Proxies to | Notes |
|---|---|---|
| `GET /api/graph/search` | `GET {BACKEND_URL}/graph/search` | Same auth forwarding; forwards `q`/`entity_type` query params |
| `GET /api/graph/entities/[id]` | `GET {BACKEND_URL}/graph/entities/{id}` | Same auth forwarding; forwards `as_of` query param |
| `POST /api/graph/extract` | `POST {BACKEND_URL}/graph/extract` | Same auth forwarding; pass-through JSON body |

## Design Principles
1. **Thin proxy, no business logic**, with one deliberate exception: the auth routes own cookie management (setting/clearing/refreshing httpOnly cookies) since that must happen server-side — see [`../Backend/authentication.md`](../Backend/authentication.md).
2. **Every RAG proxy route forwards the caller's identity**, via `serverAuthHeader.ts` reading the httpOnly access-token cookie — none of them call the backend unauthenticated on the user's behalf.

## Workflow
Browser → Next.js route handler (attaches `Authorization` from cookie for RAG routes; manages cookies directly for auth routes) → FastAPI backend → response relayed back.

## Inputs / Outputs
Mirrors [`../Backend/api.md`](../Backend/api.md) for each corresponding backend route.

## Dependencies
- [`../Backend/api.md`](../Backend/api.md)
- [`../Backend/authentication.md`](../Backend/authentication.md)

## Interactions with Other Services
Next.js server runtime → FastAPI backend, via plain HTTP (`RAG_SERVER_URL`, default `http://127.0.0.1:8000`).

## Security Considerations
Tokens live only in httpOnly cookies set by the Next.js server — never passed to or read by client-side JS. See [`../Frontend/state-management.md`](state-management.md).

## Performance Considerations
`/api/rag/stream` is the one route where "thin proxy" must still mean "don't break streaming" — confirm the hosting platform doesn't buffer SSE responses.

## Future Improvements
Add CSRF tokens if a cross-site state-changing flow is ever introduced (current cookies are `SameSite=lax`).

## Notes for Developers
Keep this table's rows 1:1 with the files under `src/app/api/auth/` and `src/app/api/rag/`. Any new RAG route must call `authHeader(req)` from `src/lib/serverAuthHeader.ts` — don't add a proxy route that silently skips auth forwarding.


## Chat UI answer contract (consumed fields)

`/api/rag/query` and `/api/rag/stream` responses are read defensively; every field below is optional and nothing is defaulted to a fake number.

- `answer`, `sources[{document,page,chunk_preview}]` (legacy; shown as unverified citations)
- `citations[{index,chunk_id,doc_id,filename,page,quote,verified}]`: rendered as inline `[n]` chips with hover/click preview and a per-answer sources panel
- `grounding_score` (number or null), `verified` (bool), `verification_reason`/`reason`: null or `verified=false` shows "Unverified" plus the reason. The stream `done` event score is ignored unless `verified === true` (the current backend hardcodes it)
- `blocked`, `block_reason`; current backend signals a block via `verification_flags` containing `GUARDRAIL_TRIGGERED: <type>`. Both render as a distinct "Blocked by guardrails" card
- `model_used`: real model id shown on the answer when present
- `GET /api/models`: shown read-only as "Model routing" (registry order: first enabled = primary, rest = fallbacks). `/query` has no model parameter, so there is no selector
- `/research` response (`ResearchResponse`): plan/sub-questions, `synthesized_answer`, `evidence_coverage`, `critique_passed`, `plan_is_fallback`, `truncated`. The call is synchronous, so the UI shows elapsed time only (no fake step progress)

Components: `CitationChip`, `AnswerActions` (Copy as Markdown, Download .md, Print/PDF via `@media print` in `globals.css`), `ResearchReport`/`ResearchPending`, `ModelRouting`, `ConfidenceBadge`, `VerificationPanel`; helpers in `src/lib/answerTypes.ts`. Follow-up suggestions are not implemented because the backend provides none.
