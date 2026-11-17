"""
Structured-output helper for gateway callers (router, grader, follow-ups, ...).

complete_json() asks the gateway for a JSON-schema constrained reply
(response_format=json_schema). Providers/models that reject response_format are
retried plain inside GroqProvider; either way the text goes through parse_json()
which repairs the usual damage (code fences, prose around the object, trailing commas).
Returns None when nothing parseable came back - callers must treat that as "unknown",
never invent a value.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

logger = logging.getLogger("drdo-rag")

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.M)


def parse_json(text: Optional[str]) -> Any:
    """Best-effort JSON extraction from model text. None if impossible."""
    if not text:
        return None
    t = _FENCE.sub("", text.strip()).strip()
    for cand in (t, _outermost(t, "{", "}"), _outermost(t, "[", "]")):
        if not cand:
            continue
        for attempt in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                return json.loads(attempt)
            except ValueError:
                continue
    return None


def _outermost(t: str, o: str, c: str) -> Optional[str]:
    i, j = t.find(o), t.rfind(c)
    return t[i:j + 1] if 0 <= i < j else None


def schema_format(name: str, schema: dict) -> dict:
    return {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}


def complete_json(task, messages: List[dict], name: str, schema: dict, *, max_tokens: int = 300,
                  prefer_model: Optional[str] = None, effort: Optional[str] = "low",
                  temperature: float = 0.0) -> Optional[Any]:
    """One gateway call returning the parsed JSON value, or None on any failure."""
    try:
        from VENUAI.ai_gateway.gateway import get_gateway
        res = get_gateway().complete(
            task, messages, max_tokens=max_tokens, temperature=temperature,
            prefer_model=prefer_model, reasoning_effort=effort,
            response_format=schema_format(name, schema),
        )
        return parse_json(res.content)
    except Exception as e:  # noqa: BLE001 - structured helpers must never break a request
        logger.warning(f"[structured] {name} call failed: {type(e).__name__}: {e}")
        return None
