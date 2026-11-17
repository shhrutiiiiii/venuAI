# Token-cost register

Every mechanism that spends Groq tokens **beyond the one answer-generation call**. Verified against the code (env names and defaults are the ones the modules read). The live on/off state of each is shown in the app at `/features` (backed by `GET /features`). Defaults are NOT changed by this document.

Model tiers: **20b** = `openai/gpt-oss-20b` (fast), **120b** = `openai/gpt-oss-120b` (primary), **safeguard** = `openai/gpt-oss-safeguard-20b`, **prompt-guard** = `meta-llama/llama-prompt-guard-2-*` (tiny classifier, a few tokens), **qwen** = `qwen/qwen3.8-27b` (eval judge only).

## Default ON

| Mechanism | Trigger | Model | Extra calls per event | Disable / tune | Advice |
|---|---|---|---|---|---|
| Contextual chunking (`contextual_chunking.py`) | per upload | 20b | 1 per chunk, capped 200/doc (so up to 200 per upload) | `CONTEXTUAL_CHUNKING=false`; `CONTEXTUAL_CHUNKING_MAX_CHUNKS` | **Biggest hidden cost.** Turn off to save tokens; retrieval still works, slightly less precise. |
| Safeguard policy classifier (`safety_classifiers.py`) | per query | safeguard | 1 (up to 1500 output tokens) | `SAFEGUARD_ENABLED=false`; `SAFEGUARD_MAX_TOKENS` | Keep for shared/production use; lower `SAFEGUARD_MAX_TOKENS` to trim. |
| Prompt Guard | per query + per uploaded chunk scan (first `INGEST_GUARD_MAX_CHUNKS`=200) | prompt-guard | 1 per query; up to 200 per upload | `PROMPT_GUARD_ENABLED=false`; `INGEST_GUARD_MAX_CHUNKS` | Tiny token cost each, but call count adds up on uploads. |
| Query rewriting (`query_rewriter.py`) | per query | 20b | ~1 (`QUERY_REWRITE_ALTERNATIVES`=1) | `QUERY_REWRITE_ENABLED=false` | Safe to disable for single-turn questions. |
| Corrective RAG (`corrective_rag.py`) | per query, only when retrieval looks weak | 20b grader (120 tok) + re-retrieval | up to `CORRECTIVE_MAX_ITERS`=2 rounds | `CORRECTIVE_RAG=off`; `CORRECTIVE_LLM_GRADER=off` (heuristic grading only); `CORRECTIVE_MAX_ITERS` | Turn off or set grader off to save tokens. |
| Follow-up suggestions (`followups.py`) | per answered query | 20b (200 tok) | 1 | `FOLLOWUPS=off` | Pure convenience; safe to disable. |
| Answer consistency check (`verifier_agent.check_consistency`) | per answer through the verifier | 20b (5 tok) | 1 tiny | none (part of verification) | Negligible. Grounding itself is local token-F1, no LLM. |
| Model cascade escalation (`model_cascade.py`) | per query when grounding is low | 20b first, then 120b | up to `MAX_ESCALATIONS`=1 extra full generation | `MODEL_CASCADE=off`; `MAX_ESCALATIONS=0`; `ESCALATE_GROUNDING_BELOW` | Can double the answer cost on hard questions; set `MAX_ESCALATIONS=0` to save. |
| Research jobs (`research_jobs.py`) | per user-started deep research | 120b/20b | quick: 3 sub-questions, budget 40k tokens; standard 5 / 120k; deep 8 / 300k | pick depth in the UI | Explicit user action only. |
| Token budgets (`token_budget.py`) | guard | n/a | caps spend: `REQUEST_TOKEN_BUDGET`=40000 per request, `TENANT_DAILY_TOKEN_BUDGET`=2,000,000 per tenant/day, downshift at `BUDGET_DOWNSHIFT_AT`=0.8 | env vars (0 = off) | Lower these to hard-cap spend. |
| Answer cache (`semantic_cache.py`) | per query | none | **saves** tokens (exact repeat hits) | `SEMANTIC_CACHE=off\|exact\|semantic` | Keep on. |

## Default OFF (no tokens until enabled)

| Mechanism | Trigger | Model | Extra calls | Enable | Note |
|---|---|---|---|---|---|
| Long-term memory (`memory_extractor.py`) | per turn (background, per opted-in user) | 20b | 1 per non-trivial turn (trivial turns skipped); recall is local embeddings | `LONG_TERM_MEMORY=on` + user opt-in | Background cost never shown to the user. |
| RAPTOR summaries (`raptor.py`) | per upload when on, or per explicit "Build summary" | 20b | up to `RAPTOR_MAX_CALLS`=6 per document | `RAPTOR=on` | Query-time retrieval of summaries is local. |
| Voice (`voice_api.py`) | per recording / per Listen click | whisper-large-v3-turbo, orpheus TTS | 1 each, explicit user action | `VOICE_ENABLED=on` | Needs Orpheus terms accepted in Groq console. |
| Online evals (`online_evals.py`) | sampled finished traces | none (local scoring) | 0 | `ONLINE_EVAL_SAMPLE` (default 0.0) | No Groq tokens. |
| Eval judge (`evals/judge.py`) | when you run evals | qwen (or `EVAL_JUDGE_API_KEY` key) | 1 per judged item | run evals manually | Dev-time only. |
| Local faithfulness (HHEM) | per answer | local CPU model | 0 | `LOCAL_FAITHFULNESS=on` | Zero Groq tokens. |
| A2A API, OTEL | external calls / telemetry | n/a | 0 unless an external agent calls | `A2A_ENABLED=on` | |
| Agent / tool loops (agentic mode) | per query when the user picks Agent mode | 120b | multiple tool-calling turns | UI toggle | Explicit user choice. |

## Top hidden consumers that are ON by default

1. Contextual chunking: up to 200 20b calls per upload.
2. Model cascade escalation: possible second full generation on weak answers.
3. Safeguard classifier: 1 call (up to 1500 output tokens) on every query.
4. Corrective RAG: up to 2 grading/retrieval rounds on weak retrievals.
5. Query rewriting + follow-ups: ~2 small 20b calls on every query.

## Quick token-saving profile

Paste into `VENUAI/.env` (does not change repo defaults):

```
CONTEXTUAL_CHUNKING=false
CORRECTIVE_RAG=off
QUERY_REWRITE_ENABLED=false
FOLLOWUPS=off
MAX_ESCALATIONS=0
# Optional, lowers safety coverage - keep on for shared deployments:
# SAFEGUARD_ENABLED=false
# Hard caps:
# REQUEST_TOKEN_BUDGET=20000
# TENANT_DAILY_TOKEN_BUDGET=500000
```
