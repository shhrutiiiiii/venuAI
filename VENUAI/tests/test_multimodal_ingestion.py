"""
Real integration tests for VENUAI/multimodal_ingestion.py. Builds an actual
PDF file using PyMuPDF (text, a heading, a drawn table grid, and an
embedded image), writes it to a temp file, and runs the real extraction
pipeline against it — no mocking of fitz/pdfplumber, since both are
genuinely installed and this is exactly what the real /upload/multimodal
route will do with a real uploaded PDF.
"""

import fitz
import pytest

from VENUAI.multimodal_document import RegionType
from VENUAI.multimodal_ingestion import extract_multimodal_document


@pytest.fixture()
def sample_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)

    # Heading — large font.
    page.insert_text((50, 50), "DRDO Technical Report", fontsize=24)
    # Body text — regular font.
    page.insert_text((50, 100), "This document describes the Agni-V missile specifications.", fontsize=11)

    # A simple table: draw grid lines so pdfplumber's line-based table
    # detector actually finds it (real detection, not guesswork).
    table_top, table_left = 150, 50
    col_width, row_height = 100, 25
    for row in range(3):
        y = table_top + row * row_height
        page.draw_line((table_left, y), (table_left + col_width * 2, y))
    for col in range(3):
        x = table_left + col * col_width
        page.draw_line((x, table_top), (x, table_top + row_height * 2))
    page.insert_text((table_left + 5, table_top + 17), "Parameter")
    page.insert_text((table_left + col_width + 5, table_top + 17), "Value")
    page.insert_text((table_left + 5, table_top + row_height + 17), "Range")
    page.insert_text((table_left + col_width + 5, table_top + row_height + 17), "5000 km")

    # An embedded image — a small generated PNG.
    from PIL import Image
    from io import BytesIO
    img = Image.new("RGB", (80, 40), color=(200, 0, 0))
    buf = BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(50, 350, 130, 390), stream=buf.getvalue())

    doc.save(str(path))
    doc.close()
    return str(path)


def test_extracts_text_and_heading_regions(sample_pdf):
    result = extract_multimodal_document(sample_pdf, document_id="doc-1", filename="sample.pdf", run_ocr=False)

    assert len(result.pages) == 1
    page = result.pages[0]
    headings = [r for r in page.regions if r.region_type == RegionType.HEADING]
    body_text = [r for r in page.regions if r.region_type == RegionType.TEXT]

    assert any("DRDO Technical Report" in h.extracted_text for h in headings)
    assert any("Agni-V" in t.extracted_text for t in body_text)


def test_extracts_table_region_with_real_content(sample_pdf):
    result = extract_multimodal_document(sample_pdf, document_id="doc-1", filename="sample.pdf", run_ocr=False)
    page = result.pages[0]

    assert len(page.tables) >= 1
    table_text = page.tables[0].extracted_text
    assert "Parameter" in table_text
    assert "5000 km" in table_text


def test_extracts_image_region_with_bounding_box(sample_pdf):
    result = extract_multimodal_document(sample_pdf, document_id="doc-1", filename="sample.pdf", run_ocr=False)
    page = result.pages[0]

    assert len(page.images) == 1
    image_region = page.images[0]
    assert image_region.image_bytes is not None
    assert len(image_region.image_bytes) > 0
    assert image_region.bounding_box is not None


def test_image_region_reports_ocr_unavailable_honestly_when_run_ocr_true(sample_pdf):
    """In this environment Tesseract is not installed — the pipeline must
    say so explicitly on the region, not silently return ocr_text=None
    indistinguishable from 'OCR found nothing.'"""
    from VENUAI.ocr_provider import TesseractOCRProvider

    result = extract_multimodal_document(sample_pdf, document_id="doc-1", filename="sample.pdf", run_ocr=True)
    page = result.pages[0]
    image_region = page.images[0]

    if TesseractOCRProvider().is_available():
        pytest.skip("Tesseract is installed in this environment")
    assert image_region.ocr_text is None
    assert image_region.ocr_unavailable_reason is not None
    assert "OCR" in image_region.ocr_unavailable_reason  # S3 sanitised wording


def test_no_region_is_ever_classified_as_chart_or_diagram(sample_pdf):
    """Regression guard for the core Phase 2 rule: this deterministic
    pipeline must never claim semantic image understanding it doesn't have."""
    result = extract_multimodal_document(sample_pdf, document_id="doc-1", filename="sample.pdf", run_ocr=False)
    all_types = {r.region_type for p in result.pages for r in p.regions}
    assert RegionType.CHART not in all_types
    assert RegionType.DIAGRAM not in all_types
    assert RegionType.EQUATION not in all_types


def test_summary_dict_is_json_serializable(sample_pdf):
    import json

    result = extract_multimodal_document(sample_pdf, document_id="doc-1", filename="sample.pdf", run_ocr=False)
    summary = result.to_summary_dict()
    serialized = json.dumps(summary)  # raises if not actually serializable
    assert "sample.pdf" in serialized
    assert summary["total_tables"] >= 1
    assert summary["total_images"] == 1
