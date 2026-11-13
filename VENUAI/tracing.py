"""Tracing seam (FROZEN signatures; Wave B contract 2.1).

Owner of the real implementation: stream B3 (replaces bodies, NOT signatures).
Consumers: B1 (pipeline + gateway), B2, B4.

This is a NO-OP PLACEHOLDER: every function is safe to call and does nothing.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional


def start_trace(trace_id: str, tenant_id: str, user_id: str, path: str) -> None:
    """Begin a trace for one request. No-op placeholder."""
    return None


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """Context manager timing a named span. No-op placeholder."""
    yield None


def emit_step(step: str, status: str, label: str = "", **detail: Any) -> None:
    """Emit a typed pipeline step event (status: start|end|error). No-op placeholder."""
    return None


def current_trace_id() -> Optional[str]:
    """Return the active trace id, or None. Placeholder always returns None."""
    return None


def record_llm_call(
    model: str,
    task: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    cached: bool = False,
) -> None:
    """Record one LLM call against the active trace. No-op placeholder."""
    return None


def finish_trace(summary: dict) -> None:
    """Finish and persist the active trace. No-op placeholder."""
    return None
