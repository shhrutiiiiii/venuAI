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


# ── Cross-chunk contradiction finding (Wave B contract 2.3) ────────────────────────────────────

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")
_FILLER = frozenset(
    "the and for with that this from have has had was were are its their which also been into over "
    "than then they them such more most only other about between while per".split()
)


def _chunk_parts(chunk) -> tuple:
    """(text, doc label) from a Document, dict or plain string."""
    if isinstance(chunk, str):
        return chunk, None
    if isinstance(chunk, dict):
        return str(chunk.get("text") or chunk.get("page_content") or ""), (
            chunk.get("doc") or chunk.get("filename") or chunk.get("doc_id"))
    meta = getattr(chunk, "metadata", None) or {}
    return getattr(chunk, "page_content", ""), (meta.get("filename") or meta.get("doc_id") or meta.get("source"))


def _unit_numbers(sentence: str) -> List[tuple]:
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", sentence)
    out = []
    for m in _NUMBER_WITH_UNIT.finditer(text):
        unit = (m.group(2) or "").lower()
        if not unit:
            continue  # unitless numbers (years, ids, counts) are too ambiguous to call a conflict
        try:
            out.append((float(m.group(1)), unit, m.span()))
        except ValueError:
            continue
    return out


def _subject_tokens(sentence: str) -> set:
    return {w for w in _WORD.findall(sentence.lower()) if w not in _FILLER}


def find_contradictions(answer: str, chunks: list, max_results: int = 5) -> List[dict]:
    """
    Cross-source numeric contradictions among the evidence chunks that the answer relies on.

    Two sentences from DIFFERENT chunks/documents conflict when they share a subject (3+ content
    words, and at least 2 of those also appear in the answer) and report the same unit with values
    differing by >= 15%. Deterministic, no LLM, no network. Returns
    ``[{"claim_a","claim_b","doc_a","doc_b"}]`` (empty when nothing is found - never invented).
    """
    answer_tokens = _subject_tokens(answer or "")
    sents = []  # (chunk_index, doc_label, sentence, tokens, numbers)
    for ci, chunk in enumerate(chunks or []):
        text, doc = _chunk_parts(chunk)
        for s in _SENT.split(text or ""):
            s = s.strip()
            if len(s) < 15:
                continue
            nums = _unit_numbers(s)
            if nums:
                sents.append((ci, doc or f"chunk-{ci + 1}", s, _subject_tokens(s), nums))
    found: List[dict] = []
    seen = set()
    for i in range(len(sents)):
        ci, da, sa, ta, na = sents[i]
        for j in range(i + 1, len(sents)):
            cj, db, sb, tb, nb = sents[j]
            if ci == cj:
                continue
            shared = ta & tb
            if len(shared) < 3 or len(shared & answer_tokens) < 2:
                continue
            for va, ua, _ in na:
                for vb, ub, _ in nb:
                    if ua != ub or max(abs(va), abs(vb)) == 0:
                        continue
                    if abs(va - vb) / max(abs(va), abs(vb)) >= RELATIVE_DIFFERENCE_THRESHOLD:
                        key = (sa, sb)
                        if key not in seen:
                            seen.add(key)
                            found.append({"claim_a": sa[:300], "claim_b": sb[:300],
                                          "doc_a": str(da), "doc_b": str(db)})
                        break
                else:
                    continue
                break
            if len(found) >= max_results:
                return found
    return found
