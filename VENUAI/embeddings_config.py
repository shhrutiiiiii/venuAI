"""Embedding model selection (ADR-B15). MiniLM stays the default; a better embedder is opt-in.

    get_embeddings()            -> cached langchain HuggingFaceEmbeddings (CPU, normalised)
    get_embedding_model_name()  -> EMBEDDING_MODEL env or the default

B1 swaps rag_server's embedding construction to call get_embeddings() (one line). Changing the
model REQUIRES re-indexing: vectors from different models are incompatible. Nothing here ever
re-indexes automatically; use ``python scripts/reindex.py`` (dry-run by default, writes a NEW
Chroma directory, never touches the live one).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger("drdo-rag")

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
# Opt-in upgrades (name -> notes). Sizes are approximate; all CPU-feasible.
RECOMMENDED: Dict[str, str] = {
    "BAAI/bge-small-en-v1.5": "33M params, 384-d, drop-in size, better retrieval than MiniLM",
    "BAAI/bge-base-en-v1.5": "110M params, 768-d, stronger, ~4x slower ingest on CPU",
    "intfloat/e5-base-v2": "110M params, 768-d (expects 'query: '/'passage: ' prefixes - not applied here)",
}
_embeddings: Any = None
_embeddings_name: str = ""


def get_embedding_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", "").strip() or DEFAULT_EMBEDDING_MODEL


def get_embeddings():
    global _embeddings, _embeddings_name
    name = get_embedding_model_name()
    if _embeddings is None or _embeddings_name != name:
        logger.info(f"Loading embedding model ({name})...")
        from langchain_community.embeddings import HuggingFaceEmbeddings
        _embeddings = HuggingFaceEmbeddings(
            model_name=name, model_kwargs={"device": "cpu"}, encode_kwargs={"normalize_embeddings": True},
        )
        _embeddings_name = name
    return _embeddings


def reset() -> None:
    global _embeddings, _embeddings_name
    _embeddings, _embeddings_name = None, ""
