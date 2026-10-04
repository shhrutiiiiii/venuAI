> This document is also the entry point for the `Backend/` documentation folder.

# Backend Architecture — VenuAI

## Purpose
Defines the shape of the backend system: a single FastAPI app running an explicit LangGraph state machine for RAG, with guardrails, memory, and verification wrapped around it.

## Responsibilities
- Accept chat/document requests from the frontend.
- Run the RAG pipeline: retrieve → generate → verify (or the ReAct / multi-agent alternatives).
- Enforce input/output safety via the guardrails engine.
- Persist conversational memory and log analytics.

## Scope
Covers the backend's overall shape and how its pieces fit together. Does NOT cover: authentication (see [`authentication.md`](authentication.md)), the HTTP contract itself (see [`api.md`](api.md)), or external service integration detail (see [`services.md`](services.md)).

## High-Level Overview
The backend is a **single FastAPI app** (`VENUAI/rag_server.py`) — there is no microservice split and no job queue. Every `/query` call runs synchronously on the request thread through an explicit **LangGraph `StateGraph`**:

```
┌───────────────────────────────────────────────────────────────┐
│                      FastAPI (rag_server.py)                   │
│   /upload   /query   /query/stream   /documents   /analytics   │
└───────────────┬─────────────────────────────┬───────────────────┘
                │                               │
                ▼                               ▼
         Guardrails Engine                 Memory Manager
     (input check: injection,          (working + semantic
      out-of-scope, PII)                memory per session)
                │                               │
                └───────────────┬───────────────┘
                                ▼
                    LangGraph StateGraph (venuai_graph)
          route_query_node ─┬─► fallback_kb_node ─┐
                             └─► retrieve_node       │
                                     │               │
                                     ▼               │
                               generate_node          │
                                     │               │
                                     ▼               │
                                verify_node           │
                                     │               │
                                     └───────┬───────┘
                                             ▼
                                            END
                                             │
                     ┌───────────────────────┴───────────────────┐
                     ▼                                             ▼
             Memory Manager.save_turn                    Analytics.log_query
               (background task)                          (background task)
```
`agent_mode` (ReAct) and `multi_agent_mode` (OrchestratorAgent) bypass this graph entirely and run their own call paths inside the same `/query` handler — see [`AI/workflows.md`](../AI/workflows.md).

## Design Principles
1. **The graph is the pipeline.** New RAG steps are added as LangGraph nodes/edges, not as nested `if` branches inside a handler function.
2. **Guardrails gate before anything else runs.** `validate_input` is called first in `/query`, before memory fetch or graph invocation — a blocked query never reaches retrieval or the LLM.
3. **Verification is not optional.** `generate_node` always feeds into `verify_node` in the standard RAG path — an unverified answer never reaches the user through that path. (Note: `/query/stream` currently does *not* run verification — see Future Improvements.)
4. **Lazy singletons for heavy resources.** The embedding model, vector store, and LLM client are all lazily instantiated module-level singletons (`get_embeddings()`, `get_vector_store()`, `get_llm()`) so the server starts fast and only pays model-load cost on first real use.
5. **Fallback over hard failure.** If no documents exist, `route_query_node` sends the request to `fallback_kb_node`, which answers from a preloaded JSON knowledge base instead of returning an error.

## Workflow
See [`Documents/index.md`](../index.md) Workflow section for the full upload-to-answer flow. This document's concern is the backend's internal shape, not the end-to-end product flow.

## Inputs
HTTP requests from the Frontend (see [`../Frontend/architecture.md`](../Frontend/architecture.md)): PDF uploads (`multipart/form-data`), chat queries (JSON), document list/delete requests.

## Outputs
JSON API responses (`QueryResponse`, `DocumentInfo`); SSE token streams (`/query/stream`); writes to ChromaDB, SQLite (audit), and JSON metadata files.

## Dependencies
- [`Database/architecture.md`](../Database/architecture.md) — the stores this backend reads/writes.
- [`Backend/services.md`](services.md) — Groq LLM, embedding model, guardrails, memory, verification integration detail.
- [`Backend/authentication.md`](authentication.md) — current (absent) auth and what's needed.
- [`Backend/api.md`](api.md) — the HTTP contract itself.
- [`AI/index.md`](../AI/index.md) — the agents and workflows this backend invokes.

## Interactions with Other Services
- **Frontend → Backend:** HTTP/JSON via Next.js API routes under `chat-app/src/app/api/rag/*`, which proxy to this FastAPI server (see [`../Frontend/api.md`](../Frontend/api.md)).
- **Backend → Groq:** synchronous HTTP calls for every LLM invocation (classification, generation, verification, synthesis) — see [`services.md`](services.md).
- **Backend → ChromaDB / SQLite / JSON files:** direct, synchronous, local-disk I/O — see [`../Database/architecture.md`](../Database/architecture.md).

