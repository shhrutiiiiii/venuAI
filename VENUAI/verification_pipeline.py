"""
Verification Pipeline — ties Evidence/Claim construction, CitationAgent,
ContradictionAgent, and CriticAgent into one call, per spec section 28:

    Retrieve → Generate → Extract claims → Citation verification
    → Contradiction analysis → Critic → (verdict)

This is additive to, not a replacement for, the existing
VerifierAgent/HallucinationDetector checks already run in
VENUAI/rag_server.py's verify_node — those remain the primary grounding/
hallucination/policy signals; this pipeline adds the claim-level evidence
trail (spec sections 25/26/27/29) on top, which neither existing check
provides on its own.
"""

from dataclasses import dataclass
from typing import List, Optional

from langchain_core.documents import Document

from VENUAI.citation_agent import CitationAgent
from VENUAI.contradiction_agent import ContradictionAgent
from VENUAI.critic_agent import CriticAgent, CritiqueResult
from VENUAI.evidence import Claim, Evidence, EvidenceGraph


@dataclass(frozen=True)
class VerificationPipelineResult:
    evidence_graph: EvidenceGraph
    critique: CritiqueResult
    # Claim-level local faithfulness (local_faithfulness.score_claims) when LOCAL_FAITHFULNESS=on.
    # None = flag off or not measurable; never a made-up number.
    local_faithfulness: Optional[dict] = None
    # Cross-source contradictions (contradiction_agent.find_contradictions); [] when none found.
    contradictions: Optional[list] = None


class VerificationPipeline:
    def __init__(self) -> None:
        self._citation_agent = CitationAgent()
        self._contradiction_agent = ContradictionAgent()
        self._critic_agent = CriticAgent()

    def run(self, question: str, answer: str, source_docs: List[Document]) -> VerificationPipelineResult:
        evidence = [Evidence.from_document(doc) for doc in source_docs]

        claims = self._citation_agent.verify_claims(answer, evidence)
        claims = self._contradiction_agent.check_claims(claims)

        graph = EvidenceGraph(question=question, claims=claims)
        critique = self._critic_agent.critique(answer, graph)

        local = None
        try:
            from VENUAI import local_faithfulness
            if local_faithfulness.enabled():
                local = local_faithfulness.score_claims(answer, [d.page_content for d in source_docs])
        except Exception:  # noqa: BLE001 - optional signal must never break verification
            local = None
        contradictions = None
        try:
            from VENUAI.contradiction_agent import find_contradictions
            contradictions = find_contradictions(answer, [d for d in source_docs])
        except Exception:  # noqa: BLE001
            contradictions = None

        return VerificationPipelineResult(
            evidence_graph=graph, critique=critique, local_faithfulness=local, contradictions=contradictions,
        )


_pipeline: "VerificationPipeline | None" = None


def get_verification_pipeline() -> VerificationPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = VerificationPipeline()
    return _pipeline
