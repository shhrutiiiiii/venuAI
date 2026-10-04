# Prompts & Context Engineering — VenuAI

## Purpose
Documents the prompt templates in use and the context-engineering logic (`context_engineer.py`) that assembles them.

## Responsibilities
List each prompt template, its call site, and the budget/ranking/compression logic that feeds it.

## Scope
Prompt text and assembly logic only. What calls these prompts is covered in [`workflows.md`](workflows.md) and [`agents.md`](agents.md).

## High-Level Overview

| Prompt | Location | Used by |
|---|---|---|
| `DRDO_RAG_PROMPT_TEMPLATE` | `rag_server.py` | Defined but superseded at the actual generate step by `build_drdo_prompt()` from `context_engineer.py` — kept as the documented reference template |
| Context-engineered prompt | `context_engineer.build_drdo_prompt()` | `generate_node` (standard RAG) and `/query/stream` |
| Fallback-KB prompt | inline in `fallback_kb_node` | When no documents exist yet |
| Intent classification prompt (`_INTENT_PROMPT`) | `agents/orchestrator.py` | `OrchestratorAgent.classify_intent` |
| Synthesis prompt | inline in `OrchestratorAgent.merge_results` | Merging multi-domain specialist answers |

## Design Principles
1. **Formatting rules are explicit and consistent** across the RAG prompts: markdown headings (`###`), tables for structured specs, bullet points, **bold** for key terms/lab codes — baked directly into the prompt text rather than post-processed.
2. **Answers must self-report absence of information** — every RAG prompt instructs the model to say "This information is not available in the uploaded DRDO documents" rather than guessing, when context doesn't contain the answer. This is a prompt-level hallucination mitigation, complementary to (not a replacement for) `verify_node`'s independent check.
3. **Context assembly is centralized in `context_engineer.py`**, not duplicated per call site:
   - `rank_chunks(docs, question)` — re-orders retrieved chunks by relevance.
   - `compress_history(history, max_turns=6)` — trims conversation history to a fixed number of recent turns.
   - `ContextBudget` — a token-budget object constraining how much of the ranked chunks + history + semantic memory can go into the final prompt.
   - `build_drdo_prompt(question, ranked_docs, compressed_history, budget, semantic_memories)` — assembles everything into the final prompt string.

## Workflow
```
raw retrieved docs ──► rank_chunks ──┐
conversation history ──► compress_history ──┤
semantic memories (from Memory Manager) ──────┤
                                               ▼
                                   build_drdo_prompt(question, ..., budget: ContextBudget)
                                               │
                                               ▼
                                          Groq LLM call
```

## Inputs
Question, retrieved documents, conversation history, semantic memories, token budget.

## Outputs
A single assembled prompt string per call.

## Dependencies
- [`workflows.md`](workflows.md) — where this fits in the generate step.
- [`../Backend/services.md`](../Backend/services.md) — the memory manager supplying semantic memories.

## Interactions with Other Services
None beyond the Groq call this feeds.

## Security Considerations
Prompt text itself does not escape/sanitize retrieved document content before interpolation — guardrails (see [`../Backend/architecture.md`](../Backend/architecture.md)) are the layer responsible for catching injection attempts in the *question*; injected content hidden inside an *uploaded document* is not separately checked by this layer today.

## Performance Considerations
`ContextBudget` exists precisely to keep prompts within token limits as document count/history length grow — any change to chunk size or history depth should be weighed against this budget, not just against "does it fit" on a single test case.

## Future Improvements
- Add a check for injected instructions hidden inside uploaded document content, not just the user's typed question.
- Consider structured-output (JSON mode / function calling) for answers that are primarily tabular specs, rather than relying on the model to produce well-formed markdown tables every time. See [`../future-additions.md`](../future-additions.md).

## Notes for Developers
If you add a new prompt template, decide whether it belongs centralized in `context_engineer.py` (if it needs ranking/budget/compression) or can stay inline (if it's a one-off like the fallback-KB prompt) — and add a row to the table above either way.
