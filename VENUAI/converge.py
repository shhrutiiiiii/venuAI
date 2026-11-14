"""
Convergence stop for iterative reasoning loops (shared with B4's research loop).

has_converged(prev, new): True when two successive answers are semantically the same
(embedding cosine >= threshold), i.e. another iteration will not change anything.
Embeddings come from the app's MiniLM model when loadable; otherwise token-Jaccard,
so the check never fails open into an extra LLM iteration.
"""
from __future__ import annotations

import math
import os
import re
from typing import Callable, List, Optional, Sequence


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    return len(ta & tb) / len(ta | tb) if ta | tb else 1.0


def default_embed(text: str) -> Optional[List[float]]:
    try:
        from VENUAI import rag_server
        return list(rag_server.get_embeddings().embed_query(text[:2000]))
    except Exception:  # noqa: BLE001
        return None


def similarity(prev: str, new: str, embed: Optional[Callable[[str], Optional[List[float]]]] = None) -> float:
    e = embed or default_embed
    try:
        va, vb = e(prev), e(new)
    except Exception:  # noqa: BLE001
        va = vb = None
    if va and vb:
        return cosine(va, vb)
    return _jaccard(prev, new)


def threshold() -> float:
    try:
        return float(os.getenv("CONVERGE_COSINE", "0.97"))
    except ValueError:
        return 0.97


def has_converged(prev: str, new: str, embed=None, thr: Optional[float] = None) -> bool:
    return similarity(prev, new, embed) >= (thr if thr is not None else threshold())
