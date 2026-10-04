> This document is also the entry point for the `Database/` documentation folder.

# Database Architecture — VenuAI

## Purpose
Documents the three separate, uncoordinated local data stores the backend uses in place of a single database, and the consequences of that split.

## Responsibilities
- Describe what lives in each store and why.
- Flag the lack of transactional coordination between them.

## Scope
Covers data persistence only. Does not cover the LangGraph runtime state (which is in-memory per-request, not persisted) — see [`../Backend/architecture.md`](../Backend/architecture.md) for that.

## High-Level Overview
There is **no relational database** in this system. Instead, three independent local stores each own one slice of state:

| Store | Technology | Location | Owns |
|---|---|---|---|
| Vector store | ChromaDB (persisted) | `VENUAI/chroma_db/` | Document chunk embeddings + metadata (doc_id, filename, page, domain tags, labs, classification, owner_id) |
| Users & tokens | SQLite | `VENUAI/data/users.db` | `users` table (id, email, bcrypt hash, role, lockout state) and `refresh_tokens` table (hashed, revocable) — see [`../Backend/authentication.md`](../Backend/authentication.md) |
| Audit log | SQLite | `VENUAI/data/guardrails_audit.db` | Every guardrail trigger (prompt injection, out-of-scope, PII, hallucination-policy block) |
| Document metadata | JSON file | `VENUAI/doc_metadata.json` | Per-document info (filename, pages, chunks, upload time, size, tags) — keyed by `doc_id` |
| Preloaded knowledge base | JSON file | `VENUAI/data/drdo_knowledge_base.json` | Static fallback KB used when no documents have been uploaded |
| Analytics | (JSON-backed, via `analytics.py`) | — | Per-query logs: latency, scores, agent used, tools called, errors |

No schema migrations exist for any of these because none of them are a relational schema — the JSON files are simply read, modified in Python, and rewritten whole (`load_metadata()` / `save_metadata()` pattern).

## Design Principles
1. **Each store is simple on its own, at the cost of coordination.** Deleting a document (`DELETE /documents/{id}`) requires three separate operations — delete from Chroma (`vs._collection.delete(where={"doc_id": doc_id})`), delete the uploaded file from disk, and remove the entry from `doc_metadata.json` — with no transaction wrapping all three. A crash between steps leaves the stores inconsistent.
2. **Chroma is the retrieval source of truth; JSON metadata is a display/index convenience.** `/documents` reads only the JSON file, not Chroma directly — if the two ever diverge, the UI's document list can disagree with what's actually retrievable.
3. **BM25 is rebuilt from the full Chroma corpus on every query**, not cached — see Performance Considerations below.

## Workflow
**Document lifecycle:**
```
Upload  ──► save PDF to disk ──► parse+chunk ──► tag (DocumentIntelligence)
              │                                        │
              ▼                                        ▼
       ChromaDB.add_documents(chunks)         doc_metadata.json[doc_id] = {...}

Query   ──► ChromaDB similarity search  ─┐
       ──► BM25Retriever.from_documents(all chroma docs) ─► EnsembleRetriever
                                          ┘

Delete  ──► ChromaDB delete(where doc_id) ──► unlink PDF file ──► remove from doc_metadata.json
            (three independent steps, no shared transaction)
```

## Inputs
Document chunks + metadata from `/upload`; guardrail verdicts; per-query logs from the memory manager and analytics module.

## Outputs
Retrieved chunks for generation; audit records; the document list shown in the frontend; analytics dashboard data.

## Dependencies
- [`../Backend/architecture.md`](../Backend/architecture.md) — the code paths that read/write these stores.

## Interactions with Other Services
Purely local disk I/O — no network calls, no separate database server process. This also means **none of these stores survive a container being destroyed** unless explicitly mounted to persistent storage in any future deployment — see [`../DevOps/deployment.md`](../DevOps/deployment.md).

## Security Considerations
No encryption at rest for any store; no access control beyond filesystem permissions. The audit SQLite database itself has no access restriction from within the app — any code path with filesystem access can read or alter it.

## Performance Considerations
- **BM25 index rebuild on every single query** (`retrieve_node` calls `vs.get()` then builds a fresh `BM25Retriever.from_documents(...)` each time) — this is O(corpus size) per query and will become the dominant cost as the document set grows. This is the single most impactful performance fix available without architectural change.
- JSON metadata files are read and rewritten in full on every mutation (`load_metadata()`/`save_metadata()`) — fine at small document counts, will degrade linearly as the number of documents grows.
- ChromaDB here runs embedded/local (not a server) — fine for one process, not safe for multiple backend replicas writing concurrently (no locking).

## Future Improvements
- Cache the BM25 index (rebuild only on document add/delete, not on every query).
- Move document metadata and analytics into a real database (SQLite-with-proper-schema at minimum, PostgreSQL if multi-instance deployment is ever needed) instead of whole-file JSON rewrites.
- Wrap the three-step document-delete sequence in compensating logic (or a real transaction once a relational store exists) so a partial failure doesn't leave orphaned data.
- If deployed with multiple backend replicas, Chroma's local/embedded mode must be replaced with a server-mode or managed vector DB — concurrent writers to the same local Chroma directory is not a supported configuration.

## Notes for Developers
Before adding a new persisted field, decide explicitly which of the three stores it belongs in — don't add a fourth ad hoc JSON file without updating this document and this domain's architecture table.
