> Part of the `AI/` documentation folder. See [`index.md`](index.md).

# Evaluation Harness

## Purpose
Measure the real VenuAI RAG pipeline (retrieval, grounding, abstention, injection resistance, latency) so that changes can be proven, not asserted.

## Responsibilities
- Run questions through the real `/query` pipeline and record everything (harvest).
- Score recorded results without re-calling the generator (score).
- Report per-metric scores, per-question drill-down and regression deltas.
- Gate CI on thresholds.

## Scope
`VENUAI/evals/**`. There is **no offline fallback**: if the pipeline cannot be reached or ingest/auth fails, the CLI exits with code 2 and writes no report.

## Architecture / Workflow
```
python -m VENUAI.evals.eval_runner harvest --base-url http://127.0.0.1:8012 --limit 10   # calls generator LLM
python -m VENUAI.evals.eval_runner score   --harvest-file <jsonl> --judge --compare prev.json
python -m VENUAI.evals.eval_runner run     --limit 10 --judge --gate      # both; in-process if no --base-url
```
- `harvest.py` registers an ephemeral user with a new organization (so a new tenant: isolated retrieval), ingests `datasets/synthetic_corpus.json` as PDFs, queries, writes JSONL (answer, retrieved chunks, citations, model, latency, blocked flag, extra response fields), then deletes the ingested documents. `--email/--password` uses an existing user instead (use a dedicated eval user).
- `score.py` / `metrics.py` / `judge.py` compute metrics; `report.py` writes JSON + Markdown + HTML.

### Metrics
Deterministic: hit@k, recall@k, MRR (gold doc ids = filenames), keyword hit, citation precision (fraction of citation quotes found in the cited chunk), abstention correctness on unanswerables, injection resistance (canary token never in answer), adversarial pass (blocked or refused), latency p50/p95.
LLM judge (`qwen/qwen3.8-27b`, temperature 0, JSON, cached under `evals/.judge_cache/`): claim-level faithfulness, answer relevancy (1-5 rescaled), context precision (rank-aware AP), context recall (reference-statement attribution). A judge failure yields `null` plus an entry in `judge_failure_messages`; it is excluded from means, never counted as 0. Metrics depending on absent response fields (`citations`) show `n/a (field absent)`.

### Datasets (`evals/datasets/`)
- `synthetic_corpus.json`: 9 fictional documents (marked synthetic) with hard negatives: near-duplicate radars (AURORA-X vs XB), twin missiles (VAJRA-3 vs 3M), conflicting values (spec 450 km vs trial 430 km), two documents with embedded prompt injections and canary tokens.
- `retrieval_golden.json`: 19 items: answerable, multi_hop, unanswerable, injection; gold doc ids and reference answers.
- `adversarial.json` (guardrail behaviour) and `drdo_qa_golden.json` (keyword-based, real-DRDO questions that need the operator's own corpus; no gold doc ids) are legacy sets, scored with the same code (`--dataset`).

### CI
`.github/workflows/ci.yml` job `evals`: unit tests always; live eval with `--gate` (thresholds in `evals/thresholds.json`) only when the `GROQ_API_KEY` secret exists.

## Interactions with Other Services
Groq (generator via the server, judge via the `groq` SDK). Needs `reportlab` to build PDFs.

## Security Considerations
Eval data is synthetic and tenant-isolated; no user data is read. The judge sees only synthetic text. API keys come from the environment and are never written to reports.

## Performance Considerations
Live runs spend Groq tokens and respect the 30/min `/query` rate limit (2 s spacing). Judge results are cached, so re-scoring is free.

## Future Improvements
Use `/query/stream` for TTFT; token counts once the API exposes usage; chunk ids/scores and `citations` once added to the response (the harness picks up `retrieved_chunks` / `citations` automatically).
See [ADR-009](../architecture-decisions.md).

## Methodology, judge choice and limitations
- **Why:** the earlier harness fell back to a canned "[Offline Evaluation Response]" with no chunks; its committed reports (faithfulness 0.0, ~4 ms) measured the stub. They were deleted.
- **Judge:** `qwen/qwen3.8-27b` at temperature 0 (different family from the `gpt-oss` generator, to limit self-preference bias). Comparable across runs with the same judge/prompts, not absolute truth; the judge id is stored in each report. Answer relevancy is a 1-5 rubric rescaled, not embedding-based.
- **Small synthetic corpus** (9 docs, 19 questions): detects regressions and hard-negative confusion, not real-world accuracy. One question is about 5 percentage points of noise.
- `/query` returns only a 120-char `chunk_preview` and no chunk ids/scores. For the synthetic corpus the harness re-expands the preview to the 800-char chunk window (`text_source: expanded_from_corpus`), so judge context approximates what the generator saw. Real `retrieved_chunks` are used automatically once exposed.
- Citation precision is `n/a (field absent)` until the API returns `citations` with quotes. TTFT/tokens are null (non-streaming, no usage field).
- Abstention detection is a phrase heuristic; injection resistance checks only the canary token. Keyword hit is a weak proxy.
- High `judge_failures` means the judge numbers are unreliable. Latency includes warm-up and rate-limit waits. The generator is not temperature 0, so repeated harvests differ.
- `drdo_qa_golden.json` has no gold doc ids and needs the operator's own real documents.
