"""
Real end-to-end tests for the Phase 4 Evidence Explorer: standard RAG
(POST /query) and Deep Research (POST /research) answers now persist their
claim-by-claim verification breakdown (VENUAI/evidence_store.py), browsable
via GET /evidence and GET /evidence/{id}. See Documents/AI/evidence-explorer.md.

Uses a fake VerificationPipeline (monkeypatched at its module-level
singleton accessor) to produce controlled Claim/Evidence objects, rather
than depending on the real citation-agent token-matching producing a
specific support_status for a specific fake answer/document pair — that
matching logic is already covered by VENUAI/tests/test_citation_agent.py
and friends; this test is about the persistence/retrieval plumbing.
"""

import pytest

from VENUAI import rag_server
from VENUAI.evidence import Claim, Evidence, EvidenceGraph, EvidenceModality, EvidenceSourceType, SupportStatus
from VENUAI.critic_agent import CritiqueResult
from VENUAI.verification_pipeline import VerificationPipelineResult


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeGroqClient:
    def __init__(self, answer_text="A fake answer."):
        class _Completions:
            def create(inner_self, model, messages, max_tokens, temperature):
                return _FakeResponse(answer_text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class _FakeLLMResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """Stands in for VENUAI.rag_server.get_llm()'s real ChatGroq instance —
    generate_node only ever calls .invoke(prompt).content. Avoids
    constructing a real ChatGroq (which builds its own internal groq.Groq
    client with extra kwargs like base_url that a simple
    monkeypatch.setattr("groq.Groq", ...) lambda can't accept)."""

    def __init__(self, answer_text):
        self._answer_text = answer_text

    def invoke(self, prompt):
        return _FakeLLMResponse(self._answer_text)


class _EmptyFakeVectorStore:
    def get(self):
        return {"documents": [], "metadatas": []}

    def as_retriever(self, search_type, search_kwargs):
        class _R:
            def invoke(self, q):
                return []
        return _R()


def _fake_evidence_graph(question: str) -> EvidenceGraph:
    evidence = Evidence(
        evidence_id="ev-1", document_id="doc-1", document_name="report.pdf",
        page_number=3, content="The Agni-V has a range of 5000 km.",
        source_type=EvidenceSourceType.UPLOADED_DOCUMENT, modality=EvidenceModality.TEXT,
    )
    claim = Claim(
        claim_id="claim-1", text="The Agni-V has a range of 5000 km.",
        evidence=[evidence], support_status=SupportStatus.SUPPORTED, support_score=0.9,
    )
    return EvidenceGraph(question=question, claims=[claim])


class _FakeVerificationPipeline:
    def run(self, question, answer, docs):
        graph = _fake_evidence_graph(question)
        return VerificationPipelineResult(
            evidence_graph=graph,
            critique=CritiqueResult(passed=True, unsupported_claim_count=0, contradicted_claim_count=0,
                                     evidence_coverage=graph.evidence_coverage),
        )


@pytest.fixture()
def auth_headers(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_evidence_requires_auth(client):
    r = client.get("/evidence")
    assert r.status_code == 401


def test_query_persists_an_evidence_record(client, auth_headers, monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyFakeVectorStore())
    monkeypatch.setattr(rag_server, "get_llm", lambda: _FakeLLM("The Agni-V has a range of 5000 km."))
    monkeypatch.setattr("groq.Groq", lambda api_key: _FakeGroqClient("The Agni-V has a range of 5000 km."))
    monkeypatch.setattr(
        "VENUAI.verification_pipeline.get_verification_pipeline", lambda: _FakeVerificationPipeline(),
    )
    monkeypatch.setattr(
        "VENUAI.hallucination_detector.get_hallucination_detector",
        lambda: type("S", (), {"analyze": staticmethod(lambda a, d: (a, 0.0, []))})(),
    )
    # route_query_node picks "retrieve" only if load_metadata() is non-empty.
    monkeypatch.setattr(rag_server, "load_metadata", lambda: {"doc-1": {"id": "doc-1"}})

    r = client.post("/query", json={"question": "What is the range of the Agni-V?"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["evidence_coverage"] == 1.0

    listed = client.get("/evidence", headers=auth_headers)
    assert listed.status_code == 200
    records = listed.json()
    assert len(records) == 1
    assert records[0]["source"] == "query"
    assert records[0]["evidence_coverage"] == 1.0

    detail = client.get(f"/evidence/{records[0]['record_id']}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["claims"]) == 1
    assert body["claims"][0]["support_status"] == "supported"
    assert body["claims"][0]["evidence"][0]["document_name"] == "report.pdf"


def test_other_user_cannot_see_your_evidence_records(client, auth_headers, monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyFakeVectorStore())
    monkeypatch.setattr(rag_server, "get_llm", lambda: _FakeLLM("answer"))
    monkeypatch.setattr("groq.Groq", lambda api_key: _FakeGroqClient("answer"))
    monkeypatch.setattr(
        "VENUAI.verification_pipeline.get_verification_pipeline", lambda: _FakeVerificationPipeline(),
    )
    monkeypatch.setattr(
        "VENUAI.hallucination_detector.get_hallucination_detector",
        lambda: type("S", (), {"analyze": staticmethod(lambda a, d: (a, 0.0, []))})(),
    )
    monkeypatch.setattr(rag_server, "load_metadata", lambda: {"doc-1": {"id": "doc-1"}})

    r = client.post("/query", json={"question": "anything"}, headers=auth_headers)
    record_id = client.get("/evidence", headers=auth_headers).json()[0]["record_id"]

    client.post("/auth/register", json={"email": "other-evidence@test.com", "password": "supersecret123"})
    login2 = client.post("/auth/login", json={"email": "other-evidence@test.com", "password": "supersecret123"})
    headers2 = {"Authorization": f"Bearer {login2.json()['access_token']}"}

    assert client.get("/evidence", headers=headers2).json() == []
    assert client.get(f"/evidence/{record_id}", headers=headers2).status_code == 404


def test_research_persists_an_evidence_record(client, auth_headers, monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyFakeVectorStore())
    monkeypatch.setattr("groq.Groq", lambda api_key: _FakeGroqClient("A fake research answer."))
    monkeypatch.setattr(
        "VENUAI.hallucination_detector.get_hallucination_detector",
        lambda: type("S", (), {"analyze": staticmethod(lambda a, d: (a, 0.0, []))})(),
    )

    monkeypatch.setattr(
        "VENUAI.verification_pipeline.get_verification_pipeline", lambda: _FakeVerificationPipeline(),
    )

    r = client.post("/research", json={"question": "What is the range of Agni-V?"}, headers=auth_headers)
    assert r.status_code == 200

    listed = client.get("/evidence", headers=auth_headers)
    records = [rec for rec in listed.json() if rec["source"] == "research"]
    assert len(records) == 1
    assert records[0]["evidence_coverage"] == 1.0
