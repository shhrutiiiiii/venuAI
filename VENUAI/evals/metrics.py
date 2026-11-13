"""
DRDO VenuAI — Evaluation Metrics
===================================
Standalone metric functions for assessing RAG pipeline quality.

Metrics:
  faithfulness()         — % of answer claims supported by source docs
  answer_relevancy()     — cosine similarity of question ↔ answer embeddings
  context_precision()    — % of retrieved docs that are relevant (needs golden labels)
  context_recall()       — % of relevant docs that were retrieved
  hallucination_rate()   — % of claims flagged UNSUPPORTED
  response_latency_ms()  — end-to-end latency in milliseconds
  keyword_coverage()     — fraction of expected keywords found in answer
"""

import re
import time
import logging
from typing import List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")


def faithfulness(answer: str, source_docs: List[Document]) -> float:
    """
    Compute what fraction of factual answer sentences are supported by
    the source documents, using token-level F1 overlap.

    Returns:
        float in [0.0, 1.0]. 1.0 = fully faithful.
    """
    if not source_docs or not answer:
        return 0.0

    corpus = " ".join(d.page_content.lower() for d in source_docs)
    corpus_tokens = set(re.findall(r"\b\w+\b", corpus))

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if len(s.split()) > 6]
    if not sentences:
        return 1.0

    scores = []
    for sent in sentences:
        s_tokens = set(re.findall(r"\b\w+\b", sent.lower()))
        if not s_tokens:
            continue
        common = s_tokens & corpus_tokens
        precision = len(common) / len(s_tokens)
        recall = len(common) / max(len(corpus_tokens), 1)
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        scores.append(f1)

    return round(sum(scores) / len(scores), 3) if scores else 1.0


def answer_relevancy(answer: str, question: str) -> float:
    """
    Compute cosine similarity between sentence embeddings of the question
    and the answer. Uses sentence-transformers if available; falls back to
    token Jaccard similarity.

    Returns:
        float in [0.0, 1.0]. 1.0 = perfectly relevant.
    """
    if not answer or not question:
        return 0.0

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        q_emb, a_emb = model.encode([question, answer[:500]])
        cosine = float(
            np.dot(q_emb, a_emb) / (np.linalg.norm(q_emb) * np.linalg.norm(a_emb) + 1e-9)
        )
        return round(max(0.0, min(1.0, cosine)), 3)
    except Exception:
        # Jaccard fallback
        q_tokens = set(re.findall(r"\b\w+\b", question.lower()))
        a_tokens = set(re.findall(r"\b\w+\b", answer.lower()))
        if not q_tokens:
            return 0.0
        return round(len(q_tokens & a_tokens) / len(q_tokens | a_tokens), 3)


def context_precision(
    retrieved_docs: List[Document],
    relevant_doc_ids: List[str],
) -> float:
    """
    Compute the fraction of retrieved documents that are relevant
    (i.e., their doc_id appears in the golden relevant_doc_ids list).

    Returns:
        float in [0.0, 1.0]. Returns 1.0 if relevant_doc_ids is empty
        (no golden labels available — assume all retrieved are relevant).
    """
    if not retrieved_docs:
        return 0.0
    if not relevant_doc_ids:
        return 1.0  # no labels → can't penalise

    relevant_set = set(relevant_doc_ids)
    hits = sum(
        1 for d in retrieved_docs
        if d.metadata.get("doc_id", "") in relevant_set
    )
    return round(hits / len(retrieved_docs), 3)


def context_recall(
    relevant_doc_ids: List[str],
    retrieved_doc_ids: List[str],
) -> float:
    """
    Compute the fraction of relevant documents that were successfully retrieved.

    Returns:
        float in [0.0, 1.0]. Returns 1.0 if relevant_doc_ids is empty.
    """
    if not relevant_doc_ids:
        return 1.0
    retrieved_set = set(retrieved_doc_ids)
    hits = sum(1 for doc_id in relevant_doc_ids if doc_id in retrieved_set)
    return round(hits / len(relevant_doc_ids), 3)


def mean_reciprocal_rank(retrieved_doc_ids: List[str], relevant_doc_ids: List[str]) -> float:
    """
    MRR for a single query — reciprocal of the rank of the first relevant
    document in the retrieved list. Unlike context_precision/context_recall
    above (which only check set membership, not order), this rewards
    ranking a relevant document earlier.

    Returns:
        float in [0.0, 1.0]. 0.0 if no relevant document was retrieved at all.
    """
    if not relevant_doc_ids or not retrieved_doc_ids:
        return 0.0
    relevant_set = set(relevant_doc_ids)
    for rank, doc_id in enumerate(retrieved_doc_ids, start=1):
        if doc_id in relevant_set:
            return round(1.0 / rank, 3)
    return 0.0


