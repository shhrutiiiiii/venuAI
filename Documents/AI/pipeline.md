# Answer pipeline (run_turn)

Code: `VENUAI/run_turn.py`, `corrective_rag.py`, `model_cascade.py`, `semantic_cache.py`, `followups.py`, `structured.py`, `token_budget.py`, `converge.py`, `ai_gateway/usage.py`. Decisions: ADR-B01..B03.

## Flow (standard RAG)
`begin_turn` (budget gate, usage meter, trace start, memory recall) -> answer cache (exact hash; semantic opt-in) -> LangGraph first pass: retrieve (rewrite, dense+BM25(+KG/RAPTOR), RRF, rerank, relevance floor, borderline grade) -> generate (cascade picks the model) -> verify (concurrent checks + safety) -> corrective stage (<= 2 iterations) -> `finish_turn` (contract fields, cache write, token accounting, memory write, trace).
ReAct (through the gateway model chain with circuit-breaker aware fallback) and multi-agent use `finish_agent_turn`.

## Corrective loop
Trigger: the NLI hallucination score >= 0.5 corroborated by the claim critic or a contradicted claim (each alone is too noisy: a correct "520 km" claim is flagged contradicted when a sibling document says 450 km), or (cascade) grounding < `ESCALATE_GROUNDING_BELOW` (default 0.1) on a 20b answer. Iteration 1 re-retrieves using the unsupported claims as extra queries and regenerates; iteration 2 regenerates with critic feedback (escalating to 120b / high effort once). Stops at `CORRECTIVE_MAX_ITERS` (hard max 2), on embedding convergence (`CONVERGE_COSINE`), or when the request is over budget. The best attempt is kept; the answer is replaced by the honest abstain text only when no claim is supported. Off for `depth="fast"`.

## Cache
Key = tenant + visible doc ids + corpus version + model config + normalised question. Never stored for blocked / abstained / unverified / KB-fallback answers, nor when history or memories were used (so a persistent chat session with working memory rarely hits the cache; this is deliberate). Invalidated on upload and delete. The semantic tier is off by default: MiniLM gives cosine >= 0.93 for Agni-V vs Agni-IV, so when enabled it needs >= 0.98 and an identical designator/number signature. `evals/semantic_cache_sweep.py` (stream B2) measures the failure modes.

## Request fields
`doc_ids` (scope; intersected with the caller's authorised docs, never widens), `depth` (fast|standard|deep), `history` as `{role, content}` or the UI's `{id, sender, text, timestamp}`.

## Response / done-event fields
`retrieved_chunks, usage, ttft_ms, latency_ms, cache_hit, model_path, followups, contradictions, corrective, trace_id, memories_used, pending_approval`. Tokens are provider-reported only (`null` / `complete:false` when unknown).

## Stream events
`data: {"step": {id, status: start|end|error, label, t_ms, detail}}` with ids `cache, retrieving, reading, generating, verifying, correcting, cascade` (`rewriting` / `reranking` happen inside retrieval and are not emitted separately). Then `{"model"}`, `{"token"}` lines and a final `{"done": true, ...}`.

## Latency profile (why /query took 20-54 s)
Measured: cold NLI model load ~50 s on the first verification (now warmed in a startup thread), 6.4 s warm for 4 claims x 6 chunks of single-pair NLI calls (now one batched call over each claim's 2 best chunks, max 6 claims), then three sequential LLM calls (generation, consistency, safeguard) of which the last two now overlap the other checks.
