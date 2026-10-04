"""
Regression test for a real, previously-hidden bug in rank_chunks():
`_parse_upload_time` fell back to `datetime.min` for any document lacking
`upload_time` metadata, and `datetime.min.timestamp()` raises
`OSError: [Errno 22] Invalid argument` on Windows (year-1 dates are outside
the platform's representable time range).

This mattered in practice, not just in theory: VENUAI/rag_server.py's
`/upload` handler attaches `doc_id`, `filename`, `file_size_kb`, `owner_id`,
`domain_tags`, `drdo_labs`, and `classification` to each chunk's Chroma
metadata — but never `upload_time` (that field only lives on the
document-level entry in doc_metadata.json). So every real chunk retrieved
from Chroma hit the broken fallback, meaning rank_chunks() — called from
generate_node, the /query/stream handler, and the ReAct agent's
drdo_kb_search tool — would crash on any real query that actually retrieved
documents, on Windows.
"""

from langchain_core.documents import Document

from VENUAI.context_engineer import rank_chunks


def test_rank_chunks_does_not_crash_on_docs_without_upload_time():
    """
    This is the exact shape of metadata real Chroma-retrieved chunks have:
    no `upload_time` key at all.
    """
    docs = [
        Document(
            page_content="DRDO missile range specifications",
            metadata={"doc_id": "doc-1", "filename": "a.pdf", "page": 0},
        ),
        Document(
            page_content="Propulsion system details",
            metadata={"doc_id": "doc-2", "filename": "b.pdf", "page": 1},
        ),
    ]
    ranked = rank_chunks(docs, "missile range")
    assert len(ranked) == 2


def test_rank_chunks_handles_mixed_upload_time_presence():
    docs = [
        Document(page_content="alpha", metadata={"filename": "a.pdf", "page": 0}),
        Document(
            page_content="beta",
            metadata={"filename": "b.pdf", "page": 0, "upload_time": "2026-01-01T00:00:00"},
        ),
    ]
    ranked = rank_chunks(docs, "alpha beta")
    assert len(ranked) == 2


def test_rank_chunks_handles_malformed_upload_time_string():
    docs = [
        Document(page_content="alpha", metadata={"filename": "a.pdf", "page": 0, "upload_time": "not-a-date"}),
    ]
    ranked = rank_chunks(docs, "alpha")
    assert len(ranked) == 1


def test_rank_chunks_empty_input_returns_empty():
    assert rank_chunks([], "anything") == []
