"""Request tracing (Wave B / B3). Frozen public signatures from contract 2.1.

A trace is held in a ``contextvars.ContextVar`` so any code running inside the
request (including ``asyncio.to_thread`` / executor work that copies context)
can call ``emit_step`` / ``span`` / ``record_llm_call`` without plumbing. With
no active trace every function is a cheap no-op. Nothing here ever raises.

Persistence: ``finish_trace`` writes to ``trace_store`` (SQLite, tenant-scoped,
no content by default). Optional OpenTelemetry export lives in
``otel_genai`` and is completely inert unless ``OTEL_EXPORTER_OTLP_ENDPOINT``
is set (this module never imports opentelemetry).

Additions beyond the frozen contract (additive): ``drain_events`` (the SSE
generator drains typed step events), ``record_tool_call``, ``active_trace``.
"""
from __future__ import annotations

import contextvars
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger("drdo-rag")

_MAX_STEPS = 200
_MAX_SPANS = 500
_MAX_CALLS = 200
_MAX_EVENTS = 1000


class _Trace:
    __slots__ = ("trace_id", "tenant_id", "user_id", "path", "t0", "started_at", "steps",
                 "events", "drained", "spans", "span_stack", "llm_calls", "tool_calls")

    def __init__(self, trace_id: str, tenant_id: str, user_id: str, path: str) -> None:
        self.trace_id = trace_id
        self.tenant_id = tenant_id or "default"
        self.user_id = user_id or ""
        self.path = path
        self.t0 = time.perf_counter()
        self.started_at = time.time()
        self.steps: dict[str, dict] = {}
        self.events: list[dict] = []
        self.drained = 0
        self.spans: list[dict] = []
        self.span_stack: list[int] = []
        self.llm_calls: list[dict] = []
        self.tool_calls: list[dict] = []

    def now_ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


_current: contextvars.ContextVar[Optional[_Trace]] = contextvars.ContextVar("venuai_trace", default=None)


def active_trace() -> Optional[_Trace]:
    return _current.get()


def start_trace(trace_id: str, tenant_id: str, user_id: str, path: str) -> None:
    """Begin a trace for one request (replaces any trace active in this context)."""
    try:
        _current.set(_Trace(str(trace_id), tenant_id, user_id, path))
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("start_trace failed: %s", e)


def current_trace_id() -> Optional[str]:
    t = _current.get()
    return t.trace_id if t else None


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """Time a named span (retrieve/rerank/generate/verify/tool ...)."""
    t = _current.get()
    if t is None:
        yield None
        return
    rec: Optional[dict] = None
    try:
        if len(t.spans) < _MAX_SPANS:
            rec = {"name": name, "t_start_ms": t.now_ms(), "t_end_ms": None, "status": "ok",
                   "attrs": _safe_attrs(attrs),
                   "parent": t.span_stack[-1] if t.span_stack else None}
            t.spans.append(rec)
            t.span_stack.append(len(t.spans) - 1)
    except Exception:  # pragma: no cover
        rec = None
    try:
        yield None
    except BaseException as e:
        if rec is not None:
            rec["status"] = "error"
            rec["attrs"]["error"] = type(e).__name__
        raise
    finally:
        if rec is not None:
            rec["t_end_ms"] = t.now_ms()
            if t.span_stack:
                t.span_stack.pop()


def emit_step(step: str, status: str, label: str = "", **detail: Any) -> None:
    """Emit a typed step event (status: start|end|error) for the stream + trace."""
    t = _current.get()
    if t is None:
        return
    try:
        now = t.now_ms()
        rec = t.steps.get(step)
        if rec is None:
            if len(t.steps) >= _MAX_STEPS:
                return
            rec = {"id": step, "label": label, "t_start_ms": now, "t_end_ms": None,
                   "status": "running", "detail": {}}
            t.steps[step] = rec
        if label:
            rec["label"] = label
        if detail:
            rec["detail"].update(_safe_attrs(detail))
        if status in ("end", "error"):
            rec["t_end_ms"] = now
            rec["status"] = "ok" if status == "end" else "error"
        if len(t.events) < _MAX_EVENTS:
            t.events.append({"step": {"id": step, "status": status, "label": rec["label"],
                                      "t_ms": now, "detail": dict(rec["detail"])}})
    except Exception as e:  # pragma: no cover
        logger.debug("emit_step failed: %s", e)


