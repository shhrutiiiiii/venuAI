# Architecture Decision Records — VenuAI

## Purpose
The record of every cross-cutting architectural decision in this codebase and why it was made. Each decision documented here, even when made implicitly by whoever wrote the original code, so future changes are informed rather than guessed at.

## Responsibilities
- Record each decision with its context, the choice made, and the consequence accepted.
- Be the place other documents link to instead of re-explaining "why" inline.

## Scope
Cross-cutting decisions only — domain-internal choices stay in their domain document.

---

### ADR-001: Groq as the LLM provider, not OpenAI/Anthropic directly
**Context:** The system needs fast, cheap LLM inference for intent classification, generation, verification, and multi-agent synthesis — several LLM calls can happen per single user query (classify → generate → verify, or classify → fan-out N agents → merge).
**Decision:** Use Groq's inference API (`groq` SDK, `langchain-groq`) throughout.
**Consequence:** Very low latency per call makes multi-call pipelines (classify-then-route-then-verify) practical without the user noticing. The tradeoff is dependency on Groq's specific hosted model catalog — the current default model string (`qwen/qwen3.8-27b`) does not match any model Groq actually serves; this should be corrected to a real Groq model ID (e.g. a `llama-3.x` or `qwen/qwen3-32b` variant) rather than assumed correct.

### ADR-002: Mandatory post-generation verification
**Context:** A RAG system grounded in uploaded defence-domain documents must not silently hallucinate facts.
**Decision:** Every generated answer passes through a `verify_node` (VerifierAgent + HallucinationDetector) before being returned, producing a grounding score, hallucination score, and an annotated/possibly-revised answer. This is a graph edge (`generate → verify → END`), not optional middleware.
**Consequence:** Every `/query` call costs at least one extra LLM round-trip. This is accepted because trustworthiness of answers matters more than shaving one call's latency. Note that `/query/stream` currently skips this step (see [`Backend/architecture.md`](Backend/architecture.md) Future Improvements) — that is an inconsistency, not a deliberate second decision.

### ADR-003: No async job queue yet
**Context:** Unlike systems with heavy background processing (e.g. document-extraction pipelines), VenuAI's slow work — PDF parsing, embedding, LLM calls — happens synchronously inside the FastAPI request handler.
**Decision:** Accept a fully synchronous request path for Phase 1. `BackgroundTasks` is used only for the two things that don't affect the response (saving memory, logging analytics) — not for the LLM work itself.
**Consequence:** Simple to reason about and debug. Does not hold up under many concurrent uploads/queries — each ties up a request thread for the full retrieve→generate→verify duration. Acceptable for a single-user/demo deployment; must be revisited (Celery/RQ-style queue, or at minimum full `async`/non-blocking LLM calls) before multi-user production load. See [`future-additions.md`](future-additions.md).

### ADR-004: Real server-side auth replacing the original mock (superseded)
**Context:** The frontend originally shipped with login screens backed only by `chat-app/src/lib/mockAuth.ts` — hardcoded demo users, fake JWT-shaped tokens in `localStorage`/`sessionStorage`, and zero enforcement on the FastAPI backend. This was explicitly flagged as unacceptable for any real deployment.
**Decision:** Replaced with real auth: `VENUAI/auth.py` (bcrypt password hashing, signed JWT access tokens, revocable hashed refresh tokens, account lockout), enforced via FastAPI dependencies on every previously-open route. The frontend's `mockAuth.ts` was deleted; `authClient.ts` calls real `/auth/*` endpoints through Next.js proxy routes that hold tokens in httpOnly cookies.
**Consequence:** Real authentication and authorization now exist. **Not yet done:** per-chunk retrieval authorization — the hybrid retriever does not filter results by document ownership/classification before generation, so multi-tenant document isolation is not yet complete end-to-end. See [`Backend/authentication.md`](Backend/authentication.md) Future Improvements.

### ADR-005: Hybrid retrieval (Chroma + BM25), not vector-only
**Context:** Pure vector similarity search misses exact keyword/code/name matches (e.g. a specific lab code or weapon designation) that a defence knowledge base needs to get right.
**Decision:** `retrieve_node` builds an `EnsembleRetriever` combining the Chroma similarity retriever and a `BM25Retriever` built from the same corpus, weighted 50/50, whenever documents already exist in the store.
**Consequence:** Better recall on exact-term queries at the cost of rebuilding the BM25 index from the full Chroma corpus on every retrieval call — this does not scale past a small-to-medium document set without caching the BM25 index. See [`Database/architecture.md`](Database/architecture.md).

