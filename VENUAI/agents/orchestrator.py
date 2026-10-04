"""
DRDO VenuAI — Orchestrator Agent
===================================
Routes user queries to the appropriate domain-specialist agent(s).
Supports parallel fan-out when a query spans multiple domains,
then synthesises a unified answer via the GeneralAgent.

Usage:
    orchestrator = OrchestratorAgent(groq_client)
    result = await orchestrator.route(question, history, memory_ctx)
"""

import asyncio
import logging
from typing import List, Optional

from VENUAI.agents.base_agent import AgentResult
from VENUAI.agents.technical_agent import TechnicalAgent
from VENUAI.agents.labs_agent import LabsAgent
from VENUAI.agents.research_agent import ResearchAgent
from VENUAI.agents.general_agent import GeneralAgent

logger = logging.getLogger("drdo-rag")

_INTENT_PROMPT = """You are a DRDO query classifier.
Classify the following question into one or more of these categories:
  technical  — weapons specs, engineering params, materials, propulsion, electronics
  labs       — DRDO labs, clusters, locations, org structure, lab mandates
  research   — ongoing projects, publications, R&D programs, technology roadmaps
  general    — any other DRDO question or cross-domain query

Respond with ONLY a comma-separated list (no spaces after commas), e.g.: technical,labs

Question: {question}
Categories:"""


class OrchestratorAgent:
    """
    Classifies query intent using a fast Groq call, then routes to
    the appropriate specialist(s). Supports parallel fan-out for
    multi-domain queries.
    """

    def __init__(self, groq_client) -> None:
        """
        Args:
            groq_client: Initialised Groq Python client.
        """
        self.groq_client = groq_client
        self._specialists = {
            "technical": TechnicalAgent(groq_client),
            "labs":      LabsAgent(groq_client),
            "research":  ResearchAgent(groq_client),
            "general":   GeneralAgent(groq_client),
        }

    # ── Intent classification ─────────────────────────────────────────────────

    def classify_intent(self, question: str) -> List[str]:
        """
        Use a fast Groq model to classify the query domain(s).

        Returns:
            List of domain strings, e.g. ['technical'] or ['labs', 'research'].
            Falls back to ['general'] if the LLM call fails.
        """
        valid_domains = {"technical", "labs", "research", "general"}
        try:
            from VENUAI.ai_gateway.gateway import get_gateway
            from VENUAI.ai_gateway.model_router import TaskType

            gateway = get_gateway(self.groq_client)
            result = gateway.complete(
                task_type=TaskType.FAST_CLASSIFICATION,
                messages=[{"role": "user", "content": _INTENT_PROMPT.format(question=question)}],
                max_tokens=20,
                temperature=0.0,
            )
            raw = result.content.strip().lower()
            domains = [d.strip() for d in raw.split(",") if d.strip() in valid_domains]
            if not domains:
                domains = ["general"]
            logger.info(f"[OrchestratorAgent] Intent classified: {domains}")
            return domains
        except Exception as e:
            logger.warning(f"[OrchestratorAgent] classify_intent failed: {e}")
            return ["general"]

    # ── Routing ───────────────────────────────────────────────────────────────

    async def route(
        self,
        question: str,
        history: Optional[List[dict]] = None,
        memory_ctx: Optional[dict] = None,
        allowed_doc_ids: Optional[List[str]] = None,
    ) -> AgentResult:
        """
        Route the query to specialist agent(s):
          - Single domain  → call that specialist directly.
          - Multiple domains → call specialists in parallel, then merge results.

        Args:
            question        : The user's question.
            history         : Prior conversation turns.
            memory_ctx      : Memory context dict from MemoryManager.get_context().
            allowed_doc_ids : doc_ids the caller is authorized to retrieve from —
                               forwarded to every specialist's retrieval call.
                               See Documents/Backend/authentication.md.

        Returns:
            A single consolidated AgentResult.
        """
        domains = self.classify_intent(question)

        if len(domains) == 1:
            agent = self._specialists.get(domains[0], self._specialists["general"])
            logger.info(f"[OrchestratorAgent] Routing to {agent.name}")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: agent.query(question, history, allowed_doc_ids)
            )
            return result

        # Parallel fan-out
        logger.info(f"[OrchestratorAgent] Fan-out to: {domains}")
        tasks = [
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda a=self._specialists.get(d, self._specialists["general"]): a.query(question, history, allowed_doc_ids)
            )
            for d in domains
        ]
        results: List[AgentResult] = await asyncio.gather(*tasks, return_exceptions=True)
        valid_results = [r for r in results if isinstance(r, AgentResult)]

        if not valid_results:
            # Previously omitted allowed_doc_ids here — a real authorization
            # gap on this specific fallback path (all specialists erroring
            # out). Fixed to match every other call site in this method.
            return self._specialists["general"].query(question, history, allowed_doc_ids)

        if len(valid_results) == 1:
            return valid_results[0]

        return self.merge_results(valid_results, question)

    # ── Result merging ────────────────────────────────────────────────────────

    def merge_results(self, results: List[AgentResult], question: str) -> AgentResult:
        """
        Synthesise multiple specialist answers into one coherent response using
        a Groq LLM call to unify the content.

        Args:
            results  : List of AgentResult from specialist agents.
            question : The original user question (for context).

        Returns:
            A single unified AgentResult.
        """
        # Combine specialist answers into a synthesis prompt
        parts = []
        all_sources = []
        all_documents = []
        for r in results:
            parts.append(f"=== {r.agent_name} ===\n{r.answer}")
            all_sources.extend(r.sources)
            all_documents.extend(r.documents)

        combined = "\n\n".join(parts)
        prompt = (
            "You are VenuAI. Below are answers from multiple DRDO specialist agents. "
            "Synthesise them into a single, coherent, well-formatted response. "
            "Remove redundant information. Preserve all technical data, tables, and sources.\n\n"
            f"Original question: {question}\n\n"
            f"Specialist answers:\n{combined}\n\n"
            "Unified answer:"
        )

        try:
            from VENUAI.ai_gateway.gateway import get_gateway
            from VENUAI.ai_gateway.model_router import TaskType

            gateway = get_gateway(self.groq_client)
            result = gateway.complete(
                task_type=TaskType.SYNTHESIS,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                temperature=0.1,
            )
            merged_answer = result.content
        except Exception as e:
            logger.error(f"[OrchestratorAgent] merge_results LLM failed: {e}")
            # Fallback: concatenate answers
            merged_answer = "\n\n".join(r.answer for r in results)

        avg_confidence = sum(r.confidence for r in results) / len(results)
        agent_names = " + ".join(r.agent_name for r in results)

        return AgentResult(
            agent_name=f"Orchestrator({agent_names})",
            answer=merged_answer,
            sources=all_sources,
            confidence=round(avg_confidence, 2),
            reasoning=f"Multi-domain query handled by {agent_names}.",
            documents=all_documents,
        )
