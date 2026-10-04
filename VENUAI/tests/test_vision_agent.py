"""
Real tests for VENUAI/agents/vision_agent.py. The central thing being
tested is the honesty boundary: a table/OCR'd-image region with real text
gets a real text-reasoning answer; a region with no usable text gets
insufficient_visual_evidence=True and an explicit explanation, never a
fabricated description of "what the image shows."
"""

from VENUAI.agents.vision_agent import VisionAgent
from VENUAI.multimodal_document import BoundingBox, DocumentRegion, RegionType


def _table_region(text="Parameter | Value\nRange | 5000 km"):
    return DocumentRegion(
        document_id="doc-1", page_number=0, region_id="r1",
        region_type=RegionType.TABLE, bounding_box=BoundingBox(0, 0, 100, 100),
        extracted_text=text,
    )


def _image_region_with_ocr(ocr_text="DRDO LABORATORY SIGN"):
    return DocumentRegion(
        document_id="doc-1", page_number=0, region_id="r2",
        region_type=RegionType.IMAGE, bounding_box=BoundingBox(0, 0, 100, 100),
        ocr_text=ocr_text,
    )


def _image_region_without_text(reason=None):
    return DocumentRegion(
        document_id="doc-1", page_number=0, region_id="r3",
        region_type=RegionType.IMAGE, bounding_box=BoundingBox(0, 0, 100, 100),
        ocr_text=None, ocr_unavailable_reason=reason,
    )


def test_table_region_answered_from_real_extracted_text():
    result = VisionAgent().analyze_region(_table_region(), "What is the range value?", groq_client=None)
    assert result.insufficient_visual_evidence is False
    assert result.basis == "table_text"
    assert "5000 km" in result.answer


def test_image_region_with_sufficient_ocr_text_is_answered():
    result = VisionAgent().analyze_region(
        _image_region_with_ocr("This laboratory develops radar systems for DRDO."),
        "What does this say?", groq_client=None,
    )
    assert result.insufficient_visual_evidence is False
    assert result.basis == "ocr_text"


def test_image_region_with_no_ocr_text_reports_insufficient_evidence():
    result = VisionAgent().analyze_region(
        _image_region_without_text(reason=None), "What does this chart show?", groq_client=None,
    )
    assert result.insufficient_visual_evidence is True
    assert "vision-capable model" in result.answer


def test_image_region_with_ocr_unavailable_names_the_real_reason():
    result = VisionAgent().analyze_region(
        _image_region_without_text(reason="Tesseract OCR engine binary is not installed"),
        "Describe this diagram.", groq_client=None,
    )
    assert result.insufficient_visual_evidence is True
    assert "OCR unavailable" in result.answer
    assert "Tesseract" in result.answer


def test_image_region_with_too_short_ocr_text_is_treated_as_insufficient():
    """A couple of stray OCR characters isn't enough to answer a real
    question about the image's content — must not be treated as sufficient."""
    result = VisionAgent().analyze_region(
        _image_region_with_ocr("12"), "What does this chart show?", groq_client=None,
    )
    assert result.insufficient_visual_evidence is True


def test_never_fabricates_an_answer_without_any_text_or_model():
    result = VisionAgent().analyze_region(
        _image_region_without_text(), "Describe the trend shown.", groq_client=None,
    )
    assert "trend" not in result.answer.lower() or "cannot" in result.answer.lower()
    assert result.insufficient_visual_evidence is True
