> Part of the `AI/` documentation folder. See [`index.md`](index.md).

# Observability and Online Evals (Wave B / B3)

## Purpose
Per-request traces, a persisted trace store, always-on LLM/tool ledgers, optional OpenTelemetry GenAI export and sampled online evals.

## Responsibilities
- `VENUAI/tracing.py`: contextvar-based trace. Frozen API: `start_trace`, `span`, `emit_step`, `current_trace_id`, `record_llm_call`, `finish_trace`. Additive: `drain_events` (the SSE generator drains typed step events), `record_tool_call`. With no active trace every call is a cheap no-op; nothing ever raises.
- `VENUAI/trace_store.py`: SQLite `data/traces.db` (tables `traces`, `llm_calls`, `tool_calls`). TTL 14 days (`TRACE_TTL_DAYS`). Tenant-scoped. No question text unless `TRACE_STORE_CONTENT=on` (then PII-redacted through `pii_redaction.redact`). Tool args are stored only as `args_sha256`.
- `VENUAI/trace_api.py`: `GET /traces/{id}`, `GET /traces?limit=`, admin `GET /ops/tool-calls`, `GET /ops/metrics`. A user sees only their own traces; an admin sees their tenant's; everything else is a 404.
- `VENUAI/otel_genai.py`: OTel GenAI semantic-convention spans (`invoke_agent`, `chat <model>` with `gen_ai.usage.input_tokens/output_tokens`, `execute_tool <name>`), convention version pinned (`GENAI_SEMCONV_VERSION`). Built after the fact from the finished trace, no hot-path cost, no content attributes. Inert: `opentelemetry` is never imported unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set (deps in `VENUAI/requirements-otel.txt`). Sentry likewise via `SENTRY_DSN`.
- `VENUAI/online_evals.py`: `ONLINE_EVAL_SAMPLE` (default 0.0). Scores the sampled trace with `local_faithfulness` when available, else a lexical support check (labelled `lexical_support`, a coarse proxy). No answer/context means `score: null`. A rolling mean below `ONLINE_EVAL_MIN_SCORE` logs a warning and sets `online_eval_regression` in `/analytics/dashboard`.
- `analytics.py` dashboard gains `ops` (tokens, p50/p95, cache hit rate, escalation rate, cost) and `online_eval`. `cost_usd` is null until the model registry carries $/Mtok.
- `.github/workflows/nightly_eval.yml`: replays the golden set; manual dispatch until `NIGHTLY_EVAL_ENABLED=true` and a `GROQ_API_KEY` secret exist.

## Integration (for the pipeline)
Call `tracing.start_trace(...)` at the start of a request, `emit_step` around pipeline phases, `record_llm_call` from the gateway, and `finish_trace({...})` at the end. `finish_trace` accepts `question`, `retrieved`, `model_path`, `cache_hit`, `verification`, `prompt_version` and the transient `answer`/`context_chunks` (used only by online evals, never persisted).

## Not yet implemented
Storage sampling by rate, trace diff UI, OTLP metrics/logs export.

## Related
[`agent-protocols.md`](agent-protocols.md), [`model-routing.md`](model-routing.md)
