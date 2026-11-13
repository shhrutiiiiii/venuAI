"""
DRDO VenuAI — Base Specialist Agent
======================================
Abstract base class and shared data structures for all specialist agents
in the multi-agent system.
"""

import json
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
        from VENUAI.rag_server import get_vector_store  # local import to avoid circular deps

        vs = get_vector_store()

        where_clauses = []
        if self.metadata_filter:
            where_clauses.append(self.metadata_filter)
        if allowed_doc_ids is not None:
            where_clauses.append({"doc_id": {"$in": allowed_doc_ids or ["__none__"]}})
        where_filter = (
            {"$and": where_clauses} if len(where_clauses) > 1
            else (where_clauses[0] if where_clauses else None)
        )

        retriever = vs.as_retriever(
            search_type="similarity",
            search_kwargs={
                "k": self.top_k,
                **({"filter": where_filter} if where_filter else {}),
            },
        )
        try:
            docs: List[Document] = retriever.invoke(question)
        except Exception as e:
            logger.warning(f"[{self.name}] Retrieval error: {e}")
            docs = []

        # Phase 5: fold in table/image region hits, same as the standard
        # RAG, Agentic RAG, and ReAct paths. Not filtered by
        # self.metadata_filter (a multimodal region carries no domain_tags
        # metadata to filter on) — only by allowed_doc_ids, the
        # authorization boundary every retrieval path shares. Best-effort:
        # a lexical-search failure here must not break the text-chunk result.
        try:
            from VENUAI.multimodal_retrieval import retrieve_multimodal_documents
            region_docs = retrieve_multimodal_documents(question, allowed_doc_ids, top_k=3)
            if region_docs:
                docs = list(docs) + region_docs
        except Exception as region_err:
            logger.warning(f"[{self.name}] multimodal region retrieval skipped: {region_err}")

        if not docs:
            from VENUAI.rag_server import load_prefilled_kb
            kb_data = load_prefilled_kb()
            if kb_data:
                kb_text = json.dumps(kb_data, indent=2, ensure_ascii=False)[:8000]
                fake_doc = Document(
                    page_content=f"DRDO Master Knowledge Base:\n{kb_text}",
                    metadata={"filename": "DRDO Master Knowledge Base (Preloaded JSON)", "page": 0}
                )
                docs = [fake_doc]

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
            f"Context:\n{context or 'No relevant documents retrieved.'}\n\n"
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
            answer = f"⚠️ {self.name} encountered an error: {str(e)}"

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
