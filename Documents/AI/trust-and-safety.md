# Trust & Safety: guardrails, injection defence, verification

## Purpose
VenuAI is a defence R&D assistant; trust is the product. Every answer path (`/query` standard, ReAct, multi-agent, KB fallback; `/query/stream`; `/research`) is guarded on the way in, hardened against hostile documents, verified honestly, and checked on the way out. Anything that cannot be measured is reported as `null` / `verified=false` with a reason, never as a made-up number.

## Responsibilities
- Input guardrails: regex (`guardrails.py`) plus the Groq Prompt Guard classifier (`safety_classifiers.py`).
- Indirect-injection defence: ingest sanitiser/scanner (`doc_sanitizer.py`) and prompt-time spotlighting/quarantine (`context_engineer.build_drdo_prompt`).
- Quote-level citations (`grounded_citations.py`).
- Fail-closed verification, output guardrails and the policy classifier (`answer_pipeline.py`).

## Workflow
1. **Input**: `GuardrailEngine.validate_input` runs on `/query`, `/query/stream` and `/research`: injection regex, sensitivity/PII, domain, then Prompt Guard (`meta-llama/llama-prompt-guard-2-86m`, 4 s timeout; on failure it logs and the regex result stands). A block returns `blocked=true`, `block_reason` (`prompt_injection`, `prompt_injection_classifier`, `sensitivity`, `out_of_scope`, `input_validation`). `/query` keeps HTTP 200 plus the legacy `GUARDRAIL_TRIGGERED` flag; `/query/stream` emits a single `blocked` SSE event and ends (no retrieval, no LLM); `/research` returns 400 with a structured `detail`.
2. **Ingest**: `sanitize_and_scan_chunks(chunks)` (called once before indexing in `/upload`) strips zero-width/bidi/tag/control characters and hidden HTML, regex-scans for instruction patterns and Prompt-Guard-scans chunks (thread-pooled, capped by `INGEST_GUARD_MAX_CHUNKS`, default 200; the rest are regex-only and `injection_scan` records which). Chunk metadata gets `injection_risk` (`none|low|medium|high`), `injection_flags`, `injection_scan`, optional `injection_guard_score`.
3. **Prompt build** (`build_drdo_prompt`): each chunk is re-sanitised (also covers pre-existing chunks with no metadata), wrapped in a per-request-nonce `<untrusted_document-NONCE id="n" ...>` block with an instruction that its content is data and must never be obeyed, prompt-structure tags inside it are defused, and `high` risk chunks are quarantined (withheld, logged, counted in `quarantined_chunks`). `info["used_docs"]` records exactly which documents were shown, so marker `[n]` maps to `used_docs[n-1]`.
4. **Citations**: the generator cites `[1]`, `[2]` and ends with a `<citations>` block of short verbatim quotes. `grounded_citations` strips the block and deterministically checks each quote against the cited chunk (case/whitespace/quote-style normalised, `...` skips allowed). Each citation: `index, chunk_id, doc_id, filename, page, quote, verified, char_start, char_end, reason`. Paraphrases and invented markers are `verified=false`.
5. **Verification** (`run_verification`): VerifierAgent + HallucinationDetector + VerificationPipeline; any error gives `verified=false`, `verification_reason="verification error: <Type>"` and null scores (no HTTP 500, no silent pass). The LLM consistency check returning "unknown" is no longer treated as consistent. Agent paths (ReAct, multi-agent) use a light mode (token-overlap grounding + hallucination detector, no claim-level scores). `verified` is true only if no error, grounding >= 0.5 and the critic passed.
6. **Output**: `validate_output` redacts API keys / Aadhaar-like numbers, blocks internal prompt-markup leaks and appends the low-grounding warning (only when grounding was actually measured). Then `openai/gpt-oss-safeguard-20b` classifies the answer against a written defence policy (classified-info leakage, weapons uplift beyond public info, PII, credential leak). `safety_check` is `passed | flagged | unavailable | disabled`; a classifier failure fails open with a logged warning and `unavailable` (never reported as passed). A flagged answer is replaced by a refusal with `blocked=true`, `block_reason="output_policy:<CATEGORY>"`.
7. **Built-in KB fallback**: when no uploaded documents match, the answer is labelled "built-in knowledge base, not from your documents" (`answer_basis="builtin_knowledge_base"`), cites the KB document, and is verified against the KB text actually used.

## Inputs / Outputs
New response fields (`/query`), also present in the `/query/stream` `done` event: `blocked`, `block_reason`, `verified`, `verification_reason`, `citations[]`, `safety_check`, `answer_basis`, `answer_basis_notice`, `quarantined_chunks`, `trace_id`. `grounding_score`, `hallucination_score`, `evidence_coverage`, `unsupported_claim_count`, `contradicted_claim_count` are now nullable (null = not measured). The stream `done` event also carries `answer`: the final citation-free (and possibly guardrail-replaced) text, which should replace the streamed text.

## Dependencies
Groq models `meta-llama/llama-prompt-guard-2-86m`, `openai/gpt-oss-safeguard-20b`. Env: `PROMPT_GUARD_ENABLED`, `PROMPT_GUARD_MODEL`, `PROMPT_GUARD_TIMEOUT`, `PROMPT_GUARD_THRESHOLD`, `SAFEGUARD_ENABLED`, `SAFEGUARD_MODEL`, `SAFEGUARD_TIMEOUT`, `INGEST_GUARD_MAX_CHUNKS`, `SEED_DEMO_USER`.

## Security Considerations
- Error hygiene: 500s return `Query execution failed. Reference trace ID: <id>` (also `X-Trace-Id`); details are logged server-side. Stream errors do the same.
- Demo users (`demo@chat.com`, `admin@chat.com`) are seeded unless `SEED_DEMO_USER=false`. Set it to `false` in production.
- Quote verification proves a quote exists in the cited chunk; it does not prove the claim follows from it (that is the claim-level critic's job).
- Prompt-Guard/safeguard calls go straight to the Groq client with short timeouts, not through the AI gateway fallback chain.
- A defence document that legitimately discusses prompt injection can be quarantined (false positive); this is logged and counted.

## Performance Considerations
Input check adds one small classifier call (~100-300 ms). Ingest adds up to 200 small classifier calls per upload (8 in parallel). Output safety adds one 20B-model call per answer; streaming runs verification after the last token, so `done` arrives later than the last token.

## Future Improvements
Route the classifiers through the gateway; persist `injection_risk` for chunks of legacy uploads by re-scanning; per-claim quote linking; UI surfacing of citations and `safety_check`.

## Notes for Developers
Tests: `VENUAI/tests/test_trust_safety.py` (canary corpus, citations, stream guardrails, fail-closed, no hardcoded scores). Classifier calls are all funnelled through `safety_classifiers._groq_chat` for easy mocking; `tests/conftest.py` disables them by default.
