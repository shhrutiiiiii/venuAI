"""
ContradictionAgent — detects when two evidence items attributed to the same
claim report materially different numeric facts (range, weight, date, cost,
etc.) — the real, checkable instance of "contradiction" spec section 24
asks for, as opposed to declaring one source correct by LLM preference
(which the spec explicitly forbids).

Deterministic: extracts numbers (with their nearby unit, if any) from each
evidence snippet and compares them. This catches the common, concrete case
("Source A says 5000 km, Source B says 3500 km") without needing an LLM
call or inventing a semantic-entailment model that doesn't exist in this
codebase. Qualitative/definitional contradictions are out of scope for this
implementation — see Documents/AI/contradiction-detection.md "Not Yet
Implemented".
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from VENUAI.evidence import Claim, Evidence, SupportStatus

# Numbers optionally followed by a unit word/abbreviation — real units
# relevant to the DRDO defence-research domain, not an exhaustive NLP model.
_NUMBER_WITH_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(km|kilometers?|kilometres?|kg|kilograms?|tons?|tonnes?|"
    r"mach|kmh|km/h|m/s|mm|cm|m|meters?|metres?|crore|million|billion|years?|"
    r"seconds?|minutes?|hours?|%|percent)?",
    re.IGNORECASE,
)

RELATIVE_DIFFERENCE_THRESHOLD = 0.15  # >15% difference between two numbers for
# the "same" fact is treated as a real conflict, not measurement noise.


@dataclass(frozen=True)
class NumericFact:
    value: float
    unit: str
    evidence_id: str


@dataclass(frozen=True)
class ContradictionReport:
    claim_id: str
    fact_a: NumericFact
    fact_b: NumericFact
    relative_difference: float
    conflict_type: str = "numeric_conflict"


def _extract_numeric_facts(evidence: Evidence) -> List[NumericFact]:
    facts = []
    for match in _NUMBER_WITH_UNIT.finditer(evidence.content):
        value_str, unit = match.groups()
        unit = (unit or "unitless").lower()
        try:
            facts.append(NumericFact(value=float(value_str), unit=unit, evidence_id=evidence.evidence_id))
        except ValueError:
            continue
    return facts


class ContradictionAgent:
    def check_claim(self, claim: Claim) -> Optional[ContradictionReport]:
        """
        Compares numeric facts across the claim's attributed evidence items
        (there must be 2+ for a contradiction to even be checkable — a claim
        backed by only one source has nothing to contradict it with).
        """
        if len(claim.evidence) < 2:
            return None

        facts_by_evidence = [(ev, _extract_numeric_facts(ev)) for ev in claim.evidence]

        for i in range(len(facts_by_evidence)):
            ev_a, facts_a = facts_by_evidence[i]
            for j in range(i + 1, len(facts_by_evidence)):
                ev_b, facts_b = facts_by_evidence[j]
                for fa in facts_a:
                    for fb in facts_b:
                        if fa.unit != fb.unit:
                            continue  # different units — not comparable without conversion
                        if fa.value == 0 and fb.value == 0:
                            continue
                        larger = max(abs(fa.value), abs(fb.value))
                        if larger == 0:
                            continue
                        rel_diff = abs(fa.value - fb.value) / larger
                        if rel_diff >= RELATIVE_DIFFERENCE_THRESHOLD:
                            return ContradictionReport(
                                claim_id=claim.claim_id,
                                fact_a=fa,
                                fact_b=fb,
                                relative_difference=round(rel_diff, 3),
                            )
        return None

    def check_claims(self, claims: List[Claim]) -> List[Claim]:
        """Returns a new list of Claims, upgrading any claim with a detected
        numeric contradiction to SupportStatus.CONTRADICTED. Does not mutate
        the input — Claim is frozen/immutable by design."""
        updated = []
        for claim in claims:
            report = self.check_claim(claim)
            if report is not None:
                updated.append(Claim(
                    claim_id=claim.claim_id,
                    text=claim.text,
                    evidence=claim.evidence,
                    support_status=SupportStatus.CONTRADICTED,
                    support_score=claim.support_score,
                ))
            else:
                updated.append(claim)
        return updated
