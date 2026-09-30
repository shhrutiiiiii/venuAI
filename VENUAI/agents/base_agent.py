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
    confidence: float = 0.0     # 0.0 → 1.0 (grounding score from VerifierAgent)
    reasoning: str = ""         # brief note on why this agent was chosen / what it did


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

    def query(self, question: str, history: Optional[List[dict]] = None) -> AgentResult:
        """
        Execute a RAG query for this agent's domain.

        Steps:
          1. Retrieve chunks from ChromaDB (with optional metadata filter).
          2. Build a domain-specific prompt.
          3. Call Groq LLM.
          4. Return AgentResult.

        Args:
            question : The user's question.
            history  : Optional list of prior conversation turns.

        Returns:
            AgentResult with answer, sources, and confidence estimate.
        """
        from VENUAI.rag_server import get_vector_store  # local import to avoid circular deps

        vs = get_vector_store()
        retriever = vs.as_retriever(
            search_type="similarity",
            search_kwargs={
                "k": self.top_k,
                **({"filter": self.metadata_filter} if self.metadata_filter else {}),
            },
        )
        try:
            docs: List[Document] = retriever.invoke(question)
        except Exception as e:
            logger.warning(f"[{self.name}] Retrieval error: {e}")
            docs = []

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
            candidate_models = [os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"), "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
            resp = None
            last_err = None
            for m in candidate_models:
                try:
                    resp = self.groq_client.chat.completions.create(
                        model=m,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=1200,
                        temperature=0.2,
                    )
                    break
                except Exception as ex:
                    last_err = ex
            if resp is None and last_err:
                raise last_err
            answer = resp.choices[0].message.content
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

        # Simple grounding estimate: fraction of docs that contributed content
        confidence = min(1.0, len(docs) / max(self.top_k, 1))

        return AgentResult(
            agent_name=self.name,
            answer=answer,
            sources=sources,
            confidence=round(confidence, 2),
            reasoning=f"{self.name} handled this query (domain: {self.domain}).",
        )
