"""
Real tests for VENUAI/agents/base_agent.py:
  1. A pre-existing bug: `os` and `json` were used but never imported in
     base_agent.py — BaseSpecialistAgent.query() raised NameError the moment
     it was actually invoked (the fallback-model list referenced `os.getenv`,
     and the empty-retrieval fallback referenced `json.dumps`). Multi-agent
     mode was therefore broken in practice. Fixed by adding the imports.
  2. The same retrieval-authorization filtering added to retrieve_node() is
     now also applied to BaseSpecialistAgent.query(), since specialist agents
     retrieve directly from the vector store too (multi-agent mode was not
     actually exempt from the cross-tenant retrieval gap).

No network calls: a fake Groq client and a fake vector store stand in for
the real ones, so this tests the actual control flow without hitting Groq
or an embedding model.
"""

from langchain_core.documents import Document

from VENUAI.agents.base_agent import AgentResult, BaseSpecialistAgent


class _FakeRetriever:
    def __init__(self, docs, search_kwargs):
        self._docs = docs
        self._search_kwargs = search_kwargs

    def invoke(self, question):
        where = self._search_kwargs.get("filter")
        if not where:
            return list(self._docs)

        def _matches(meta, clause):
            if "$and" in clause:
                return all(_matches(meta, c) for c in clause["$and"])
            (field, cond), = clause.items()
            if "$in" in cond:
                return meta.get(field) in cond["$in"]
            return meta.get(field) == cond

        return [d for d in self._docs if _matches(d.metadata, where)]


class _FakeVectorStore:
    def __init__(self, docs):
        self._docs = docs

    def as_retriever(self, search_type, search_kwargs):
        return _FakeRetriever(self._docs, search_kwargs)


class _FakeChatCompletions:
    def create(self, model, messages, max_tokens, temperature):
        class _Choice:
            class message:
                content = "fake answer"
        class _Resp:
            choices = [_Choice()]
        return _Resp()


class _FakeGroqClient:
    class chat:
        completions = _FakeChatCompletions()


def _sample_docs():
    return [
        Document(page_content="alpha", metadata={"doc_id": "doc-mine", "filename": "a.pdf", "page": 0}),
        Document(page_content="beta", metadata={"doc_id": "doc-other", "filename": "b.pdf", "page": 0}),
    ]


def test_base_specialist_agent_query_does_not_raise_nameerror(monkeypatch):
    """Regression test for the pre-existing missing-import bug."""
    import VENUAI.rag_server as rag_server
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    agent = BaseSpecialistAgent(groq_client=_FakeGroqClient())
    result = agent.query("what is the range of system X?", history=None)

    assert isinstance(result, AgentResult)
    assert result.answer == "fake answer"


def test_base_specialist_agent_filters_retrieval_by_allowed_doc_ids(monkeypatch):
    import VENUAI.rag_server as rag_server
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    agent = BaseSpecialistAgent(groq_client=_FakeGroqClient())
    result = agent.query("anything", history=None, allowed_doc_ids=["doc-mine"])

    seen_filenames = {s.document for s in result.sources}
    assert seen_filenames == {"a.pdf"}
    assert "b.pdf" not in seen_filenames


def test_base_specialist_agent_admin_sees_all(monkeypatch):
    import VENUAI.rag_server as rag_server
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    agent = BaseSpecialistAgent(groq_client=_FakeGroqClient())
    result = agent.query("anything", history=None, allowed_doc_ids=None)

    seen_filenames = {s.document for s in result.sources}
    assert seen_filenames == {"a.pdf", "b.pdf"}
