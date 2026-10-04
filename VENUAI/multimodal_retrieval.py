"""
Multimodal retrieval — spec section 8's "table retrieval" / "visual
retrieval" channels, for the real content this phase can actually extract
(table text, OCR'd image text). This is lexical (token-overlap) retrieval
over stored region text, reusing the same scoring approach as
VENUAI.citation_agent (stopword-filtered token F1) rather than inventing a
third scoring function — not vector/embedding-based retrieval, since no
multimodal embedding index was built in this phase (see
Documents/AI/multimodal.md "Not Yet Implemented" for what that would need).

Authorization is enforced the same way as every other retrieval path in
this codebase (Documents/Backend/authentication.md): callers pass
allowed_doc_ids and only regions from documents in that set are searched.
"""

from dataclasses import dataclass
from typing import List, Optional

from langchain_core.documents import Document

from VENUAI.citation_agent import _tokenize
from VENUAI.evidence import Evidence
from VENUAI.multimodal_document import BoundingBox, DocumentRegion, RegionType


def _search_score(query_tokens: set, content_tokens: set) -> float:
    """
    Query-term recall: fraction of the (stopword-filtered) query tokens
    found in the region's content.

    Deliberately NOT citation_agent's token-F1 scorer, which requires 2+
    overlapping tokens before scoring above 0 — that's the right anti-false-
    positive rule when comparing a full claim *sentence* against an evidence
    *paragraph* (see citation_agent.py), but search queries are typically
    1-3 words; requiring 2+ overlapping tokens would make a single-keyword
    search like "range" or "revenue" always return zero results, which was
    caught by VENUAI/tests/test_region_search_endpoint.py when it was
    first (incorrectly) reusing that scorer.
    """
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens)
    return overlap / len(query_tokens)


@dataclass(frozen=True)
class RegionSearchResult:
    evidence: Evidence
    score: float


def _region_from_stored_dict(doc_id: str, region_dict: dict, page_number: int) -> DocumentRegion:
    bbox = region_dict.get("bounding_box")
    return DocumentRegion(
        document_id=doc_id,
        page_number=page_number,
        region_id=region_dict["region_id"],
        region_type=RegionType(region_dict["region_type"]),
        bounding_box=BoundingBox(**bbox) if bbox else None,
        extracted_text=region_dict.get("extracted_text", ""),
        ocr_text=region_dict.get("ocr_text"),
        ocr_unavailable_reason=region_dict.get("ocr_unavailable_reason"),
        content_hash=region_dict.get("content_hash", ""),
    )


def search_multimodal_regions(
    query: str,
    all_metadata: dict,
    allowed_doc_ids: Optional[List[str]],
    top_k: int = 5,
    region_types: Optional[List[RegionType]] = None,
) -> List[RegionSearchResult]:
    """
    Lexical search over TABLE/IMAGE regions across every multimodal
    document the caller is authorized to see.

    Args:
        query: search text.
        all_metadata: the dict returned by VENUAI.rag_server.load_metadata() —
            passed in rather than imported, to avoid a circular import
            (rag_server imports this module for the search endpoint).
        allowed_doc_ids: same authorization contract as every other
            retrieval path — None means unrestricted (admin), [] means
            nothing visible.
        top_k: max results.
        region_types: restrict to specific region types (defaults to TABLE + IMAGE).
    """
    region_types = region_types or [RegionType.TABLE, RegionType.IMAGE]
    query_tokens = _tokenize(query)

    candidates: List[RegionSearchResult] = []
    for doc_id, doc_meta in all_metadata.items():
        if not doc_meta.get("is_multimodal"):
            continue
        if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
            continue

        summary = doc_meta.get("multimodal_summary", {})
        for page in summary.get("pages", []):
            for region_dict in page.get("regions", []):
                if region_dict["region_type"] not in {rt.value for rt in region_types}:
                    continue

                text = region_dict.get("extracted_text") or region_dict.get("ocr_text") or ""
                if not text.strip():
                    continue

                score = _search_score(query_tokens, _tokenize(text))
                if score <= 0:
                    continue

                region = _region_from_stored_dict(doc_id, region_dict, page["page_number"])
                evidence = Evidence.from_region(
                    region, owner_id=doc_meta.get("owner_id"),
                    classification=doc_meta.get("classification", "UNKNOWN"),
                )
                candidates.append(RegionSearchResult(evidence=evidence, score=round(score, 3)))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


def region_results_to_documents(results: List[RegionSearchResult]) -> List[Document]:
    """
    Adapts RegionSearchResult (this module's own type) into plain LangChain
    `Document`s, using the exact same metadata shape (`filename`, `page`
    0-indexed) that the text-chunk retrieval path already produces — see
    `VENUAI.rag_server.retrieve_node`/`generate_node` and
    `VENUAI.agentic_rag._retrieve`. This is what lets
    `rank_chunks`/`build_drdo_prompt`/`verify_node` (none of which know or
    care that a `Document` originated from a table/image region rather than
    a text chunk) consume region results without any changes of their own —
    see Documents/AI/multimodal.md "Integration with the default RAG path".
    """
    docs = []
    for r in results:
        docs.append(Document(
            page_content=r.evidence.content,
            metadata={
                "filename": r.evidence.document_name,
                "page": r.evidence.page_number - 1,  # Evidence.from_region is 1-indexed; Document convention here is 0-indexed
                "doc_id": r.evidence.document_id,
                "is_multimodal_region": True,
                "region_score": r.score,
            },
        ))
    return docs


def retrieve_multimodal_documents(
    query: str, allowed_doc_ids: Optional[List[str]], top_k: int = 3,
) -> List[Document]:
    """
    Convenience entry point for the default RAG/Agentic RAG paths: load the
    real document metadata and search multimodal regions, already adapted
    to `Document` objects. Local import of `rag_server.load_metadata`
    mirrors the existing pattern in `VENUAI.agentic_rag._retrieve` (local
    import of `rag_server.get_vector_store`) — avoids a circular import,
    since `rag_server` also imports this module for `/documents/regions/search`.
    """
    from VENUAI.rag_server import load_metadata
    results = search_multimodal_regions(query, load_metadata(), allowed_doc_ids, top_k=top_k)
    return region_results_to_documents(results)
