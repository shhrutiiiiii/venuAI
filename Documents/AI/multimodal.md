# Multimodal Document Intelligence — VenuAI

## Purpose
Documents Phase 2's real multimodal capability: what's genuinely extracted from PDFs, what's genuinely retrievable, and what's explicitly not implemented (not faked) because it needs infrastructure this deployment doesn't have.

## Responsibilities
- Describe the document/page/region object model.
- Describe the real extraction pipeline and its two backing libraries.
- State plainly what "multimodal RAG" does and does not mean here.

## Scope
Covers `VENUAI/multimodal_document.py`, `VENUAI/multimodal_ingestion.py`, `VENUAI/multimodal_retrieval.py`, and the `/upload/multimodal`, `/documents/{id}/regions`, `/documents/{id}/regions/{id}/ask`, `/documents/regions/search` routes. Vision/OCR specifics are in [`vision.md`](vision.md).

## High-Level Overview
A PDF uploaded via `POST /upload/multimodal` is parsed by two real, installed libraries — **not** mocked:
- **PyMuPDF (fitz)**: page layout → `TEXT`/`HEADING` regions (heading = font size ≥1.3× the page's median), plus embedded-image extraction with real bounding boxes and raw bytes.
- **pdfplumber**: real table detection (grid-line based) → `TABLE` regions with extracted cell text.

Every region in `VENUAI/multimodal_document.py`'s `RegionType` enum that this pipeline can actually produce (`TEXT`, `HEADING`, `TABLE`, `IMAGE`) is genuinely assigned by real parsing logic — verified end-to-end in `VENUAI/tests/test_multimodal_ingestion.py` against an actual generated PDF (text, a drawn table grid, and an embedded image), not a mocked one. `CHART`, `DIAGRAM`, and `EQUATION` exist in the enum but are **never assigned** — distinguishing a chart from a diagram from a photo from a logo requires visual/semantic understanding this deployment doesn't have. See [`vision.md`](vision.md).

This is a **separate document path from `/upload`** — multimodal ingestion does not embed anything into Chroma. A PDF can go through `/upload` (RAG, text-chunk embedding), `/upload/multimodal` (region extraction), or both, independently.

## Design Principles
1. **Never claim a classification the extraction can't support.** `TEXT`/`HEADING`/`TABLE`/`IMAGE` are deterministic and real; `CHART`/`DIAGRAM`/`EQUATION` are reserved for when a real vision model exists, and `VENUAI/tests/test_multimodal_ingestion.py::test_no_region_is_ever_classified_as_chart_or_diagram` is a standing regression guard against ever assigning them without one.
2. **OCR unavailability is reported, not hidden.** Every `IMAGE` region either has real `ocr_text`, or an explicit `ocr_unavailable_reason` naming why — see [`vision.md`](vision.md).
3. **Authorization applies to multimodal documents exactly like text documents.** Ownership (`owner_id`) + classification (`PUBLIC`/`UNKNOWN`) gate `/documents/{id}/regions`, the `ask` endpoint, and `search_multimodal_regions` — same rule as [`../Backend/authentication.md`](../Backend/authentication.md), reusing the same `_check_document_access` pattern as the rest of `rag_server.py`.
4. **Resource limits are real, not cosmetic.** `MAX_MULTIMODAL_PAGES = 50` rejects oversized PDFs before processing — there's no background job queue (Phase 1 ADR-003), so an unbounded page count is a real DoS vector on this synchronous endpoint.

## Workflow
```
POST /upload/multimodal (PDF)
  │  validate_upload() — magic bytes, size limit (same as /upload)
  │  page-count check (≤ MAX_MULTIMODAL_PAGES)
  ▼
extract_multimodal_document()
  ├─ PyMuPDF: text/heading regions + embedded images (+ bounding boxes)
  └─ pdfplumber: table regions (+ bounding boxes)
  │  (each IMAGE region optionally OCR'd — see vision.md)
  ▼
Stored in doc_metadata.json (same store as /upload, as "multimodal_summary")
  │
  ├─ GET /documents/{id}/regions           → full region summary (auth-checked)
  ├─ POST /documents/{id}/regions/{id}/ask  → VisionAgent over one region
  └─ POST /documents/regions/search         → lexical search across all accessible multimodal docs
```

## Inputs
A PDF file; a search query; a question about a specific region.

## Outputs
A `MultimodalDocument` summary (JSON-serializable, excludes raw image bytes); region search results; `VisionAgent` answers with an explicit `insufficient_visual_evidence` flag.

## Dependencies
- [`vision.md`](vision.md) — the OCR/vision honesty boundary.
- [`../Backend/authentication.md`](../Backend/authentication.md) — the authorization model this reuses.
- `VENUAI/evidence.py` — `Evidence.from_region()` is how a region becomes citable evidence.

## Interactions with Other Services
**Updated (Phase 4):** standard RAG (`VENUAI.rag_server.retrieve_node`) and Agentic RAG (`VENUAI.agentic_rag._retrieve`) now both fold in table/image region hits via `VENUAI.multimodal_retrieval.retrieve_multimodal_documents()`, on top of the text-chunk Chroma+BM25 results — a question asked through the normal `/query` or `/research` path now surfaces a relevant table from a multimodal-ingested document, not just plain-text chunks. `POST /documents/regions/search` remains a separate, standalone endpoint for browsing regions directly (e.g. from a document's region list in the UI) — the two paths share the same underlying `search_multimodal_regions()` function, just a different entry point. The ReAct agent and `/query/stream` do **not** yet get this fold-in — see Future Improvements.

## Security Considerations
Same upload validation as `/upload` (magic bytes, size cap); same auth/ownership model; a real page-count cap against oversized-PDF DoS. No new attack surface beyond what `/upload` already has — this endpoint parses the same untrusted-PDF input, just with two additional libraries (PyMuPDF, pdfplumber), both of which should be kept current for their own CVE history like any PDF-parsing dependency.

## Performance Considerations
PyMuPDF + pdfplumber both re-parse the same PDF independently (two full passes) — acceptable at the `MAX_MULTIMODAL_PAGES` ceiling, would need optimization (e.g. a single shared parse) if that ceiling were raised significantly.

## Future Improvements
1. ~~Wire multimodal retrieval into the main `/query` pipeline~~ — **done, all five modes (Phase 4 + 5).** Standard RAG and Agentic RAG in Phase 4; `/query/stream`, the ReAct agent's `drdo_kb_search` tool, and multi-agent specialist retrieval (`BaseSpecialistAgent.query`) in Phase 5 — see `VENUAI/tests/test_multimodal_rag_integration.py` and `VENUAI/tests/test_phase5_multimodal_integration.py`. Every retrieval path in this codebase now folds in table/image region hits identically.
2. **Real semantic chart/diagram classification** once a vision-capable model is configured — see [`vision.md`](vision.md).
3. **Vector/embedding-based multimodal retrieval** (today's is lexical/token-overlap) if lexical search proves insufficient at scale.

## Notes for Developers
If you're tempted to add chart/diagram/equation detection via image heuristics (aspect ratio, color histogram, etc.) — don't, without updating this document and the standing regression test explicitly. Those heuristics are not the same thing as actually understanding the image's content, and presenting them as such is exactly the fabrication this phase was built to avoid.

## CPU alternative to ColPali (Wave B, stream B2)
ColPali / ColQwen retrieve page *images* with a vision-language model (3B+ parameters, patch-level late interaction). That needs a GPU-class runtime and a vision model this deployment does not have (see [`vision.md`](vision.md)), so it is **not implemented**. `multimodal_retrieval.colpali_retrieve()` is an env-gated stub: with `COLPALI_ENABLED=on` it raises `NotImplementedError` (fail loudly) instead of pretending.

What runs on CPU instead: page regions (tables, images) carry OCR/table text from ingestion; `retrieve_multimodal_documents` scores that text lexically per authorised document and `retrieval.retrieve_documents(include_regions=True)` blends those region documents into the hybrid (dense + BM25 -> rerank) candidate pool, so a question answered by a table or a scanned figure caption can surface its region with page and bounding box provenance. Limits: image regions without OCR text have no retrievable content (the placeholder is never presented as a description), and purely visual questions (shapes, colours) cannot be answered. Reconsider ColPali only if a GPU host and a vision model are provisioned.
