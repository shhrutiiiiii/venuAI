"""Local (CPU, zero-token) claim-level faithfulness scoring (Wave B contract 2.3).

    score_claims(answer, chunks) -> {"score": float|None, "claims": [{"text","supported","score"}],
                                     "model": str, ...}

Backends, tried in order, loaded lazily and cached:
  1. HHEM-2.1-open (``vectara/hallucination_evaluation_model``, via transformers, CPU) - or any model
     named in ``LOCAL_FAITHFULNESS_MODEL`` that exposes ``predict([(premise, hypothesis)])``.
  2. The NLI cross-encoder already used by hallucination_detector (``cross-encoder/nli-deberta-v3-small``).
Models are loaded from the local HF cache only, unless ``LOCAL_FAITHFULNESS_DOWNLOAD=on`` (the server
never downloads hundreds of MB on a user request by surprise).

Honesty rule: when no backend can be loaded, or the answer has no checkable claim, ``score`` is None
(and ``model`` says why / which). Nothing is ever faked or defaulted.

Scoring: each claim is scored against its best-matching chunks (lexical pre-selection keeps the pair
count small); a claim's score is the max support probability over those chunks; ``supported`` means
score >= threshold; the overall ``score`` is the mean claim score (``supported_fraction`` is also
returned). No LLM call, no network (unless download is enabled).

Env: LOCAL_FAITHFULNESS_MODEL, LOCAL_FAITHFULNESS_DOWNLOAD (off), LOCAL_FAITHFULNESS_THRESHOLD,
     LOCAL_FAITHFULNESS_MAX_CLAIMS (12), LOCAL_FAITHFULNESS_CHUNKS_PER_CLAIM (3).
The ``LOCAL_FAITHFULNESS=on`` flag that enables this in the answer path is read by the callers.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("drdo-rag")

HHEM_MODEL = "vectara/hallucination_evaluation_model"
NLI_MODEL = "cross-encoder/nli-deberta-v3-small"

_lock = threading.Lock()
_backend: Any = None
_backend_tried = False


def enabled() -> bool:
    return os.getenv("LOCAL_FAITHFULNESS", "off").strip().lower() in ("1", "true", "on", "yes")


def _download_ok() -> bool:
    return os.getenv("LOCAL_FAITHFULNESS_DOWNLOAD", "off").strip().lower() in ("1", "true", "on", "yes")


class _HHEMBackend:
    default_threshold = 0.5

    def __init__(self, name: str) -> None:
        from transformers import AutoModelForSequenceClassification
        self.name = name
        self._model = AutoModelForSequenceClassification.from_pretrained(
            name, trust_remote_code=True, local_files_only=not _download_ok(),
        )
        self._model.eval()

    def predict_pairs(self, pairs: Sequence[Tuple[str, str]]) -> List[float]:
        import torch
        with torch.no_grad():
            out = self._model.predict(list(pairs))
        return [float(x) for x in out]


class _NLIBackend:
    default_threshold = 0.7

    def __init__(self, name: str = NLI_MODEL) -> None:
        from sentence_transformers import CrossEncoder
        self.name = name
        self._model = CrossEncoder(name, max_length=512, local_files_only=not _download_ok())

    def predict_pairs(self, pairs: Sequence[Tuple[str, str]]) -> List[float]:
        import numpy as np
        logits = np.asarray(self._model.predict(list(pairs), apply_softmax=False), dtype=float)
        logits = logits.reshape(len(pairs), -1)
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
        # cross-encoder/nli-deberta-v3-*: label order is [contradiction, entailment, neutral]
        return [float(p[1]) for p in probs]


def _get_backend() -> Any:
    """Lazy singleton; None when nothing can be loaded (never raises)."""
    global _backend, _backend_tried
    with _lock:
        if _backend_tried:
            return _backend
        _backend_tried = True
        wanted = os.getenv("LOCAL_FAITHFULNESS_MODEL", HHEM_MODEL).strip() or HHEM_MODEL
        candidates = []
        if wanted != NLI_MODEL:
            candidates.append(lambda: _HHEMBackend(wanted))
        candidates.append(lambda: _NLIBackend(NLI_MODEL))
        for make in candidates:
            try:
                _backend = make()
                logger.info(f"[LocalFaithfulness] backend loaded: {_backend.name}")
                return _backend
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[LocalFaithfulness] backend unavailable ({type(e).__name__}: {str(e)[:120]})")
        _backend = None
        return None


def reset_backend() -> None:
    global _backend, _backend_tried
    with _lock:
        _backend, _backend_tried = None, False


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")
_CITE_RE = re.compile(r"\s*\[(?:\d+(?:\s*,\s*\d+)*)\]|\s*\(source[^)]*\)", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def split_claims(answer: str, min_words: int = 5) -> List[str]:
    """Sentence-level claim candidates: markdown/citation markers stripped; questions, headings and
    very short fragments dropped."""
    claims: List[str] = []
    for raw in _SENT_SPLIT.split(answer or ""):
        s = _BULLET_RE.sub("", raw.strip())
        s = _CITE_RE.sub("", s).replace("**", "").replace("__", "").strip()
        if not s or s.startswith("#") or s.endswith("?") or s.startswith("|") or s.startswith("---"):
            continue
        if len(s.split()) < min_words:
            continue
        low = s.lower()
        if low.startswith(("note:", "source:", "sources:", "answer based on", "warning")):
            continue
        claims.append(s)
    return claims


def _tok(s: str) -> set:
    return set(_TOKEN_RE.findall(s.lower()))


def _best_chunks(claim: str, chunks: List[str], n: int) -> List[str]:
    ct = _tok(claim)
    scored = sorted(((len(ct & _tok(c)) / (len(ct) or 1), i) for i, c in enumerate(chunks)), reverse=True)
    return [chunks[i] for _s, i in scored[:n]]


def _threshold(backend: Any) -> float:
    try:
        return float(os.getenv("LOCAL_FAITHFULNESS_THRESHOLD", ""))
    except ValueError:
        return getattr(backend, "default_threshold", 0.5)


def score_claims(answer: str, chunks: List[str]) -> Dict[str, Any]:
    """See module docstring. Never raises; ``score`` None means 'not measured'."""
    result: Dict[str, Any] = {"score": None, "claims": [], "model": "unavailable"}
    backend = _get_backend()
    if backend is None:
        result["reason"] = "no local faithfulness model could be loaded"
        return result
    result["model"] = backend.name
    chunk_texts = [c for c in (chunks or []) if isinstance(c, str) and c.strip()]
    claims = split_claims(answer)[: int(os.getenv("LOCAL_FAITHFULNESS_MAX_CLAIMS", "12"))]
    if not claims:
        result["reason"] = "no checkable claims in the answer"
        return result
    if not chunk_texts:
        # Nothing to check against: every claim is unsupported by (absent) evidence, but we have
        # no basis for a numeric score either.
        result["claims"] = [{"text": c, "supported": False, "score": 0.0} for c in claims]
        result["score"] = 0.0
        result["supported_fraction"] = 0.0
        result["reason"] = "no evidence chunks provided"
        return result
    per = int(os.getenv("LOCAL_FAITHFULNESS_CHUNKS_PER_CLAIM", "3"))
    pairs: List[Tuple[str, str]] = []
    owners: List[int] = []
    for i, claim in enumerate(claims):
        for ch in _best_chunks(claim, chunk_texts, per):
            pairs.append((ch[:1800], claim))
            owners.append(i)
    try:
        probs = backend.predict_pairs(pairs)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[LocalFaithfulness] scoring failed: {type(e).__name__}: {str(e)[:120]}")
        result["reason"] = f"scoring error: {type(e).__name__}"
        return result
    best = [0.0] * len(claims)
    for o, p in zip(owners, probs):
        best[o] = max(best[o], float(p))
    thr = _threshold(backend)
    out = [{"text": c, "supported": s >= thr, "score": round(s, 4)} for c, s in zip(claims, best)]
    result["claims"] = out
    result["score"] = round(sum(best) / len(best), 4)
    result["supported_fraction"] = round(sum(1 for c in out if c["supported"]) / len(out), 4)
    result["threshold"] = thr
    return result
