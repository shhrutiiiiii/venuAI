# Future Additions — VenuAI

## Purpose
Non-normative roadmap of what's missing for production readiness and what further AI capability could be added. Nothing here is implemented — this document previews, it does not describe current behavior. See [`index.md`](index.md) for what exists today.

## Responsibilities
Keep a single prioritized list so "what should we build next" doesn't have to be re-derived from scratch each session.

## Scope
Two tracks: production hardening (making the existing system safe/scalable) and AI capability expansion (making it smarter/broader).

## Track A — Production Hardening (do before any real deployment)
1. ~~**Real authentication & authorization** on every backend route~~ — **done.** `VENUAI/auth.py` (bcrypt, signed JWTs, revocable hashed refresh tokens, account lockout) now gates `/upload`, `/query`, `/query/stream`, `/documents`, and admin-only `/analytics/dashboard`. ~~Per-chunk retrieval authorization~~ — **done for all four query modes** (standard RAG, streaming, multi-agent, and ReAct — `get_visible_doc_ids` filters Chroma + BM25 results by `owner_id`/classification/`tenant_id` before generation, and the ReAct agent's tools are built fresh per request bound to the caller's identity via `agent_tools.build_agent_tools()`). See [`Backend/authentication.md`](Backend/authentication.md).
2. ~~**Test suite + CI**~~ — **started.** `VENUAI/tests/` now has real, passing pytest coverage for auth/authorization/upload-validation (20 tests), plus `.github/workflows/ci.yml` running backend tests and frontend lint/typecheck/build. Still missing: RAG-pipeline tests, frontend component tests, E2E tests.
3. ~~**Containerization**~~ — **done.** `VENUAI/Dockerfile`, `chat-app/Dockerfile`, and root `docker-compose.yml` with named volumes for Chroma/uploads/auth data. Not yet pushed to a registry or deployed anywhere.
4. ~~**Fix the default Groq model string**~~ — **done.** Default is now `llama-3.3-70b-versatile`, a real Groq-served model.
5. **Pin dependency versions** in `requirements.txt` — **done**, minimum-version pins added; consider exact pins (`==`) for full reproducibility.
6. **Persistent storage for deployment** — **done** via docker-compose named volumes (`chroma_data`, `uploaded_docs`, `app_data`).
7. **Cache the BM25 index** instead of rebuilding it from the full corpus on every query — still open. See [`Database/architecture.md`](Database/architecture.md) Performance Considerations.
8. ~~Add verification to ReAct mode and multi-agent mode~~ — **done (Phase 5).** Both now run the real claim-level `VerificationPipeline` (not just standard mode's `verify_node`) — see [`AI/workflows.md`](AI/workflows.md) and [`AI/evidence-explorer.md`](AI/evidence-explorer.md). **Still open:** `/query/stream`'s `grounding_score` remains a hardcoded `0.8` — no `VerificationPipeline` equivalent there yet.
9. **Remove dev cruft**: root `main.py` (unrelated voice-CLI prototype), `scratch.py` files, `.gradio/flagged`, committed `__pycache__` — still present, not removed in this pass (left alone rather than deleted without explicit instruction).
10. **Rate limiting** — **done** (`slowapi`, per-IP limits on auth/upload/query routes). ~~Structured/observable logging~~ — **done (Phase 5)**: real JSON log output via `LOG_FORMAT=json` — see [`DevOps/monitoring.md`](DevOps/monitoring.md). Still open: actually shipping those logs to an aggregator (genuine infra dependency).
11. ~~Multi-tenancy~~ — **done (Phase 4).** Every user and owned resource (documents, datasets, multimodal documents, knowledge-graph entities/relationships) is now scoped to a tenant; admin is a per-tenant role, not a cross-tenant superuser. Tenant management (rename, quotas, add/list members) and per-tenant document/dataset/user quotas are also done — see [`Backend/multi-tenancy.md`](Backend/multi-tenancy.md) and the Research Workspace's "Team & Quotas" tab. **Still open:** no member removal/deactivation endpoint; no real invite-token/email flow (admin provisions the account and shares the password directly); no real cross-tenant platform-operator role.
12. ~~Security Center / admin visibility into guardrail triggers~~ — **done (Phase 4).** `GET /security/violations` (tenant-scoped, admin-only) plus a real Security Center page — see [`Backend/multi-tenancy.md`](Backend/multi-tenancy.md) and [`Frontend/routing.md`](Frontend/routing.md).
13. ~~Google/GitHub OAuth~~ — **done (Phase 5), blocked only on real provider credentials.** See [`Backend/oauth.md`](Backend/oauth.md).
14. ~~Password reset via real email~~ — **done (Phase 5), blocked only on real SMTP credentials.** See [`Backend/password-reset.md`](Backend/password-reset.md).
15. ~~Sandboxed code execution~~ — **done (Phase 5).** Real Docker-container isolation, admin-only — see [`AI/code-sandbox.md`](AI/code-sandbox.md).
16. **A real, valid `GROQ_API_KEY` for this development environment** — the key currently in `VENUAI/.env` returns `401 Invalid API Key` against Groq's real API; every LLM-dependent feature has no working credential to call out with here. Not a code gap — see [`Backend/architecture.md`](Backend/architecture.md) Notes for Developers.

## Track B — AI Capability Expansion
Grouped by area; none of this exists today.

**Retrieval / Knowledge**
- ~~Multi-modal RAG~~ — **done.** Real PDF table/image extraction (PyMuPDF + pdfplumber), region-level `VisionAgent`, lexical region search, and (Phase 5) region hits folded into every retrieval path — standard RAG, Agentic RAG, `/query/stream`, the ReAct agent, and multi-agent specialists — see [`AI/multimodal.md`](AI/multimodal.md). **Still open:** OCR is architecturally real but functionally blocked (Tesseract binary not installed — see [`AI/vision.md`](AI/vision.md)); no vision-capable model exists, so chart/diagram/equation semantic classification is explicitly never attempted.
- Cross-encoder re-ranking after initial hybrid retrieval, instead of relying on vector+BM25 ensembling alone.
- Query rewriting / HyDE — rewrite vague questions into better retrieval queries before searching.
- ~~Knowledge graph layer~~ — **done.** Real entity/relationship extraction (deterministic regex + LLM-validated), temporal relationships, two tested storage backends (SQLite default, Postgres now genuinely docker-compose-deployable, both tenant-isolated), `/graph/*` APIs, `/research`'s `extract_graph` integration, automatic population on every `/upload` (Phase 5), and a real search/inspect/extract/visualize panel in the Research Workspace UI (Phase 5 added the node-link visualization) — see [`AI/knowledge-graph.md`](AI/knowledge-graph.md). **Still open:** Postgres isn't the CI-tested default (the docker-compose profile isn't started in CI, so Postgres-backend tests there remain skip-when-unreachable).
- Async/incremental document ingestion with progress status, instead of a blocking `/upload` call.

**Agents & Reasoning**
- ~~A planning/decomposition agent for genuinely multi-step requests~~ — **done.** `ResearchPlannerAgent` + `DeepResearchAgent` (plan → per-subquestion `AgenticRAG` → synthesize → verify), exposed via `POST /research` — see [`AI/agents.md`](AI/agents.md).
- ~~Tool-use beyond the current `ALL_TOOLS` set for the ReAct agent — code execution (sandboxed)~~ — **done (Phase 5).** Real Docker-container-isolated Python execution, admin-only — see [`AI/code-sandbox.md`](AI/code-sandbox.md). Structured DB queries as a tool remain unbuilt.
- ~~Self-reflection/retry loop~~ — **done** for the `AgenticRAG` path (`VENUAI/agentic_rag.py`'s bounded retrieve-evaluate-re-retrieve loop) and for citation/contradiction/critic verification (`VENUAI/verification_pipeline.py`). Standard `/query`'s `verify_node` still does not feed a failing check back into regeneration — only `AgenticRAG` does.
- Persistent, cross-session user-level memory/preferences, beyond the current per-session working/semantic memory. (Research memory tier added; user-preference tier still open.)
- ~~A `DataAnalystAgent` exists but has no upload endpoint/UI~~ — **done.** Full `/datasets/upload` → `/datasets/{id}/analyze` API, ownership/security, and a real frontend workspace (upload → ask → computed results + bar chart) — see [`AI/data-analyst.md`](AI/data-analyst.md).
- ~~Real MCP deployment~~ — **done (Phase 5), with a real caveat.** `VENUAI/mcp_server.py` was completely broken (stale `FastMCP` import against the installed `mcp` 2.x SDK, never caught by any test) — fixed, and given a real authenticated `streamable-http` transport (bearer key, constant-time check, refuses to start without one) on top of the pre-existing stdio transport — see [`AI/mcp-deployment.md`](AI/mcp-deployment.md). **Still open:** actual public hosting (TLS/domain/reverse proxy) is genuine infrastructure this environment doesn't have; the server's tools still assume one trusted caller per connection, not distinct per-request end-user identity.

**Generation Quality**
- Structured output / function calling for spec tables and comparisons, rather than hoping the model produces well-formed markdown.
- ~~Exact-span citations~~ — **partially done.** `CitationAgent` attributes each claim to specific `Evidence` items (document + page + content, token-overlap scored) — see [`AI/agents.md`](AI/agents.md) and `VENUAI/evidence.py`. Not yet exact-character-span within a chunk.
- ~~Evidence Explorer — browsing past claim/evidence verification results~~ — **done.** `VENUAI/evidence_store.py` persists every `/query` (standard, ReAct, and multi-agent modes — Phase 5 extended the latter two) and `/research` answer's claim-by-claim support breakdown (previously ephemeral/discarded); `GET /evidence`, `GET /evidence/{id}`, and a real frontend page (`chat-app/src/app/evidence/page.tsx`) make it browsable — see [`AI/evidence-explorer.md`](AI/evidence-explorer.md). **Still open:** no retention/pruning policy.

## Dependencies
- [`Backend/architecture.md`](Backend/architecture.md), [`Backend/authentication.md`](Backend/authentication.md), [`Database/architecture.md`](Database/architecture.md), [`DevOps/deployment.md`](DevOps/deployment.md), [`AI/workflows.md`](AI/workflows.md) — each Track A item traces back to a gap named in one of these.
- [`Backend/multi-tenancy.md`](Backend/multi-tenancy.md), [`Backend/oauth.md`](Backend/oauth.md), [`Backend/password-reset.md`](Backend/password-reset.md), [`AI/code-sandbox.md`](AI/code-sandbox.md), [`AI/mcp-deployment.md`](AI/mcp-deployment.md), [`AI/evidence-explorer.md`](AI/evidence-explorer.md) — Phase 4/5 additions tracked above.

## Interactions with Other Services
N/A — this is a planning document.

## Security Considerations
Track A item 1 (real auth) is a security item, not a feature nicety — treat it as blocking, not optional, before any deployment beyond a single trusted user's own machine.

## Performance Considerations
Track A items 6–7 are the two most likely to actually bite as usage grows (storage loss on restart, and query-time cost growing with corpus size).

## Future Improvements
This document itself — reprioritize as items are completed, and don't let it silently go stale relative to what's actually been built.

## Notes for Developers
When an item from this list gets implemented, move its description into the relevant domain document (as current-state fact) and delete it from here — don't let this file accumulate completed work described as still-future.
