"""
Real tests of per-chunk retrieval authorization — the gap explicitly flagged
in Documents/Backend/authentication.md as "the most important remaining gap"
(document-level ownership was enforced, but retrieve_node did not filter
chunks by owner before they reached the LLM prompt). This exercises the
actual retrieve_node() code path with a fake vector store (to avoid pulling
in the real embedding model in a unit test) and the actual
get_visible_doc_ids() authorization logic with no mocking.
"""

from langchain_core.documents import Document

from VENUAI import rag_server
from VENUAI.auth import AuthenticatedUser


class _FakeRetriever:
    def __init__(self, docs, search_kwargs):
        self._docs = docs
        self._search_kwargs = search_kwargs

    def invoke(self, question):
        allowed = self._search_kwargs.get("filter", {}).get("doc_id", {}).get("$in")
        if allowed is None:
            return list(self._docs)
        allowed_set = set(allowed)
        return [d for d in self._docs if d.metadata.get("doc_id") in allowed_set]


class _FakeVectorStore:
    """Stands in for the real Chroma store — same `.get()` / `.as_retriever()` shape."""

    def __init__(self, docs):
        self._docs = docs

    def get(self):
        return {
            "documents": [d.page_content for d in self._docs],
            "metadatas": [d.metadata for d in self._docs],
        }

    def as_retriever(self, search_type, search_kwargs):
        return _FakeRetriever(self._docs, search_kwargs)


def _sample_docs():
    return [
        Document(page_content="alpha chunk", metadata={"doc_id": "doc-mine"}),
        Document(page_content="beta chunk", metadata={"doc_id": "doc-other-user"}),
        Document(page_content="gamma chunk", metadata={"doc_id": "doc-public"}),
    ]


def _user(role="user", user_id="user-1"):
    return AuthenticatedUser(id=user_id, email=f"{user_id}@test.com", role=role)


def test_get_visible_doc_ids_admin_sees_everything_in_their_tenant(isolated_auth_db, monkeypatch):
    """
    Admin sees every document within their own tenant, but admin is a
    per-tenant role, not a cross-tenant superuser (Phase 4 multi-tenancy —
    see Documents/Backend/multi-tenancy.md): a doc in a different tenant
    must stay invisible even to an admin.
    """
    meta = {
        "doc-mine": {"owner_id": "user-1", "classification": "UNKNOWN", "tenant_id": "default"},
        "doc-other-user": {"owner_id": "user-2", "classification": "UNKNOWN", "tenant_id": "default"},
        "doc-other-tenant": {"owner_id": "user-3", "classification": "UNKNOWN", "tenant_id": "other-tenant"},
    }
    monkeypatch.setattr(rag_server, "load_metadata", lambda: meta)
    visible = set(rag_server.get_visible_doc_ids(_user(role="admin")))
    assert visible == {"doc-mine", "doc-other-user"}


def test_get_visible_doc_ids_user_sees_own_and_public_only(isolated_auth_db, monkeypatch):
    meta = {
        "doc-mine": {"owner_id": "user-1", "classification": "UNKNOWN", "tenant_id": "default"},
        "doc-other-user": {"owner_id": "user-2", "classification": "UNKNOWN", "tenant_id": "default"},
        "doc-public": {"owner_id": "user-2", "classification": "PUBLIC", "tenant_id": "default"},
    }
    monkeypatch.setattr(rag_server, "load_metadata", lambda: meta)
    visible = set(rag_server.get_visible_doc_ids(_user(role="user", user_id="user-1")))
    assert visible == {"doc-mine", "doc-public"}


def test_get_visible_doc_ids_excludes_other_tenants_even_if_public(isolated_auth_db, monkeypatch):
    meta = {
        "doc-mine": {"owner_id": "user-1", "classification": "UNKNOWN", "tenant_id": "default"},
        "doc-public-other-tenant": {"owner_id": "user-9", "classification": "PUBLIC", "tenant_id": "other-tenant"},
    }
    monkeypatch.setattr(rag_server, "load_metadata", lambda: meta)
    visible = set(rag_server.get_visible_doc_ids(_user(role="user", user_id="user-1")))
    assert visible == {"doc-mine"}


def test_retrieve_node_filters_out_other_users_chunks(monkeypatch):
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    state = {
        "question": "anything",
        "top_k": 5,
        "allowed_doc_ids": ["doc-mine", "doc-public"],
    }
    result = rag_server.retrieve_node(state)
    returned_doc_ids = {d.metadata["doc_id"] for d in result["documents"]}

    assert "doc-other-user" not in returned_doc_ids
    assert returned_doc_ids == {"doc-mine", "doc-public"}


def test_retrieve_node_returns_nothing_when_user_has_no_visible_docs(monkeypatch):
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    state = {"question": "anything", "top_k": 5, "allowed_doc_ids": []}
    result = rag_server.retrieve_node(state)

    assert result["documents"] == []


def test_retrieve_node_admin_sees_all_chunks_unfiltered(monkeypatch):
    fake_vs = _FakeVectorStore(_sample_docs())
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: fake_vs)

    state = {"question": "anything", "top_k": 5, "allowed_doc_ids": None}
    result = rag_server.retrieve_node(state)
    returned_doc_ids = {d.metadata["doc_id"] for d in result["documents"]}

    assert returned_doc_ids == {"doc-mine", "doc-other-user", "doc-public"}
