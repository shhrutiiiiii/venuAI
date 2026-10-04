"""
OCR provider abstraction — mirrors VENUAI/ai_gateway/provider.py's pattern
(an ABC + a real implementation + a clear "not available" signal) for the
same reason: so a genuinely working OCR backend can be swapped in without
redesigning callers, and so unavailability is reported honestly rather
than faked.

TesseractOCRProvider is a REAL implementation — it calls pytesseract
(genuinely installed in this environment) against a real Tesseract OCR
binary. Whether that binary is actually present is checked at call time,
not assumed: if it's missing, `extract_text` raises
OCRProviderUnavailableError with instructions, rather than returning a
fabricated empty/fake result that looks like a successful OCR pass.
"""

import logging
from abc import ABC, abstractmethod
from io import BytesIO

logger = logging.getLogger("drdo-rag")


class OCRProviderUnavailableError(RuntimeError):
    """Raised when the OCR backend's underlying engine isn't actually
    available — e.g. the Tesseract binary isn't installed/on PATH. Callers
    must handle this as a real, reportable gap (see
    VENUAI/agents/vision_agent.py), never silently substitute a fabricated
    OCR result."""


class OCRProvider(ABC):
    @abstractmethod
    def extract_text(self, image_bytes: bytes) -> str:
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...


class TesseractOCRProvider(OCRProvider):
    def is_available(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def extract_text(self, image_bytes: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as e:
            raise OCRProviderUnavailableError(
                f"pytesseract/Pillow not installed: {e}"
            )

        try:
            pytesseract.get_tesseract_version()
        except Exception as e:
            raise OCRProviderUnavailableError(
                "Tesseract OCR engine binary is not installed or not on PATH. "
                "pytesseract (the Python wrapper) is installed, but it requires "
                "the actual Tesseract binary — see "
                "https://github.com/tesseract-ocr/tesseract#installing-tesseract. "
                f"Underlying error: {e}"
            )

        image = Image.open(BytesIO(image_bytes))
        return pytesseract.image_to_string(image).strip()


_provider: "OCRProvider | None" = None


def get_ocr_provider() -> OCRProvider:
    global _provider
    if _provider is None:
        _provider = TesseractOCRProvider()
    return _provider
