"""
Evidence & Claim models — the common data structures citation verification,
contradiction analysis, and research reports are built on.

This does not replace QuerySource (VENUAI/agents/base_agent.py) or the
document-chunk Document objects already flowing through retrieve_node —
it's a richer, provenance-complete wrapper agents that need citation
verification/contradiction analysis construct FROM those, not a parallel
retrieval system. `Evidence.from_document()` is the conversion point.
"""

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class EvidenceSourceType(str, Enum):
    UPLOADED_DOCUMENT = "uploaded_document"
    PRELOADED_KB = "preloaded_kb"
    WEB_SEARCH = "web_search"


class EvidenceModality(str, Enum):
    """Which VENUAI.multimodal_document.RegionType this Evidence came from —
    None/TEXT for the pre-Phase-2 text-chunk path, so existing callers of
    Evidence.from_document() are unaffected."""
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    document_id: str
    document_name: str
    page_number: int
    content: str
    source_type: EvidenceSourceType
    relevance_score: float = 0.0
    retrieval_method: str = "unknown"   # "vector" | "bm25" | "hybrid" | "web"
    owner_id: Optional[str] = None
    classification: str = "UNKNOWN"
    content_hash: str = ""
    modality: EvidenceModality = EvidenceModality.TEXT
    bounding_box: Optional[dict] = None  # {"x0","y0","x1","y1"} — only set for TABLE/IMAGE evidence

    @staticmethod
    def from_region(region, owner_id: Optional[str] = None, classification: str = "UNKNOWN") -> "Evidence":
        """
        Build Evidence from a VENUAI.multimodal_document.DocumentRegion
        (table or image region from the real extraction pipeline in
        VENUAI/multimodal_ingestion.py). For an IMAGE region, `content` is
        the OCR text if OCR was available and found something, otherwise
        an explicit placeholder string naming why — never a fabricated
        description of what the image shows.
        """
        if region.region_type.value == "table":
            modality = EvidenceModality.TABLE
            content = region.extracted_text
        else:
            modality = EvidenceModality.IMAGE
            if region.ocr_text:
                content = region.ocr_text
            elif region.ocr_unavailable_reason:
                content = f"[Image region — OCR unavailable: {region.ocr_unavailable_reason}]"
            else:
                content = "[Image region — no text detected by OCR]"

        bbox = None
        if region.bounding_box is not None:
            bbox = {"x0": region.bounding_box.x0, "y0": region.bounding_box.y0,
                    "x1": region.bounding_box.x1, "y1": region.bounding_box.y1}

        return Evidence(
            evidence_id=region.region_id,
            document_id=region.document_id,
            document_name=region.document_id,  # caller may override with the real filename
            page_number=region.page_number + 1,
            content=content,
            source_type=EvidenceSourceType.UPLOADED_DOCUMENT,
            relevance_score=0.0,
            retrieval_method="multimodal_region",
            owner_id=owner_id,
            classification=classification,
            content_hash=region.content_hash,
            modality=modality,
            bounding_box=bbox,
        )

    @staticmethod
    def from_document(doc, retrieval_method: str = "hybrid", relevance_score: float = 0.0) -> "Evidence":
        """Build Evidence from a LangChain Document as returned by
        retrieve_node/rank_chunks — the actual, real retrieval path."""
        meta = doc.metadata or {}
        content = doc.page_content
        doc_id = meta.get("doc_id", meta.get("filename", "unknown"))
        return Evidence(
            evidence_id=hashlib.sha256(f"{doc_id}:{meta.get('page', 0)}:{content[:200]}".encode()).hexdigest()[:16],
            document_id=str(doc_id),
            document_name=meta.get("filename", "Unknown Document"),
            page_number=meta.get("page", 0) + 1,
            content=content,
            source_type=(
                EvidenceSourceType.PRELOADED_KB
                if meta.get("filename", "").startswith("DRDO Master Knowledge Base")
                else EvidenceSourceType.UPLOADED_DOCUMENT
            ),
            relevance_score=relevance_score,
            retrieval_method=retrieval_method,
            owner_id=meta.get("owner_id"),
            classification=meta.get("classification", "UNKNOWN"),
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        )


class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    evidence: List[Evidence] = field(default_factory=list)
    support_status: SupportStatus = SupportStatus.UNSUPPORTED
    support_score: float = 0.0   # 0.0–1.0, lexical-overlap based — see citation_agent.py


@dataclass(frozen=True)
class EvidenceGraph:
    """Question → Claims → Evidence, per spec section 25. A thin aggregate,
    not a graph database — see Documents/AI/knowledge-graph.md for why a
    real graph DB is a Phase 3/infrastructure item, not fabricated here."""
    question: str
    claims: List[Claim] = field(default_factory=list)

    @property
    def unsupported_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.support_status == SupportStatus.UNSUPPORTED]

    @property
    def contradicted_claims(self) -> List[Claim]:
        return [c for c in self.claims if c.support_status == SupportStatus.CONTRADICTED]

    @property
    def evidence_coverage(self) -> float:
        """Fraction of claims with at least one supporting evidence item —
        one real, documented confidence signal (spec section 29), not a
        single fabricated 'confidence: 97%' number."""
        if not self.claims:
            return 0.0
        supported = sum(1 for c in self.claims if c.evidence)
        return round(supported / len(self.claims), 3)
