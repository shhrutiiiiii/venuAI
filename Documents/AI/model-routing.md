# Model Routing — VenuAI

## Purpose
Documents the AI Gateway (Phase 1): `VENUAI/ai_gateway/` — the model registry, router, provider abstraction, retry/circuit-breaker policy, and telemetry that replaced three separate, duplicated ad hoc Groq-calling code paths.

## Responsibilities
- Describe what each `ai_gateway` module owns.
- State plainly which real call paths route through it, and which don't yet.
- Name the provider abstraction's real vs. declared-but-unimplemented members.

## Scope
Covers `VENUAI/ai_gateway/*` only. Multimodal/vision routing is [`vision.md`](vision.md); the agents that call the gateway are in [`agents.md`](agents.md).

## High-Level Overview
Before this existed, three call sites each reimplemented model-name fallback independently and inconsistently: `rag_server.py`'s `create_groq_completion`, `orchestrator.py`'s `classify_intent`/`merge_results`, and `base_agent.py`'s `BaseSpecialistAgent.query`. Two of those three (orchestrator, base_agent) and a fourth (`verifier_agent.py`'s `check_consistency`) turned out to have the exact same latent bug — `os.getenv(...)` called with no `import os`, silently caught by their own `except Exception` and always falling back to degraded behavior. Migrating them onto the gateway fixed all three as a side effect of removing the broken code, not a separate patch — see `Documents/architecture-decisions.md` ADR-001 and the regression tests named below.

| Module | Owns |
|---|---|
| `model_registry.py` | The real model catalog (Groq only), `ModelCapability` tags (`FAST`/`REASONING`/`VERIFIER`/... — `VISION` deliberately absent, see [`vision.md`](vision.md)), `enabled` reflecting live `GROQ_API_KEY` presence |
| `model_router.py` | `route(task_type) -> ordered fallback chain`, honoring the `GROQ_MODEL` env override first |
| `provider.py` | `ModelProvider` ABC; `GroqProvider` (real, wraps the existing `groq` SDK client); `OpenAIProvider`/`LocalProvider` (declared interface only — both raise `NotImplementedError`, no credentials/runtime configured) |
| `gateway.py` | `AIGateway.complete()` — the single call surface; bounded retries + backoff/jitter per model, a per-model in-memory circuit breaker, `AIServiceUnavailableError` when the whole chain fails |
| `telemetry.py` | SQLite log (`VENUAI/data/model_routing_log.db`) of every call: task type, chain tried, model used, fallback count, latency, token counts, success/error — never logs message content |

## Current Catalog and Behaviour (ADR-009)
| Model | Capabilities | Context | Notes |
|---|---|---|---|
| `openai/gpt-oss-120b` | REASONING | 131,072 | Primary; `reasoning_effort` supported |
| `openai/gpt-oss-20b` | FAST | 131,072 | Classification; last-resort generation fallback |
| `qwen/qwen3.8-27b` | REASONING, JUDGE | 131,072 | Different family: fallback and independent eval judge; reasoning hidden via `reasoning_format` |
| `openai/gpt-oss-safeguard-20b` | SAFETY | 131,072 | Policy classifier, never in a chat chain |
| `meta-llama/llama-prompt-guard-2-86m` | PROMPT_GUARD | 512 | Injection classifier, never in a chat chain |

Context windows come from Groq `GET /openai/v1/models`. The retired `llama-3.3-70b-versatile` / `llama-3.1-8b-instant` are not in the registry.

