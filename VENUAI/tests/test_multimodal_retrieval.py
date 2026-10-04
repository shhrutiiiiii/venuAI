"""
Real tests for VENUAI/multimodal_retrieval.py — lexical search over stored
table/image region text, with authorization enforced the same way as every
other retrieval path in this codebase.
"""

from VENUAI.multimodal_retrieval import search_multimodal_regions
from VENUAI.multimodal_document import RegionType


def _sample_metadata():
    return {
        "doc-mine": {
            "is_multimodal": True,
            "owner_id": "user-1",
            "classification": "UNKNOWN",
            "multimodal_summary": {
                "pages": [{
                    "page_number": 0,
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
        "doc-other-user": {
            "is_multimodal": True,
            "owner_id": "user-2",
            "classification": "UNKNOWN",
            "multimodal_summary": {
                "pages": [{
                    "page_number": 0,
                    "regions": [{
                        "region_id": "r2",
                        "region_type": "table",
                        "extracted_text": "Parameter | Value\nRange | 3000 km",
                        "bounding_box": None,
                        "content_hash": "h2",
                    }],
                }],
            },
        },
        "doc-text-only": {
            "is_multimodal": False,  # a regular /upload RAG document — must be skipped entirely
        },
    }


def test_single_keyword_query_still_matches():
    """
    Regression test: citation_agent's token-F1 scorer requires 2+ overlapping
    tokens, which made this function always return zero results for any
    single-word search (a real bug caught while building the endpoint test).
    A one-word query that's genuinely present in the content must match.
    """
    results = search_multimodal_regions("Range", _sample_metadata(), allowed_doc_ids=["doc-mine"])
    assert len(results) == 1


def test_finds_matching_table_region():
    results = search_multimodal_regions("What is the range parameter?", _sample_metadata(), allowed_doc_ids=["doc-mine"])
    assert len(results) == 1
    assert "5000 km" in results[0].evidence.content


def test_excludes_unauthorized_documents():
    results = search_multimodal_regions("range parameter", _sample_metadata(), allowed_doc_ids=["doc-mine"])
    doc_ids = {r.evidence.document_id for r in results}
    assert "doc-other-user" not in doc_ids


def test_admin_sees_all_documents():
    results = search_multimodal_regions("range parameter", _sample_metadata(), allowed_doc_ids=None)
    doc_ids = {r.evidence.document_id for r in results}
    assert "doc-mine" in doc_ids
    assert "doc-other-user" in doc_ids


def test_non_multimodal_documents_are_never_searched():
    results = search_multimodal_regions("anything", _sample_metadata(), allowed_doc_ids=None)
    doc_ids = {r.evidence.document_id for r in results}
    assert "doc-text-only" not in doc_ids


def test_irrelevant_query_returns_no_results():
    results = search_multimodal_regions("completely unrelated banana recipe", _sample_metadata(), allowed_doc_ids=None)
    assert results == []


def test_results_are_sorted_by_score_descending():
    results = search_multimodal_regions("range parameter value", _sample_metadata(), allowed_doc_ids=None)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
