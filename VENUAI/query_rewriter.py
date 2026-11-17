"""
History-aware query rewriting for retrieval (FAST model via the AI gateway).

A follow-up like "what about its range?" is useless as a search query. Before
retrieval we rewrite it into a standalone query using the recent conversation,
and optionally 1-2 alternative phrasings that are fused with RRF. The original
question is still what the generator answers; only retrieval uses these.

The LLM call is skipped when there is no history, or when the question already
looks standalone, so single-turn questions cost nothing extra.
Env: QUERY_REWRITE_ENABLED (true), QUERY_REWRITE_ALTERNATIVES (1, max 2).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger("drdo-rag")

_ANAPHORA = {
    "it", "its", "they", "them", "their", "this", "that", "those", "these", "he", "she", "his",
    "her", "former", "latter", "same", "also", "another", "such", "there", "then",
}
_FOLLOWUP_OPENERS = ("and ", "what about", "how about", "why", "so ", "but ", "ok ", "also ", "then ")
MAX_HISTORY_TURNS = 6
MAX_TURN_CHARS = 400


@dataclass
class RewriteResult:
    retrieval_query: str
    alternatives: List[str] = field(default_factory=list)
    used_llm: bool = False


def rewrite_enabled() -> bool:
    return os.getenv("QUERY_REWRITE_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _alt_count() -> int:
    try:
        return max(0, min(2, int(os.getenv("QUERY_REWRITE_ALTERNATIVES", "1"))))
    except ValueError:
        return 1


def looks_standalone(question: str) -> bool:
    q = question.strip().lower()
    words = re.findall(r"[\w-]+", q)
    if len(words) < 6:
        return False
    if q.startswith(_FOLLOWUP_OPENERS):
        return False
    return not any(w in _ANAPHORA for w in words)


def _turn_parts(turn: dict) -> tuple:
    role = str(turn.get("role") or turn.get("sender") or "user").lower()
    role = "Assistant" if role in ("ai", "assistant", "bot", "model") else "User"
    text = str(turn.get("content") or turn.get("text") or "")
    return role, text[:MAX_TURN_CHARS]


def format_history(history: Optional[List[dict]]) -> str:
    lines = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        if isinstance(turn, dict):
            role, text = _turn_parts(turn)
            if text.strip():
                lines.append(f"{role}: {text.strip()}")
    return "\n".join(lines)


def fast_complete(prompt: str, max_tokens: int = 200) -> str:
    """FAST-model completion through the AI gateway (gateway itself untouched)."""
    from groq import Groq
    from VENUAI.ai_gateway.gateway import get_gateway
    from VENUAI.ai_gateway.model_router import TaskType
    from VENUAI.rag_server import GROQ_API_KEY

    gw = get_gateway(Groq(api_key=GROQ_API_KEY))
    res = gw.complete(
        task_type=TaskType.FAST_CLASSIFICATION,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens, temperature=0.0,
    )
    return res.content


def _parse(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def rewrite_query(
    question: str, history: Optional[List[dict]] = None,
    complete: Optional[Callable[[str, int], str]] = None,
) -> RewriteResult:
    """Never raises: any failure yields the original question."""
    fallback = RewriteResult(retrieval_query=question)
    hist = format_history(history)
    if not rewrite_enabled() or not hist or looks_standalone(question):
        return fallback
    n_alt = _alt_count()
    prompt = (
        "Rewrite the user's latest question into a standalone search query for a document "
        "knowledge base, resolving pronouns and references using the conversation. Keep exact "
        "names, model numbers and identifiers verbatim. Do not answer the question.\n"
        f"Also give {n_alt} alternative phrasing(s) using different keywords.\n"
        'Reply with JSON only: {"standalone": "...", "alternatives": ["..."]}\n\n'
        f"Conversation:\n{hist}\n\nLatest question: {question}"
    )
    try:
        raw = (complete or fast_complete)(prompt, 200)
    except Exception as err:
        logger.warning(f"[QueryRewrite] skipped ({err})")
        return fallback
    data = _parse(raw)
    if not data:
        return fallback
    standalone = str(data.get("standalone") or "").strip()
    if not standalone or len(standalone) > 400:
        return fallback
    alts = [str(a).strip() for a in (data.get("alternatives") or []) if isinstance(a, str)]
    alts = [a for a in alts if a and len(a) <= 400 and a.lower() != standalone.lower()][:n_alt]
    return RewriteResult(retrieval_query=standalone, alternatives=alts, used_llm=True)


def expand_query_llm(
    question: str, round_num: int, previous_query: str = "",
    complete: Optional[Callable[[str, int], str]] = None,
) -> str:
    """Re-retrieval round: a differently-worded query. Returns the original on failure."""
    if not rewrite_enabled():
        return question
    prompt = (
        "The search for the question below returned weak evidence. Write ONE different search "
        "query (different keywords, synonyms, more specific technical terms) that could find "
        "the missing passages. Keep exact names/identifiers. Reply with the query only.\n"
        f"Question: {question}\nPrevious query: {previous_query or question}\nRound: {round_num}"
    )
    try:
        out = (complete or fast_complete)(prompt, 80).strip().strip('"')
    except Exception as err:
        logger.warning(f"[QueryRewrite] expansion skipped ({err})")
        return question
    out = out.splitlines()[0].strip() if out else ""
    return out if 0 < len(out) <= 400 else question
