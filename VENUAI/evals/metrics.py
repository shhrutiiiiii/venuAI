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
