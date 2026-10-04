# Backend API Contract — VenuAI

## Purpose
The HTTP contract exposed by `VENUAI/rag_server.py` — every route, its request/response shape, and its behavior.

## Responsibilities
- Be the single source of truth for what the backend's HTTP surface looks like.
- Stay in sync with `rag_server.py` — update this file in the same change as any route edit.

## Scope
Covers only the FastAPI routes in `rag_server.py`. Does not cover the Next.js proxy routes in `chat-app/src/app/api/rag/*` — see [`../Frontend/api.md`](../Frontend/api.md) for those.

## High-Level Overview
Base URL in local dev: `http://localhost:8000`. Routes marked **auth** require `Authorization: Bearer <access_token>`; **admin** additionally requires the `admin` role — see [`authentication.md`](authentication.md).

| Method | Route | Auth | Request | Response | Purpose |
|---|---|---|---|---|---|
| POST | `/auth/register` | — | `{email, password}` | `UserOut` | Create a new account |
| POST | `/auth/login` | — | `{email, password}` | `TokenResponse` | Exchange credentials for access + refresh tokens |
| POST | `/auth/refresh` | — | `{refresh_token}` | `TokenResponse` | Rotate a refresh token for a new (access, refresh) pair |
| POST | `/auth/logout` | — | `{refresh_token}` | 204 | Revoke a refresh token |
| GET | `/auth/me` | auth | — | `UserOut` | Current authenticated user |
| GET | `/health` | — | — | `{status, service, version, documents_ingested, vector_store, embedding_model, llm, note}` | Health/info check |
| POST | `/upload` | auth | `multipart/form-data`, PDF file | `DocumentInfo` | Parse, chunk, tag, embed, and store a PDF; uploader becomes owner |
| POST | `/query` | auth | `QueryRequest` (JSON) | `QueryResponse` (JSON) | Ask a question — standard RAG, ReAct agent, or multi-agent mode |
| POST | `/query/stream` | auth | `QueryRequest` (JSON) | `text/event-stream` (SSE) | Same as `/query` but streamed token-by-token; currently skips the verification step |
| GET | `/documents` | auth | — | `List[DocumentInfo]` | List documents the caller can see: own uploads + anything classified `PUBLIC`; admins see all |
| DELETE | `/documents/{doc_id}` | auth | path param | `{status, doc_id, filename}` | Remove a document and its chunks — owner or admin only |
| GET | `/kb/preset` | — | — | preloaded KB JSON | Return the bundled DRDO knowledge base used as fallback |
| GET | `/analytics/dashboard` | admin | — | aggregated analytics JSON | Dashboard data (query counts, latency, scores) |
| POST | `/research` | auth | `{question}` | `ResearchResponse` | `DeepResearchAgent` — multi-step research; synchronous, up to 120s. See [`../AI/agents.md`](../AI/agents.md) |
| POST | `/upload/multimodal` | auth | `multipart/form-data`, PDF file | multimodal summary JSON | Extract page/table/image regions — does NOT embed into Chroma. See [`../AI/multimodal.md`](../AI/multimodal.md) |
| GET | `/documents/{doc_id}/regions` | auth | path param | multimodal summary JSON | Stored region summary for a multimodal-ingested document — owner/admin/PUBLIC only |
| POST | `/documents/{doc_id}/regions/{region_id}/ask` | auth | `{question}` | `RegionQuestionResponse` | `VisionAgent` over one region — see [`../AI/vision.md`](../AI/vision.md) |
| POST | `/documents/regions/search` | auth | `{query, top_k}` | `List[RegionSearchResultOut]` | Lexical search across TABLE/IMAGE regions of every multimodal document the caller can see |

### `QueryRequest`
```json
{
  "question": "string (required)",
  "top_k": 4,
  "history": null,
  "session_id": "default-session",
  "user_id": null,
  "agent_mode": false,
  "multi_agent_mode": false
}
```

### `QueryResponse`
```json
{
  "answer": "string",
  "sources": [{"document": "string", "page": 0, "chunk_preview": "string"}],
  "model": "string",
  "chunks_retrieved": 0,
  "grounding_score": 0.0,
  "hallucination_score": 0.0,
  "verification_flags": [],
  "annotated_answer": "string|null"
}
```

## Design Principles
1. **Backward-compatible request shape.** New `QueryRequest` fields (`session_id`, `user_id`, `agent_mode`, `multi_agent_mode`) all default to safe values so older clients keep working.
2. **One response model regardless of mode.** Standard RAG, ReAct, and multi-agent modes all return the same `QueryResponse` shape — callers don't need mode-specific parsing, even though field meaning (e.g. `grounding_score`) is computed differently per mode.
3. **Errors are real HTTP errors.** Empty questions → 400; malformed/non-PDF uploads → 400/422; missing documents on delete → 404; pipeline exceptions → 500 with the error message in `detail`.

## Workflow
See [`architecture.md`](architecture.md) for how a `/query` call moves through guardrails → memory → the LangGraph pipeline.

## Inputs
HTTP requests, as tabulated above.

## Outputs
HTTP responses, as tabulated above.

## Dependencies
- [`architecture.md`](architecture.md) — the pipeline behind these routes.
- [`services.md`](services.md) — what each route calls internally.
- [`../Frontend/api.md`](../Frontend/api.md) — the Next.js layer that proxies to this contract.

## Interactions with Other Services
Called exclusively by the Next.js API routes in `chat-app/src/app/api/rag/*` in the current deployment topology — see [`../Frontend/api.md`](../Frontend/api.md).

## Security Considerations
Every route above except `/health`, `/kb/preset`, and the four pre-login `/auth/*` routes requires a valid access token; `/analytics/dashboard` additionally requires the `admin` role. See [`authentication.md`](authentication.md) for what's still open (per-chunk retrieval authorization).

## Performance Considerations
`/upload` is fully synchronous — PDF parse, chunk, auto-tag, embed, and persist all happen before the response returns. Large PDFs or many concurrent uploads will hold the request open for the full duration; see [`architecture.md`](architecture.md) Performance Considerations.

## Future Improvements
- Add `429`/rate-limit responses once any rate-limiting exists.
- Add pagination to `/documents` and `/analytics/dashboard` once the datasets are large enough to matter.
- Version the API (`/v1/...`) before any external consumer depends on this contract.

## Notes for Developers
If you add, remove, or change a field on `QueryRequest`/`QueryResponse`, update this table in the same commit — this document is the only place the contract is described outside the Pydantic models themselves.
