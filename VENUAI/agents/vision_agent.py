"""
VisionAgent — spec section 7, held to spec section 68's rule as directly as
any module in this codebase: "do not claim multimodal/vision support unless
actually tested."

This deployment has no vision-capable model configured — confirmed by
VENUAI/ai_gateway/model_registry.py (no ModelCapability.VISION entry
exists) and tested by
VENUAI/tests/test_ai_gateway.py::test_no_vision_model_is_registered. A text
LLM cannot see an image's pixels; routing an image question to one and
having it respond would be exactly the fabrication this project forbids.

So VisionAgent does the honest thing a vision-less system actually can do:
  1. If the target region has OCR text (or is a TABLE region, which is
     already structured text, not pixels) — answer using that real
     extracted text via AIGateway (text reasoning over text, not vision).
  2. If the question needs actual visual/semantic interpretation (a chart's
     shape, a diagram's structure, "what does this image show") and there's
     no usable text — return insufficient_visual_evidence=True with a clear
     message naming the real blocker, rather than guessing from the
     region's surrounding context and presenting that guess as having
     "seen" the image.

See Documents/AI/vision.md for exactly what this does and does not cover,
and what a real implementation needs (a vision-capable model/provider).
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("drdo-rag")

_MIN_USABLE_TEXT_LENGTH = 15  # shorter than this isn't enough to answer most
# questions about a figure's content — treated as "no usable text."

_INTERPRETATION_PROMPT = """You are analyzing a region extracted from a document. \
The following text was extracted from that region (via direct text extraction for \
tables, or OCR for images). Answer the question using ONLY this extracted text — \
do not infer anything about the region's visual appearance, layout, or any content \
not present in the text below.

Extracted text:
{text}

Question: {question}

Answer:"""


@dataclass(frozen=True)
class VisionAnalysisResult:
    answer: str
    insufficient_visual_evidence: bool
    basis: str  # "ocr_text" | "table_text" | "none"


class VisionAgent:
    def analyze_region(self, region, question: str, groq_client=None) -> VisionAnalysisResult:
        usable_text = None
        basis = "none"

        if region.region_type.value == "table":
            usable_text = region.extracted_text
            basis = "table_text"
        elif region.region_type.value == "image":
            if region.ocr_text and len(region.ocr_text.strip()) >= _MIN_USABLE_TEXT_LENGTH:
                usable_text = region.ocr_text
                basis = "ocr_text"

        if not usable_text or not usable_text.strip():
            reason = (
                f"OCR unavailable ({region.ocr_unavailable_reason})"
                if getattr(region, "ocr_unavailable_reason", None)
                else "no text could be extracted from this region"
            )
            return VisionAnalysisResult(
                answer=(
                    "This question requires visually interpreting the region's content "
                    f"(e.g. a chart's shape or a diagram's structure), which this deployment "
                    f"cannot do — no vision-capable model is configured, and {reason}. "
                    "See Documents/AI/vision.md."
                ),
                insufficient_visual_evidence=True,
                basis="none",
            )

        if groq_client is None:
            return VisionAnalysisResult(answer=usable_text, insufficient_visual_evidence=False, basis=basis)

        try:
            from VENUAI.ai_gateway.gateway import get_gateway
            from VENUAI.ai_gateway.model_router import TaskType

            gateway = get_gateway(groq_client)
            result = gateway.complete(
                task_type=TaskType.GENERATION,
                messages=[{"role": "user", "content": _INTERPRETATION_PROMPT.format(text=usable_text, question=question)}],
                max_tokens=400,
                temperature=0.1,
            )
            return VisionAnalysisResult(answer=result.content, insufficient_visual_evidence=False, basis=basis)
        except Exception as e:
            logger.warning(f"[VisionAgent] LLM interpretation failed, returning raw extracted text: {e}")
            return VisionAnalysisResult(answer=usable_text, insufficient_visual_evidence=False, basis=basis)