- **Chain order:** `GROQ_MODEL` override (must be an enabled chat model), then models with the task's capability (latency class, then catalog order), then every other enabled chat model. GENERATION: gpt-oss-120b, qwen3.8-27b, gpt-oss-20b. FAST_CLASSIFICATION: gpt-oss-20b first.
- **Reasoning effort:** FAST_CLASSIFICATION `low`; GENERATION/SYNTHESIS/VERIFICATION `medium`. Passed only to models with `reasoning_style="effort"`; reasoning is excluded from content (`include_reasoning=False` / `reasoning_format="hidden"`, plus a defensive `<think>` strip) and `max_tokens` is increased by a reasoning headroom.
- **Timeouts:** all clients come from `provider.get_groq_client()/get_async_groq_client()/get_chat_groq()`: `GROQ_TIMEOUT_SECONDS` (default 30), SDK `max_retries=1`; the gateway owns retries.
- **Streaming:** `AIGateway.stream()` (sync) / `astream()` (async, worker threads) use the same chain and circuit breaker; retry/fallback only before the first chunk. The result exposes `model_used`, `ttft_ms`; telemetry (`model_routing_log.db`, column `ttft_ms`) is written when the stream ends. A failure after the first chunk is raised, not retried. `/query/stream` emits a `{"model": ...}` SSE event before tokens.
- **Routed through the gateway:** `generate_node` and `fallback_kb_node` (via `get_llm()`, a thin gateway adapter), `/query/stream`, and all agents. **Not** gateway-routed: the ReAct agent (needs a LangChain chat model) is built with `get_chat_groq` on the router's first model; its response reports that model.
- **Labels:** `model` in responses is the model that answered; `/health` reports `llm.primary_model` and `llm.fallback_chain` from the registry.

## Design Principles
1. **One call surface, not three.** Any new LLM-dependent code should call `AIGateway.complete(task_type, messages, ...)`, not construct its own retry loop.
2. **`GROQ_MODEL` override is respected, not silently overridden by "smarter" routing** — an operator who sets this env var still gets it tried first for every task type.
3. **Unavailable providers fail loud and specific**, never silently substituted — `OpenAIProvider`/`LocalProvider` raise `NotImplementedError` with the exact missing credential/runtime named.
4. **Never hallucinate when the model layer is down.** `AIServiceUnavailableError` propagates to the caller; nothing catches it and fabricates an answer.

## Workflow
```
Agent calls AIGateway.complete(task_type, messages)
   │
   ▼
model_router.route(task_type)  →  ordered model chain (GROQ_MODEL override first, then by latency_class)
   │
   ▼
for each model in chain:
   skip if its circuit is open
   try provider.complete() with bounded retries + backoff
   success → record telemetry, return
   │
   ▼ (all models exhausted)
record telemetry (failure) → raise AIServiceUnavailableError
```

## Inputs
`task_type` (`FAST_CLASSIFICATION` | `GENERATION` | `SYNTHESIS` | `VERIFICATION`), a messages list, generation params.

## Outputs
A `CompletionResult` (content, model_id, provider, token counts) or `AIServiceUnavailableError`.

## Dependencies
- `VENUAI/agents/orchestrator.py`, `VENUAI/agents/base_agent.py`, `VENUAI/verifier_agent.py`, `VENUAI/research_planner.py`, `VENUAI/agentic_rag.py`, `VENUAI/agents/deep_research_agent.py`, `VENUAI/agents/data_analyst_agent.py`, `VENUAI/agents/vision_agent.py` — all route their LLM calls through this gateway.

## Interactions with Other Services
**Not covered by this gateway**: only the ReAct agent (LangGraph prebuilt needs a LangChain chat model); see "Current Catalog and Behaviour" above. `generate_node`, `fallback_kb_node` and `/query/stream` now go through the gateway (ADR-009).

`GET /models` (Phase 4) exposes `model_registry.get_catalog()` read-only, `enabled` reflecting live provider configuration — backs the Research Workspace's "Agents & Models" panel (`chat-app/src/app/workspace/page.tsx`). Listing only, no runtime model-switching UI.

## Security Considerations
No new secret-handling — the gateway is constructed with the same `groq_client` callers already built from `GROQ_API_KEY`; it doesn't read or store the key itself.

## Performance Considerations
The circuit breaker is in-memory, per-process — a restart resets it. Acceptable for a single-process deployment (Phase 1 ADR-003); would need a shared store (Redis) if ever run with multiple backend replicas.

## Future Improvements
1. Route the ReAct agent through the gateway too (needs a LangChain-compatible adapter; generate/fallback/stream are done).
2. Smarter routing by historical failure rate / cost, once real usage data exists in `model_routing_log.db` to inform it — today's routing is capability + env-override + latency-class only, deliberately not "smart" without data behind it.
3. A real `OpenAIProvider`/`LocalProvider` implementation if/when those credentials or a local runtime are actually configured.

## Notes for Developers
If you add a new LLM call site, check `model_router.TaskType` first — add a new task type only if none of the four existing ones fit, and route through `AIGateway.complete()` rather than calling a provider directly.
