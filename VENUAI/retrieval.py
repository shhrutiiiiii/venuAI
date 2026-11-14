"""
Shared retrieval pipeline (used by rag_server.retrieve_node, agentic_rag and
agent_tools so all three behave identically).

    queries (standalone rewrite + alternatives)
      -> dense (Chroma) + BM25 (cached in-process index) per query
      -> Reciprocal Rank Fusion  -> candidate pool (RETRIEVAL_CANDIDATES x top_k)
      -> cross-encoder rerank (VENUAI/reranker.py)  [fallback: fusion order]
      -> relevance floor (RETRIEVAL_MIN_SCORE / RETRIEVAL_MIN_SIMILARITY)
      -> top_k, or insufficient_context=True when nothing survives.

Authorization is applied inside every stage: Chroma gets a doc_id filter and
the BM25 search is masked by the caller's allowed doc_ids. An empty allowed
list retrieves nothing. See Documents/AI/retrieval.md.

The BM25 index is built once over the whole corpus and cached; it is
invalidated by `bump_corpus_version()` (called on upload/delete) and, as a
safety net for out-of-process writers, when the Chroma chunk count changes.
Note: the previous implementation used langchain's BM25Retriever, which needs
the `rank_bm25` package that was never in requirements.txt, so the BM25 half
silently fell back to dense-only. This module has no such dependency.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")

RRF_K = 60
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it of on or that the to was were what which "
    "who whom with how does do did its this these those their there than then into about".split()
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-_./][A-Za-z0-9]+)*")


# ── configuration ─────────────────────────────────────────────────────────────

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def candidate_multiplier() -> int:
    return max(1, _env_int("RETRIEVAL_CANDIDATES", 4))


def min_rerank_score() -> float:
    # Calibrated for cross-encoder/ms-marco-MiniLM-L-6-v2 (sigmoid output); see
    # Documents/AI/retrieval.md and VENUAI/evals/retrieval_bench.py.
    return _env_float("RETRIEVAL_MIN_SCORE", 0.02)


def min_similarity() -> float:
    # Used only when no reranker is available: cosine similarity of normalised
    # all-MiniLM-L6-v2 embeddings. Off-topic text sits around 0.0-0.2.
    return _env_float("RETRIEVAL_MIN_SIMILARITY", 0.2)


# ── tokenisation + BM25 ───────────────────────────────────────────────────────

def tokenize(text: str) -> List[str]:
    """Lowercase tokens; hyphenated ids ("Agni-IV") yield the compound AND parts."""
    out: List[str] = []
    for m in _TOKEN_RE.findall(text.lower()):
        parts = re.split(r"[-_./]", m)
        if len(parts) > 1:
            out.append("".join(parts))
            out.extend(p for p in parts if p)
        else:
            out.append(m)
    return [t for t in out if t not in _STOPWORDS]


class BM25Index:
    """Okapi BM25 over the full corpus; search is masked by an allowed doc set."""

    def __init__(self, texts: Sequence[str], metadatas: Sequence[dict], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.texts = list(texts)
        self.metadatas = [m or {} for m in metadatas]
        self.postings: Dict[str, List[Tuple[int, int]]] = {}
        self.doc_len: List[int] = []
        for i, t in enumerate(self.texts):
            toks = tokenize(self._index_text(i))
            self.doc_len.append(len(toks))
            tf: Dict[str, int] = {}
            for tok in toks:
                tf[tok] = tf.get(tok, 0) + 1
            for tok, c in tf.items():
                self.postings.setdefault(tok, []).append((i, c))
        self.n = len(self.texts)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 1.0

    def _index_text(self, i: int) -> str:
        # Contextual-retrieval prefix is indexed but never displayed.
        prefix = self.metadatas[i].get("context_prefix") or ""
        return f"{prefix}\n{self.texts[i]}" if prefix else self.texts[i]

    def search(self, query: str, k: int, allowed: Optional[set]) -> List[Tuple[int, float]]:
        scores: Dict[int, float] = {}
        for tok in set(tokenize(query)):
            plist = self.postings.get(tok)
            if not plist:
                continue
            df = len(plist)
            idf = math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))
            for i, tf in plist:
                if allowed is not None and self.metadatas[i].get("doc_id") not in allowed:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                scores[i] = scores.get(i, 0.0) + idf * tf * (self.k1 + 1) / denom
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]

    def document(self, i: int) -> Document:
        # A fresh Document each time: callers attach scores to metadata.
        return Document(page_content=self.texts[i], metadata=dict(self.metadatas[i]))


_cache_lock = threading.Lock()
_corpus_version = 0
_cache: Dict[str, Any] = {"vs": None, "key": None, "index": None}


def bump_corpus_version(tenant_id: Optional[str] = None) -> None:
    """Call after any upload / delete so the next query rebuilds the BM25 index and the
    answer cache is invalidated (tenant_id scopes the invalidation; None = all tenants)."""
    global _corpus_version
    with _cache_lock:
        _corpus_version += 1
        _cache.update(vs=None, key=None, index=None)
    try:
        from VENUAI import semantic_cache
        semantic_cache.invalidate(tenant_id)
    except Exception as err:  # noqa: BLE001 - cache invalidation must never break an upload
        logger.warning(f"[Retrieval] answer-cache invalidation failed: {err}")


def get_corpus_version() -> int:
    return _corpus_version


def get_vectorstore():
    """Seam for other modules (agents, research, health): the app's shared Chroma store.
    Imported lazily so retrieval.py never imports rag_server at module load."""
    from VENUAI import rag_server
    return rag_server.get_vector_store()


_MULTIHOP_CUES = re.compile(r"\b(both|and also|as well as|relationship|related to|connected|compare|"
                            r"which .* (of|that)|who .* (of|that)|between)\b", re.I)


def _flag(name: str) -> bool:
    return os.getenv(name, "off").strip().lower() in ("on", "1", "true", "force")


def _optional_sources(question: str, tenant_id: Optional[str], allowed_doc_ids: Optional[List[str]],
                      force_kg: bool) -> List[List[Document]]:
    """KG-PPR (B2 kg_retrieval) and RAPTOR summaries (B2 raptor) as extra RRF sources.
    Guarded: absent module / flag off / any error -> no extra source."""
    out: List[List[Document]] = []
    if (_flag("KG_RETRIEVAL") and (force_kg or os.getenv("KG_RETRIEVAL", "").lower() == "force"
                                   or _MULTIHOP_CUES.search(question))):
        try:
            from VENUAI import kg_retrieval
            docs = kg_retrieval.retrieve_graph(question, tenant_id or "default", allowed_doc_ids, k=8)
            if docs:
                for d in docs:
                    d.metadata = {**(d.metadata or {}), "retriever": d.metadata.get("retriever", "kg_ppr")}
                out.append(list(docs))
        except ImportError:
            logger.debug("kg_retrieval not present")
        except Exception as err:  # noqa: BLE001
            logger.warning(f"[Retrieval] KG retrieval skipped: {err}")
    if _flag("RAPTOR"):
        try:
            from VENUAI import raptor
            docs = raptor.retrieve_summaries(question, tenant_id or "default", allowed_doc_ids, k=3)
            if docs:
                out.append(list(docs))
        except ImportError:
            logger.debug("raptor not present")
        except Exception as err:  # noqa: BLE001
            logger.warning(f"[Retrieval] RAPTOR retrieval skipped: {err}")
    return out


def _expand_parents(vs, docs: List[Document], max_chars: int = 1800) -> None:
    """Parent-section expansion: widen each kept chunk with its same-page neighbours."""
    for d in docs:
        m = d.metadata or {}
        if m.get("doc_id") is None or m.get("page") is None or m.get("region_id") is not None:
            continue
        try:
            got = vs.get(where={"$and": [{"doc_id": m["doc_id"]}, {"page": m["page"]}]})
            texts = [t for t in (got or {}).get("documents") or [] if t and t != d.page_content]
        except Exception:  # noqa: BLE001
            continue
        extra = ""
        for t in texts:
            if len(d.page_content) + len(extra) + len(t) > max_chars:
                break
            extra += "\n" + t
        if extra:
            d.page_content = d.page_content + extra
            d.metadata = {**m, "expanded_parent": True}


def _collection_count(vs) -> Optional[int]:
    try:
        return int(vs._collection.count())
    except Exception:
        return None


def get_bm25_index(vs) -> Optional[BM25Index]:
    """Cached corpus-wide index; None if the corpus is empty or unreadable."""
    with _cache_lock:
        key = (_corpus_version, _collection_count(vs))
        if _cache["vs"] is vs and _cache["key"] == key:
            return _cache["index"]
        try:
            data = vs.get()
        except Exception as err:
            logger.warning(f"[Retrieval] BM25 corpus load failed: {err}")
            return None
        texts = (data or {}).get("documents") or []
        index = BM25Index(texts, (data or {}).get("metadatas") or [{}] * len(texts)) if texts else None
        _cache.update(vs=vs, key=key, index=index)
        logger.info(f"[Retrieval] BM25 index built over {len(texts)} chunks (cached).")
        return index


# ── dense search ──────────────────────────────────────────────────────────────

def _similarity_from_distance(vs, dist: float) -> float:
    try:
        space = (vs._collection.metadata or {}).get("hnsw:space", "l2")
    except Exception:
        space = "l2"
    # Embeddings are normalised, so cosine = 1 - squared_l2/2 (Chroma l2 is squared).
    return 1.0 - dist / 2.0 if space == "l2" else 1.0 - dist


def _dense(vs, query: str, k: int, allowed: Optional[List[str]]) -> List[Tuple[Document, Optional[float]]]:
    flt = None
    if allowed is not None:
        flt = {"doc_id": {"$in": allowed or ["__none__"]}}
    if hasattr(vs, "similarity_search_with_score"):
        try:
            res = vs.similarity_search_with_score(query, k=k, filter=flt) if flt else \
                vs.similarity_search_with_score(query, k=k)
            return [(d, _similarity_from_distance(vs, s)) for d, s in res]
        except Exception as err:
            logger.warning(f"[Retrieval] dense search with scores failed ({err}); retrying plain")
    kwargs: Dict[str, Any] = {"k": k}
    if flt:
        kwargs["filter"] = flt
    docs = vs.as_retriever(search_type="similarity", search_kwargs=kwargs).invoke(query)
    return [(d, None) for d in docs]


# ── pipeline ──────────────────────────────────────────────────────────────────

def _key(doc: Document) -> str:
    m = doc.metadata or {}
    h = hashlib.md5(doc.page_content.encode("utf-8", "ignore")).hexdigest()
    return f"{m.get('doc_id')}|{m.get('page')}|{h}"


def rerank_text(doc: Document) -> str:
    prefix = (doc.metadata or {}).get("context_prefix") or ""
    return f"{prefix}\n{doc.page_content}" if prefix else doc.page_content


@dataclass
class RetrievalResult:
    documents: List[Document] = field(default_factory=list)
    scores: List[dict] = field(default_factory=list)
    insufficient_context: bool = False
    signal: str = "none"            # "rerank" | "similarity" | "fusion"
    queries: List[str] = field(default_factory=list)
    retrieval_query: str = ""


def retrieve_documents(
    vs, question: str, top_k: int, allowed_doc_ids: Optional[List[str]],
    history: Optional[List[dict]] = None, rewrite: bool = True,
    extra_queries: Optional[List[str]] = None, include_regions: bool = True,
    tenant_id: Optional[str] = None, expand_parent: bool = False, force_kg: bool = False,
) -> RetrievalResult:
    from VENUAI import reranker
    from VENUAI.query_rewriter import rewrite_query

    if allowed_doc_ids is not None and not allowed_doc_ids:
        return RetrievalResult(retrieval_query=question, queries=[question])  # nothing visible

    retrieval_query, queries = question, [question]
    if rewrite:
        rw = rewrite_query(question, history)
        retrieval_query = rw.retrieval_query
        queries = [rw.retrieval_query] + rw.alternatives
    for q in extra_queries or []:
        if q and q not in queries:
            queries.append(q)

    pool = top_k * candidate_multiplier()
    allowed_set = set(allowed_doc_ids) if allowed_doc_ids is not None else None
    index = get_bm25_index(vs)

    fused: Dict[str, float] = {}
    docs_by_key: Dict[str, Document] = {}
    sim_by_key: Dict[str, float] = {}
    bm25_hit: set = set()
    for q in queries:
        try:
            for rank, (d, sim) in enumerate(_dense(vs, q, pool, allowed_doc_ids)):
                k_ = _key(d)
                docs_by_key.setdefault(k_, d)
                fused[k_] = fused.get(k_, 0.0) + 1.0 / (RRF_K + rank + 1)
                if sim is not None:
                    sim_by_key[k_] = max(sim, sim_by_key.get(k_, -1.0))
        except Exception as err:
            logger.warning(f"[Retrieval] dense search failed: {err}")
        if index is not None:
            for rank, (i, _score) in enumerate(index.search(q, pool, allowed_set)):
                d = index.document(i)
                k_ = _key(d)
                docs_by_key.setdefault(k_, d)
                fused[k_] = fused.get(k_, 0.0) + 1.0 / (RRF_K + rank + 1)
                bm25_hit.add(k_)

    for src in _optional_sources(question, tenant_id, allowed_doc_ids, force_kg):
        for rank, d in enumerate(src):
            k_ = _key(d)
            docs_by_key.setdefault(k_, d)
            fused[k_] = fused.get(k_, 0.0) + 1.0 / (RRF_K + rank + 1)
            bm25_hit.add(k_)

    ordered = sorted(fused, key=fused.get, reverse=True)[:pool]
    cands = [docs_by_key[k_] for k_ in ordered]

    if include_regions:
        try:
            from VENUAI.multimodal_retrieval import retrieve_multimodal_documents
            for rd in retrieve_multimodal_documents(retrieval_query, allowed_doc_ids, top_k=3) or []:
                if _key(rd) not in fused:
                    cands.append(rd)
                    ordered.append(_key(rd))
                    docs_by_key[_key(rd)] = rd
                    fused[_key(rd)] = 0.0
        except Exception as err:
            logger.warning(f"[Retrieval] multimodal region retrieval skipped ({err})")

    if not cands:
        return RetrievalResult(retrieval_query=retrieval_query, queries=queries)

    scores = reranker.rerank_scores(retrieval_query, [rerank_text(d) for d in cands])
    kept: List[Tuple[float, Document, dict]] = []
    if scores is not None:
        floor = min_rerank_score()
        signal = "rerank"
        for d, s, k_ in zip(cands, scores, ordered):
            if s >= floor:
                kept.append((s, d, {"rerank_score": round(s, 4), "fusion_score": round(fused[k_], 5)}))
        kept.sort(key=lambda t: t[0], reverse=True)
    else:
        floor = min_similarity()
        signal = "similarity"
        for d, k_ in zip(cands, ordered):
            sim = sim_by_key.get(k_)
            region = (d.metadata or {}).get("region_id") is not None or fused[k_] == 0.0
            # Keep when similarity is unknown (cannot judge), is above the
            # floor, or the chunk has a lexical (BM25) match.
            if region or sim is None or sim >= floor or k_ in bm25_hit:
                kept.append((fused[k_], d, {"similarity": None if sim is None else round(sim, 4),
                                           "fusion_score": round(fused[k_], 5)}))
        kept.sort(key=lambda t: t[0], reverse=True)
        if not any(sim_by_key.get(k_) is not None for k_ in ordered):
            signal = "fusion"

    kept = kept[:top_k]
    out_docs, out_scores = [], []
    for rank_score, d, info in kept:
        d.metadata = dict(d.metadata or {})
        d.metadata["retrieval_score"] = float(rank_score)
        d.metadata["retrieval_signal"] = signal
        out_docs.append(d)
        out_scores.append({"doc_id": d.metadata.get("doc_id"), "page": d.metadata.get("page"), **info})

    if expand_parent and out_docs:
        _expand_parents(vs, out_docs)
    return RetrievalResult(
        documents=out_docs, scores=out_scores,
        insufficient_context=not out_docs, signal=signal,
        queries=queries, retrieval_query=retrieval_query,
    )


# ── ingest-side helper ────────────────────────────────────────────────────────

def index_chunks(vs, chunks: List[Document]) -> None:
    """
    Persist finalised chunks. When chunks carry a `context_prefix` (contextual
    chunking) the embedding is computed from prefix+text while the stored
    document stays the original text; otherwise this is exactly
    vs.add_documents(). Always invalidates the BM25 cache.
    """
    if not chunks:
        return
    if any((c.metadata or {}).get("context_prefix") for c in chunks):
        texts = [rerank_text(c) for c in chunks]
        embs = vs._embedding_function.embed_documents(texts)
        vs._collection.upsert(
            ids=[str(uuid.uuid4()) for _ in chunks],
            embeddings=embs,
            documents=[c.page_content for c in chunks],
            metadatas=[c.metadata for c in chunks],
        )
    else:
        vs.add_documents(chunks)
    bump_corpus_version()
