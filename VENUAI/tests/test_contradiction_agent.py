"""Real tests for VENUAI/contradiction_agent.py — pure numeric-comparison
logic, no LLM/network involved."""

from langchain_core.documents import Document

from VENUAI.contradiction_agent import ContradictionAgent
from VENUAI.evidence import Claim, Evidence, SupportStatus


def _evidence(content: str, doc_id: str) -> Evidence:
    return Evidence.from_document(
        Document(page_content=content, metadata={"doc_id": doc_id, "filename": f"{doc_id}.pdf", "page": 0})
    )


def test_detects_numeric_contradiction_between_two_sources():
    ev_a = _evidence("The missile has a range of 5000 km.", "doc-a")
    ev_b = _evidence("The missile has a range of 3200 km.", "doc-b")
    claim = Claim(claim_id="c1", text="The missile has a range of 5000 km.", evidence=[ev_a, ev_b],
                  support_status=SupportStatus.SUPPORTED, support_score=0.9)

    report = ContradictionAgent().check_claim(claim)
    assert report is not None
    assert report.fact_a.unit == "km"
    assert report.relative_difference >= 0.15


def test_no_contradiction_for_consistent_numbers():
    ev_a = _evidence("The missile has a range of 5000 km.", "doc-a")
    ev_b = _evidence("The missile's range is approximately 5050 km.", "doc-b")
    claim = Claim(claim_id="c1", text="The missile has a range of ~5000 km.", evidence=[ev_a, ev_b],
                  support_status=SupportStatus.SUPPORTED, support_score=0.9)

    report = ContradictionAgent().check_claim(claim)
    assert report is None  # within RELATIVE_DIFFERENCE_THRESHOLD


def test_no_contradiction_with_mismatched_units():
    ev_a = _evidence("The system weighs 500 kg.", "doc-a")
    ev_b = _evidence("The system has a range of 500 km.", "doc-b")
    claim = Claim(claim_id="c1", text="irrelevant", evidence=[ev_a, ev_b],
                  support_status=SupportStatus.SUPPORTED, support_score=0.9)

    report = ContradictionAgent().check_claim(claim)
    assert report is None  # kg vs km — not comparable, must not false-positive


def test_single_evidence_item_cannot_contradict_itself():
    ev_a = _evidence("The missile has a range of 5000 km.", "doc-a")
    claim = Claim(claim_id="c1", text="irrelevant", evidence=[ev_a],
                  support_status=SupportStatus.SUPPORTED, support_score=0.9)

    assert ContradictionAgent().check_claim(claim) is None


def test_check_claims_upgrades_contradicted_claim_status():
    ev_a = _evidence("The missile has a range of 5000 km.", "doc-a")
    ev_b = _evidence("The missile has a range of 1000 km.", "doc-b")
    claim = Claim(claim_id="c1", text="irrelevant", evidence=[ev_a, ev_b],
                  support_status=SupportStatus.SUPPORTED, support_score=0.9)

    updated = ContradictionAgent().check_claims([claim])
    assert updated[0].support_status == SupportStatus.CONTRADICTED
    # Original claim object must remain unmutated (frozen dataclass semantics).
    assert claim.support_status == SupportStatus.SUPPORTED
