"""Real tests for Evidence.from_region() (VENUAI/evidence.py Phase 2
extension) — building Evidence from multimodal DocumentRegion objects."""

from VENUAI.evidence import Evidence, EvidenceModality
from VENUAI.multimodal_document import BoundingBox, DocumentRegion, RegionType


def test_from_region_table_sets_table_modality_and_text():
    region = DocumentRegion(
        document_id="doc-1", page_number=2, region_id="r1",
        region_type=RegionType.TABLE, bounding_box=BoundingBox(1, 2, 3, 4),
        extracted_text="Parameter | Value\nRange | 5000 km",
        content_hash="abc123",
    )
    ev = Evidence.from_region(region, owner_id="user-1", classification="PUBLIC")

    assert ev.modality == EvidenceModality.TABLE
    assert "5000 km" in ev.content
    assert ev.page_number == 3  # 0-indexed region page -> 1-indexed Evidence page
    assert ev.owner_id == "user-1"
    assert ev.classification == "PUBLIC"
    assert ev.bounding_box == {"x0": 1, "y0": 2, "x1": 3, "y1": 4}


def test_from_region_image_with_ocr_text_uses_ocr_text_as_content():
    region = DocumentRegion(
        document_id="doc-1", page_number=0, region_id="r2",
        region_type=RegionType.IMAGE, bounding_box=None,
        ocr_text="DRDO LABORATORY",
    )
    ev = Evidence.from_region(region)
    assert ev.modality == EvidenceModality.IMAGE
    assert ev.content == "DRDO LABORATORY"


def test_from_region_image_without_ocr_text_states_the_real_reason_not_a_fabricated_description():
    region = DocumentRegion(
        document_id="doc-1", page_number=0, region_id="r3",
        region_type=RegionType.IMAGE, bounding_box=None,
        ocr_text=None, ocr_unavailable_reason="Tesseract binary not found",
    )
    ev = Evidence.from_region(region)
    assert "OCR unavailable" in ev.content
    assert "Tesseract" in ev.content
