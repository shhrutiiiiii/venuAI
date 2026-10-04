"""
Real tests for VENUAI/evidence.py, VENUAI/citation_agent.py, and
VENUAI/critic_agent.py — no LLM calls, no network; these are deterministic
by design (see citation_agent.py docstring), so the tests exercise the
actual scoring logic end-to-end.
"""

from langchain_core.documents import Document

from VENUAI.citation_agent import CitationAgent
from VENUAI.critic_agent import CriticAgent
from VENUAI.evidence import Evidence, EvidenceGraph, EvidenceSourceType, SupportStatus


def _evidence(content: str, doc_id="doc-1", filename="a.pdf") -> Evidence:
    return Evidence.from_document(
        Document(page_content=content, metadata={"doc_id": doc_id, "filename": filename, "page": 0}),
        retrieval_method="hybrid",
    )


def test_evidence_from_document_hashes_content_consistently():
    ev1 = _evidence("The Agni-V missile has a range of 5000 km.")
    ev2 = _evidence("The Agni-V missile has a range of 5000 km.")
    assert ev1.content_hash == ev2.content_hash
    assert ev1.evidence_id == ev2.evidence_id


def test_citation_agent_marks_well_supported_claim():
    agent = CitationAgent()
    evidence = [_evidence("The Agni-V missile has an operational range of approximately 5000 kilometers.")]
    claims = agent.verify_claims("The Agni-V missile has a range of 5000 kilometers.", evidence)

    assert len(claims) == 1
    assert claims[0].support_status == SupportStatus.SUPPORTED
    assert len(claims[0].evidence) == 1


def test_citation_agent_marks_unsupported_claim():
    agent = CitationAgent()
    evidence = [_evidence("DRDO's LRDE develops radar systems in Bangalore.")]
    claims = agent.verify_claims("The moon is made of green cheese.", evidence)

    assert len(claims) == 1
    assert claims[0].support_status == SupportStatus.UNSUPPORTED
    assert claims[0].evidence == []


def test_citation_agent_handles_multi_sentence_answer():
    agent = CitationAgent()
    evidence = [
        _evidence("The Agni-V missile has a range of 5000 km.", doc_id="doc-1"),
        _evidence("Pinaka is a multi-barrel rocket launcher system.", doc_id="doc-2"),
    ]
    answer = "The Agni-V missile has a range of 5000 km. Bananas are a good source of potassium."
    claims = agent.verify_claims(answer, evidence)

    assert len(claims) == 2
    statuses = {c.support_status for c in claims}
    assert SupportStatus.SUPPORTED in statuses
    assert SupportStatus.UNSUPPORTED in statuses


def test_evidence_graph_coverage_and_unsupported_properties():
    agent = CitationAgent()
    evidence = [_evidence("The Agni-V missile has a range of 5000 km.")]
    answer = "The Agni-V missile has a range of 5000 km. Elephants can fly using their ears."
    claims = agent.verify_claims(answer, evidence)
    graph = EvidenceGraph(question="What is the range of Agni-V?", claims=claims)

    assert len(graph.unsupported_claims) == 1
    assert graph.evidence_coverage == 0.5


def test_critic_passes_when_most_claims_supported():
    agent = CitationAgent()
    evidence = [_evidence("The Agni-V missile has a range of 5000 km.")]
    answer = "The Agni-V missile has a range of 5000 km."
    claims = agent.verify_claims(answer, evidence)
    graph = EvidenceGraph(question="q", claims=claims)

    result = CriticAgent().critique(answer, graph)
    assert result.passed is True
    assert result.unsupported_claim_count == 0


def test_critic_fails_when_most_claims_unsupported():
    agent = CitationAgent()
    evidence = [_evidence("Completely unrelated content about radar systems.")]
    answer = "Bananas are purple. The sky is made of cheese. Fish can breathe fire."
    claims = agent.verify_claims(answer, evidence)
    graph = EvidenceGraph(question="q", claims=claims)

    result = CriticAgent().critique(answer, graph)
    assert result.passed is False
    assert result.unsupported_claim_count == 3
    assert any(i.severity.value == "high" for i in result.issues)


def test_critic_flags_overclaiming_paired_with_unsupported_claims():
    agent = CitationAgent()
    evidence = [_evidence("Some unrelated evidence about laboratories.")]
    answer = "This is definitely and absolutely a fact about dragons breathing fire."
    claims = agent.verify_claims(answer, evidence)
    graph = EvidenceGraph(question="q", claims=claims)

    result = CriticAgent().critique(answer, graph)
    descriptions = " ".join(i.description for i in result.issues)
    assert "Overconfident language" in descriptions
