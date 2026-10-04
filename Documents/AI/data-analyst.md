# Data Analyst Agent — VenuAI

## Purpose
Documents the full, real, end-to-end Data Analyst feature: dataset upload → deterministic pandas analysis → chart-shaped results → frontend display, per spec section 21.

## Responsibilities
- Describe the dataset storage model and why it's separate from the RAG document store.
- State the one rule the whole feature is built around: every number comes from pandas, never from the LLM.
- Cover the full UI → API → agent → response → UI path, since that's what this feature was explicitly required to deliver end-to-end.

## Scope
Covers `VENUAI/dataset_security.py`, `VENUAI/agents/data_analyst_agent.py`, the `/datasets/*` routes, and the frontend `DataAnalystWorkspace`/`DataBarChart` components.

## High-Level Overview
`DataAnalystAgent.analyze(df, question, groq_client)` (Phase 1) computes real statistics with pandas/NumPy based on keyword + column-name matching against the question (mean/sum/correlation/growth-rate/groupby/missing-values/duplicates, falling back to `describe()`). The LLM is only ever asked to phrase an explanation of numbers that were already computed — see the module's own docstring and `_INTERPRETATION_PROMPT`, which explicitly instructs the model not to recompute or contradict the given statistics. `DataAnalysisResult.to_chart_data()` (Phase 2 addition) is a pure, additive presentation-layer transform of those same numbers into a bar-chart shape — it does not touch the computation branches.

Phase 2's job was wiring this into something an actual user can drive from the UI:

```
Browser                          Next.js proxy             FastAPI backend
───────                          ──────────────             ───────────────
DataAnalystWorkspace
  upload .csv/.xlsx     ──────►  /api/datasets/upload ────► POST /datasets/upload
                                                              (VENUAI/dataset_security.py:
                                                               extension, size, magic bytes
                                                               for xlsx, real pandas parse +
                                                               row/column caps, owner_id set)
  dataset list           ◄─────  /api/datasets         ◄──── GET /datasets
                                                              (owner/PUBLIC/admin filter)
  ask a question          ──────►  /api/datasets/{id}/analyze ──► POST /datasets/{id}/analyze
                                                              (ownership check → guardrail
                                                               input check → load stored file
                                                               → DataAnalystAgent.analyze())
  result + chart           ◄─────  (same route)          ◄──── DatasetAnalyzeResponse
                                                              (computed_values, interpretation,
                                                               chart_data)
DataBarChart (inline SVG) renders chart_data exactly as computed server-side
```

Storage is deliberately **separate** from the RAG document store (`DATASET_DIR`/`DATASET_META_FILE`, distinct from `UPLOAD_DIR`/`META_FILE`) — a dataset is a different kind of object (tabular, analyzed by pandas) from a RAG document (chunked, embedded into Chroma), and conflating the two stores would make both harder to reason about.

## Design Principles
1. **The pandas computation logic is untouched.** Phase 2 added `to_chart_data()` as a new method and new API/frontend layers around `DataAnalystAgent` — it did not modify any `analyze()` branch, per the explicit instruction this work was scoped under.
2. **Security mirrors the existing upload pattern, not a new one.** `VENUAI/dataset_security.py` follows the same shape as `VENUAI/upload_security.py` (extension allowlist, size cap, content verification, filename sanitization) with the two additions tabular data actually needs: real-parse validation (CSV has no magic bytes, so a failed `pd.read_csv`/`read_excel` *is* the validation) and row/column caps (`MAX_ROWS=100_000`, `MAX_COLUMNS=200`) — this endpoint is synchronous with no job queue (ADR-003), so an unbounded dataset is a real resource-exhaustion vector, not just a style concern.
3. **Authorization is identical to every other object in this system.** `tenant_id` is checked first (Phase 4 — see [`../Backend/multi-tenancy.md`](../Backend/multi-tenancy.md)), then `owner_id` + `classification` (`PUBLIC`/`UNKNOWN`), same `_check_dataset_access` pattern as documents/multimodal regions.
4. **The frontend never recomputes anything.** `DataBarChart` renders exactly the `labels`/`values` the backend sent — no client-side math beyond scaling bar heights to pixel space.

