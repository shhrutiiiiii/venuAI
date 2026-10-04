# Workflows — VenuAI

## Purpose
Diagrams all three LangGraph/agent execution paths behind `/query`, side by side, so the difference between them is explicit rather than something a reader has to reconstruct from `rag_server.py`.

## Responsibilities
Show each mode's state machine and what quality signals (if any) it produces.

## Scope
Execution-path detail only. Agent internals are [`agents.md`](agents.md); prompt text is [`prompts.md`](prompts.md).

## High-Level Overview

### 1. Standard RAG (`venuai_graph`, the default)
```
START
  │
  ▼
route_query_node  ──no documents──► fallback_kb_node ──► END
  │ documents exist
  ▼
retrieve_node   (Chroma + BM25 EnsembleRetriever)
  │
  ▼
generate_node   (context_engineer prompt → Groq)
  │
  ▼
verify_node     (VerifierAgent → grounding_score; HallucinationDetector → hallucination_score, annotated_answer)
  │
  ▼
END
```
Produces real `grounding_score` and `hallucination_score` on every answer.

### 2. ReAct agent (`agent_mode: true`)
```
create_react_agent(ChatGroq, ALL_TOOLS)
  │
  ▼
agent.invoke({"messages": [user question]}, recursion_limit=8)
  │
  ▼
final message content returned as the answer
```
No retrieval step, no verify step. `grounding_score` is hardcoded to `0.7` in the response — **not a measured score**, a placeholder constant. `tools_called` is collected by scanning returned messages for `tool_calls`.

### 3. Multi-agent (`multi_agent_mode: true`)
```
OrchestratorAgent.route(question, history, mem_ctx)
  │  (see agents.md Workflow for the classify/fan-out/merge detail)
  ▼
AgentResult(answer, sources, confidence, ...)
```
`grounding_score` in the response is the orchestrator's average specialist `confidence` — a self-reported LLM confidence, not an independent verification score like mode 1's.

## Design Principles
1. **`grounding_score` still differs by mode — `evidence_coverage`/`critique_passed` no longer do (Phase 5).** Modes 2 and 3's `grounding_score` still comes from the generating process itself (a hardcoded constant, or self-reported agent confidence), not an independent check — that real difference remains. But all three modes now run the actual claim-level `VerificationPipeline` and report genuine `evidence_coverage`/`unsupported_claim_count`/`contradicted_claim_count`/`critique_passed`, using each mode's real retrieved documents (`AgentResult.documents` for multi-agent; `collected_docs`, threaded out of `drdo_kb_search`'s closure, for ReAct) — see [`evidence-explorer.md`](evidence-explorer.md). Anyone building on `grounding_score` specifically still needs to check `model`/`agent_used`; anyone building on the evidence/critique fields does not.
2. **`/query/stream` follows a stripped-down version of mode 1** — retrieval + context-engineered generation, but **no `verify_node` equivalent at all**. Its `grounding_score` in the final SSE event is a hardcoded `0.8`. It does fold in multimodal region hits now (Phase 5), the same as every other retrieval path — just without the claim-level verification step.

## Workflow
See diagrams above — this document's entire content is workflow diagrams by design.

## Inputs
Same as [`index.md`](index.md).

## Outputs
Same `QueryResponse` shape across all modes (see [`../Backend/api.md`](../Backend/api.md)), with the caveat above about what `grounding_score` actually measures per mode.

## Dependencies
- [`agents.md`](agents.md)
- [`prompts.md`](prompts.md)
- [`../Backend/architecture.md`](../Backend/architecture.md)

## Interactions with Other Services
Covered per-mode above.

## Security Considerations
N/A beyond what's already covered in [`../Backend/architecture.md`](../Backend/architecture.md).

## Performance Considerations
Mode 1 is the most expensive per answer (retrieve + generate + verify = at least 2 LLM calls); mode 3 can be more expensive still when multi-domain (see [`agents.md`](agents.md) Performance Considerations); mode 2 is bounded by `recursion_limit=8` tool-call iterations.

## Future Improvements
- Make `grounding_score`'s meaning consistent across modes, or rename the field per-mode so callers don't assume it's always an independently-verified score — still open; only the evidence/critique fields were unified in Phase 5.
- ~~Add verification to mode 2/3~~ — **done (Phase 5)**, see Design Principle 1. `/query/stream` still has no `verify_node`/`VerificationPipeline` equivalent at all — it only gained the multimodal-region retrieval fold-in.

## Notes for Developers
If you're consuming `grounding_score` from the frontend (e.g. in `VerificationPanel`), remember it means three different things depending on which mode produced it — check `model`/`agent_used` in the response before treating the number as independently verified.
