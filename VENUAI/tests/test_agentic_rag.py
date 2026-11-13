"""
Real tests for VENUAI/agentic_rag.py's adaptive retrieve-evaluate-re-retrieve
loop. Uses a fake vector store and a fake Groq client — no network, no
embedding model — to exercise the actual loop control flow: does it stop
when evidence is sufficient, does it re-retrieve when it isn't, and does it
respect the hard round/time limits rather than looping forever.
"""

from langchain_core.documents import Document

from VENUAI import rag_server
from VENUAI.agentic_rag import AgenticRAG, MAX_RETRIEVAL_ROUNDS


class _FakeRetriever:
    def __init__(self, docs, search_kwargs):
        self._docs, self._kwargs = docs, search_kwargs

    def invoke(self, question):
        allowed = self._kwargs.get("filter", {}).get("doc_id", {}).get("$in")
        if allowed is None:
            return list(self._docs)
        allowed_set = set(allowed)
        return [d for d in self._docs if d.metadata.get("doc_id") in allowed_set]


class _FakeVectorStore:
    def __init__(self, docs):
        self._docs = docs

    def get(self):
        return {"documents": [d.page_content for d in self._docs], "metadatas": [d.metadata for d in self._docs]}

    def as_retriever(self, search_type, search_kwargs):
        return _FakeRetriever(self._docs, search_kwargs)


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeGroqClient:
    def __init__(self, answer_text):
        self.answer_text = answer_text
        self.calls = 0
        outer = self

        class _Completions:
            def create(inner_self, model, messages, max_tokens, temperature, **_kw):
                outer.calls += 1
                return _FakeResponse(outer.answer_text)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _setup(monkeypatch, docs, answer_text):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _FakeVectorStore(docs))
    monkeypatch.setattr("groq.Groq", lambda api_key=None, **_kw: _FakeGroqClient(answer_text))
    monkeypatch.setattr(
        "VENUAI.hallucination_detector.get_hallucination_detector",
        lambda: type("S", (), {"analyze": staticmethod(lambda a, d: (a, 0.0, []))})(),
    )


def test_stops_after_one_round_when_evidence_is_sufficient(monkeypatch):
    docs = [Document(page_content="The Agni-V missile has a range of 5000 km.",
                      metadata={"doc_id": "d1", "filename": "a.pdf", "page": 0})]
    _setup(monkeypatch, docs, "The Agni-V missile has a range of 5000 km.")

    result = AgenticRAG().run("What is the range of the Agni-V missile?", top_k=4, allowed_doc_ids=None)

    assert result.rounds_used == 1
    assert result.loop_limit_reached is False
    assert result.evidence_coverage == 1.0


def test_respects_authorization_filter_during_retrieval(monkeypatch):
    docs = [
        Document(page_content="The Agni-V missile has a range of 5000 km.", metadata={"doc_id": "doc-mine", "filename": "a.pdf", "page": 0}),
        Document(page_content="Some other user's private content.", metadata={"doc_id": "doc-other", "filename": "b.pdf", "page": 0}),
    ]
    _setup(monkeypatch, docs, "The Agni-V missile has a range of 5000 km.")

    result = AgenticRAG().run("range?", top_k=4, allowed_doc_ids=["doc-mine"])
    doc_ids = {d.metadata.get("doc_id") for d in result.documents}
    assert "doc-other" not in doc_ids


def test_never_exceeds_max_retrieval_rounds(monkeypatch):
    """No evidence at all, ever — the loop must still terminate, not hang."""
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _FakeVectorStore([]))
    monkeypatch.setattr("groq.Groq", lambda api_key=None, **_kw: _FakeGroqClient("I don't know."))
    monkeypatch.setattr(
        "VENUAI.hallucination_detector.get_hallucination_detector",
        lambda: type("S", (), {"analyze": staticmethod(lambda a, d: (a, 0.0, []))})(),
    )

    result = AgenticRAG().run("anything", top_k=4, allowed_doc_ids=None)

    assert result.rounds_used <= MAX_RETRIEVAL_ROUNDS
    assert result.loop_limit_reached is True