### ADR-006: Multi-agent routing via LLM intent classification
**Context:** Different question types (technical specs vs. lab/org structure vs. research programs) benefit from differently-tuned specialist prompts rather than one generic prompt.
**Decision:** `OrchestratorAgent.classify_intent` makes a fast, low-token Groq call to label the query into one or more of `technical/labs/research/general`, then routes to matching specialist agent(s) in parallel, merging multi-domain results via a synthesis LLM call.
**Consequence:** Classification itself can misroute (it's a best-effort LLM call with a hardcoded fallback to `general` on failure) — this is a soft-routing system, not a deterministic one. See [`AI/agents.md`](AI/index.md).

### ADR-007: Cross-encoder reranking, relevance floor, cached BM25, contextual chunking
**Context:** The ADR-005 hybrid never really ran (its BM25 half needed the uninstalled `rank_bm25`, so it silently degraded to dense-only), the only "reranking" was a BM25/recency/page-position heuristic that does not measure relevance, nothing could say "not in the documents", and follow-up questions were searched verbatim.
**Decision:** One shared pipeline (`VENUAI/retrieval.py`): optional LLM query rewrite (FAST model) -> dense + own cached BM25 -> RRF -> cross-encoder rerank (`cross-encoder/ms-marco-MiniLM-L-6-v2`, configurable) -> relevance floor -> abstain graph node when nothing survives; contextual-chunk prefixes at ingest; configurable embedding model recorded in collection metadata. Every model-dependent step falls back to the previous behaviour if unavailable.
**Consequence:** Offline benchmark (synthetic near-twin corpus): hit@1 0.36 -> 0.95, MRR 0.59 -> 0.97. Costs: ~one extra CPU model (~90 MB), up to one FAST LLM call per follow-up query and per chunk at upload (`CONTEXTUAL_CHUNKING=false` disables). The floor is calibrated on synthetic data and must be re-checked on real corpora. Supersedes the BM25 consequence in ADR-005. Details: [`AI/retrieval.md`](AI/retrieval.md).

### ADR-008: Layered trust & safety on every answer path
**Context:** `/query/stream` had no input guardrails, `validate_output` was dead code, grounding scores of 0.9/0.8/0.7 were hardcoded, verifier errors returned 500 or passed silently, and retrieved documents were pasted into the prompt as trusted text.
**Decision:** One guarded pipeline for all paths: regex + Prompt Guard on input; ingest-time sanitising/scanning and prompt-time spotlighting with quarantine for documents (treated as untrusted data); quote-level `[n]` citations verified deterministically; fail-closed verification (`verified=false` + reason, scores `null` when unmeasured); output guardrails plus a `gpt-oss-safeguard-20b` policy classifier that fails open only with an explicit `safety_check="unavailable"`; trace-id error hygiene. Details in [`AI/trust-and-safety.md`](AI/trust-and-safety.md).
**Consequence:** Extra small classifier calls per request and per ingest; response fields became nullable and gained `blocked`, `verified`, `citations`, `safety_check`. Frontends must not default missing scores to a made-up value. Supersedes the "`/query/stream` skips verification" note in ADR-002.

### ADR-009: Real harvest/score evaluation with a cross-family LLM judge (no offline stub)
- **Context:** the previous harness silently fell back to a canned "[Offline Evaluation Response]" with no chunks, producing committed reports with faithfulness 0.0 and 4 ms latency, which looked like measurements but measured nothing.
- **Decision:** `VENUAI/evals` runs only against the real pipeline (in-process TestClient or `--base-url`) and aborts loudly otherwise. Harvest (calls the generator) and score (deterministic metrics + judge) are separate steps over a JSONL file. The judge is `qwen/qwen3.8-27b`, a different model family from the `gpt-oss` generator, at temperature 0 with an on-disk cache. Judge failures are reported, never scored as 0. The eval corpus is synthetic and ingested into a throwaway tenant. RAGAS-style metrics are implemented in-house instead of adding the `ragas` dependency.
- **Consequences:** eval numbers are trustworthy and comparable across branches; live eval costs Groq tokens, so CI runs it only when `GROQ_API_KEY` exists. Details: [`AI/evaluation.md`](AI/evaluation.md). (ID prefixed `E` to avoid colliding with ADR numbers added in parallel.)

### ADR-B21: Contextvar tracing with a tenant-scoped trace store; OTel strictly opt-in
**Context:** B1/B2/B4 needed one tracing seam; observability must not cost latency or leak other users' data or content.
**Decision:** `tracing.py` keeps a contextvar trace (no-op without one) persisted at `finish_trace` to SQLite, tenant/owner scoped, content off by default, TTL 14 days. OTel GenAI spans are built from the finished trace and exported only if `OTEL_EXPORTER_OTLP_ENDPOINT` is set; `opentelemetry` is never imported otherwise.
**Consequence:** zero default overhead and no new dependency; the pinned OTel convention version may need bumping upstream. See [`AI/observability.md`](AI/observability.md).

### ADR-B22: Guarded tool execution with human approval and MCP tool pinning
**Context:** MCP and code tools are high-risk; tool descriptions can change under a "rug pull".
**Decision:** all tool calls pass `guarded_call` (policy, schema validation, breaker, approval, timeout/retry, ledger with hashed args). MCP tools default to approval; tool definitions are pinned by sha256 and disabled on drift until admin re-approval. The fake MCP client results were removed.
**Consequence:** safer by default; approvals add a round trip for MCP tools (`TOOL_APPROVAL=off` is an explicit opt-out). See [`AI/agent-protocols.md`](AI/agent-protocols.md).

### ADR-B23: MCP 2025-11-25 alignment and opt-in A2A
**Decision:** the MCP server validates `MCP-Protocol-Version`, publishes protected-resource metadata, accepts VenuAI JWTs with tenant scoping, and restricts `upload_document` to an allowlisted directory. A2A is off unless `A2A_ENABLED=on`, authenticated, rate limited, and routed through the orchestrator.
**Consequence:** the previous arbitrary-file read through `upload_document` is closed.

## Workflow
N/A — this document records decisions, it does not describe a runtime flow.

## Inputs / Outputs
N/A.

## Dependencies
Linked from every domain document that needs to justify a "why," rather than re-explaining it.

## Interactions with Other Services
N/A.

## Security Considerations
See ADR-004 — the mock-auth decision is the single most important security gap in the system and is tracked here precisely so it cannot be quietly forgotten.

## Performance Considerations
See ADR-001, ADR-003, ADR-005 — the three decisions with the most direct performance consequence.

### ADR-010: Current Groq model catalog, gateway-owned streaming, central client policy
**Context:** `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` were retired on Groq, but were still the default/primary and the verifier's documented fast model. Three LLM paths (LangGraph `generate_node`/`fallback_kb_node`, `/query/stream`, the ReAct agent) bypassed the gateway, so they had no shared fallback, circuit breaker or telemetry; Groq clients had no timeout; responses were labelled with a hardcoded `groq/compound`.
**Decision:** (1) The registry lists only models verified against Groq `GET /openai/v1/models`: `openai/gpt-oss-120b` (primary), `openai/gpt-oss-20b` (fast), `qwen/qwen3.8-27b` (different family: second-opinion fallback and `JUDGE` capability for evals), `openai/gpt-oss-safeguard-20b` (`SAFETY`) and `meta-llama/llama-prompt-guard-2-86m` (`PROMPT_GUARD`); classifiers are never in a chat fallback chain. (2) Every Groq client is built in `ai_gateway/provider.py` (`get_groq_client`, `get_async_groq_client`, `get_chat_groq`) with `GROQ_TIMEOUT_SECONDS` (default 30) and SDK `max_retries=1`; the gateway owns retries. (3) `AIGateway.stream()/astream()` walk the same chain and circuit breaker, retrying/falling back only before the first chunk; telemetry records model, latency and time-to-first-token. (4) `model_used` and `/health` report the model that actually answered / the registry's configured chain. (5) gpt-oss models get a per-task `reasoning_effort` (low for classification, medium otherwise), reasoning text is excluded from content, and `max_tokens` is widened to leave room for reasoning tokens.
**Consequence:** One place to correct a model id or timeout. The ReAct agent still needs a LangChain chat model, so it is built via the central helper on the router's first model but does not get gateway fallback; a mid-stream failure is surfaced to the client rather than silently retried. Supersedes the model-id remark in ADR-001.

## ADR-B31: Deep Research v2 as persistent jobs
**Decision:** research runs as SQLite-persisted jobs (plan -> editable plan -> parallel isolated sub-question agents -> synthesis -> separate citation pass with deterministic quote verification). Default depth `quick` (3 sub-questions, 40k tokens, 120 s); budget overruns return a flagged partial report (`budget_exceeded`). Jobs interrupted by restart are resumable. Legacy `POST /research` delegates via `research_jobs.run_research_sync`. See Documents/AI/deep-research.md.

## ADR-B32: Opt-in long-term memory; working memory keyed by (tenant, user, session)
**Decision:** long-term memory needs `LONG_TERM_MEMORY=on` AND a per-user opt-in; FAST-model extraction, PII-redacted, 300-char cap, deterministic ADD/UPDATE/NOOP by similarity. Security fix: working memory was keyed by session id only, and the API default session id "default-session" was shared by all users; it is now keyed by (tenant, user, session). See Documents/AI/memory.md.

## ADR-B33: Voice and report export as env-gated / bounded routers
**Decision:** voice is off unless `VOICE_ENABLED`, never stores audio, validates magic bytes/size/duration; export renders DOCX/PDF/MD from sanitized markdown (no HTML pass-through, 2 MB cap). See Documents/AI/voice.md.

### ADR-B01: One shared turn pipeline (`run_turn`) for every answer producer
**Context:** /query, /query/stream, the ReAct agent and multi-agent routing each did their own verification, safety, bookkeeping and response assembly, so fixes (and new fields) had to be made four times and the paths drifted.
**Decision:** `VENUAI/run_turn.py` owns the common head and tail: budget gate + usage meter + memory recall (`begin_turn`), answer cache, the capped corrective stage, and `finish_turn` / `finish_agent_turn` (contract fields, cache write, token accounting, memory write, trace). Producers differ only in how the raw answer is made (LangGraph first pass, gateway stream, ReAct through the gateway model chain, orchestrator). Verification's independent checks (LLM consistency, batched NLI, claim pipeline, safeguard classifier) run concurrently and the NLI/reranker models are warmed in a startup thread. Profiled cause of the 20-54 s latency: first-request NLI model load (~50 s cold) plus single-pair NLI calls (4 claims x 6 chunks = 6.4 s warm).
**Consequence:** one place to change guardrail/verify/cite behaviour. The node functions in rag_server stay (tests and the a4 harness use them); `/query` still runs `venuai_graph` for the first pass.

### ADR-B02: Corrective RAG, model cascade and a conservative answer cache
**Context:** a single pass could return a poorly supported answer; everything ran on the 120b model; repeated questions paid full price.
**Decision:** (1) Corrective loop (`corrective_rag.py`): cheap grading from rerank scores, LLM grader only for borderline retrievals, then at most `CORRECTIVE_MAX_ITERS` (hard cap 2) iterations - re-retrieve with the unsupported claims as extra queries, regenerate with critic feedback - stopping early on embedding convergence; abstain only when no claim is supported. The trigger is the measured claim-level critic / NLI hallucination score, NOT the token-F1 grounding number (about 0.3 for correct answers). (2) Cascade (`model_cascade.py`): rules classify simple lookups -> gpt-oss-20b low effort; synthesis/ambiguous -> 120b; escalate to 120b high effort when the verifier is unhappy (shares the corrective cap, `MAX_ESCALATIONS`). (3) Cache (`semantic_cache.py`): exact-hash tier on; semantic tier OFF by default and, if enabled, threshold >= 0.98 plus an exact match on designator tokens/numbers, because MiniLM scores Agni-V vs Agni-IV and AURORA-X vs AURORA-XB >= 0.93. Entries are scoped by tenant + visible doc set + corpus version + model config, never written for blocked/abstained/unverified answers or when history/memories were used, and invalidated on upload/delete.
**Consequence:** extra LLM spend is bounded and mostly zero on the happy path; wrong cross-question cache hits are structurally excluded.

### ADR-B03: Token budgets, structured outputs and the response contract
**Context:** no per-request or per-tenant limit existed; small LLM helpers parsed free text; clients could not see how an answer was made.
**Decision:** a contextvar `UsageMeter` records provider-reported tokens for every gateway call (null / `complete:false` when unreported, never estimated). `REQUEST_TOKEN_BUDGET` stops further calls (`BudgetExceeded`), `TENANT_DAILY_TOKEN_BUDGET` (SQLite counter) downshifts to the 20b model at 80% and refuses with HTTP 429 at 100%. Small helpers use `response_format=json_schema` via `structured.complete_json` (the provider retries plain when a model rejects it; parsing is repair-tolerant). /query and the stream done event gain `retrieved_chunks, usage, ttft_ms, latency_ms, cache_hit, model_path, followups, contradictions, corrective, trace_id, memories_used, pending_approval`; /query/stream emits typed `{"step": ...}` events. Follow-ups ride the generation call (`<followups>` tail) and cost an extra fast-model call only when the tail is missing.
**Consequence:** additive API; clients that ignore unknown fields/steps keep working.
## ADR-S1.1: Refresh-token families, token_version, single-use reset tokens
**Decision:** every refresh token belongs to a family; rotation is one `BEGIN IMMEDIATE` transaction (exactly one concurrent caller wins). Replaying an already-rotated token (outside a 5 s benign-race grace) revokes the whole family and is audited. `users.token_version` is embedded as the `tv` claim and checked on every request; it is bumped by logout, logout-all, password reset and role change, so access tokens die immediately instead of at expiry. Reset tokens are random 32 bytes, stored hashed, 15 min, single use, bound to the current password hash, and a newer token voids older ones.
**Consequence:** one extra column read per request (the user row is already loaded). Access tokens minted before this change are rejected once (users re-login).

## ADR-S1.2: Uniform authentication failures and lockout
**Decision:** unknown email, wrong password and locked account return the identical 401 and each cost one bcrypt verify; lockout is checked before the password, is not extended while active, and escalates exponentially (5 min doubling, 1 h cap). `forgot-password` always returns 202 and does its work in a background task (so timing and SMTP failures leak nothing). The locked state is therefore invisible to the client (UX cost accepted for no enumeration oracle).

## ADR-S1.3: Registration cannot join an existing tenant without an invite
**Decision:** self-registration creates a personal tenant (or one named by `organization_name`); `tenant_id` is only honoured together with a valid single-use `invite_token` from `tenant_invites` (hashed, expiring, optionally email-bound). Password policy (>= 12 chars, <= 72 bytes, not common, not the email name) applies to signup and reset only; login keeps the legacy minimum so existing accounts still work.
**Consequence:** the old "join default tenant by default" behaviour is gone; S2 owns the admin route that calls `auth.create_invite`.

## ADR-S1.4: OAuth hardening
**Decision:** state is random, stored hashed server-side, single-use, bound to the browser through an httpOnly `venuai_oauth_binding` cookie, with PKCE S256 (the verifier never leaves the server). `redirect_uri` must match an allow-list. Only provider-verified emails count (Google `verified_email`; GitHub primary+verified from `/user/emails`). Identities are keyed on `(provider, sub)`. An existing password account is never auto-linked by email (409); an authenticated "connect" flow is not built. New OAuth users get a personal tenant.

## ADR-S1.5: Demo and admin seeding
**Decision:** `SEED_DEMO_USER` (default on only outside production) seeds one demo user, never an admin; production always refuses it. First admin via `python -m VENUAI.auth create-admin` or one-time `BOOTSTRAP_ADMIN_EMAIL/PASSWORD`. The login demo box needs `NEXT_PUBLIC_SHOW_DEMO=true`.

## ADR-S1.6: CSRF and proxy trust on the Next side
**Decision:** `src/proxy.ts` rejects (403) every unsafe `/api/*` request that is not same-origin (Sec-Fetch-Site / Origin) or lacks `X-Requested-With: venuai`; a global fetch wrapper installed from AuthContext adds the header. The refresh cookie is `SameSite=Strict` and scoped to `/api/auth`. Proxies forward the client IP (`X-Forwarded-For`, nth entry from the right per `TRUSTED_PROXY_HOPS`) plus `X-VenuAI-Proxy-Secret`; the backend trusts the header only with that secret (`VENUAI/client_ip.py`, for S4's rate-limit key).
**Consequence:** any non-browser caller of the Next API must send the header and a same-origin marker.

## ADR-S1.7: Append-only security audit log
**Decision:** `VENUAI/security_audit.py` writes `security_events` (SQLite triggers abort UPDATE/DELETE); `log(event, user_id, tenant_id, ip, detail)` never raises and never receives secrets.

## Future Improvements
New ADRs should be appended here, never inserted out of order — each ADR number is permanent once written, even if a later decision supersedes it (supersession should be noted in the superseding ADR, not by editing the old one).

## Notes for Developers
If you make a decision that a future reader would otherwise have to reverse-engineer from a diff, write it here in the same change.

### ADR-B11: Graph retrieval via Personalized PageRank (HippoRAG-2 style), multi-hop only
**Context:** Dense/BM25 retrieval cannot connect facts that live in different documents (A mentions X and Y, B mentions Y and Z). The knowledge graph already links documents to entities (`/upload` auto-population).
**Decision:** `kg_retrieval.retrieve_graph(query, tenant_id, allowed_doc_ids, k, *, vs=None)` seeds entities by name match (20b NER only if `KG_NER_LLM=on` and nothing matched), runs PPR (damping 0.5, numpy power iteration, no new dependency) over an entity + per-document passage graph, then returns the best chunks of the top documents with `retriever="kg_ppr"` and an explainable `kg_path`. Tenant isolation: only the tenant graph is read; with an ACL, edges whose document is not authorised are dropped before PPR and results are filtered again. B1 calls it as a third RRF source only for multi-hop-classified queries under `KG_RETRIEVAL=on`. Stores gained `list_entities` / `list_relationships`.
**Consequence:** Works on the document-level graph that exists today (documents-mention-entities); precision improves if richer LLM extraction (`/graph/extract`) populates entity-entity edges. Any failure returns `[]`.

### ADR-B12: RAPTOR summaries are optional and token-budgeted
**Decision:** `raptor.py` (default `RAPTOR=off`) clusters chunk embeddings (KMeans), summarises clusters with the fast model (low effort) in at most `RAPTOR_MAX_CALLS` (6) calls per document over 2 levels, stores them in a separate Chroma collection `summaries` tagged tenant/doc/level, and exposes `retrieve_summaries` / `delete_summaries`. **Consequence:** summaries are LLM paraphrases, so they are retrieval aids; answers should still cite original chunks.

### ADR-B13: Local faithfulness never fakes a score
**Decision:** `local_faithfulness.score_claims` loads HHEM-2.1-open (or `LOCAL_FAITHFULNESS_MODEL`) and falls back to the already-used NLI cross-encoder, both from the local cache only (`LOCAL_FAITHFULNESS_DOWNLOAD=on` to fetch). No backend or no checkable claim means `score: None` with a reason. Exposed via `VerificationPipelineResult.local_faithfulness` when `LOCAL_FAITHFULNESS=on`. **Consequence:** NLI models are weak on numeric paraphrase (5000 km vs "5,000 - 8,000 km" can score low); treat the score as a signal, not a verdict. `find_contradictions` is deterministic (same-unit numeric conflicts >= 15% across different documents).

### ADR-B14: PII redaction and safety-classifier routing
**Decision:** `pii_redaction.redact/redact_obj` mask e-mail, Aadhaar (Verhoeff), cards (Luhn), PAN, phones, API keys/JWT/PEM and `password=` pairs; classification markings are kept. Guardrail audit logs use the full `redact`; user-facing answers use the narrower `redact_identifiers` (IDs and secrets only, because public contact details are legitimate). Safety classifiers resolve model ids from registry capabilities (`SAFETY`, `PROMPT_GUARD`) as an ordered fallback chain with telemetry, through the central Groq client; `*_MODEL` overrides the head and `*_FALLBACK_MODELS` appends (the 22m prompt guard is not in the registry, so it is opt-in via env).

### ADR-B15: Embedding model is opt-in and never re-indexed automatically
**Decision:** `embeddings_config.get_embeddings()` reads `EMBEDDING_MODEL` (MiniLM default). `scripts/reindex.py` re-embeds into a NEW Chroma directory (dry-run unless `--run`, refuses non-empty targets) and never touches the live index. **Consequence:** switching requires an explicit operator step; the existing mismatch warning in `rag_server` covers a wrong combination.

### ADR-B16: The built-in knowledge base is chunked per entry
**Context:** the KB fallback fed `json.dumps(kb)[:8000]` as one document, so verification compared claims to a raw JSON dump (grounding about 0.1) and no entry could be cited.
**Decision:** `kb_documents.py` renders each organisation / cluster / laboratory / weapon-platform entry as a readable Document (`title`, `kb_entry_id`, `doc_id=kb:<id>`, `kb_fallback=True`, filename "Built-in knowledge base (not from your documents) - <title>") and retrieves with BM25-style scoring. `kb_fallback_documents(question, k)` is the helper for fallback paths; specialist agents already use it. **Consequence:** claims verify against the matching entry; the KB is still labelled as built-in, never as the user's documents.

### ADR-B17: Eval additions are dry-run first
**Decision:** `semantic_cache_sweep`, `calibration`, `reasoning_experiment`, `chunk_size_sweep` added; `harvest --resume`; judge key isolation (`EVAL_JUDGE_API_KEY`); five poisoned-document cases with exact canaries. Anything that spends Groq tokens needs `--live`. **Finding:** with MiniLM, near-miss pairs (AURORA-XB vs AURORA-X, Agni-V vs Agni-IV) reach cosine >= 0.93 in 2 of 4 cases while only 1 of 4 paraphrases does, so a 0.93 semantic cache tier can serve wrong answers; keep it off or raise the threshold (>= 0.98 on this sample) and rely on the exact-hash tier.

### ADR-S5.1: Hash-pinned Python lock; loose `requirements.in`
Direct dependencies live upper-bounded in `VENUAI/requirements.in`; `uv pip compile --universal --generate-hashes` produces `requirements.lock`, which CI and Docker install with `--require-hashes`. Rationale: reproducible, tamper-evident installs and weekly Dependabot/pip-audit signal. The voice prototype libraries are split out because they cannot be built in the server image. Exceptions to pip-audit live in `VENUAI/audit-ignore.txt` with owner and expiry (CI fails on expired entries).

### ADR-S5.2: Separate dev and prod compose files
Dev keeps convenience ports on localhost; prod publishes only the TLS proxy and runs every service read-only, cap-dropped and resource-limited. Rationale: a single file with defaults tends to leak dev conveniences (published DB/API ports, default passwords) into production.

### ADR-S5.3: Security scanning is a separate workflow
`.github/workflows/security.yml` (pip-audit, npm audit, Bandit, gitleaks, CodeQL, Trivy fs and image, SBOM, compose policy) runs on push, PRs and weekly, with all actions pinned by commit SHA and `permissions: contents: read` by default.

### ADR-S4.1: One module owns HTTP hardening; production fails fast
**Context:** headers, CORS, limits and error shapes were scattered or absent (wildcard CORS methods/headers, `str(e)` in responses, per-IP-only limits, `/docs` always on, silent weak config).
**Decision:** `VENUAI/http_security.py` (wired by one `register_security(app, limiter)` call in rag_server.py) provides a pure-ASGI middleware (request id, security headers on every response including errors and preflights, body-size limits, last-resort 500), a global rate-limit middleware, strict CORS, global exception handlers and an authenticated `GET /capabilities`. `VENUAI/config.py` validates configuration at start-up: with `ENV=production` (or `APP_ENV`) a weak `JWT_SECRET_KEY`, wildcard/local/non-https `ALLOWED_ORIGINS`, non-https `FRONTEND_URL`, debug flags or demo seeding abort start-up with every problem listed. `/docs`, `/redoc`, `/openapi.json` are off in production unless `ENABLE_DOCS=1`.
**Consequences:** pure ASGI (not BaseHTTPMiddleware) so SSE is never buffered. Rate keys are the JWT subject, else the client IP; `X-Forwarded-For` is honoured only when the peer matches `TRUSTED_PROXY`. Expensive LLM/upload routes cost 3-10 units of the per-user bucket and also hit a per-IP and a per-user daily bucket. No response carries `str(exception)`: use `http_security.safe_error(e, "public message", status)`.

### ADR-S4.2: CSP starts Report-Only and without nonces
**Decision:** security headers for the Next app live in `next.config.ts`; the CSP is set in `src/proxy.ts` as `Content-Security-Policy-Report-Only` (`CSP_ENFORCE=1` flips it) with `script-src 'self' 'unsafe-inline'` because nonces would force every page to dynamic rendering. Reports go to `/api/csp-report` (bounded, logged without query strings). The frontend discovers optional backend features through `/api/capabilities`, which now prefers the backend's `/capabilities` over probing `/openapi.json`.
### ADR-S3.1: Uploads are bounded while reading, then structurally scanned
**Decision:** `upload_security.read_upload_capped` reads 1 MB chunks and aborts with 413 once the running total passes the cap; a declared `Content-Length` over the cap is rejected before any read (the Next proxies repeat the check in `lib/requestLimits.ts`). PDFs must start with `%PDF-`, end with `%%EOF`, contain none of `/JavaScript /JS /Launch /EmbeddedFile(s) /OpenAction /RichMedia /SubmitForm /GoToR` (raw bytes, including `#XX`-escaped names, plus the pypdf catalog for object-stream cases), stay under `MAX_PDF_PAGES` (300) and parse inside `PDF_PARSE_TIMEOUT_SECONDS`. Filenames with control/bidi characters, `..`, separators, over 255 chars or dangerous double extensions are rejected (400).
**Consequence:** Active-content PDFs that used to be accepted are now 422. Limits are env-configurable (`MAX_PDF_PAGES`, `DATASET_MAX_ROWS`, `DATASET_MAX_COLUMNS`, `DATASET_MAX_ZIP_ENTRIES`, `DATASET_MAX_UNCOMPRESSED_MB`, `DATASET_MAX_COMPRESSION_RATIO`). The parse timeout abandons the worker thread (it cannot be killed); a process-level rlimit worker is not done.

### ADR-S3.2: XLSX/CSV decompression-bomb defence
**Decision:** XLSX central directory is inspected before pandas: entry count, declared uncompressed total (100 MB), per-entry ratio (100), encryption flag, `vbaProject.bin`, external links, ActiveX/embeddings, `..` members. Parsing uses `nrows=MAX_ROWS+1`. CSV must be UTF-8, no NULs, delimiter sniffed. Parse errors are generic (detail logged). `neutralise_formula/neutralise_dataframe` prefix `'` to `= + - @ \t \r` cells for any export/echo path.
**Consequence:** A zip bomb is refused from headers in milliseconds. A bomb that lies in its central directory is still bounded by the 20 MB upload cap plus openpyxl's own parse; a streaming decompression counter is future work.

### ADR-S3.3: One SSRF gate for outbound URLs
**Decision:** `net_safety.validate_url/safe_get`: https only, every resolved address must be public (private, loopback, link-local incl. 169.254.169.254, CGNAT, multicast, reserved, IPv4-mapped IPv6 blocked), the connection goes to the validated IP with SNI/Host preserved (DNS rebinding), redirects re-validated per hop (max 3), size and time capped. The MCP HTTP client re-validates on every request unless `MCP_ALLOW_PRIVATE_URLS=1` (dev only). `oauth.py` (S1) can import `safe_get`; its endpoints are fixed provider URLs.

### ADR-S3.4: MCP client command allowlist and minimal env
**Decision:** stdio servers must use `npx|node|python|python3|uvx|uv|docker` as a bare command name; `npx`/`uvx` packages must be version-pinned (`pkg@1.2.3`). Child processes get only PATH/HOME/TEMP-style variables plus the server's explicitly configured `env` (no GROQ key inherited; the default config no longer passes one). MCP tool errors no longer include exception text.

### ADR-S3.5: Sandbox hardening
**Decision:** `docker run` now adds `--cap-drop ALL --user 65534:65534 --ipc none --memory-swap == --memory --pull never`, `--ulimit nofile/fsize/nproc`, `noexec` tmpfs; stdout/stderr are read with a byte cap at read time (a flood kills the container); at most `SANDBOX_MAX_CONCURRENT_PER_USER` (2) runs per user. `SANDBOX_IMAGE` is env-set (pin `python@sha256:...` in production); the image is never pulled at request time (`SANDBOX_AUTO_PULL=1` allows `ensure_image_pulled` to pull at deploy time). If the API runs in Compose, use a separate runner or gVisor rather than mounting docker.sock (S5).

### ADR-S3.6: Request bounds and LLM output sanitising
**Decision:** `request_limits.py`: question/query 4000 chars, history at most 20 items of `user|assistant` role x 4000 chars (40k total), `top_k` 1..20, `session_id` `^[A-Za-z0-9_-]{1,64}$`, graph text 50k. Violations return a 422 whose body lists field locations only (no echoed input). `output_sanitizer.neutralise_markdown`, called from `GuardrailEngine.validate_output` (so both `/query` and `/query/stream` final text), removes markdown/HTML images and scripts, and turns links to non-allowlisted hosts into `label (host)`. Allowed: relative paths, `#anchors`, `cite:`/`citation:`/`doc:` and hosts in `OUTPUT_LINK_ALLOWLIST` / `OUTPUT_IMAGE_ALLOWLIST`. Prompt-injection detection runs on NFKC-normalised, zero-width-stripped text plus base64/hex-decoded variants.
**Consequence:** The current UI renders answers as plain text (no markdown renderer), so streamed tokens are not an exfil channel today; if a markdown renderer is added it must apply the same sanitiser to partial text.
### ADR-S2.1: One authorization module, 404 for everything not visible
**Decision:** `VENUAI/authz.py` owns tenant/owner/PUBLIC visibility. Not visible (other tenant or non-owner private) and missing are indistinguishable (404, same body); 403 only for visible-but-forbidden actions and role gates. **Consequence:** UI code keyed on 403 for documents/datasets must treat 404 as "no access"; admin-only pages still receive 403.

### ADR-S2.2: Tenant is part of every memory and retrieval key
**Decision:** memory (working, episodic, semantic, research, long-term) and vector retrieval are keyed/filtered by tenant as well as user; `ScopedDocIds` carries the tenant through existing `allowed_doc_ids` plumbing so no caller signature changed. Legacy `sessions` rows are migrated under tenant `default`; legacy Chroma semantic facts without `tenant_id` are no longer recalled (safe default).

### ADR-S2.3: Deny-by-default tool policy with operator switches
**Decision:** code execution needs role admin AND `ENABLE_CODE_EXECUTION=1`; per-request tool budget; unscoped legacy tools off unless `ALLOW_UNSCOPED_TOOLS=1`; server-wide MCP pin approval needs `SUPERADMIN_USER_IDS`. **Consequence:** these are new env vars; defaults are the safe ones.
