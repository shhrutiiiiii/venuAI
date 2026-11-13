"""PII redaction seam (FROZEN signatures; Wave B contract 2.3).

Owner of the real implementation: stream B2. Consumers: B3 (trace store), B1 (logs).

This is a NO-OP PLACEHOLDER: identity functions until B2 replaces the bodies.
"""
from __future__ import annotations

from typing import Any


def redact(text: str) -> str:
    """Return text with PII masked. Placeholder: identity."""
    return text


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings in a JSON-like object. Placeholder: identity."""
    return obj
