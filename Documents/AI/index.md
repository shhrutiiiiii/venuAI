> Entry point for the `AI/` documentation folder.

# AI System — VenuAI

## Purpose
Describes how VenuAI's AI layer is organized: the LangGraph workflows, the multi-agent orchestrator, and the prompts/context-engineering that feed every LLM call.

## Responsibilities
- Explain the relationship between the three query modes (standard RAG, ReAct agent, multi-agent).
- Point to the leaf documents for agent detail, workflow detail, and prompt detail.

## Scope
Covers the AI/agent layer only. The FastAPI plumbing around it is [`../Backend/architecture.md`](../Backend/architecture.md).

## High-Level Overview
A single `/query` request can run through one of **three distinct execution paths**, selected by two boolean flags on `QueryRequest` (`agent_mode`, `multi_agent_mode`):

| Mode | Flag | Mechanism | Doc |
|---|---|---|---|
| Standard RAG | (default, both false) | LangGraph `StateGraph`: retrieve → generate → verify (or retrieve → abstain) | [`workflows.md`](workflows.md), [`retrieval.md`](retrieval.md) |
| ReAct agent | `agent_mode: true` | LangGraph prebuilt ReAct agent + `ALL_TOOLS` | [`workflows.md`](workflows.md), [`agents.md`](agents.md) |
| Multi-agent | `multi_agent_mode: true` | `OrchestratorAgent`: classify → route to specialist(s) → synthesize | [`agents.md`](agents.md) |

Every mode ultimately calls Groq via the services documented in [`../Backend/services.md`](../Backend/services.md); what differs is how the prompt is assembled and how many LLM calls happen before an answer is returned.

## Design Principles
1. **Three modes, one response shape.** Regardless of mode, the caller always gets back a `QueryResponse` — see [`../Backend/api.md`](../Backend/api.md).
2. **Scores are real or null.** The old hardcoded 0.9/0.8/0.7 grounding values are gone; ReAct and multi-agent use a light real check (token-overlap grounding + hallucination detector), streaming verifies after the last token, and unmeasured values are `null` with `verified=false` and a reason. Multi-agent `confidence` is now `retrieval_coverage` (a retrieval signal, not correctness). See [`trust-and-safety.md`](trust-and-safety.md).
3. **Prompts are assembled, not hardcoded strings scattered per call site.** `context_engineer.py` centralizes ranking, history compression, and prompt budget logic — see [`prompts.md`](prompts.md).

## Workflow
See [`workflows.md`](workflows.md) for the full diagram of all three modes.

## Inputs
A user question, conversation history, session/user identifiers, and the two mode flags.

## Outputs
A generated answer, sources, and quality scores (mode-dependent — see [`workflows.md`](workflows.md)).

## Dependencies
- [`agents.md`](agents.md) — the orchestrator and specialist agents.
- [`workflows.md`](workflows.md) — the LangGraph state machines for all three modes.
- [`prompts.md`](prompts.md) — prompt templates and context engineering.
- [`../Backend/services.md`](../Backend/services.md) — the Groq/embedding layer underneath all of this.
- [`model-routing.md`](model-routing.md) — the AI Gateway/Model Registry/Router (Phase 1).
- [`multimodal.md`](multimodal.md), [`vision.md`](vision.md) — real multimodal document extraction and the vision/OCR honesty boundary (Phase 2).
- [`data-analyst.md`](data-analyst.md) — the Data Analyst Agent's full upload → analyze → chart UI path (Phase 1 agent, Phase 2 end-to-end wiring).
- [`knowledge-graph.md`](knowledge-graph.md) — entity/relationship extraction, temporal relationships, SQLite/Postgres storage (Phase 3).
- [`trust-and-safety.md`](trust-and-safety.md) — guardrails on every path, injection defence, quote-verified citations, fail-closed verification.
- [`evidence-explorer.md`](evidence-explorer.md) — persisted claim-by-claim verification results, browsable after the fact (Phase 4).
- [`code-sandbox.md`](code-sandbox.md) — real, Docker-isolated code execution for the ReAct agent (Phase 5).
- [`mcp-deployment.md`](mcp-deployment.md) — the MCP server's real SDK-compatibility fix and its new authenticated HTTP transport (Phase 5).

## Interactions with Other Services
All three modes call out to Groq (see [`../Backend/services.md`](../Backend/services.md)); standard mode and multi-agent mode also call ChromaDB for retrieval.

## Security Considerations
Guardrails run before any of these three modes are entered — see [`../Backend/architecture.md`](../Backend/architecture.md) Design Principle 2. `/query/stream` and `/research` apply the same input guardrails; all answer paths also run output guardrails (see [`trust-and-safety.md`](trust-and-safety.md)).

## Performance Considerations
Multi-agent mode is the most expensive: intent classification (1 call) + N specialist calls in parallel + a synthesis call if N>1 — see [`agents.md`](agents.md).

## Future Improvements
- ~~Add verification to ReAct and multi-agent modes, not just standard mode~~ — **done (Phase 5)**, see Design Principle 2. `grounding_score` itself still isn't unified across modes — a separate, smaller remaining gap.
- See [`../future-additions.md`](../future-additions.md) for broader AI-capability additions (reranking, multi-modal RAG, knowledge graph, planning agent, self-critique loop, structured output).

## Notes for Developers
If you're about to add a fourth mode, stop and ask whether it should instead be a new LangGraph node/branch within the existing graph — three independent, hand-maintained execution paths inside one handler is already the maximum this should grow without refactoring `/query` into a true single graph with mode as a routing decision, not three separate code blocks.
