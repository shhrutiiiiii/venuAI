"""
Agentic RAG — adaptive retrieve → evaluate → re-retrieve loop, per spec
section 18. Upgrades the static retrieve-once-generate-once path with a
bounded loop that checks whether retrieved evidence is actually sufficient
before generating, and retrieves again (with an expanded query) if not.

Reuses the real retrieval/generation building blocks already in this
codebase — VENUAI.rag_server.get_vector_store, VENUAI.context_engineer's
ranking/prompt-building, VENUAI.ai_gateway for the LLM call, and
VENUAI.verification_pipeline for the evidence-sufficiency check itself —
rather than reimplementing retrieval or prompting a second time.

Hard limits (spec section 18/65): MAX_RETRIEVAL_ROUNDS, MAX_RUNTIME_SECONDS.
Exceeding either returns the best answer found so far with
loop_limit_reached=True, rather than looping forever or raising.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")

MAX_RETRIEVAL_ROUNDS = 3
MAX_RUNTIME_SECONDS = 45
SUFFICIENT_EVIDENCE_COVERAGE = 0.6  # evidence_coverage threshold to stop retrieving


@dataclass
class AgenticRAGResult:
    answer: str
    documents: List[Document]
    evidence_coverage: float
    critique_passed: bool
    rounds_used: int
    loop_limit_reached: bool = False


def _retrieve(
    question: str, top_k: int, allowed_doc_ids: Optional[List[str]],
    history: Optional[List[dict]] = None, rewrite: bool = False,
) -> List[Document]:
    """Same pipeline as VENUAI.rag_server.retrieve_node (shared
    VENUAI.retrieval.retrieve_documents: dense + cached BM25 -> RRF ->
    cross-encoder rerank -> relevance floor, multimodal regions included).
    Local import avoids a circular dependency with rag_server."""
    from VENUAI.rag_server import get_vector_store
    from VENUAI.retrieval import retrieve_documents

    return retrieve_documents(
        get_vector_store(), question, top_k, allowed_doc_ids,
        history=history, rewrite=rewrite,
    ).documents


def _expand_query(question: str, round_num: int, previous_query: str = "") -> str:
    """Re-retrieval round query: an LLM-written alternative phrasing (FAST
    model via the gateway; VENUAI/query_rewriter.py), replacing the old canned
    'overview background context' suffix. Returns the original question on
    failure, in which case round 2 finds nothing new and the loop stops."""
    from VENUAI.query_rewriter import expand_query_llm
    return expand_query_llm(question, round_num, previous_query)


class AgenticRAG:
    def run(
        self,
        question: str,
        top_k: int = 4,
        allowed_doc_ids: Optional[List[str]] = None,
        history: Optional[List[dict]] = None,
    ) -> AgenticRAGResult:
        from VENUAI.context_engineer import ContextBudget, rank_chunks, compress_history, build_drdo_prompt
        from VENUAI.ai_gateway.gateway import get_gateway
        from VENUAI.ai_gateway.model_router import TaskType
        from VENUAI.verification_pipeline import get_verification_pipeline
        from VENUAI.ai_gateway.provider import get_groq_client

        t_start = time.time()
        all_docs: List[Document] = []
        seen_keys = set()
        rounds_used = 0
        loop_limit_reached = False

        current_query = question
        for round_num in range(1, MAX_RETRIEVAL_ROUNDS + 1):
            if time.time() - t_start > MAX_RUNTIME_SECONDS:
                loop_limit_reached = True
                break

            rounds_used = round_num
            new_docs = _retrieve(current_query, top_k, allowed_doc_ids, history=history, rewrite=(round_num == 1))
            for doc in new_docs:
                key = (doc.metadata.get("doc_id"), doc.metadata.get("page"), doc.page_content[:80])
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_docs.append(doc)

            # Cheap sufficiency check: do we have *any* evidence at all yet?
            # The real coverage number (claim-by-claim) only exists after a
            # draft answer is generated — see below — so this is a
            # necessary-but-not-sufficient early-exit, not the final verdict.
            if all_docs:
                break
            current_query = _expand_query(question, round_num, current_query)

        if time.time() - t_start > MAX_RUNTIME_SECONDS:
            loop_limit_reached = True

        ranked_docs = rank_chunks(all_docs, question)
        prompt_text = build_drdo_prompt(
            question=question,
            ranked_docs=ranked_docs,
            compressed_history=compress_history(history or []),
            budget=ContextBudget(),
            semantic_memories=[],
        )

        gateway = get_gateway(get_groq_client())
        result = gateway.complete(task_type=TaskType.GENERATION, messages=[{"role": "user", "content": prompt_text}])
        answer = result.content

        pipeline_result = get_verification_pipeline().run(question, answer, ranked_docs)

        # One re-retrieval round if the generated answer turned out poorly
        # grounded and we still have retrieval rounds and time budget left —
        # the actual "evaluate → re-retrieve" loop spec section 18 asks for,
        # now informed by the real per-claim evidence_coverage rather than
        # just "did we get any chunks at all."
        while (
            pipeline_result.evidence_graph.evidence_coverage < SUFFICIENT_EVIDENCE_COVERAGE
            and rounds_used < MAX_RETRIEVAL_ROUNDS
            and time.time() - t_start <= MAX_RUNTIME_SECONDS
        ):
            rounds_used += 1
            current_query = _expand_query(question, rounds_used, current_query)
            new_docs = _retrieve(current_query, top_k, allowed_doc_ids)
            added = False
            for doc in new_docs:
                key = (doc.metadata.get("doc_id"), doc.metadata.get("page"), doc.page_content[:80])
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_docs.append(doc)
                    added = True
            if not added:
                break  # nothing new to retrieve — stop rather than loop uselessly

            ranked_docs = rank_chunks(all_docs, question)
            prompt_text = build_drdo_prompt(
                question=question, ranked_docs=ranked_docs,
                compressed_history=compress_history(history or []),
                budget=ContextBudget(), semantic_memories=[],
            )
            result = gateway.complete(task_type=TaskType.GENERATION, messages=[{"role": "user", "content": prompt_text}])
            answer = result.content
            pipeline_result = get_verification_pipeline().run(question, answer, ranked_docs)

        if rounds_used >= MAX_RETRIEVAL_ROUNDS and pipeline_result.evidence_graph.evidence_coverage < SUFFICIENT_EVIDENCE_COVERAGE:
            loop_limit_reached = True

        return AgenticRAGResult(
            answer=answer,
            documents=ranked_docs,
            evidence_coverage=pipeline_result.evidence_graph.evidence_coverage,
            critique_passed=pipeline_result.critique.passed,
            rounds_used=rounds_used,
            loop_limit_reached=loop_limit_reached,
        )
