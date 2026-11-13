"""
Cross-encoder reranker (lazy, thread-safe, optional).

Why a cross-encoder: bi-encoder (Chroma) and BM25 scores are computed per
side; a cross-encoder reads (query, passage) jointly and is far better at
separating near-duplicates such as "Agni-IV range" vs "Agni-V range". It also
produces an absolute relevance score, which lets the pipeline abstain when
nothing in the corpus is actually relevant.

Model: env RERANKER_MODEL, default `cross-encoder/ms-marco-MiniLM-L-6-v2`
(22M params, Apache-2.0, CPU-friendly, ~90 MB). Stronger options (slower /
larger): `BAAI/bge-reranker-base` (278M), `BAAI/bge-reranker-v2-m3` (568M,
multilingual). Scores go through a sigmoid so they are in [0, 1];
RETRIEVAL_MIN_SCORE is calibrated per model (see Documents/AI/retrieval.md).

If the model cannot be loaded (offline, no torch, bad name) a warning is
logged once and `rerank_scores` returns None; callers then fall back to the
hybrid-fusion order. RERANKER_ENABLED=false disables it explicitly.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import List, Optional

logger = logging.getLogger("drdo-rag")

DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_lock = threading.Lock()
_model = None
_model_name: Optional[str] = None
_load_failed = False


def reranker_enabled() -> bool:
    return os.getenv("RERANKER_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def reranker_model_name() -> str:
    return os.getenv("RERANKER_MODEL", "").strip() or DEFAULT_RERANKER_MODEL


def get_reranker():
    """Return the loaded CrossEncoder, or None if disabled / unavailable."""
    global _model, _model_name, _load_failed
    if not reranker_enabled():
        return None
    name = reranker_model_name()
    if _model is not None and _model_name == name:
        return _model
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        if _load_failed and _model_name == name:
            return None
        _model_name = name
        try:
            from sentence_transformers import CrossEncoder
            logger.info(f"[Reranker] loading cross-encoder {name}...")
            _model = CrossEncoder(name, max_length=512)
            _load_failed = False
            logger.info("[Reranker] loaded")
        except Exception as err:  # offline, missing weights, no torch...
            _model = None
            _load_failed = True
            logger.warning(
                f"[Reranker] could not load '{name}' ({err}); falling back to "
                "hybrid-fusion ordering without a cross-encoder."
            )
        return _model


def reset_reranker() -> None:
    """Test hook: forget the cached model / failure flag."""
    global _model, _model_name, _load_failed
    with _lock:
        _model, _model_name, _load_failed = None, None, False


def rerank_scores(query: str, texts: List[str]) -> Optional[List[float]]:
    """Relevance in [0,1] for each text, or None when no reranker is available."""
    if not texts:
        return []
    model = get_reranker()
    if model is None:
        return None
    try:
        import torch
        scores = model.predict(
            [(query, t) for t in texts], batch_size=16,
            activation_fn=torch.nn.Sigmoid(), show_progress_bar=False,
        )
        return [float(s) for s in scores]
    except Exception as err:
        logger.warning(f"[Reranker] scoring failed ({err}); using fusion order")
        return None
