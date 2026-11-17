"""OpenTelemetry GenAI-semantic-convention export + optional Sentry (B3).

STRICTLY INERT by default: this module does not import ``opentelemetry`` (or
``sentry_sdk``) at import time, and ``export_trace`` is a no-op unless
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. Install the optional deps from
``requirements-otel.txt`` to use it.

Spans are built from a finished trace dict (see ``tracing.finish_trace``) with
explicit start/end timestamps, so there is no hot-path cost during the request:

* root span ``invoke_agent venuai`` (``gen_ai.operation.name=invoke_agent``)
* one ``chat <model>`` span per LLM call (``gen_ai.operation.name=chat``,
  ``gen_ai.provider.name``, ``gen_ai.request.model``,
  ``gen_ai.usage.input_tokens`` / ``gen_ai.usage.output_tokens``)
* one ``execute_tool <name>`` span per tool call (``gen_ai.tool.name``)

No prompt/answer content is attached (``gen_ai.input.messages`` etc. are
opt-in content attributes in the convention; we never set them).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("drdo-rag")

# GenAI semantic conventions are still "Development" status upstream; pin the
# version we implemented against so attribute drift is explicit.
GENAI_SEMCONV_VERSION = "1.37.0"
PROVIDER_NAME = "groq"

_tracer: Any = None
_sentry_ready = False


def enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def _get_tracer():
    """Lazily build a tracer. Only ever called when ``enabled()``."""
    global _tracer
    if _tracer is not None:
        return _tracer
    from opentelemetry import trace as otel_trace  # lazy: optional dep
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", "venuai"),
        "venuai.genai_semconv": GENAI_SEMCONV_VERSION,
    }))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    otel_trace.set_tracer_provider(provider)
    _tracer = otel_trace.get_tracer("venuai", GENAI_SEMCONV_VERSION)
    return _tracer


def build_span_specs(trace: dict) -> list[dict]:
    """Pure function: trace dict -> list of span specs (name, attrs, start/end ns, parent idx).
    Separated from the SDK so the convention mapping is unit-testable without opentelemetry."""
    t0_ns = int(float(trace.get("started_at") or 0) * 1e9)
    total_ms = float(trace.get("total_ms") or 0)
    specs: list[dict] = [{
        "name": "invoke_agent venuai", "parent": None, "start_ns": t0_ns,
        "end_ns": t0_ns + int(total_ms * 1e6), "error": False,
        "attrs": {"gen_ai.operation.name": "invoke_agent", "gen_ai.provider.name": PROVIDER_NAME,
                  "gen_ai.agent.name": "venuai", "venuai.trace_id": trace.get("trace_id"),
                  "venuai.path": trace.get("path"), "venuai.cache_hit": bool(trace.get("cache_hit"))},
    }]
    for c in trace.get("llm_calls", []):
        end_ms = float(c.get("t_ms") or total_ms)
        start_ms = max(0.0, end_ms - float(c.get("latency_ms") or 0))
        specs.append({
            "name": f"chat {c.get('model')}", "parent": 0,
            "start_ns": t0_ns + int(start_ms * 1e6), "end_ns": t0_ns + int(end_ms * 1e6), "error": False,
            "attrs": {"gen_ai.operation.name": "chat", "gen_ai.provider.name": PROVIDER_NAME,
                      "gen_ai.request.model": c.get("model"), "gen_ai.response.model": c.get("model"),
                      "gen_ai.usage.input_tokens": int(c.get("prompt_tokens") or 0),
                      "gen_ai.usage.output_tokens": int(c.get("completion_tokens") or 0),
                      "venuai.task": c.get("task"), "venuai.cached": bool(c.get("cached"))},
        })
    for tc in trace.get("tool_calls", []):
        specs.append({
            "name": f"execute_tool {tc.get('tool')}", "parent": 0, "start_ns": t0_ns,
            "end_ns": t0_ns + int(float(tc.get("latency_ms") or 0) * 1e6),
            "error": tc.get("status") not in (None, "ok"),
            "attrs": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tc.get("tool"),
                      "venuai.args_sha256": tc.get("args_sha256"), "venuai.decision": tc.get("decision")},
        })
    return specs


def export_trace(trace: dict) -> bool:
    """Export a finished trace. Returns False (never raises) when disabled/failing."""
    if not enabled():
        return False
    try:
        tracer = _get_tracer()
        from opentelemetry import trace as otel_trace  # lazy
        from opentelemetry.trace import Status, StatusCode

        specs = build_span_specs(trace)
        made: list = []
        for s in specs:
            parent_ctx = otel_trace.set_span_in_context(made[s["parent"]]) if s["parent"] is not None else None
            sp = tracer.start_span(s["name"], context=parent_ctx, start_time=s["start_ns"],
                                   attributes={k: v for k, v in s["attrs"].items() if v is not None})
            if s["error"]:
                sp.set_status(Status(StatusCode.ERROR))
            made.append(sp)
        for sp, s in reversed(list(zip(made, specs))):
            sp.end(end_time=s["end_ns"])
        return True
    except Exception as e:
        logger.warning("OTel export failed (continuing): %s", e)
        return False


def init_sentry() -> bool:
    """Initialise Sentry only if ``SENTRY_DSN`` is set and ``sentry_sdk`` is installed."""
    global _sentry_ready
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn or _sentry_ready:
        return _sentry_ready
    try:
        import sentry_sdk  # lazy: optional dep
        sentry_sdk.init(dsn=dsn, send_default_pii=False,
                        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")))
        _sentry_ready = True
    except Exception as e:
        logger.warning("SENTRY_DSN set but sentry_sdk unavailable/failed: %s", e)
    return _sentry_ready
