"""
Phase 5 — the multimodal-region fold-in (VENUAI.multimodal_retrieval,
introduced in the Phase 4 gap-closing pass for standard RAG and Agentic
RAG only) now also reaches the three retrieval paths that were still
missing it: the ReAct agent's drdo_kb_search tool, multi-agent specialist
retrieval (BaseSpecialistAgent.query), and /query/stream. See
Documents/AI/multimodal.md "Integration with the default RAG path".
"""

import json

import pytest
from langchain_core.documents import Document

from VENUAI import agent_tools, rag_server
from VENUAI.agents.base_agent import BaseSpecialistAgent


def _multimodal_metadata():
    return {
        "doc-table": {
            "is_multimodal": True,
            "owner_id": "user-1",
            "tenant_id": "default",
            # PUBLIC so the real-HTTP streaming test (which computes
            # allowed_doc_ids from the authenticated demo user's actual
            # ownership, not a hand-picked allowed_doc_ids like the ReAct
            # tool/specialist-agent tests below) can see it too.
            "classification": "PUBLIC",
            "multimodal_summary": {
                "pages": [{
                    "page_number": 2,
                    "regions": [{
                        "region_id": "r1",
                        "region_type": "table",
                        "extracted_text": "Parameter | Value\nRange | 5000 km",
                        "bounding_box": None,
                        "content_hash": "h1",
                    }],
                }],
            },
        },
    }


class _EmptyFakeVectorStore:
    def get(self):
        return {"documents": [], "metadatas": []}

    def as_retriever(self, search_type, search_kwargs):
        class _R:
            def invoke(self, q):
                return []
        return _R()


# ── ReAct agent's drdo_kb_search tool ────────────────────────────────────────

def test_react_tool_folds_in_multimodal_region_hit(monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyFakeVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _multimodal_metadata)

    tools = agent_tools.build_agent_tools(user_id="user-1", allowed_doc_ids=None)
    search_tool = next(t for t in tools if t.name == "drdo_kb_search")

    result = search_tool.invoke({"query": "range parameter", "top_k": 5})
    assert "5000 km" in result


def test_react_tool_region_fold_in_respects_allowed_doc_ids(monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyFakeVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _multimodal_metadata)

    tools = agent_tools.build_agent_tools(user_id="user-2", allowed_doc_ids=[])
    search_tool = next(t for t in tools if t.name == "drdo_kb_search")

    result = search_tool.invoke({"query": "range parameter", "top_k": 5})
    assert "5000 km" not in result


# ── Multi-agent specialist retrieval (BaseSpecialistAgent.query) ────────────

class _FakeChatCompletions:
    def create(self, model, messages, max_tokens, temperature):
        class _Choice:
            class message:
                content = "fake specialist answer"
        class _Resp:
            choices = [_Choice()]
        return _Resp()


class _FakeGroqClient:
    class chat:
        completions = _FakeChatCompletions()


def test_specialist_agent_folds_in_multimodal_region_hit(monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyFakeVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _multimodal_metadata)

    agent = BaseSpecialistAgent(groq_client=_FakeGroqClient())
    result = agent.query("What is the range parameter?", history=None, allowed_doc_ids=None)

    assert any("5000 km" in chunk for chunk in [s.chunk_preview for s in result.sources])


# ── /query/stream ────────────────────────────────────────────────────────────

class _FakeStreamChunkDelta:
    def __init__(self, content):
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content):
        self.delta = _FakeStreamChunkDelta(content)


class _FakeStreamChunk:
    def __init__(self, content):
        self.choices = [_FakeStreamChoice(content)]


class _FakeStreamCompletions:
    def create(self, model, messages, max_tokens, temperature, stream):
        return iter([_FakeStreamChunk("The range is 5000 km.")])


class _FakeStreamGroqClient:
    def __init__(self, api_key=None):
        class _Chat:
            completions = _FakeStreamCompletions()
        self.chat = _Chat()


@pytest.fixture()
def auth_headers(client):
    login = client.post("/auth/login", json={"email": "demo@chat.com", "password": "demo12345"})
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_query_stream_folds_in_multimodal_region_hit(client, auth_headers, monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyFakeVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _multimodal_metadata)
    monkeypatch.setattr("groq.Groq", _FakeStreamGroqClient)

    r = client.post(
        "/query/stream", json={"question": "What is the range parameter?"}, headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.text
    done_line = next(line for line in body.splitlines() if "done" in line)
    payload = json.loads(done_line[len("data: "):])
    sources_preview = " ".join(s["chunk_preview"] for s in payload["sources"])
    assert "5000 km" in sources_preview
