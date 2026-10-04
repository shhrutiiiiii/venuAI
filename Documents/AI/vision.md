# Vision & OCR — VenuAI

## Purpose
States exactly what this deployment can and cannot do with images — the single most important honesty boundary in Phase 2. Read this before touching `VENUAI/agents/vision_agent.py` or `VENUAI/ocr_provider.py`.

## Responsibilities
- Name the real blocker (no vision-capable model configured) precisely.
- Describe what `VisionAgent` actually does instead, and why that's a legitimate capability rather than a workaround.
- Describe OCR's real/blocked split in this environment.

## Scope
Covers `VENUAI/agents/vision_agent.py` and `VENUAI/ocr_provider.py` only.

## High-Level Overview
**There is no vision-capable model in this deployment.** `VENUAI/ai_gateway/model_registry.py` registers zero `ModelCapability.VISION` entries — confirmed by the standing regression test `VENUAI/tests/test_ai_gateway.py::test_no_vision_model_is_registered`. Groq's text-chat models used throughout this codebase cannot see pixels; routing an image question to one and presenting its guess as a real answer would be exactly the fabrication spec section 68 forbids.

**OCR is a real, installed library (`pytesseract`) wrapping a Tesseract binary that is not installed in this environment.** `VENUAI/ocr_provider.py`'s `TesseractOCRProvider.is_available()` checks this at call time — today it returns `False` here. `extract_text()` then raises `OCRProviderUnavailableError` naming the exact gap (install the Tesseract binary, not the Python package, which is already present) rather than returning a fabricated empty-string "success."

Given both of these constraints, `VisionAgent.analyze_region()` does the one honest thing available:
```
Region has usable text (table extraction, or OCR that succeeded)?
  ├─ YES → answer via AIGateway, reasoning over that real extracted text
  │        (text-in-text-out — a legitimate LLM capability, not vision)
  └─ NO  → insufficient_visual_evidence=True, with the real reason named
           (no vision model configured, and/or OCR unavailable/empty)
```
This is tested exhaustively in `VENUAI/tests/test_vision_agent.py` — including a regression test (`test_never_fabricates_an_answer_without_any_text_or_model`) that asserts the agent refuses to guess.

## Design Principles
1. **Text reasoning over real extracted text is not vision — and must never be presented as if it were.** `VisionAgent`'s successful path answers using OCR'd or table text that genuinely exists; it never infers visual layout, color, shape, or anything not present in that text.
2. **A short/empty OCR result is treated as "no usable text," not "nothing interesting here."** `_MIN_USABLE_TEXT_LENGTH = 15` chars — a couple of stray OCR characters from a logo is not enough basis to answer a real question about an image's content.
3. **The unavailability reason is always the real one.** `ocr_unavailable_reason` is threaded from `OCRProviderUnavailableError`'s actual message all the way to the final answer text — never a generic "something went wrong."

## Workflow
See [`multimodal.md`](multimodal.md) Workflow — `VisionAgent` is invoked from `POST /documents/{id}/regions/{id}/ask`.

## Inputs
A `DocumentRegion` (table or image, from real extraction — see [`multimodal.md`](multimodal.md)) and a natural-language question.

## Outputs
A `VisionAnalysisResult`: `answer`, `insufficient_visual_evidence: bool`, `basis` (`"ocr_text"` | `"table_text"` | `"none"`).

## Dependencies
- [`multimodal.md`](multimodal.md) — where regions come from.
- `VENUAI/ai_gateway/` — the text-reasoning call when there is usable text.

## Interactions with Other Services
`VisionAgent` calls `AIGateway.complete()` (Phase 1) only when it has real text to reason over; it never calls any image/vision endpoint, because none is configured.

## Security Considerations
No new surface — OCR operates on already-extracted image bytes from an already-validated PDF upload (see [`multimodal.md`](multimodal.md) Security Considerations).

## Performance Considerations
OCR (when available) runs synchronously per image region, inside the `/upload/multimodal` request — for PDFs with many images, this could become the dominant cost. Not yet measured, since Tesseract isn't installed in this environment to measure against.

## Future Improvements (all genuinely blocked on external resources, not effort)
1. **Install the Tesseract binary** on the deployment host to make the already-written `TesseractOCRProvider` actually functional — zero code changes needed, this is purely an infrastructure/ops step.
2. **Configure a real vision-capable model** (e.g. a vision-enabled Groq model if/when one becomes available, or another provider) and register it in `ModelRegistry` with `ModelCapability.VISION`. Once that exists, `VisionAgent` should be extended to call it directly for regions lacking usable text, rather than only ever falling back to "insufficient evidence."
3. Until #2 exists, do not add any heuristic (file size, aspect ratio, color analysis) that tries to approximate "what kind of image is this" — that is exactly the fabrication this document exists to prevent. See `Documents/AI/multimodal.md` Notes for Developers.

## Notes for Developers
If a future change makes `VisionAgent` return a confident-sounding answer for an image with no usable text and no vision model, that is a regression against this document and against `test_never_fabricates_an_answer_without_any_text_or_model` — treat it as a bug, not a feature.
