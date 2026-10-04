# VenuAI

AI-powered research assistant built for DRDO (Defence Research and Development Organisation)-style use cases. Upload PDF documents (specs, lab reports, research papers) and chat with an AI that answers questions grounded in them — a RAG system layered with multi-agent routing, hallucination verification, safety guardrails, conversational memory, and usage analytics.

**Current status: working prototype.** The RAG pipeline, LangGraph workflow engine, multi-agent orchestrator, guardrails, hallucination detection, memory, and streaming all run end-to-end. Authentication is mocked, and there is no test suite, containerization, or CI/CD yet.

## Documentation

This repository is documentation-first going forward: every architectural decision is written down and kept in sync with the code. Start here, in order:

1. [`Agent.md`](Agent.md) — repository entry point and operating rules.
2. [`Documents/index.md`](Documents/index.md) — master index of every domain (Backend, Database, Frontend, DevOps, AI) and the end-to-end system flow.
3. [`Documents/architecture-decisions.md`](Documents/architecture-decisions.md) — the record of every cross-cutting decision and why it was made.
4. [`Documents/future-additions.md`](Documents/future-additions.md) — production-hardening roadmap and AI-capability expansion ideas, not yet built.

Do not duplicate architecture detail here — if something needs explaining, it belongs in `Documents/`, linked from `Documents/index.md`.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python), LangChain + LangGraph, Groq LLM |
| Vector store | ChromaDB (local) + BM25 hybrid retrieval |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS v4 |
| Audit/analytics storage | SQLite + local JSON files |

See [`Documents/Backend/architecture.md`](Documents/Backend/architecture.md) and [`Documents/Frontend/architecture.md`](Documents/Frontend/architecture.md) for the full picture, and [`explanation.md`](explanation.md) for a plain-language feature walkthrough.
