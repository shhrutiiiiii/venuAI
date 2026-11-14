"""
Central bounds for request-model fields (LLM10 unbounded consumption / input validation).
Violations surface as a generic 422 (see `sanitized_validation_handler`) which never echoes
the offending input back to the caller.
"""

import re
from typing import Annotated, List, Optional

from pydantic import AfterValidator, Field, StringConstraints

QUESTION_MAX_CHARS = 4000
HISTORY_MAX_ITEMS = 20
HISTORY_ITEM_MAX_CHARS = 4000
HISTORY_TOTAL_MAX_CHARS = 40_000
TOP_K_MIN, TOP_K_MAX = 1, 20
GRAPH_TEXT_MAX_CHARS = 50_000
SESSION_ID_RE = r"^[A-Za-z0-9_-]{1,64}$"
_ALLOWED_HISTORY_ROLES = {"user", "assistant"}
_UUID_LIKE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

Question = Annotated[str, StringConstraints(max_length=QUESTION_MAX_CHARS)]
SessionId = Annotated[str, StringConstraints(pattern=SESSION_ID_RE)]
TopK = Annotated[int, Field(ge=TOP_K_MIN, le=TOP_K_MAX)]
ShortLabel = Annotated[str, StringConstraints(max_length=255)]
GraphText = Annotated[str, StringConstraints(max_length=GRAPH_TEXT_MAX_CHARS)]
UserIdField = Annotated[str, StringConstraints(max_length=128)]


def _check_history(items: Optional[List[dict]]) -> Optional[List[dict]]:
    if items is None:
        return None
    if len(items) > HISTORY_MAX_ITEMS:
        raise ValueError("history too long")
    total = 0
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("invalid history item")
        role, content = it.get("role"), it.get("content")
        if role not in _ALLOWED_HISTORY_ROLES or not isinstance(content, str):
            raise ValueError("invalid history item")
        if len(content) > HISTORY_ITEM_MAX_CHARS:
            raise ValueError("history item too long")
        total += len(content)
        if total > HISTORY_TOTAL_MAX_CHARS:
            raise ValueError("history too large")
        out.append({"role": role, "content": content})
    return out


History = Annotated[Optional[List[dict]], AfterValidator(_check_history)]


def validate_doc_id(value: str) -> str:
    """doc/dataset ids are server-generated UUIDs; reject anything else (traversal, injection)."""
    if not isinstance(value, str) or not _UUID_LIKE_RE.match(value):
        raise ValueError("invalid id")
    return value


async def sanitized_validation_handler(request, exc):
    """422 handler that reports only field locations, never the submitted values."""
    from fastapi.responses import JSONResponse

    errors = [{"loc": [str(p) for p in e.get("loc", [])], "msg": "invalid value"} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})
