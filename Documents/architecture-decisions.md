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

## Future Improvements
New ADRs should be appended here, never inserted out of order — each ADR number is permanent once written, even if a later decision supersedes it (supersession should be noted in the superseding ADR, not by editing the old one).

## Notes for Developers
If you make a decision that a future reader would otherwise have to reverse-engineer from a diff, write it here in the same change.
