"""
Real tests for VENUAI/research_planner.py and
VENUAI/agents/deep_research_agent.py — fake Groq client and fake vector
store, no network/embeddings, exercising actual control flow: planning,
fallback-on-failure, per-subquestion retrieval, and synthesis.
"""

from langchain_core.documents import Document

from VENUAI import rag_server
from VENUAI.agents.deep_research_agent import DeepResearchAgent
from VENUAI.research_planner import MAX_SUBQUESTIONS, ResearchPlannerAgent


class _FakeChoice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeGroqClient:
    def __init__(self, responses):
        """responses: a list of strings returned in order, one per call;
        the last one repeats once exhausted."""
        self._responses = responses
        self.calls = 0
        outer = self

        class _Completions:
            def create(inner_self, model, messages, max_tokens, temperature):
                idx = min(outer.calls, len(outer._responses) - 1)
                outer.calls += 1
                return _FakeResponse(outer._responses[idx])

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class _FakeRetriever:
    def __init__(self, docs, search_kwargs):
        self._docs, self._kwargs = docs, search_kwargs

    def invoke(self, question):
        allowed = self._kwargs.get("filter", {}).get("doc_id", {}).get("$in")
        if allowed is None:
            return list(self._docs)
        return [d for d in self._docs if d.metadata.get("doc_id") in set(allowed)]


class _FakeVectorStore:
    def __init__(self, docs):
        self._docs = docs

    def get(self):
        return {"documents": [d.page_content for d in self._docs], "metadatas": [d.metadata for d in self._docs]}

    def as_retriever(self, search_type, search_kwargs):
        return _FakeRetriever(self._docs, search_kwargs)


def _stub_hallucination_detector(monkeypatch):
    monkeypatch.setattr(
        "VENUAI.hallucination_detector.get_hallucination_detector",
        lambda: type("S", (), {"analyze": staticmethod(lambda a, d: (a, 0.0, []))})(),
    )


# ── ResearchPlannerAgent ──────────────────────────────────────────────────────

def test_planner_parses_numbered_list(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    client = _FakeGroqClient(["1. What is the range?\n2. What is the payload?\n3. Who developed it?"])

    plan = ResearchPlannerAgent().plan("Tell me about the Agni-V missile.", client)

    assert plan.is_fallback is False
    assert len(plan.subquestions) == 3
    assert plan.subquestions[0] == "What is the range?"


def test_planner_respects_max_subquestions_cap(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    lines = "\n".join(f"{i}. Subquestion {i}" for i in range(1, 10))
    client = _FakeGroqClient([lines])

    plan = ResearchPlannerAgent().plan("broad question", client, max_subquestions=5)
    assert len(plan.subquestions) <= 5


def test_planner_falls_back_on_unparseable_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    client = _FakeGroqClient([""])  # empty response — nothing to parse

    plan = ResearchPlannerAgent().plan("some question", client)
    assert plan.is_fallback is True
    assert plan.subquestions == ["some question"]


def test_planner_falls_back_on_model_failure(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")

    class _BrokenClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("simulated failure")

    plan = ResearchPlannerAgent().plan("some question", _BrokenClient())
    assert plan.is_fallback is True
    assert plan.subquestions == ["some question"]


# ── DeepResearchAgent ──────────────────────────────────────────────────────────

def test_deep_research_single_subquestion_skips_synthesis_call(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _stub_hallucination_detector(monkeypatch)

    docs = [Document(page_content="The Agni-V missile has a range of 5000 km.",
                      metadata={"doc_id": "d1", "filename": "a.pdf", "page": 0})]
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _FakeVectorStore(docs))

    # First call = planner (empty → fallback to single subquestion).
    # Second call = AgenticRAG's generation call.
    client = _FakeGroqClient(["", "The Agni-V missile has a range of 5000 km."])
    monkeypatch.setattr("groq.Groq", lambda api_key: client)

    result = DeepResearchAgent().research("What is the range of the Agni-V missile?", client)

    assert result.plan_is_fallback is True
    assert len(result.subquestion_results) == 1
    assert result.synthesized_answer == "The Agni-V missile has a range of 5000 km."
    assert result.truncated is False


def test_deep_research_multi_subquestion_runs_synthesis(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _stub_hallucination_detector(monkeypatch)

    docs = [
        Document(page_content="The Agni-V missile has a range of 5000 km.", metadata={"doc_id": "d1", "filename": "a.pdf", "page": 0}),
        Document(page_content="The Agni-V missile was developed by DRDL.", metadata={"doc_id": "d2", "filename": "b.pdf", "page": 0}),
    ]
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _FakeVectorStore(docs))

    client = _FakeGroqClient([
        "1. What is the range?\n2. Who developed it?",   # planner
        "The range is 5000 km.",                          # subq 1 generation
        "It was developed by DRDL.",                       # subq 2 generation
        "The Agni-V has a range of 5000 km and was developed by DRDL.",  # synthesis
    ])
    monkeypatch.setattr("groq.Groq", lambda api_key: client)

    result = DeepResearchAgent().research("Tell me about the Agni-V.", client)

    assert result.plan_is_fallback is False
    assert len(result.subquestion_results) == 2
    assert "DRDL" in result.synthesized_answer


def test_deep_research_respects_authorization(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    _stub_hallucination_detector(monkeypatch)

    docs = [
        Document(page_content="My own document content.", metadata={"doc_id": "doc-mine", "filename": "a.pdf", "page": 0}),
        Document(page_content="Someone else's private content.", metadata={"doc_id": "doc-other", "filename": "b.pdf", "page": 0}),
    ]
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _FakeVectorStore(docs))
    client = _FakeGroqClient(["", "My own document content."])
    monkeypatch.setattr("groq.Groq", lambda api_key: client)

    result = DeepResearchAgent().research("question", client, allowed_doc_ids=["doc-mine"])

    all_filenames = " ".join(r.answer for r in result.subquestion_results) + result.synthesized_answer
    assert "private" not in all_filenames
