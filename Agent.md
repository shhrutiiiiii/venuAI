# Agent.md — VenuAI

## Purpose
This is the entry point for every AI agent or human contributor picking up work in this repository, in any session. It exists so that nobody — human or AI — has to read the whole codebase or documentation tree to make a correct change. Read this file first, always, before touching any other file.

## Responsibilities
- State what VenuAI is, in plain terms.
- Enforce minimal-context loading: point to the next hop, never dump the whole tree.
- Declare the global rules that apply to every domain and every document.
- Define the standard workflow an agent follows for any incoming task.

## Scope
Covers repository-wide orientation and governance only. Domain-specific detail (backend, database, frontend, DevOps, AI-agent operating rules) lives one hop away in `Documents/`. This file explicitly does NOT contain architecture detail — see [`Documents/index.md`](Documents/index.md) for that.

## High-Level Overview
**VenuAI** is an AI-powered research assistant built for DRDO (Defence Research and Development Organisation)-style use cases. A user uploads PDF documents (technical specs, lab reports, research papers) and chats with an AI that answers questions grounded in those documents, using Retrieval-Augmented Generation (RAG) layered with multi-agent routing, hallucination verification, safety guardrails, conversational memory, and usage analytics.

**Current build phase: working prototype, not production-deployed.** The RAG pipeline, LangGraph workflow engine, multi-agent orchestrator, guardrails engine, hallucination detector, memory manager, and streaming all run end-to-end today. Authentication, test coverage, containerization, and CI/CD do not exist yet — see [`Documents/future-additions.md`](Documents/future-additions.md).

## Design Principles
1. **Documentation-first, going forward.** Existing code predates this documentation tree, so documents here describe the system as it actually is today, not an aspirational design. Any *new* component should get a document under `Documents/` before (or alongside) its implementation.
2. **Minimal-context loading.** An agent working on a task loads only: this file → `Documents/index.md` → the relevant domain's folder → the specific leaf document(s) needed. Never read the entire `Documents/` tree to make one change.
3. **Documents describe reality, and are corrected the moment reality changes.** If code changes and a document is now wrong, update the document in the same change — don't let drift accumulate.
4. **Domain ownership.** An agent working within one domain (e.g., Backend) does not silently edit another domain's documents (e.g., Database). Cross-domain changes are flagged to the user, not made unilaterally.
5. **Never hide known gaps.** Where the system currently diverges from a production-safe design (e.g., mock authentication, no test suite), the relevant document says so explicitly rather than describing the ideal state as if it were real.

## Workflow
Every incoming task follows this sequence:
1. **Identify the domain(s) the task touches** (Backend, Database, Frontend, DevOps, AI) — ask the user if ambiguous, do not guess silently for non-trivial tasks.
2. **Read [`Documents/index.md`](Documents/index.md)** to confirm which folder and entry document govern that domain.
3. **Read that domain's folder index**, then the specific leaf document(s) relevant to the task — not the whole folder.
4. **Treat the document as the current spec.** If the task requires deviating from it, update the document to match in the same change.
5. **If the task is cross-domain**, read each relevant domain's entry document, and flag to the user any place where two domains' documents appear to conflict, rather than silently reconciling them.

## Inputs
Incoming task requests from the user, in any form (feature request, bug report, "how does X work," architecture question).

## Outputs
Code changes, document updates, or direct answers — always grounded in the documents this file points to, not re-derived from scratch each session.

## Dependencies
- [`Documents/index.md`](Documents/index.md) — the master map; every task's second stop after this file.

## Interactions with Other Services
N/A — this is a documentation root, not a runtime component.

## Security Considerations
Never fabricate credentials, API keys, or example secrets in any document in this tree — use clearly-fake placeholders (e.g., `<GROQ_API_KEY>`) and never a string that could be mistaken for a real value. Note that the current codebase's `.env` handling and authentication are known-weak (see [`Documents/Backend/authentication.md`](Documents/Backend/authentication.md)) — documenting this honestly is required, not optional.

## Performance Considerations
N/A — this file is read once per session and is deliberately short; performance-sensitive detail lives in domain documents (see [`Documents/Backend/architecture.md`](Documents/Backend/architecture.md)).

## Future Improvements
- Add real authentication, a test suite, containerization, and CI/CD — tracked in [`Documents/future-additions.md`](Documents/future-additions.md).
- Expand AI capability (reranking, multi-modal RAG, knowledge graph, planning agent) — also tracked in [`Documents/future-additions.md`](Documents/future-additions.md).

## Notes for Developers
This project is **brownfield** — the documents in this tree describe an existing, working codebase, not a greenfield design. When a document and the code disagree, verify against the code (it is the ground truth) and fix the document. Start every new session by reading this file, then `Documents/index.md` — never assume context carries over from a prior session.
