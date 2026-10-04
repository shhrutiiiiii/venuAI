"""
DeepResearchAgent — the multi-step research agent spec section 19 asks for.

Deliberately kept out of VENUAI/agents/research_agent.py's `ResearchAgent`
class/name — that module is the existing single-shot RAG specialist for
DRDO programme/publication questions (registered as
"research_specialist_agent" in VENUAI/agents/registry.py). This is a
different, more involved capability: decompose → retrieve-per-subquestion
→ synthesize → verify, registered separately as "deep_research_agent" so
the two are never confused in code, docs, or the registry.

Pipeline:
    question → ResearchPlannerAgent.plan() → N subquestions
             → AgenticRAG.run() per subquestion (bounded, authorization-filtered)
             → AIGateway SYNTHESIS call combining sub-answers
             → VerificationPipeline on the synthesized answer
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("drdo-rag")

MAX_RUNTIME_SECONDS = 120  # hard ceiling across the whole multi-subquestion run


@dataclass(frozen=True)
class SubquestionResult:
    subquestion: str
    answer: str
    evidence_coverage: float


@dataclass(frozen=True)
class DeepResearchResult:
    question: str
    plan_is_fallback: bool
    subquestion_results: List[SubquestionResult] = field(default_factory=list)
    synthesized_answer: str = ""
    evidence_coverage: float = 0.0
    critique_passed: bool = False
    truncated: bool = False  # True if MAX_RUNTIME_SECONDS cut the run short
    # Phase 4 — Evidence Explorer (VENUAI/evidence_store.py). Real
    # VENUAI.evidence.Claim objects from the VerificationPipeline run
    # below, not yet serialized — the caller (POST /research) serializes
    # via evidence_store.serialize_claims() the same way
    # VENUAI.rag_server.verify_node does for standard RAG.
    claims: list = field(default_factory=list)


_SYNTHESIS_PROMPT = """You are VenuAI's research synthesis step. Below are answers to several \
sub-questions that together address one research question. Combine them into a single, \
coherent, well-organized answer. Preserve technical details and do not introduce claims \
that are not present in the sub-answers below.

Original research question: {question}

Sub-question answers:
{combined}

Synthesized answer:"""


class DeepResearchAgent:
    def research(
        self,
        question: str,
        groq_client,
        allowed_doc_ids: Optional[List[str]] = None,
    ) -> DeepResearchResult:
        import time

        from VENUAI.research_planner import ResearchPlannerAgent
        from VENUAI.agentic_rag import AgenticRAG
        from VENUAI.ai_gateway.gateway import get_gateway
        from VENUAI.ai_gateway.model_router import TaskType
        from VENUAI.verification_pipeline import get_verification_pipeline

        t_start = time.time()
        plan = ResearchPlannerAgent().plan(question, groq_client)

        subquestion_results: List[SubquestionResult] = []
        all_docs = []
        truncated = False

        for sq in plan.subquestions:
            if time.time() - t_start > MAX_RUNTIME_SECONDS:
                truncated = True
                break
            rag_result = AgenticRAG().run(sq, top_k=4, allowed_doc_ids=allowed_doc_ids)
            subquestion_results.append(SubquestionResult(
                subquestion=sq, answer=rag_result.answer, evidence_coverage=rag_result.evidence_coverage,
            ))
            all_docs.extend(rag_result.documents)

        if not subquestion_results:
            return DeepResearchResult(
                question=question, plan_is_fallback=plan.is_fallback,
                synthesized_answer="Research could not be completed — no sub-question was answered in time.",
                truncated=True,
            )

        if len(subquestion_results) == 1:
            synthesized = subquestion_results[0].answer
        else:
            combined = "\n\n".join(
                f"Q: {r.subquestion}\nA: {r.answer}" for r in subquestion_results
            )
            gateway = get_gateway(groq_client)
            result = gateway.complete(
                task_type=TaskType.SYNTHESIS,
                messages=[{"role": "user", "content": _SYNTHESIS_PROMPT.format(question=question, combined=combined)}],
                max_tokens=1500,
                temperature=0.1,
            )
            synthesized = result.content

        pipeline_result = get_verification_pipeline().run(question, synthesized, all_docs)

        return DeepResearchResult(
            question=question,
            plan_is_fallback=plan.is_fallback,
            subquestion_results=subquestion_results,
            synthesized_answer=synthesized,
            evidence_coverage=pipeline_result.evidence_graph.evidence_coverage,
            critique_passed=pipeline_result.critique.passed,
            truncated=truncated,
            claims=pipeline_result.evidence_graph.claims,
        )
