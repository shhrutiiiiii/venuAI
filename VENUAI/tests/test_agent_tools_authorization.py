"""
Real tests for VENUAI/agent_tools.build_agent_tools() — the ReAct agent's
per-request, identity-bound tools. This was the last of the four query modes
(standard, streaming, multi-agent, ReAct) with no caller-identity filter on
retrieval; closing it required a real refactor (tools built fresh per
request instead of as shared module-level singletons), not a one-line patch.

Also verifies the module-level `memory_recall` bug this incidentally fixed:
previously the LLM supplied `user_id` itself as a tool argument, so a
malicious/confused agent turn could request another user's memory. The
factory version binds `user_id` by closure and removes it from the tool's
exposed parameters entirely — the LLM cannot choose whose memory to recall.
"""

import json

from langchain_core.documents import Document

from VENUAI import agent_tools, rag_server


class _FakeRetriever:
    def __init__(self, docs, search_kwargs):
        self._docs = docs
        self._search_kwargs = search_kwargs

    def invoke(self, query):
        allowed = self._search_kwargs.get("filter", {}).get("doc_id", {}).get("$in")
        if allowed is None:
            return list(self._docs)
        allowed_set = set(allowed)
        return [d for d in self._docs if d.metadata.get("doc_id") in allowed_set]


class _FakeVectorStore:
    def __init__(self, docs):
        self._docs = docs

    def as_retriever(self, search_type, search_kwargs):
        return _FakeRetriever(self._docs, search_kwargs)


def _sample_docs():
    return [
        Document(page_content="alpha content", metadata={"doc_id": "doc-mine", "filename": "a.pdf", "page": 0}),
        Document(page_content="beta content", metadata={"doc_id": "doc-other", "filename": "b.pdf", "page": 0}),
    ]


def _sample_meta():
    return {
        "doc-mine": {
            "filename": "a.pdf", "pages": 1, "chunks": 1, "size_kb": 1.0,
            "upload_time": "2026-01-01T00:00:00", "tags": {"domain_tags": ["technical"]},
        },
        "doc-other": {
            "filename": "b.pdf", "pages": 1, "chunks": 1, "size_kb": 1.0,
            "upload_time": "2026-01-01T00:00:00", "tags": {"domain_tags": ["labs"]},
        },
    }


def test_drdo_kb_search_tool_only_sees_allowed_docs(monkeypatch):
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    tools = agent_tools.build_agent_tools(user_id="user-1", allowed_doc_ids=["doc-mine"])
    search_tool = next(t for t in tools if t.name == "drdo_kb_search")

    result = search_tool.invoke({"query": "anything", "top_k": 5})
    assert "a.pdf" in result
    assert "b.pdf" not in result


def test_drdo_kb_search_tool_admin_unfiltered(monkeypatch):
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    tools = agent_tools.build_agent_tools(user_id="admin-1", allowed_doc_ids=None)
    search_tool = next(t for t in tools if t.name == "drdo_kb_search")

    result = search_tool.invoke({"query": "anything", "top_k": 5})
    assert "a.pdf" in result
    assert "b.pdf" in result


def test_list_knowledge_base_tool_filters_by_allowed_doc_ids(monkeypatch):
    monkeypatch.setattr(rag_server, "load_metadata", lambda: _sample_meta())

    tools = agent_tools.build_agent_tools(user_id="user-1", allowed_doc_ids=["doc-mine"])
    list_tool = next(t for t in tools if t.name == "list_knowledge_base")

    result = list_tool.invoke({})
    assert "a.pdf" in result
    assert "b.pdf" not in result


def test_document_summary_tool_rejects_unauthorized_doc(monkeypatch):
    monkeypatch.setattr(rag_server, "load_metadata", lambda: _sample_meta())

    tools = agent_tools.build_agent_tools(user_id="user-1", allowed_doc_ids=["doc-mine"])
    summary_tool = next(t for t in tools if t.name == "document_summary")

    own_doc = summary_tool.invoke({"doc_id": "doc-mine"})
    assert "a.pdf" in own_doc

    other_doc = summary_tool.invoke({"doc_id": "doc-other"})
    assert "not authorized" in other_doc.lower()


def test_memory_recall_tool_does_not_expose_user_id_parameter():
    """
    The LLM must not be able to choose whose memory to recall — user_id is
    bound by closure, not an exposed tool argument.
    """
    tools = agent_tools.build_agent_tools(user_id="user-1", allowed_doc_ids=None)
    memory_tool = next(t for t in tools if t.name == "memory_recall")

    schema = memory_tool.args
    assert "user_id" not in schema
    assert "query" in schema


def test_memory_recall_tool_always_uses_bound_user_id(monkeypatch):
    captured = {}

    class _FakeMemoryManager:
        def get_context(self, session_id, user_id, query, tenant_id="default"):
            captured["user_id"] = user_id
            captured["tenant_id"] = tenant_id
            return {}

    import VENUAI.memory_manager as memory_manager
    monkeypatch.setattr(memory_manager, "get_memory_manager", lambda: _FakeMemoryManager())

    tools = agent_tools.build_agent_tools(user_id="the-real-caller", allowed_doc_ids=None)
    memory_tool = next(t for t in tools if t.name == "memory_recall")
    memory_tool.invoke({"query": "what did we discuss last time?"})

    assert captured["user_id"] == "the-real-caller"


def test_code_execution_tool_denied_for_regular_user():
    """EXECUTE_CODE is admin-only (VENUAI.policy_engine.ROLE_PERMISSIONS) —
    a regular user must get a real denial, not an attempted execution."""
    tools = agent_tools.build_agent_tools(user_id="user-1", allowed_doc_ids=None, user_role="user")
    code_tool = next(t for t in tools if t.name == "code_execution")

    result = code_tool.invoke({"code": "print('should not run')"})
    assert "denied" in result.lower()


def test_code_execution_tool_allowed_for_admin(monkeypatch):
    """Doesn't require real Docker — stubs VENUAI.code_sandbox.run_python
    to prove the policy check passes through to the real sandbox call for
    an admin caller, independent of whether Docker is actually running on
    this test host."""
    from VENUAI import code_sandbox

    captured = {}

    def _fake_run_python(code, timeout_seconds=10):
        captured["code"] = code
        return code_sandbox.SandboxResult(stdout="4\n", stderr="", exit_code=0, timed_out=False, duration_seconds=0.1)

    monkeypatch.setattr(code_sandbox, "run_python", _fake_run_python)

    tools = agent_tools.build_agent_tools(user_id="admin-1", allowed_doc_ids=None, user_role="admin")
    code_tool = next(t for t in tools if t.name == "code_execution")

    result = code_tool.invoke({"code": "print(2+2)"})
    assert "4" in result
    assert captured["code"] == "print(2+2)"
