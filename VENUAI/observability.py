"""
Observability — request-id generation/propagation and structured telemetry
helpers, per spec section 37.

Real scope for this phase: every /query, /query/stream, and /upload call
gets a request_id, logged alongside the existing log lines (not replacing
them) so a single request's path through guardrails → retrieval →
generation → verification can be grepped by one id. Full distributed
tracing (OpenTelemetry spans exported to a collector) is an infrastructure
dependency (a collector/backend to export to) — not fabricated here; see
Documents/AI/observability.md "Not Yet Implemented".

Never logs secrets or raw document/answer content — only shape and outcome,
consistent with VENUAI/ai_gateway/telemetry.py's existing rule.
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("drdo-rag")


class JSONLogFormatter(logging.Formatter):
    """
    Real structured (JSON) log output — Phase 5. Emits one JSON object
    per line (ECS-ish: timestamp/level/logger/message plus any `extra=`
    fields the caller passed to the logging call), so log output is
    machine-parseable by a real log aggregator (CloudWatch, Datadog, an
    ELK/Loki stack, `jq` on a plain file) without a separate parser having
    to guess at this project's plain-text log line shapes.

    Does NOT send logs anywhere — exporting to an actual aggregator is a
    real infrastructure dependency (a configured shipping agent, an
    API key for a hosted log service) this module cannot fabricate; see
    Documents/DevOps/monitoring.md "Not Yet Implemented". This only
    changes the *shape* of what already goes to stdout/stderr, which a
    real log collector can then tail.
    """

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Any caller-supplied extra=... fields (logger.info(msg, extra={"k": v}))
        # ride along as their own JSON keys, not swallowed into the message string.
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)  # only include values that actually serialize
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """
    Call once at process startup (see VENUAI/rag_server.py). Structured
    JSON output is opt-in via LOG_FORMAT=json — plain text remains the
    default, since it's what every developer running this locally is
    already used to reading. A real deployment sets LOG_FORMAT=json so
    its log aggregator gets parseable lines.
    """
    log_format = os.getenv("LOG_FORMAT", "text").lower()
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JSONLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


def configure_error_reporting() -> bool:
    """Optional Sentry init - inert (no import) unless SENTRY_DSN is set. See VENUAI/otel_genai.py."""
    if not os.getenv("SENTRY_DSN", "").strip():
        return False
    from VENUAI import otel_genai
    return otel_genai.init_sentry()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class RequestTrace:
    request_id: str
    route: str
    user_id: Optional[str] = None
    _start_time: float = field(default_factory=time.time)
    _stage_timings: dict = field(default_factory=dict)

    def stage(self, name: str):
        return _StageTimer(self, name)

    def elapsed_ms(self) -> float:
        return (time.time() - self._start_time) * 1000

    def log_summary(self, success: bool, extra: Optional[dict] = None) -> None:
        logger.info(
            f"[trace={self.request_id}] route={self.route} user={self.user_id} "
            f"success={success} total_ms={self.elapsed_ms():.1f} "
            f"stages={self._stage_timings} extra={extra or {}}"
        )


class _StageTimer:
    def __init__(self, trace: RequestTrace, name: str) -> None:
        self._trace = trace
        self._name = name
        self._start = 0.0

    def __enter__(self):
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = (time.time() - self._start) * 1000
        self._trace._stage_timings[self._name] = round(elapsed_ms, 1)
        return False  # never swallow exceptions
