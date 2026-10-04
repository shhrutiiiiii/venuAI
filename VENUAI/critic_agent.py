"""
CriticAgent — reviews a generated answer's claims for quality issues,
after CitationAgent has attributed each claim to evidence.

Deterministic, not an LLM call — reuses VerifierAgent's existing
overconfident-phrase list (VENUAI/verifier_agent.py) rather than
redefining a second copy of the same wordlist, per the "don't create
parallel systems" instruction.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List

from VENUAI.evidence import Claim, EvidenceGraph, SupportStatus
from VENUAI.verifier_agent import VerifierAgent

UNSUPPORTED_RATIO_FAIL_THRESHOLD = 0.4  # >40% unsupported claims → critique fails


class IssueSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class CritiqueIssue:
    severity: IssueSeverity
    description: str
    affected_claim_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CritiqueResult:
    passed: bool
    issues: List[CritiqueIssue] = field(default_factory=list)
    unsupported_claim_count: int = 0
    contradicted_claim_count: int = 0
    evidence_coverage: float = 0.0


class CriticAgent:
    def critique(self, answer: str, graph: EvidenceGraph) -> CritiqueResult:
        issues: List[CritiqueIssue] = []

        unsupported = graph.unsupported_claims
        contradicted = graph.contradicted_claims

        if unsupported:
            ratio = len(unsupported) / len(graph.claims) if graph.claims else 0.0
            issues.append(CritiqueIssue(
                severity=IssueSeverity.HIGH if ratio > UNSUPPORTED_RATIO_FAIL_THRESHOLD else IssueSeverity.MEDIUM,
                description=f"{len(unsupported)}/{len(graph.claims)} claims have no supporting evidence.",
                affected_claim_ids=[c.claim_id for c in unsupported],
            ))

        if contradicted:
            issues.append(CritiqueIssue(
                severity=IssueSeverity.HIGH,
                description=f"{len(contradicted)} claim(s) are contradicted by retrieved evidence.",
                affected_claim_ids=[c.claim_id for c in contradicted],
            ))

        answer_lower = answer.lower()
        overclaim_hits = [p for p in VerifierAgent._OVERCONFIDENT_PHRASES if p in answer_lower]
        if overclaim_hits and unsupported:
            # Overclaiming language is only flagged as an issue when it's
            # paired with actually-unsupported claims — confident language
            # backed by real evidence isn't a defect.
            issues.append(CritiqueIssue(
                severity=IssueSeverity.MEDIUM,
                description=f"Overconfident language ({', '.join(overclaim_hits)}) used alongside unsupported claims.",
            ))

        ratio_unsupported = len(unsupported) / len(graph.claims) if graph.claims else 0.0
        passed = ratio_unsupported <= UNSUPPORTED_RATIO_FAIL_THRESHOLD and not contradicted

        return CritiqueResult(
            passed=passed,
            issues=issues,
            unsupported_claim_count=len(unsupported),
            contradicted_claim_count=len(contradicted),
            evidence_coverage=graph.evidence_coverage,
        )