def drain_events() -> list[dict]:
    """Return step events emitted since the last drain (SSE: one ``data:`` line each)."""
    t = _current.get()
    if t is None:
        return []
    out = t.events[t.drained:]
    t.drained = len(t.events)
    return out


def record_llm_call(
    model: str,
    task: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    cached: bool = False,
) -> None:
    """Record one LLM call against the active trace (and, at finish, the ledger)."""
    t = _current.get()
    if t is None:
        return
    try:
        if len(t.llm_calls) < _MAX_CALLS:
            t.llm_calls.append({
                "model": model, "task": task, "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "latency_ms": float(latency_ms or 0.0), "cached": bool(cached),
                "ts": time.time(), "t_ms": t.now_ms(),
            })
    except Exception:  # pragma: no cover
        pass


def record_tool_call(tool: str, args: Any = None, *, status: str = "ok", decision: str = "allowed",
                     approver: Optional[str] = None, latency_ms: Optional[float] = None,
                     error: Optional[str] = None, user_id: str = "", tenant_id: str = "") -> None:
    """Write the always-on tool ledger row (hashed args) and attach to the active trace."""
    t = _current.get()
    try:
        from VENUAI import trace_store
        trace_store.record_tool_call(
            tool, args, tenant_id=tenant_id or (t.tenant_id if t else ""),
            user_id=user_id or (t.user_id if t else ""), trace_id=t.trace_id if t else None,
            decision=decision, approver=approver, status=status, latency_ms=latency_ms, error=error)
        if t is not None and len(t.tool_calls) < _MAX_CALLS:
            t.tool_calls.append({"tool": tool, "args_sha256": trace_store.sha256_json(args),
                                 "status": status, "decision": decision, "latency_ms": latency_ms})
    except Exception:  # pragma: no cover
        pass


def finish_trace(summary: dict) -> None:
    """Finish + persist the active trace. ``summary`` may carry: question,
    retrieved, model_path, cache_hit, verification, prompt_version, and the
    transient ``answer`` / ``context_chunks`` (used only by online evals,
    never persisted)."""
    t = _current.get()
    if t is None:
        return
    try:
        from VENUAI import trace_store
        summary = dict(summary or {})
        answer = summary.pop("answer", None)
        chunks = summary.pop("context_chunks", None)
        question = summary.pop("question", None)
        trace = {
            "trace_id": t.trace_id, "tenant_id": t.tenant_id, "user_id": t.user_id, "path": t.path,
            "started_at": t.started_at, "total_ms": t.now_ms(),
            "question_hash": summary.pop("question_hash", None)
            or (trace_store.question_hash(question) if question else None),
            "steps": list(t.steps.values()), "spans": t.spans,
            "llm_calls": t.llm_calls, "tool_calls": t.tool_calls,
            "retrieved": summary.pop("retrieved", []) or [],
            "model_path": summary.pop("model_path", None) or _models_from(t),
            "cache_hit": bool(summary.pop("cache_hit", False)),
            "verification": summary.pop("verification", None) or {},
            "prompt_version": summary.pop("prompt_version", None),
        }
        if summary:
            trace["extra"] = summary
        if question:
            trace["question"] = question
        try:
            from VENUAI import online_evals
            result = online_evals.maybe_evaluate(trace, answer, chunks)
            if result:
                trace["online_eval"] = result
        except Exception as e:  # pragma: no cover
            logger.debug("online eval skipped: %s", e)
        trace_store.save_trace(trace)
        try:
            from VENUAI import otel_genai
            if otel_genai.enabled():
                otel_genai.export_trace(trace)
        except Exception as e:  # pragma: no cover
            logger.debug("otel export skipped: %s", e)
    except Exception as e:  # pragma: no cover
        logger.debug("finish_trace failed: %s", e)
    finally:
        _current.set(None)


def _models_from(t: _Trace) -> list:
    seen: list = []
    for c in t.llm_calls:
        if c["model"] and c["model"] not in seen:
            seen.append(c["model"])
    return seen


def _safe_attrs(attrs: dict) -> dict:
    out = {}
    for k, v in attrs.items():
        out[str(k)] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)[:200]
    return out
