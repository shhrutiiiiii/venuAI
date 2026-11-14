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

## Future Improvements
New ADRs should be appended here, never inserted out of order — each ADR number is permanent once written, even if a later decision supersedes it (supersession should be noted in the superseding ADR, not by editing the old one).

## Notes for Developers
If you make a decision that a future reader would otherwise have to reverse-engineer from a diff, write it here in the same change.
