# Documents Index — VenuAI

## Purpose
This is the map of the entire VenuAI documentation tree. Any agent or contributor asking "where do I look for X?" starts here, second stop after [`Agent.md`](../Agent.md).

## Responsibilities
- List every domain and its entry document.
- Give a shallow, two-minute system overview (depth lives in domain documents, never duplicated here).
- State platform-wide design principles.
- Show the major end-to-end flow so a reader can trace one query through every layer without opening every file.

## Scope
Covers navigation and cross-cutting principles for the whole repository: the Python RAG/agent backend (`VENUAI/`) and the Next.js frontend (`chat-app/`).

## High-Level Overview
A user uploads a PDF into the knowledge base, then asks questions in a chat UI. The backend retrieves relevant chunks (hybrid vector + keyword search), classifies the query's domain, optionally routes it to one or more specialist agents, generates an answer grounded in the retrieved context plus conversational memory, verifies that answer against its sources for hallucination before returning it, and logs the whole turn for analytics. All of this is modeled as an explicit **LangGraph** state machine, not ad hoc branching.

| Domain | Entry Document | Concern |
|---|---|---|
| Backend | [`Backend/architecture.md`](Backend/architecture.md) | FastAPI server, LangGraph workflow, RAG pipeline, guardrails, memory, verification |
| Database | [`Database/architecture.md`](Database/architecture.md) | ChromaDB vector store, SQLite audit log, JSON metadata/analytics stores |
| Frontend | [`Frontend/architecture.md`](Frontend/architecture.md) | Next.js chat UI: streaming chat, verification panel, analytics dashboard, auth pages |
| DevOps | [`DevOps/deployment.md`](DevOps/deployment.md) | Current (manual, local-only) run model and what's missing for real deployment |
| AI | [`AI/index.md`](AI/index.md) | Multi-agent orchestration, prompts, LangGraph workflows, context engineering |
| Evaluation | [`AI/evaluation.md`](AI/evaluation.md) | Real RAG eval harness: harvest/score, metrics, judge, datasets, CI gates |

## Design Principles
1. **Everything slow and LLM-dependent runs through an explicit graph, not inline branching.** The RAG pipeline is a LangGraph `StateGraph` (`retrieve → generate → verify`), so new steps are added as nodes/edges, not nested `if`s. See [`AI/workflows.md`](AI/workflows.md).
2. **Every answer is checked before it's trusted.** Generation is always followed by a verification step (grounding score, hallucination score) before the answer reaches the user. See [`Backend/architecture.md`](Backend/architecture.md) and [ADR-002](architecture-decisions.md#adr-002-mandatory-post-generation-verification).
3. **Guardrails run on input before any retrieval or generation happens.** Prompt-injection, out-of-scope, and PII checks gate the request at the top of `/query`, not after. See [`Backend/architecture.md`](Backend/architecture.md).
4. **Fallback over failure.** If no documents have been uploaded, the system answers from a preloaded knowledge base rather than erroring. See [`AI/workflows.md`](AI/workflows.md).
5. **Single backend entry point.** The Next.js frontend never talks to ChromaDB, SQLite, or Groq directly — only to the FastAPI `rag_server.py` (via Next.js API routes that proxy to it). See [`Frontend/architecture.md`](Frontend/architecture.md).
6. **Known gaps are documented, not hidden.** Where the system is still unsafe for production (per-chunk retrieval authorization, dev cruft not yet removed), the relevant document says so plainly. See [`Backend/authentication.md`](Backend/authentication.md) and [`future-additions.md`](future-additions.md).

## Architecture Review & Key Observations
Non-obvious characteristics of the current system, each worth knowing before changing it:
- **The backend is fully synchronous per-request** — there is no job queue. A `/query` call blocks on embedding, retrieval, generation, and verification all on the same request thread. This is fine at prototype load, not at concurrent multi-user load. See [ADR-003](architecture-decisions.md#adr-003-no-async-job-queue-yet).
- **Three separate, uncoordinated local stores** — ChromaDB (vectors), SQLite (guardrails audit), and flat JSON files (document metadata, analytics) — with no shared transaction boundary. See [`Database/architecture.md`](Database/architecture.md).
- ~~**Frontend authentication is entirely mocked**~~ — **superseded.** Real server-side auth now exists (`VENUAI/auth.py`): bcrypt-hashed passwords, signed JWTs, revocable refresh tokens, enforced on every route. One real gap remains: per-chunk retrieval isn't yet filtered by document ownership. See [`Backend/authentication.md`](Backend/authentication.md). See [ADR-004](architecture-decisions.md#adr-004-mock-auth-for-prototype-phase).

## Workflow
**Upload → query → verified answer path:**
```
User (browser, chat-app)
   │  upload PDF
   ▼
FastAPI /upload  ──────────────► PyPDFLoader → chunk → embed → ChromaDB
   │
   ▼
User asks a question
   │  POST /query  (or /query/stream)
   ▼
Guardrails (input check: injection / out-of-scope / PII)
   │  passed
   ▼
Memory Manager  ─── fetch working + semantic memory for this session
   │
   ▼
LangGraph: route_query_node
   ├─ no docs yet ──► fallback_kb_node ──► answer from preloaded DRDO KB ──► END
   └─ docs exist ──► retrieve_node (Chroma + BM25 hybrid search)
                           │
                           ▼
                     generate_node (context-engineered prompt → Groq LLM)
                           │
                           ▼
                     verify_node (VerifierAgent + HallucinationDetector)
                           │
                           ▼
                          END  ──► QueryResponse (answer, sources, grounding/hallucination scores)
                           │
                           ▼
            Memory Manager saves turn · Analytics logs the query (both background tasks)
```
Multi-agent mode and ReAct agent mode replace the middle of this graph — see [`AI/workflows.md`](AI/workflows.md) for both.

## Inputs
User-uploaded PDF files; user chat questions; verification/agent-mode toggles from the frontend.

## Outputs
Grounded chat answers with source citations, grounding/hallucination scores, and annotated text; analytics records; audit log entries for guardrail triggers.

## Dependencies
- [`Backend/architecture.md`](Backend/architecture.md)
- [`Database/architecture.md`](Database/architecture.md)
- [`Frontend/architecture.md`](Frontend/architecture.md)
- [`DevOps/deployment.md`](DevOps/deployment.md)
- [`AI/index.md`](AI/index.md)
- [`architecture-decisions.md`](architecture-decisions.md)
- [`future-additions.md`](future-additions.md) — non-normative roadmap of what's missing for production and what AI capability could be added next

## Interactions with Other Services
N/A — this is a documentation index, not a runtime component. Runtime service interactions are detailed in [`Backend/architecture.md`](Backend/architecture.md).

## Security Considerations
N/A at index level — see [`Backend/authentication.md`](Backend/authentication.md) for the current (weak) auth state and [`Backend/architecture.md`](Backend/architecture.md) for the guardrails engine.

## Performance Considerations
N/A at index level — see [`Backend/architecture.md`](Backend/architecture.md) for the synchronous-request-path concern.

## Future Improvements
- Add real authentication and authorization on every backend route.
- Add a job queue so slow LLM/embedding calls don't block the request thread under concurrent load.
- See [`future-additions.md`](future-additions.md) for the full list, including additional AI capabilities (reranking, multi-modal RAG, knowledge graph, planning agent, self-critique loop).

## Notes for Developers
Keep this table's rows in sync with what actually exists on disk under `Documents/` — if you add a domain folder, add its row here in the same change. Do not duplicate architectural depth here; if you find yourself writing more than one paragraph about how something works, that content belongs in the domain document, with this index linking to it.
