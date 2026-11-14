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

### ADR-S2.1: One authorization module, 404 for everything not visible
**Decision:** `VENUAI/authz.py` owns tenant/owner/PUBLIC visibility. Not visible (other tenant or non-owner private) and missing are indistinguishable (404, same body); 403 only for visible-but-forbidden actions and role gates. **Consequence:** UI code keyed on 403 for documents/datasets must treat 404 as "no access"; admin-only pages still receive 403.

### ADR-S2.2: Tenant is part of every memory and retrieval key
**Decision:** memory (working, episodic, semantic, research, long-term) and vector retrieval are keyed/filtered by tenant as well as user; `ScopedDocIds` carries the tenant through existing `allowed_doc_ids` plumbing so no caller signature changed. Legacy `sessions` rows are migrated under tenant `default`; legacy Chroma semantic facts without `tenant_id` are no longer recalled (safe default).

### ADR-S2.3: Deny-by-default tool policy with operator switches
**Decision:** code execution needs role admin AND `ENABLE_CODE_EXECUTION=1`; per-request tool budget; unscoped legacy tools off unless `ALLOW_UNSCOPED_TOOLS=1`; server-wide MCP pin approval needs `SUPERADMIN_USER_IDS`. **Consequence:** these are new env vars; defaults are the safe ones.
