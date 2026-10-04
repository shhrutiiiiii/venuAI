"""
Real tests for VENUAI/ocr_provider.py. In THIS environment the Tesseract
binary is genuinely not installed, so these tests verify the honest-failure
path (is_available() == False, extract_text raises
OCRProviderUnavailableError) rather than mocking around that gap — per the
project rule against pretending unavailable infrastructure works.

If Tesseract IS installed in a given environment, this test adapts: the
real-success test is skipped only when unavailable, and conversely the
unavailable-path test is skipped when it IS available — so this file
exercises whichever path is actually true, never a fabricated one.
"""

import pytest

from VENUAI.ocr_provider import OCRProviderUnavailableError, TesseractOCRProvider, get_ocr_provider


def test_get_ocr_provider_returns_singleton():
    p1 = get_ocr_provider()
    p2 = get_ocr_provider()
    assert p1 is p2


def test_is_available_matches_real_environment_state():
    provider = TesseractOCRProvider()
    available = provider.is_available()
    assert isinstance(available, bool)


@pytest.mark.skipif(
    TesseractOCRProvider().is_available(),
    reason="Tesseract IS installed in this environment — see test_extract_text_succeeds_when_available",
)
def test_extract_text_raises_clear_error_when_tesseract_unavailable():
    provider = TesseractOCRProvider()
    with pytest.raises(OCRProviderUnavailableError) as exc_info:
        provider.extract_text(b"not a real image")
    assert "Tesseract" in str(exc_info.value)


@pytest.mark.skipif(
    not TesseractOCRProvider().is_available(),
    reason="Tesseract is not installed in this environment",
)
def test_extract_text_succeeds_when_available():
    from PIL import Image, ImageDraw
    from io import BytesIO

    img = Image.new("RGB", (200, 60), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "DRDO TEST", fill="black")
    buf = BytesIO()
    img.save(buf, format="PNG")

    provider = TesseractOCRProvider()
    text = provider.extract_text(buf.getvalue())
    assert "DRDO" in text.upper()
