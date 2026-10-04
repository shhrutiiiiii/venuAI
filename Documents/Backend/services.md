# External & Internal Services — VenuAI

## Purpose
Documents every service the backend integrates with — external (Groq) and internal module-level services (guardrails, memory, verification, analytics, document intelligence) — and the contract each one exposes to `rag_server.py`.

## Responsibilities
- List each service, what it does, and how it's invoked.
- Note lazy-loading/singleton patterns so initialization cost is understood.

## Scope
Covers integration points only. Internal algorithmic detail of each module lives in that module's own file comments/docstrings, not duplicated here.

## High-Level Overview

| Service | Module | Role |
|---|---|---|
| Groq LLM | `groq` SDK / `langchain-groq` | Every generation, classification, verification, and synthesis call |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` via `langchain_community.embeddings.HuggingFaceEmbeddings` | Turns document chunks and queries into vectors for Chroma |
| Vector store | `langchain_community.vectorstores.Chroma` | Stores/retrieves document chunk embeddings |
| Guardrails | `VENUAI/guardrails.py` → `get_guardrail_engine()` | Input/output safety checks, audit logging |
| Memory | `VENUAI/memory_manager.py` → `get_memory_manager()` | Working + semantic memory per session |
| Verification | `VENUAI/verifier_agent.py` → `get_verifier()` | Grounding score + answer revision against sources |
| Hallucination detection | `VENUAI/hallucination_detector.py` → `get_hallucination_detector()` | Hallucination score + annotated answer |
| Context engineering | `VENUAI/context_engineer.py` | Chunk ranking, history compression, prompt assembly within a token budget |
| Document intelligence | `VENUAI/doc_intelligence.py` | Auto-tags uploaded documents (domain, labs, classification) |
| Analytics | `VENUAI/analytics.py` → `get_analytics()` | Logs every query's latency/scores/agent-used for the dashboard |
| Agents | `VENUAI/agents/*` | Orchestrator + specialist agents for multi-agent mode — see [`../AI/agents.md`](../AI/agents.md) |

## Design Principles
1. **Lazy singletons for anything expensive to load.** `get_embeddings()`, `get_vector_store()`, `get_llm()`, `get_guardrail_engine()`, `get_memory_manager()`, `get_verifier()`, `get_hallucination_detector()`, `get_analytics()` all follow the same `global _x; if _x is None: ...` pattern — first call pays init cost, every call after is free.
2. **Groq calls always specify an explicit model**, defaulting to `GROQ_MODEL` env var (currently set to an invalid model string — see [ADR-001](../architecture-decisions.md#adr-001-groq-as-the-llm-provider-not-openaianthropic-directly)). `create_groq_completion()` in `rag_server.py` tries a hardcoded fallback list of model names if the primary fails.
3. **Every internal service degrades gracefully where it can.** Guardrail/classification failures fall back to permissive defaults (e.g. `classify_intent` falls back to `["general"]` on error) rather than hard-crashing the request — a deliberate tradeoff of availability over strict correctness.

## Workflow
Called from within the LangGraph nodes and the `/query` handler — see [`architecture.md`](architecture.md) for the call sequence.

## Inputs
Text (questions, document chunks, answers to verify), configuration (model names, API keys via `.env`).

## Outputs
LLM completions, embeddings, guardrail verdicts, memory context dicts, verification scores, analytics writes.

## Dependencies
- [`architecture.md`](architecture.md) — where these services are called from.
- [`../AI/agents.md`](../AI/agents.md), [`../AI/prompts.md`](../AI/prompts.md) — the agent/prompt layer built on top of the Groq service.

## Interactions with Other Services
- **Backend → Groq:** outbound HTTPS, one call per LLM invocation; no caching, no batching.
- **Backend → HuggingFace model weights:** downloaded/cached locally on first embedding-model load (CPU inference).
- **Backend → SQLite (guardrails audit):** synchronous write on every guardrail trigger — see [`../Database/architecture.md`](../Database/architecture.md).

## Security Considerations
`GROQ_API_KEY` is read from `.env` via `python-dotenv` and raises `RuntimeError` at import time if missing — fails loud, which is correct. No key rotation or secret-manager integration exists.

## Performance Considerations
- The embedding model runs on **CPU** (`model_kwargs={"device": "cpu"}`) — fine for prototype-scale document counts, a likely bottleneck if the knowledge base grows large or query volume increases.
- `create_groq_completion`'s fallback-model-list retry means a failing primary model silently adds latency (one failed call per fallback attempt) rather than failing fast — worth knowing when debugging slow responses.

## Future Improvements
- Correct `GROQ_MODEL`'s default to an actual Groq-served model ID.
- Consider GPU inference or a hosted embedding API if CPU embedding becomes a bottleneck.
- Add caching for the BM25 index (rebuilt from the full corpus on every retrieval today — see [`../Database/architecture.md`](../Database/architecture.md)).

## Notes for Developers
When adding a new internal service module, follow the existing lazy-singleton + `get_x()` accessor pattern already used throughout this file's table — it's the established convention in this codebase, not just incidental repetition.