## Workflow
See the diagram above. On the frontend, the Data Analyst workspace is the 4th tab in `chat-app/src/app/chat/page.tsx`'s workspace switcher (`workspaceMode === "data_analyst"`), rendering `DataAnalystWorkspace` — a self-contained component (its own upload control, dataset list, question input, and results panel) rather than threading dataset state through the chat page's existing (already large) state, to keep this feature's surface area in the chat page itself minimal: one state value, one tab button, one render branch, one conditional hiding the unrelated chat input bar for this mode.

## Inputs
A `.csv`/`.xlsx` file; a natural-language question about an uploaded dataset.

## Outputs
`DatasetInfo` (schema: columns, dtypes, row count); `DatasetAnalyzeResponse` (operation, computed_values, interpretation, chart_data).

## Dependencies
- `VENUAI/upload_security.py` — the pattern `dataset_security.py` mirrors.
- [`../Backend/authentication.md`](../Backend/authentication.md) — the ownership/classification rule reused here.
- [`agents.md`](agents.md) — `DataAnalystAgent`'s registry entry.

## Interactions with Other Services
Standalone — not wired into the RAG `/query` pipeline or the knowledge graph. A dataset's contents are not retrievable by `/query` or `/graph/search`.

## Security Considerations
- Real content validation: CSV is validated by successfully parsing it with pandas (no magic bytes exist for CSV); XLSX is validated by both its zip magic bytes (`PK\x03\x04`) and a successful `pd.read_excel`.
- `MAX_ROWS`/`MAX_COLUMNS` caps reject oversized datasets before they're stored, not just before they're analyzed.
- Prompt-injection guardrail check runs on the analysis question before `DataAnalystAgent.analyze()` is called — same check as `/query` and `/research`.
- File storage uses collision-proof, non-client-controlled filenames (`generate_storage_name`), same path-traversal protection as `upload_security.py`.

## Performance Considerations
`/datasets/{id}/analyze` re-reads and re-parses the stored CSV/XLSX file from disk on every call (no in-memory caching of parsed DataFrames) — acceptable at the `MAX_ROWS`/`MAX_COLUMNS` ceiling; would need a cache if that ceiling were raised or call volume grew significantly.

## Verification (Phase 4 gap-closing pass)
The full chain was verified end-to-end over real HTTP, through the actual Next.js proxy routes (`/api/datasets/upload`, `/api/datasets`, `/api/datasets/{id}/analyze`) — not just the FastAPI backend directly: a real backend (`uvicorn`) and a real frontend (`next start`) were run together, a real CSV was logged in via `/api/auth/login` (httpOnly cookies), uploaded, and analyzed with a real question, confirming pandas computed the correct answer (a four-row sum) and `chart_data` came back in exactly the shape `DataBarChart.tsx`'s `ChartData` type expects. The test artifacts were deleted and both verification server processes stopped afterward.

**What this does and does not prove:** every hop in "browser → Next.js proxy → FastAPI → DataAnalystAgent → pandas → response → chart-shaped JSON" is now confirmed correct and real. It does **not** confirm the literal DOM-level interaction (file-input `onChange`, button `onClick`, `DataBarChart` actually painting pixels) — that last mile needs either a human clicking through it, or browser automation, which this project's standing instructions require explicit per-use permission for.

## Future Improvements
1. Cache parsed DataFrames (keyed by dataset id + file mtime) if repeated analysis calls on the same dataset become a measured bottleneck.
2. A richer chart vocabulary (line charts for time-series growth, scatter for correlation pairs) beyond the current bar-only `DataBarChart` — deliberately kept to one real, working chart type in this phase rather than a half-built set of several.
3. Wire dataset content into the knowledge graph / research pipeline (e.g., "what does this dataset say about X" inside a `DeepResearchAgent` run) — not done in this phase; `DataAnalystAgent` and `DeepResearchAgent` remain independent capabilities today.

## Notes for Developers
If you're tempted to have the LLM "double-check" or "refine" a computed number, don't — re-read `DataAnalystAgent`'s module docstring and `_INTERPRETATION_PROMPT`. The one invariant this entire feature depends on is that every number a user sees was computed by pandas, not generated by a model.
