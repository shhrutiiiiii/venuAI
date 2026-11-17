"""
Follow-up question suggestions (<= 3).

Folded into the generation call: the prompt asks the model to end with
<followups>["...","...","..."]</followups> after the <citations> block, so the normal
path costs no extra call. split_followups() strips it wherever it appears;
FollowupStreamFilter hides it from streamed tokens. Only when the model omitted the
block (and FOLLOWUPS=on) does fallback_followups() make ONE fast-model low-effort call,
and never for abstained / blocked / unverified-empty answers.
"""
from __future__ import annotations

import os
import re
from typing import List, Tuple

from VENUAI.structured import complete_json, parse_json

FOLLOWUP_RULE = (
    "7. After the </citations> block add ONE final line: <followups>[\"q1\",\"q2\",\"q3\"]</followups> "
    "- a JSON array of up to 3 short, natural follow-up questions the user might ask next, answerable "
    "from the documents. Omit the line if the answer says the information is not available."
)

_BLOCK = re.compile(r"<followups>(.*?)</followups>", re.S | re.I)
_OPEN = "<followups"


def enabled() -> bool:
    return os.getenv("FOLLOWUPS", "on").strip().lower() not in ("off", "0", "false")


def _clean(items) -> List[str]:
    out: List[str] = []
    for it in items or []:
        if isinstance(it, str):
            t = it.strip().strip('"').strip()
            if 5 <= len(t) <= 200 and t not in out:
                out.append(t)
    return out[:3]


def split_followups(raw: str) -> Tuple[str, List[str]]:
    """(raw without the followups block, parsed list)."""
    m = _BLOCK.search(raw or "")
    if not m:
        # a truncated/unclosed block must not leak into the answer
        i = (raw or "").lower().find(_OPEN)
        return ((raw[:i].rstrip() if i >= 0 else raw), [])
    items = parse_json(m.group(1))
    if items is None:
        items = [ln.lstrip("-*0123456789. ").strip() for ln in m.group(1).splitlines() if ln.strip()]
    return (raw[:m.start()] + raw[m.end():]).rstrip(), _clean(items if isinstance(items, list) else [])


class FollowupStreamFilter:
    """Hides <followups>...</followups> from the visible token stream."""

    def __init__(self) -> None:
        self._buf = ""
        self._suppress = False

    def feed(self, delta: str) -> str:
        if self._suppress:
            return ""
        self._buf += delta
        i = self._buf.lower().find(_OPEN)
        if i >= 0:
            out, self._buf, self._suppress = self._buf[:i], "", True
            return out
        keep = 0
        for k in range(1, len(_OPEN)):
            if self._buf.lower().endswith(_OPEN[:k]):
                keep = k
        out, self._buf = self._buf[:len(self._buf) - keep], self._buf[len(self._buf) - keep:]
        return out

    def flush(self) -> str:
        if self._suppress:
            return ""
        out, self._buf = self._buf, ""
        return out


_SCHEMA = {"type": "object", "properties": {"followups": {"type": "array", "items": {"type": "string"},
                                                          "maxItems": 3}}, "required": ["followups"]}


def fallback_followups(question: str, answer: str) -> List[str]:
    """One cheap structured call; [] on any failure."""
    from VENUAI.ai_gateway.model_router import TaskType
    from VENUAI.model_cascade import fast_model_id
    prompt = (f"Question: {question[:400]}\n\nAnswer: {answer[:1200]}\n\n"
              "Suggest up to 3 short follow-up questions the user could ask next about the same documents. "
              'Reply as JSON: {"followups": ["...", "..."]}')
    data = complete_json(TaskType.FAST_CLASSIFICATION, [{"role": "user", "content": prompt}],
                         "followups", _SCHEMA, max_tokens=200, prefer_model=fast_model_id())
    return _clean(data.get("followups")) if isinstance(data, dict) else []
