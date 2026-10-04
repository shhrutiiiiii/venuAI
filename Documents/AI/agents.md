# Agents — VenuAI

## Purpose
Documents every agent in `VENUAI/agents/`: what it is, what it's specialized for, and how the orchestrator uses it.

## Responsibilities
Describe the orchestrator's classification/routing/merging logic and each specialist agent's role.

## Scope
Covers the multi-agent system and the ReAct agent's tool-use. Prompt text itself is in [`prompts.md`](prompts.md).

## High-Level Overview

| Agent | File | Role |
|---|---|---|
| `OrchestratorAgent` | `agents/orchestrator.py` | Classifies query intent, routes to specialist(s), merges multi-domain results |
| `TechnicalAgent` | `agents/technical_agent.py` | Weapons specs, engineering params, materials, propulsion, electronics |
| `LabsAgent` | `agents/labs_agent.py` | DRDO labs, clusters, locations, org structure |
| `ResearchAgent` | `agents/research_agent.py` | Ongoing projects, publications, R&D programs, roadmaps |
| `GeneralAgent` | `agents/general_agent.py` | Catch-all / cross-domain queries |
| `AgentResult` | `agents/base_agent.py` | Shared result dataclass: `agent_name`, `answer`, `sources`, `confidence`, `reasoning` |
| ReAct agent | via `agent_tools.py` + LangGraph `create_react_agent` | Tool-calling agent for `agent_mode=true`, not part of the orchestrator |

**Phase 1/2 additions** — authoritative source is `VENUAI/agents/registry.py` (`AgentRegistry`); this table is a human-readable summary of it, kept in sync in the same change that adds an agent:

| Agent | File | Role |
|---|---|---|
| `VerifierAgent` | `verifier_agent.py` | Heuristic grounding/policy/consistency checks (no model calls except the consistency check, routed through AIGateway) |
| `ResearchPlannerAgent` | `research_planner.py` | Decomposes a research question into ≤5 sub-questions via LLM; falls back to a single-question plan on failure |
| `DeepResearchAgent` | `agents/deep_research_agent.py` | Multi-step research: plan → per-sub-question AgenticRAG → synthesize → verify. Exposed via `POST /research`. **Not** the same as the `ResearchAgent` RAG specialist above — see its docstring |
| `DataAnalystAgent` | `agents/data_analyst_agent.py` | Deterministic pandas analysis (mean/sum/correlation/growth/groupby/missing/duplicates); LLM only narrates already-computed numbers |
| `VisionAgent` | `agents/vision_agent.py` | Answers questions about table/image regions using real extracted text (table text or OCR) — never fabricates visual interpretation; see [`vision.md`](vision.md) |

## Design Principles
1. **Classification is a single fast, low-token Groq call**, not a heavier reasoning step — `classify_intent()` asks for a comma-separated category list with `max_tokens=20, temperature=0.0` and falls back to `["general"]` on any failure or unparseable response.
2. **Single-domain queries skip the fan-out/merge machinery entirely** — only genuinely multi-domain queries pay the cost of parallel specialist calls + a synthesis call.
3. **Parallel fan-out uses `asyncio.gather(..., return_exceptions=True)`** — one specialist failing doesn't fail the whole request; `merge_results` only operates on the specialists that actually returned a valid `AgentResult`.
4. **Merging is itself an LLM call**, not string concatenation — `merge_results` builds a synthesis prompt asking the LLM to deduplicate and unify multiple specialists' answers, with a hardcoded string-concatenation fallback only if that synthesis call itself fails.

## Workflow
```
question
  │
  ▼
classify_intent(question)  ── Groq call, temperature=0.0, max_tokens=20
  │
  ├─ 1 domain  ──► that specialist's .query(question, history)  ──► AgentResult
  │
  └─ N domains ──► asyncio.gather( specialist_i.query(...) for i in domains )
                        │
                        ▼
                  merge_results(valid_results, question)  ── Groq synthesis call
                        │
                        ▼
                     AgentResult(agent_name="Orchestrator(A + B)", confidence=avg(...))
```

## Inputs
The user's question, conversation history, memory context dict (currently accepted by `route()` but not deeply used inside specialist `.query()` calls beyond being passed through), and `allowed_doc_ids` — the caller's document-visibility set (see [`../Backend/authentication.md`](../Backend/authentication.md)), which *is* enforced: it's forwarded to every specialist's retrieval call to filter which chunks they can retrieve.

## Outputs
A single `AgentResult` (answer, sources, confidence, reasoning) regardless of how many specialists were involved.

## Dependencies
- [`workflows.md`](workflows.md) — where multi-agent mode sits relative to the standard LangGraph path.
- [`prompts.md`](prompts.md) — the intent-classification and synthesis prompt text.
- [`../Backend/services.md`](../Backend/services.md) — the Groq client all of this runs on.

## Interactions with Other Services
Each specialist agent and the orchestrator itself call Groq directly (each agent is constructed with its own `groq_client` reference) — there is no shared rate-limiting or batching across these calls.

`GET /agents` (Phase 4) exposes the real `VENUAI/agents/registry.py` contents read-only — any authenticated user can see what agents exist, their risk level, and their tool/permission requirements. This backs the Research Workspace's "Agents & Models" panel (`chat-app/src/app/workspace/page.tsx`). It is a registry listing, not a control surface — there is no endpoint to enable/disable or reconfigure an agent from the UI.

## Security Considerations
Classification failures degrade to `general`, never to an error state — this is a deliberate availability-over-precision choice; it also means a misrouted query is silent (no flag is raised when the LLM-based classifier gets it wrong).

## Performance Considerations
Multi-domain queries cost `1 (classify) + N (parallel specialists) + 1 (merge)` Groq calls. The parallel specialist calls run via `run_in_executor` (each specialist's `.query()` is apparently synchronous) rather than native async Groq calls — functional, but an extra thread-pool hop per specialist.

## Future Improvements
- Add a confidence threshold below which the orchestrator asks a clarifying question instead of guessing a domain.
- Consider a planning step for genuinely multi-step requests, rather than only parallel single-shot specialist calls — see [`../future-additions.md`](../future-additions.md).

## Notes for Developers
When adding a new specialist domain, add it to `OrchestratorAgent._specialists`, add its category to `_INTENT_PROMPT` and `valid_domains` in `classify_intent`, and add a row to the table above in the same change.
