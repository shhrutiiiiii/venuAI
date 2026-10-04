"""
Real tests proving multimodal table/image regions are now folded into the
default RAG (`VENUAI.rag_server.retrieve_node`) and Agentic RAG
(`VENUAI.agentic_rag._retrieve`) retrieval paths — previously a table
uploaded via /upload/multimodal was only reachable through the standalone
/documents/regions/search endpoint, never through a normal /query or
/research question. See Documents/AI/multimodal.md "Integration with the
default RAG path".
"""

from langchain_core.documents import Document

from VENUAI import agentic_rag, rag_server


def _sample_multimodal_metadata():
    return {
        "doc-table": {
            "is_multimodal": True,
            "owner_id": "user-1",
            "tenant_id": "default",
            "classification": "UNKNOWN",
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


class _EmptyVectorStore:
    """No text chunks at all — forces the text-chunk path to return
    nothing, isolating what the multimodal fold-in alone contributes."""

    def get(self):
        return {"documents": [], "metadatas": []}

    def as_retriever(self, search_type, search_kwargs):
        class _Empty:
            def invoke(self, query):
                return []
        return _Empty()


def test_retrieve_node_folds_in_multimodal_region_hit(monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _sample_multimodal_metadata)

    state = {"question": "What is the range parameter?", "top_k": 4, "allowed_doc_ids": None}
    result = rag_server.retrieve_node(state)

    docs = result["documents"]
    assert any("5000 km" in d.page_content for d in docs)
    region_doc = next(d for d in docs if "5000 km" in d.page_content)
    assert region_doc.metadata["is_multimodal_region"] is True
    assert region_doc.metadata["page"] == 2  # Evidence.from_region is 1-indexed (3); Document convention is 0-indexed


def test_retrieve_node_respects_allowed_doc_ids_for_multimodal_regions(monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _sample_multimodal_metadata)

    state = {"question": "What is the range parameter?", "top_k": 4, "allowed_doc_ids": []}
    result = rag_server.retrieve_node(state)

    assert result["documents"] == []


def test_agentic_rag_retrieve_folds_in_multimodal_region_hit(monkeypatch):
    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _EmptyVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _sample_multimodal_metadata)

    docs = agentic_rag._retrieve("range parameter", top_k=4, allowed_doc_ids=None)
    assert any("5000 km" in d.page_content for d in docs)


def test_multimodal_fold_in_failure_does_not_break_text_retrieval(monkeypatch):
    """A broken load_metadata() (or any other failure in the multimodal
    lookup) must degrade to text-only results, never raise out of
    retrieve_node — this is a best-effort addition, not a hard dependency."""

    class _TextOnlyVectorStore:
        def get(self):
            return {"documents": ["real text chunk"], "metadatas": [{"doc_id": "doc-text", "page": 0, "filename": "a.pdf"}]}

        def as_retriever(self, search_type, search_kwargs):
            class _R:
                def invoke(self, query):
                    return [Document(page_content="real text chunk", metadata={"doc_id": "doc-text", "page": 0, "filename": "a.pdf"})]
            return _R()

    def _broken_load_metadata():
        raise RuntimeError("simulated metadata store failure")

    monkeypatch.setattr(rag_server, "get_vector_store", lambda: _TextOnlyVectorStore())
    monkeypatch.setattr(rag_server, "load_metadata", _broken_load_metadata)

    state = {"question": "anything", "top_k": 4, "allowed_doc_ids": None}
    result = rag_server.retrieve_node(state)

    assert any(d.page_content == "real text chunk" for d in result["documents"])