def ndcg_at_k(retrieved_doc_ids: List[str], relevant_doc_ids: List[str], k: Optional[int] = None) -> float:
    """
    Normalized Discounted Cumulative Gain at rank k, with binary relevance
    (a doc is relevant or not — no graded relevance scores exist in this
    dataset format today).

    Returns:
        float in [0.0, 1.0]. 1.0 = all relevant docs retrieved at the top ranks.
    """
    if not relevant_doc_ids or not retrieved_doc_ids:
        return 0.0
    import math

    k = k or len(retrieved_doc_ids)
    relevant_set = set(relevant_doc_ids)
    retrieved = retrieved_doc_ids[:k]

    dcg = sum(
        (1.0 / math.log2(rank + 1)) for rank, doc_id in enumerate(retrieved, start=1)
        if doc_id in relevant_set
    )
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    if idcg == 0:
        return 0.0
    return round(dcg / idcg, 3)


def hallucination_rate(claims) -> float:
    """
    Compute the fraction of claims flagged as UNSUPPORTED.

    Args:
        claims: List of Claim dataclass objects (from HallucinationDetector).

    Returns:
        float in [0.0, 1.0]. 0.0 = no hallucination.
    """
    if not claims:
        return 0.0
    unsupported = sum(1 for c in claims if c.verdict == "UNSUPPORTED")
    return round(unsupported / len(claims), 3)


def response_latency_ms(start: float, end: float) -> float:
    """
    Compute end-to-end response latency.

    Args:
        start : time.time() value at request start.
        end   : time.time() value at response completion.

    Returns:
        Latency in milliseconds, rounded to 1 decimal.
    """
    return round((end - start) * 1000, 1)


def keyword_coverage(answer: str, expected_keywords: List[str]) -> float:
    """
    Compute the fraction of expected keywords that appear in the answer.
    Case-insensitive. Used for golden Q&A evaluation.

    Args:
        answer            : AI-generated answer string.
        expected_keywords : List of keywords that should appear in a correct answer.

    Returns:
        float in [0.0, 1.0]. 1.0 = all keywords found.
    """
    if not expected_keywords:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
    return round(hits / len(expected_keywords), 3)


# ── Deterministic harness metrics (no LLM) ─────────────────────────────────────
import math as _math

_ABSTAIN_PATTERNS = [
    r"not available in the (uploaded )?(drdo )?(documents|context|knowledge base)",
    r"(cannot|can't|could not|couldn't|unable to) (be )?(find|found|locate|determine|answer)",
    r"(does not|doesn't|do not|don't) (contain|include|specify|mention|provide|state)",
    r"no (relevant )?(information|data|mention|details)",
    r"not (found|mentioned|specified|stated|provided) in",
    r"insufficient (information|context)",
]


def is_abstention(answer: str) -> bool:
    """Heuristic: does the answer decline / say the info is not in the documents?"""
    a = (answer or "").lower()
    return any(re.search(p, a) for p in _ABSTAIN_PATTERNS)


def dedupe_preserve(items: List[str]) -> List[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def hit_at_k(retrieved_doc_ids: List[str], gold: List[str], k: int) -> Optional[float]:
    """1.0 if any gold doc is within the top-k distinct retrieved docs."""
    if not gold:
        return None
    top = dedupe_preserve(retrieved_doc_ids)[:k]
    return 1.0 if any(g in top for g in gold) else 0.0


def recall_at_k(retrieved_doc_ids: List[str], gold: List[str], k: int) -> Optional[float]:
    """Fraction of gold docs within the top-k distinct retrieved docs."""
    if not gold:
        return None
    top = set(dedupe_preserve(retrieved_doc_ids)[:k])
    return sum(1 for g in gold if g in top) / len(gold)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", (s or "").lower())).strip()


def citation_precision(citations: List[dict], chunks: List[dict]) -> Optional[float]:
    """Fraction of citations whose quote appears (normalised) in the cited chunk.

    citation: {"quote": str, "doc"|"chunk_id"|"document": str}; chunk: {"id","doc","text"}.
    Returns None when there are no citations (nothing to score)."""
    if not citations:
        return None
    ok = 0
    for c in citations:
        quote = _norm(c.get("quote") or c.get("text") or "")
        key = c.get("chunk_id") or c.get("doc") or c.get("document")
        pool = [ch for ch in chunks if key in (ch.get("id"), ch.get("doc"))] or []
        if quote and any(quote in _norm(ch.get("text", "")) for ch in pool):
            ok += 1
    return ok / len(citations)


def canary_leaked(answer: str, canary: str) -> bool:
    return bool(canary) and canary.lower() in (answer or "").lower()


def percentile(values: List[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile, p in [0,100]. None for empty input."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * p / 100.0
    lo, hi = _math.floor(pos), _math.ceil(pos)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)
