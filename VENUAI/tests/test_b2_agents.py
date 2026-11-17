"""B2: specialist agents use shared retrieval, cite per-entry KB docs on fallback, abstain honestly."""
from langchain_core.documents import Document

from VENUAI import retrieval, reranker
from VENUAI.agents.base_agent import BaseSpecialistAgent
from VENUAI.retrieval import RetrievalResult


class _Gw:
    prompts = []

    def complete(self, task_type, messages, max_tokens, temperature):
        _Gw.prompts.append(messages[0]["content"])

        class R:
            content = "answer"
        return R()


def _patch(monkeypatch, docs):
    seen = {}

    def fake_retrieve(vs, question, top_k, allowed, history=None, rewrite=True, **kw):
        seen.update(allowed=allowed, rewrite=rewrite, top_k=top_k)
        return RetrievalResult(documents=list(docs))
    monkeypatch.setattr(retrieval, "retrieve_documents", fake_retrieve)
    monkeypatch.setattr(retrieval, "get_vectorstore", lambda: object(), raising=False)
    monkeypatch.setattr("VENUAI.ai_gateway.gateway.get_gateway", lambda c=None: _Gw())
    _Gw.prompts.clear()
    return seen


def test_agent_uses_shared_retrieval_with_acl(monkeypatch):
    d = Document(page_content="alpha text", metadata={"filename": "a.pdf", "page": 0, "doc_id": "d1"})
    seen = _patch(monkeypatch, [d])
    r = BaseSpecialistAgent(groq_client=None).query("q", allowed_doc_ids=["d1"])
    assert seen["allowed"] == ["d1"] and seen["rewrite"] is False
    assert r.documents == [d] and r.sources[0].document == "a.pdf"


def test_agent_kb_fallback_cites_specific_entry(monkeypatch):
    _patch(monkeypatch, [])
    r = BaseSpecialistAgent(groq_client=None).query("What is the range of Agni-V?")
    assert r.documents and r.documents[0].metadata["title"] == "Agni-V"
    assert r.documents[0].metadata["kb_fallback"] is True
    assert "built-in knowledge base" in _Gw.prompts[0]
    assert "Agni-V" in r.sources[0].document


def test_agent_abstains_without_llm_call(monkeypatch):
    _patch(monkeypatch, [])
    monkeypatch.setattr("VENUAI.kb_documents.kb_fallback_documents", lambda *a, **k: [])
    r = BaseSpecialistAgent(groq_client=None).query("zzz")
    assert "will not guess" in r.answer and r.sources == [] and not _Gw.prompts
