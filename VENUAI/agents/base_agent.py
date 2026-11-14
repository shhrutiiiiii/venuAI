"""
DRDO VenuAI — Base Specialist Agent
======================================
Abstract base class and shared data structures for all specialist agents
in the multi-agent system.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("drdo-rag")


# ── Shared types ────────────────────────────────────────────────────────────────

@dataclass
class QuerySource:
    """A single source citation from a retrieved document chunk."""
    document: str
    page: int
    chunk_preview: str


@dataclass
class AgentResult:
    """Standardised output returned by every specialist agent."""
    agent_name: str
    answer: str
    sources: List[QuerySource] = field(default_factory=list)
    # DEPRECATED alias of retrieval_coverage (kept so old callers/tests keep working). It was never a
    # grounding/confidence measure; real grounding is computed by answer_pipeline.run_verification.
    confidence: float = 0.0
    # Fraction of top_k slots filled by retrieval (a retrieval signal, NOT answer correctness).
    retrieval_coverage: Optional[float] = None
    reasoning: str = ""         # brief note on why this agent was chosen / what it did
    # Phase 5 — Evidence Explorer integration for multi-agent mode
    # (VENUAI/evidence_store.py). The real retrieved Document objects
    # (text chunks + any folded-in multimodal regions), not just their
    # QuerySource summaries — VerificationPipeline needs the full
    # page_content to build real Evidence/Claim objects from, which
    # QuerySource's truncated chunk_preview can't provide.
    documents: List[Document] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.retrieval_coverage is None:
            self.retrieval_coverage = self.confidence
        else:
            self.confidence = self.retrieval_coverage


# ── BaseSpecialistAgent ─────────────────────────────────────────────────────────

class BaseSpecialistAgent:
    """
    Abstract base class for all VenuAI specialist agents.

    Subclasses must define:
        name             : str  — human-readable agent name
        domain           : str  — domain this agent covers
        system_prompt    : str  — tailored LLM system instruction
        metadata_filter  : dict — optional ChromaDB where-clause for domain filtering

    and may override query() for custom behaviour.
    """

    name: str = "BaseAgent"
    domain: str = "general"
    system_prompt: str = "You are a helpful DRDO research assistant."
    metadata_filter: Optional[dict] = None

    def __init__(self, groq_client, top_k: int = 4) -> None:
        """
        Args:
            groq_client : Initialised Groq Python client.
            top_k       : Number of chunks to retrieve per query.
        """
        self.groq_client = groq_client
        self.top_k = top_k

    # ── Retrieval (shared pipeline) ───────────────────────────────────────────

    def _retrieve(
        self, question: str, history: Optional[List[dict]], allowed_doc_ids: Optional[List[str]],
    ) -> List[Document]:
        """Specialists use the same hybrid retrieval as the main pipeline (VENUAI/retrieval.py):
        dense + BM25 fusion, cross-encoder rerank + abstention floor, table/image regions, doc ACL."""
        try:
            from VENUAI import retrieval
            getter = getattr(retrieval, "get_vectorstore", None)
            if getter is None:
                from VENUAI.rag_server import get_vector_store as getter  # local import: avoid cycle
            res = retrieval.retrieve_documents(
                getter(), question, self.top_k, allowed_doc_ids, history=history, rewrite=False,
            )
            docs = list(res.documents)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{self.name}] Retrieval error: {e}")
            return []
        if self.metadata_filter:
            docs = [d for d in docs if all((d.metadata or {}).get(k) == v for k, v in self.metadata_filter.items()
                                           if not str(k).startswith("$"))]
        return docs

    # ── Core query method ─────────────────────────────────────────────────────

    def query(
        self,
        question: str,
        history: Optional[List[dict]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
    ) -> AgentResult:
        """
        Execute a RAG query for this agent's domain.

        Steps:
          1. Retrieve chunks from ChromaDB (with optional metadata filter).
          2. Build a domain-specific prompt.
          3. Call Groq LLM.
          4. Return AgentResult.

        Args:
            question        : The user's question.
            history         : Optional list of prior conversation turns.
            allowed_doc_ids : doc_ids the caller is authorized to retrieve from
                               (None = unrestricted/admin; [] = none visible).
                               See Documents/Backend/authentication.md.

        Returns:
            AgentResult with answer, sources, and confidence estimate.
        """
        docs = self._retrieve(question, history, allowed_doc_ids)
        kb_used = False
        if not docs:
            # Built-in KB fallback: per-entry citable documents (VENUAI/kb_documents.py), never a raw
            # JSON dump, and always labelled as built-in (kb_fallback metadata).
            try:
                from VENUAI.kb_documents import kb_fallback_documents
                docs = kb_fallback_documents(question, k=self.top_k)
                kb_used = bool(docs)
            except Exception as kb_err:  # noqa: BLE001
                logger.warning(f"[{self.name}] KB fallback unavailable: {kb_err}")
                docs = []

        if not docs:
            # Abstain honestly: nothing retrieved from the user's documents and nothing in the built-in KB.
            return AgentResult(
                agent_name=self.name,
                answer=("I could not find relevant information in your documents or the built-in "
                        "knowledge base, so I will not guess."),
                sources=[], retrieval_coverage=0.0,
                reasoning=f"{self.name} abstained: no relevant context retrieved.",
                documents=[],
            )

        context = "\n\n".join(
            f"[{d.metadata.get('filename', 'Unknown')}, p.{d.metadata.get('page', 0)+1}]\n{d.page_content}"
            for d in docs
        )

        history_str = ""
        if history:
            history_str = "\n".join(
                f"{'User' if m.get('role') == 'user' else 'Assistant'}: {m.get('content','')}"
                for m in history[-6:]
            )

        prompt = (
            f"{self.system_prompt}\n\n"
            f"Chat history:\n{history_str or 'None'}\n\n"
            f"{'NOTE: the context below comes from the built-in knowledge base, not the user documents.' + chr(10) if kb_used else ''}"
            f"Context:\n{context}\n\n"
            f"Question: {question}\n\n"
            "Answer (formatted with headings, tables, and bullet points):"
        )

        try:
            from VENUAI.ai_gateway.gateway import get_gateway
            from VENUAI.ai_gateway.model_router import TaskType

            gateway = get_gateway(self.groq_client)
            result = gateway.complete(
                task_type=TaskType.GENERATION,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
                temperature=0.2,
            )
            answer = result.content
        except Exception as e:
            logger.error(f"[{self.name}] LLM call failed: {e}")
            answer = f"⚠️ {self.name} encountered an error: ({type(e).__name__}; details in server log)"

        # Build sources
        sources = []
        seen: set = set()
        for doc in docs:
            key = (doc.metadata.get("filename", "Unknown"), doc.metadata.get("page", 0))
            if key not in seen:
                seen.add(key)
                sources.append(QuerySource(
                    document=key[0],
                    page=key[1] + 1,
                    chunk_preview=doc.page_content[:120].strip() + "…",
                ))

        # Retrieval coverage (fraction of top_k filled) - NOT a grounding/confidence score.
        coverage = min(1.0, len(docs) / max(self.top_k, 1))

        return AgentResult(
            agent_name=self.name,
            answer=answer,
            sources=sources,
            retrieval_coverage=round(coverage, 2),
            reasoning=f"{self.name} handled this query (domain: {self.domain}).",
            documents=docs,
        )
