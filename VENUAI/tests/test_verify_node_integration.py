"""
Integration test: VENUAI.rag_server.verify_node actually runs the new
VerificationPipeline (citation + contradiction + critic) in addition to the
existing VerifierAgent/HallucinationDetector checks, and the new fields
flow all the way through to the returned state dict — no network calls
(fake Groq client), no real embedding model needed (this node doesn't
retrieve, it only verifies an already-generated answer).
"""

from langchain_core.documents import Document

from VENUAI import rag_server


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeGroqClient:
    def __init__(self, response_text="YES"):
        self.response_text = response_text

        class _Completions:
            def create(inner_self, model, messages, max_tokens, temperature):
                return _FakeResponse(response_text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class _StubHallucinationDetector:
    """Stands in for the real HallucinationDetector, which lazily loads a
    sentence-transformers CrossEncoder model (a real, heavy, network-fetched
    dependency unrelated to what this test is verifying — the new
    claim-level fields' wiring through verify_node, not the pre-existing
    hallucination-score mechanism)."""

    def analyze(self, answer, docs):
        return answer, 0.0, []


def test_verify_node_returns_claim_level_fields(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    # verify_node constructs its own Groq() client internally — patch the
    # class so it returns our fake instead of hitting the network.
    monkeypatch.setattr("groq.Groq", lambda api_key: _FakeGroqClient())
    monkeypatch.setattr("VENUAI.hallucination_detector.get_hallucination_detector", lambda: _StubHallucinationDetector())

    docs = [
        Document(page_content="The Agni-V missile has a range of 5000 km.",
                 metadata={"doc_id": "d1", "filename": "a.pdf", "page": 0}),
    ]
    state = {
        "question": "What is the range of the Agni-V missile?",
        "answer": "The Agni-V missile has a range of 5000 km.",
        "documents": docs,
    }

    result = rag_server.verify_node(state)

    assert "evidence_coverage" in result
    assert "unsupported_claim_count" in result
    assert "contradicted_claim_count" in result
    assert "critique_passed" in result
    assert result["evidence_coverage"] == 1.0
    assert result["critique_passed"] is True


def test_verify_node_flags_unsupported_claims(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setattr("groq.Groq", lambda api_key: _FakeGroqClient())
    monkeypatch.setattr("VENUAI.hallucination_detector.get_hallucination_detector", lambda: _StubHallucinationDetector())

    docs = [
        Document(page_content="DRDO's LRDE develops radar systems.",
                 metadata={"doc_id": "d1", "filename": "a.pdf", "page": 0}),
    ]
    state = {
        "question": "What color is the moon?",
        "answer": "The moon is made of green cheese and tastes like strawberries.",
        "documents": docs,
    }

    result = rag_server.verify_node(state)
    assert result["unsupported_claim_count"] >= 1
    assert result["critique_passed"] is False
