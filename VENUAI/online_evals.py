"""Online evals on a sample of finished traces (Wave B / B3).

``ONLINE_EVAL_SAMPLE`` (0.0-1.0, default 0.0 = off) is the probability that a
finished trace is scored. Scoring is local and LLM-free:

* ``local_faithfulness.score_claims`` (B2) when that module and its model are
  available (``method="local_faithfulness"``), else
* a lexical citation/support check: the fraction of answer sentences whose
  content words are mostly present in some retrieved chunk
  (``method="lexical_support"``). It is a coarse proxy and labelled as such.

If there is no answer/context to score, the result has ``score: None`` - never
a made-up number. A rolling mean below ``ONLINE_EVAL_MIN_SCORE`` (default 0.5,
over >= ``ONLINE_EVAL_MIN_N`` samples) logs a warning and raises the
``online_eval_regression`` flag exposed through ``/analytics/dashboard``.
"""
from __future__ import annotations

import logging
import os
import random
import re
from typing import Any, Optional

logger = logging.getLogger("drdo-rag")

_WORD = re.compile(r"[a-z0-9]{3,}")
_SENT = re.compile(r"(?<=[.!?])\s+")
_STOP = {"the", "and", "for", "that", "with", "this", "from", "are", "was", "were", "has", "have",
         "its", "not", "but", "can", "which", "their", "also", "into", "than", "such"}


def sample_rate() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("ONLINE_EVAL_SAMPLE", "0.0"))))
    except ValueError:
        return 0.0


def lexical_support(answer: str, chunks: list[str]) -> dict:
    ctx = set()
    for c in chunks:
        ctx.update(w for w in _WORD.findall((c or "").lower()) if w not in _STOP)
    sentences = [s.strip() for s in _SENT.split(answer or "") if len(s.strip()) > 20]
    if not sentences or not ctx:
        return {"score": None, "claims": 0}
    supported = 0
    for s in sentences:
        words = [w for w in _WORD.findall(s.lower()) if w not in _STOP]
        if words and sum(1 for w in words if w in ctx) / len(words) >= 0.6:
            supported += 1
    return {"score": round(supported / len(sentences), 3), "claims": len(sentences)}


def score(answer: Optional[str], chunks: Optional[list]) -> dict:
    texts = [c if isinstance(c, str) else getattr(c, "page_content", str(c)) for c in (chunks or [])]
    if not answer or not texts:
        return {"method": None, "score": None, "reason": "no answer/context captured"}
    try:
        from VENUAI.local_faithfulness import score_claims  # B2, optional
        r = score_claims(answer, texts)
        if r.get("score") is not None:
            return {"method": "local_faithfulness", "score": round(float(r["score"]), 3),
                    "claims": len(r.get("claims", [])), "model": r.get("model")}
    except Exception:
        pass
    r = lexical_support(answer, texts)
    return {"method": "lexical_support", **r}


def maybe_evaluate(trace: dict, answer: Optional[str] = None, chunks: Optional[list] = None,
                   _rand=random.random) -> Optional[dict]:
    rate = sample_rate()
    if rate <= 0.0 or _rand() >= rate:
        return None
    try:
        result = score(answer, chunks)
        result["sampled_rate"] = rate
        _check_regression(result)
        return result
    except Exception as e:
        logger.debug("online eval failed: %s", e)
        return None


def _threshold() -> tuple[float, int]:
    try:
        return float(os.getenv("ONLINE_EVAL_MIN_SCORE", "0.5")), int(os.getenv("ONLINE_EVAL_MIN_N", "5"))
    except ValueError:
        return 0.5, 5


def status(limit: int = 50) -> dict[str, Any]:
    """Dashboard block: rolling mean + regression flag over stored online evals."""
    from VENUAI import trace_store
    scores = [e["score"] for e in trace_store.recent_online_evals(limit) if e.get("score") is not None]
    thr, min_n = _threshold()
    mean = round(sum(scores) / len(scores), 3) if scores else None
    return {"enabled": sample_rate() > 0, "sample_rate": sample_rate(), "n": len(scores),
            "mean_score": mean, "threshold": thr,
            "online_eval_regression": bool(mean is not None and len(scores) >= min_n and mean < thr)}


def _check_regression(current: dict) -> None:
    st = status()
    if st["online_eval_regression"]:
        logger.warning("[online_evals] REGRESSION: rolling mean %.3f < %.2f over %d samples",
                       st["mean_score"], st["threshold"], st["n"])
