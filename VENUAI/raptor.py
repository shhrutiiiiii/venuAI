"""RAPTOR-style hierarchical summaries (Wave B contract 2.3). Default OFF (RAPTOR=off): costs LLM tokens.

    build_summaries(doc_id, tenant_id, chunks) -> int          # number of summary nodes stored
    retrieve_summaries(query, tenant_id, allowed_doc_ids, k=3) -> list[Document]
    delete_summaries(doc_id) -> None                           # call on document delete

Level 1: KMeans over chunk embeddings, each cluster summarised by the fast model (low effort).
Level 2: the level-1 summaries are summarised once more (only if >=3 summaries).
Budget: at most RAPTOR_MAX_CALLS summarisation calls per document (default 6). Summaries live in a
separate Chroma collection ``summaries`` tagged tenant_id / doc_id / level; they are retrieved
semantically and returned as Documents with ``retriever="raptor"`` and ``level``. Summaries are
LLM-written paraphrases: callers should treat them as retrieval aids and still cite original chunks.
All failures return 0 / [] (optional feature).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")
COLLECTION = "summaries"
_col: Any = None


def enabled() -> bool:
    return os.getenv("RAPTOR", "off").strip().lower() in ("1", "true", "on", "yes")


def max_calls() -> int:
    try:
        return max(0, int(os.getenv("RAPTOR_MAX_CALLS", "6")))
    except ValueError:
        return 6


def _collection():
    global _col
    if _col is None:
        import chromadb
        from pathlib import Path
        path = os.getenv("RAPTOR_CHROMA_DIR") or str(Path(__file__).resolve().parent / "chroma_db")
        _col = chromadb.PersistentClient(path=path).get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine"})
    return _col


def _embed(texts: List[str]) -> List[List[float]]:
    from VENUAI.embeddings_config import get_embeddings
    return get_embeddings().embed_documents(texts)


def _embed_query(text: str) -> List[float]:
    from VENUAI.embeddings_config import get_embeddings
    return get_embeddings().embed_query(text)


def _summarize(texts: List[str]) -> str:
    from VENUAI.ai_gateway.gateway import get_gateway
    from VENUAI.ai_gateway.model_router import TaskType
    body = "\n\n".join(t[:1200] for t in texts)[:6000]
    res = get_gateway().complete(
        task_type=TaskType.FAST_CLASSIFICATION, max_tokens=400, temperature=0.1,
        messages=[{"role": "user", "content":
                   "Summarise the passages below in 3-5 factual sentences. Keep names, numbers and units exactly. "
                   "Do not add anything that is not in the passages.\n\n" + body}],
    )
    return (res.content or "").strip()


def _cluster(vectors: List[List[float]], n_clusters: int) -> List[int]:
    import numpy as np
    from sklearn.cluster import KMeans
    n_clusters = max(1, min(n_clusters, len(vectors)))
    if n_clusters == 1:
        return [0] * len(vectors)
    return [int(x) for x in KMeans(n_clusters=n_clusters, n_init=3, random_state=0).fit_predict(np.asarray(vectors))]


def _text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        return c.get("text") or ""
    return getattr(c, "page_content", "") or ""


def build_summaries(doc_id: str, tenant_id: str, chunks: list) -> int:
    texts = [t for t in (_text(c) for c in chunks or []) if t.strip()]
    budget = max_calls()
    if len(texts) < 4 or budget == 0:
        return 0
    try:
        delete_summaries(doc_id)  # rebuild is idempotent
        stored = 0
        l1_clusters = min(max(2, len(texts) // 5), max(1, budget - 1))
        labels = _cluster(_embed(texts), l1_clusters)
        summaries: List[str] = []
        for cl in sorted(set(labels)):
            if budget <= 0:
                break
            members = [t for t, lb in zip(texts, labels) if lb == cl]
            s = _summarize(members)
            budget -= 1
            if s:
                summaries.append(s)
        if not summaries:
            return 0
        _store(doc_id, tenant_id, summaries, level=1)
        stored += len(summaries)
        if len(summaries) >= 3 and budget > 0:
            top = _summarize(summaries)
            if top:
                _store(doc_id, tenant_id, [top], level=2)
                stored += 1
        return stored
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAPTOR] build failed for {doc_id}: {type(e).__name__}: {e}")
        return 0


def _store(doc_id: str, tenant_id: str, summaries: List[str], level: int) -> None:
    _collection().upsert(
        ids=[uuid.uuid4().hex for _ in summaries], documents=summaries, embeddings=_embed(summaries),
        metadatas=[{"doc_id": doc_id, "tenant_id": tenant_id, "level": level} for _ in summaries],
    )


def delete_summaries(doc_id: str) -> None:
    try:
        _collection().delete(where={"doc_id": doc_id})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAPTOR] delete failed for {doc_id}: {e}")


def retrieve_summaries(query: str, tenant_id: str, allowed_doc_ids: Optional[List[str]], k: int = 3) -> List[Document]:
    if allowed_doc_ids is not None and not allowed_doc_ids:
        return []
    try:
        where: dict = {"tenant_id": tenant_id}
        if allowed_doc_ids is not None:
            where = {"$and": [where, {"doc_id": {"$in": list(allowed_doc_ids)}}]}
        res = _collection().query(query_embeddings=[_embed_query(query)], n_results=k, where=where)
        out = []
        for text, meta in zip((res.get("documents") or [[]])[0], (res.get("metadatas") or [[]])[0]):
            md = dict(meta or {})
            md.update(retriever="raptor", source=f"summary of {md.get('doc_id')}",
                      filename=f"Summary (level {md.get('level')})")
            out.append(Document(page_content=text, metadata=md))
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[RAPTOR] retrieval failed: {type(e).__name__}: {e}")
        return []
