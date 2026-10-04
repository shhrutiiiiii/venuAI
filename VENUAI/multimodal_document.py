"""
Multimodal document object model — spec section 5. Replaces "a document is
just text chunks" with a real page/region representation so an answer can
later cite Document → Page → Region → Evidence, not just Document → Page.

This is a data model only — VENUAI/multimodal_ingestion.py is what actually
populates it from a real PDF using pdfplumber (tables) and PyMuPDF (text
layout + embedded images), both genuinely installed and working in this
environment. Nothing here is a placeholder: every field this module defines
is actually set by the ingestion pipeline, not left as a TODO.
"""

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RegionType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    TABLE = "table"
    IMAGE = "image"
    CHART = "chart"        # see DocumentRegion docstring — never actually assigned by
    DIAGRAM = "diagram"    # this phase's ingestion pipeline; reserved for when a real
    EQUATION = "equation"  # vision model exists to tell these apart from a generic IMAGE.
    CODE = "code"
    LIST = "list"
    FOOTNOTE = "footnote"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class DocumentRegion:
    """
    One extracted region of one page.

    `region_type` is set by the real, deterministic extraction logic in
    multimodal_ingestion.py (layout analysis for TEXT/HEADING, pdfplumber
    table detection for TABLE, PyMuPDF embedded-image extraction for IMAGE)
    — it is NEVER set to CHART, DIAGRAM, or EQUATION, because distinguishing
    an embedded image that happens to be a chart from one that's a photo or
    a logo requires visual/semantic understanding this deployment doesn't
    have (no vision-capable model — see VENUAI/agents/vision_agent.py and
    Documents/AI/vision.md). An IMAGE region is exactly what it says: an
    extracted image, nothing claimed about its content until a vision
    model (or OCR, for any text baked into it) actually looks at it.
    """
    document_id: str
    page_number: int
    region_id: str
    region_type: RegionType
    bounding_box: Optional[BoundingBox]
    extracted_text: str = ""
    ocr_text: Optional[str] = None          # None = OCR not attempted/unavailable, "" = attempted, found nothing
    ocr_unavailable_reason: Optional[str] = None
    image_bytes: Optional[bytes] = None      # only set for IMAGE regions
    extraction_confidence: float = 1.0       # 1.0 for deterministic text/table extraction; lower for OCR
    content_hash: str = ""

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


@dataclass
class MultimodalPage:
    page_number: int
    width: float
    height: float
    regions: List[DocumentRegion] = field(default_factory=list)

    @property
    def tables(self) -> List[DocumentRegion]:
        return [r for r in self.regions if r.region_type == RegionType.TABLE]

    @property
    def images(self) -> List[DocumentRegion]:
        return [r for r in self.regions if r.region_type == RegionType.IMAGE]

    @property
    def text_regions(self) -> List[DocumentRegion]:
        return [r for r in self.regions if r.region_type in (RegionType.TEXT, RegionType.HEADING)]


@dataclass
class MultimodalDocument:
    document_id: str
    filename: str
    pages: List[MultimodalPage] = field(default_factory=list)

    @property
    def total_tables(self) -> int:
        return sum(len(p.tables) for p in self.pages)

    @property
    def total_images(self) -> int:
        return sum(len(p.images) for p in self.pages)

    def to_summary_dict(self) -> dict:
        """JSON-serializable summary (excludes raw image_bytes) for storage
        alongside the existing doc_metadata.json pattern — see
        VENUAI/rag_server.py's /upload/multimodal route."""
        return {
            "document_id": self.document_id,
            "filename": self.filename,
            "page_count": len(self.pages),
            "total_tables": self.total_tables,
            "total_images": self.total_images,
            "pages": [
                {
                    "page_number": p.page_number,
                    "width": p.width,
                    "height": p.height,
                    "regions": [
                        {
                            "region_id": r.region_id,
                            "region_type": r.region_type.value,
                            "bounding_box": (
                                {"x0": r.bounding_box.x0, "y0": r.bounding_box.y0,
                                 "x1": r.bounding_box.x1, "y1": r.bounding_box.y1}
                                if r.bounding_box else None
                            ),
                            "extracted_text": r.extracted_text[:500],
                            "ocr_text": r.ocr_text[:500] if r.ocr_text else r.ocr_text,
                            "ocr_unavailable_reason": r.ocr_unavailable_reason,
                            "extraction_confidence": r.extraction_confidence,
                            "content_hash": r.content_hash,
                        }
                        for r in p.regions
                    ],
                }
                for p in self.pages
            ],
        }
