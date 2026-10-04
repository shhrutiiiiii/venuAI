"""
Phase 5 — ReAct mode and multi-agent mode now also run the claim-level
VerificationPipeline and persist to the Evidence Explorer
(VENUAI/evidence_store.py), using the real Document objects each mode's
retrieval actually produced (previously only standard RAG and Deep
Research did this — see Documents/AI/evidence-explorer.md).
"""

import pytest
from langchain_core.documents import Document

from VENUAI import rag_server
from VENUAI.evidence import Claim, Evidence, EvidenceGraph, EvidenceModality, EvidenceSourceType, SupportStatus
from VENUAI.critic_agent import CritiqueResult
from VENUAI.verification_pipeline import VerificationPipelineResult


def _fake_evidence_graph(question: str) -> EvidenceGraph:
    evidence = Evidence(
        evidence_id="ev-1", document_id="doc-1", document_name="report.pdf",
        page_number=1, content="The Agni-V has a range of 5000 km.",
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


# ── Multi-agent mode ─────────────────────────────────────────────────────────

def test_multi_agent_persists_an_evidence_record(client, auth_headers, monkeypatch):
    import asyncio
    from VENUAI.agents.base_agent import AgentResult

    fake_result = AgentResult(
        agent_name="GeneralAgent", answer="The Agni-V has a range of 5000 km.",
        sources=[], confidence=0.8,
        documents=[Document(page_content="The Agni-V has a range of 5000 km.",
                             metadata={"filename": "report.pdf", "page": 0})],
    )

    async def _fake_route(self, question, history, memory_ctx, allowed_doc_ids=None):
        return fake_result

    monkeypatch.setattr("VENUAI.agents.orchestrator.OrchestratorAgent.route", _fake_route)
    monkeypatch.setattr(
        "VENUAI.verification_pipeline.get_verification_pipeline", lambda: _FakeVerificationPipeline(),
    )

    r = client.post(
        "/query", json={"question": "range of Agni-V?", "multi_agent_mode": True}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["evidence_coverage"] == 1.0

    records = [rec for rec in client.get("/evidence", headers=auth_headers).json() if rec["source"] == "multi_agent"]
    assert len(records) == 1
    assert records[0]["evidence_coverage"] == 1.0


def test_multi_agent_with_no_documents_reports_honest_zero_coverage(client, auth_headers, monkeypatch):
    """
    No documents retrieved → VerificationPipeline still runs (on an empty
    evidence list) and the record is still saved — "nothing was
    verifiable for this answer" is itself a real fact worth persisting
    (same design decision as the KB-fallback path for standard RAG — see
    Documents/AI/evidence-explorer.md Design Principle 1), not a reason to
    skip. The only thing that must never happen is a fabricated non-zero
    coverage score.
    """
    from VENUAI.agents.base_agent import AgentResult

    fake_result = AgentResult(agent_name="GeneralAgent", answer="I don't know.", sources=[], confidence=0.1, documents=[])

    async def _fake_route(self, question, history, memory_ctx, allowed_doc_ids=None):
        return fake_result

    monkeypatch.setattr("VENUAI.agents.orchestrator.OrchestratorAgent.route", _fake_route)

    r = client.post(
        "/query", json={"question": "anything unanswerable", "multi_agent_mode": True}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["evidence_coverage"] == 0.0

    records = [rec for rec in client.get("/evidence", headers=auth_headers).json() if rec["source"] == "multi_agent"]
    assert len(records) == 1
    assert records[0]["evidence_coverage"] == 0.0


# ── ReAct agent mode ─────────────────────────────────────────────────────────

def test_react_agent_persists_an_evidence_record_and_real_sources(client, auth_headers, monkeypatch):
    from langchain_core.messages import AIMessage

    def _fake_create_react_agent(llm, tools):
        search_tool = next(t for t in tools if t.name == "drdo_kb_search")

        class _FakeReactAgent:
            def invoke(self, inputs, config=None):
                # Actually calling the real tool populates collected_docs —
                # the mechanism under test, not a shortcut around it.
                search_tool.invoke({"query": "range of Agni-V", "top_k": 5})
                return {"messages": [AIMessage(content="The Agni-V has a range of 5000 km.")]}

        return _FakeReactAgent()

    monkeypatch.setattr("langgraph.prebuilt.create_react_agent", _fake_create_react_agent)
    monkeypatch.setattr("langchain_groq.ChatGroq", lambda **kwargs: object())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _TextHitVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", lambda: {})
    monkeypatch.setattr(
        "VENUAI.verification_pipeline.get_verification_pipeline", lambda: _FakeVerificationPipeline(),
    )

    r = client.post(
        "/query", json={"question": "range of Agni-V?", "agent_mode": True}, headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["evidence_coverage"] == 1.0
    assert body["chunks_retrieved"] == 1
    assert any("report.pdf" == s["document"] for s in body["sources"])

    records = [rec for rec in client.get("/evidence", headers=auth_headers).json() if rec["source"] == "react_agent"]
    assert len(records) == 1


class _TextHitVectorStore:
    def as_retriever(self, search_type, search_kwargs):
        class _R:
            def invoke(self, query):
                return [Document(page_content="The Agni-V has a range of 5000 km.",
                                  metadata={"filename": "report.pdf", "page": 0, "doc_id": "doc-1"})]
        return _R()