## Security Considerations
- **Authentication and authorization are now enforced on every route** via `VENUAI/auth.py` — `get_current_user` on `/upload`, `/query`, `/query/stream`, `/documents`; `require_admin` on `GET /analytics/dashboard`; ownership checks on `DELETE /documents/{id}`. See [`authentication.md`](authentication.md) for the full design. **Remaining gap:** per-chunk retrieval authorization — `retrieve_node` doesn't yet filter hybrid-search results by `owner_id`/classification.
- CORS origins come from `ALLOWED_ORIGINS` (env-driven), not a hardcoded list — see [`authentication.md`](authentication.md).
- Rate limiting (`slowapi`, per-client-IP) is applied to auth, upload, and query routes.
- Guardrails (`guardrails.py`) remain the line of defense against malicious *content* (prompt injection/jailbreak regex matching, out-of-scope filtering, PII sanitization, hallucination-score threshold policy), independent of and layered on top of the identity-based access control above.
- Secrets (`GROQ_API_KEY`, `JWT_SECRET_KEY`) are loaded via `python-dotenv` from a local `.env` file; there is no secret-manager integration yet. The server refuses to start without a real `JWT_SECRET_KEY`.

## Performance Considerations
- **The entire request path is synchronous** — embedding, vector search, BM25 index rebuild, and every LLM call happen on the FastAPI request thread. There is no job queue (see [ADR-003](../architecture-decisions.md#adr-003-no-async-job-queue-yet)). Under concurrent load this will serialize/slow down, not fail outright, but will not scale past light demo traffic.
- `multi_agent_mode` calls `asyncio.run()` from inside a synchronous route handler — this is a mixed sync/async pattern that works but is not a coherent async architecture; it should not be copied as a pattern for new code.
- The embedding model and LLM clients are lazy-loaded once per process (module-level singletons) — the first request after a cold start pays the model-load cost; subsequent requests don't.

## Future Improvements
- Add authentication/authorization middleware in front of every route.
- Add verification to `/query/stream`, which currently returns unverified streamed answers — an inconsistency with the standard `/query` path.
- Introduce a real job queue (Celery/RQ/arq) if concurrent multi-user load becomes a real requirement, per [ADR-003](../architecture-decisions.md#adr-003-no-async-job-queue-yet).
- Fix the default Groq model string (`qwen/qwen3.8-27b`), which does not match Groq's actual model catalog — see [ADR-001](../architecture-decisions.md#adr-001-groq-as-the-llm-provider-not-openaianthropic-directly).

## Notes for Developers
Before adding any new LLM-dependent step, ask: "does this belong as a LangGraph node, or is it a different mode (ReAct/multi-agent) that bypasses the graph?" Mixing the two inside one handler is how `/query` became three different execution paths (standard, `agent_mode`, `multi_agent_mode`) sharing one function — keep that distinction explicit when you touch this file.

**Two real bugs found and fixed in the Phase 4 gap-closing pass, neither of which any prior test had caught** (no test exercised the real `POST /upload` endpoint end-to-end before `VENUAI/tests/test_upload_graph_autopopulation.py`):
1. `upload_document()` imported `RecursiveCharacterTextSplitter` from `langchain.text_splitter`, a module path that no longer exists in the installed `langchain` version (it moved to the separate `langchain-text-splitters` package). Every call to `POST /upload` raised `ModuleNotFoundError` and returned a 500. Fixed by importing from `langchain_text_splitters` directly, and the package is now pinned in `VENUAI/requirements.txt`.
2. `pypdf` was listed in `requirements.txt` (required by `PyPDFLoader`) but was never actually installed in this environment — `POST /upload` failed with a 422 ("pypdf package not found") on every call. Installed for real (`pip install pypdf>=5.0.0`); the pin already existed and was correct, the environment just didn't match it.

If you add a new endpoint that imports from `langchain`/`langchain_community`, don't assume a sibling module's import path is still valid just because it predates your change — these two bugs sat undetected because the only tests touching document ingestion used `/upload/multimodal` (PyMuPDF-based, a separate code path) rather than the real `/upload`.

**A third, environment-level finding from the same pass (not a code bug — nothing to fix in this repo):** the `GROQ_API_KEY` configured in this development environment's `VENUAI/.env` is not a valid key — a direct `client.models.list()` call against it returns `401 Invalid API Key`. This means every real-LLM-dependent feature (standard RAG generation, the verifier, citation/contradiction/critic agents, the ReAct/multi-agent/research paths, Phase 5's vision-model story below) has no working credential to actually call Groq with in this specific environment, independent of anything this codebase does. The test suite is unaffected (every test that exercises an LLM call mocks `groq.Groq`/`ChatGroq`/`AIGateway` — none depend on a real key), but a manual smoke-test or demo in this environment needs a real key set in `VENUAI/.env` first. This is exactly the kind of "requires external credentials" gap this project's standing rule asks to be marked, not silently worked around — see [`../AI/vision.md`](../AI/vision.md) for the compounding vision-model gap this creates.
