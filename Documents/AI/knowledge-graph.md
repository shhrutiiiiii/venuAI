# Knowledge Graph — VenuAI

## Purpose
Documents Phase 3's real knowledge graph: the data model, the two genuinely-working storage backends, the extraction logic, temporal relationships, and its integration with the Research pipeline.

## Responsibilities
- Describe `VENUAI/knowledge_graph/` (models, store, extraction).
- State precisely what "PostgreSQL initially" means here: a real, tested backend that exists but isn't yet this project's deployed default.
- Document the "never invent a relationship" guarantee and how it's enforced.

## Scope
Covers `VENUAI/knowledge_graph/*` and the `/graph/*` routes plus `/research`'s `extract_graph` integration. Evidence/Claim fundamentals are [`../`](../) `VENUAI/evidence.py` (Phase 1).

## High-Level Overview
Two real storage backends behind one `GraphStore` interface:
- **`SqliteGraphStore`** (default) — zero-config, same pattern as every other store in this codebase (`auth.py`, `memory_manager.py`, `guardrails.py`). File: `VENUAI/data/knowledge_graph.db`.
- **`PostgresGraphStore`** — a real implementation using `psycopg2`, **verified end-to-end against an actual local PostgreSQL 18 instance** during development (same schema, same CRUD/authorization/temporal behavior as SQLite — see `VENUAI/tests/test_knowledge_graph_store.py`, which runs every test against both backends and only skips Postgres when none is reachable). Not the project's default: no `docker-compose.yml` service, `.env.example` entry, or CI step currently provisions a Postgres database for it. Activate with `KNOWLEDGE_GRAPH_BACKEND=postgres` plus standard `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` (or `DATABASE_URL`) env vars, pointing at a database that already exists (this class creates tables, not databases — normal Postgres operational practice).

Entity/relationship types match spec sections 22/23 exactly — see `VENUAI/knowledge_graph/models.py`'s `EntityType`/`RelationshipType` enums.

Two real extractors (`VENUAI/knowledge_graph/extraction.py`):
- **`extract_deterministic()`** — regex-based, reusing the *same* lab-code/weapon-name patterns `VENUAI.memory_manager` already uses for semantic-memory fact extraction (imported, not duplicated). Produces only `MENTIONS` relationships — the entire "claim" is that the text contains the string, so there is nothing to invent.
- **`extract_llm()`** — a real LLM call via `AIGateway` (Phase 1) asking for entities/relationships as strict JSON. Every item is validated against the real enums; anything that doesn't match (a hallucinated type, a relationship referencing an entity the model didn't actually list) is **dropped**, never coerced. On any parse/call failure, returns empty — the same fail-safe discipline as `VENUAI.research_planner`.

## Design Principles
1. **Every relationship carries real provenance.** `Relationship.source_evidence_id` points at a real `VENUAI.evidence.Evidence.evidence_id` — there is no code path in this phase that creates a relationship without one.
2. **A hallucinated type is dropped, not repaired.** `extract_llm()` never tries to guess "the closest real type" for something the model invented — see `test_llm_extraction_drops_invalid_entity_type`.
3. **Entity ids are deterministic** (`Entity.make_id` hashes `tenant_id:type:normalized_name`) — the same real-world entity mentioned across multiple extractions resolves to one node, not a duplicate per mention. The `tenant_id` is in that hash, not just an extra filter column, specifically so two different tenants (Phase 4) both mentioning the same name (e.g. "LRDE") get two distinct nodes rather than colliding on one upserted row — see [`../Backend/multi-tenancy.md`](../Backend/multi-tenancy.md) Design Principle 3.
4. **Temporal fields are optional, never fabricated.** A relationship with no `valid_from`/`valid_until` is treated as "currently valid, no known end" — not defaulted to any specific date that wasn't actually observed.
5. **Same authorization rule as everything else, tenant match first.** `tenant_id` is checked before `owner_id`/`classification` (`PUBLIC`/`UNKNOWN`) on every read — identical pattern to documents/datasets/multimodal regions — see [`../Backend/authentication.md`](../Backend/authentication.md) and [`../Backend/multi-tenancy.md`](../Backend/multi-tenancy.md).

## Workflow
```
POST /graph/extract {text, use_llm}
   │  guardrail check (same as /query, /research, /datasets/.../analyze)
   ▼
extract_deterministic(evidence) + extract_llm(evidence)  [if use_llm]
   │  (both validated/provenance-stamped — see Design Principles)
   ▼
GraphStore.add_entity() / add_relationship()  [upsert — same entity mentioned
                                                 again doesn't duplicate]
   │
   ├─ GET /graph/entities/{id}?as_of=DATE   → entity + temporally-filtered relationships
   └─ GET /graph/search?q=...&entity_type=  → name search
```

**Automatic population** (Phase 4, `POST /upload`):
```
POST /upload  (the standard RAG document-ingestion path)
   │  parse → chunk → embed/store in Chroma → DocumentIntelligence tagging → save_metadata()
   ▼
extract_deterministic() over the document's text  [NO extract_llm() call here]
   │
   ▼
Stored in the graph automatically — no separate /graph/extract call needed
```
Every document uploaded through the normal path now seeds the graph automatically, using only the fast, free, regex-based `extract_deterministic()` — never `extract_llm()`, which stays an explicit opt-in (`POST /graph/extract` with `use_llm: true`, or `/research`'s `extract_graph` flag) specifically because it makes a real LLM call and this project has no background job queue (ADR-003) to hide that latency behind. A graph-population failure is caught and logged, never raised — it must not fail the document upload itself. See `VENUAI/tests/test_upload_graph_autopopulation.py`.

**Research pipeline integration** (`POST /research` with `extract_graph: true`):
```
DeepResearchAgent.research() → synthesized_answer (already citation/
                                 contradiction/critique-verified —
                                 VENUAI/verification_pipeline.py)
   │
   ▼
Wrapped as one Evidence(document_name=f"Research: {question}")
   │
   ▼
extract_deterministic() + extract_llm() over that Evidence
   │
   ▼
Stored in the graph — response reports graph_entities_extracted /
graph_relationships_extracted counts
```
Building the graph from the *verified, synthesized* research answer (not raw unverified retrieval chunks) means graph entries inherit the same evidence-grounding guarantee the research pipeline already provides.

## Inputs
Text to extract from (`/graph/extract`), a research question (`/research` with `extract_graph: true`).

## Outputs
`Entity`/`Relationship` records, stored and queryable; `EntityWithRelationshipsResponse` for a single entity's neighborhood.

## Dependencies
- `VENUAI/evidence.py` — the provenance model every relationship points back to.
- `VENUAI/ai_gateway/` — the LLM call `extract_llm()` makes.
- `VENUAI/agents/deep_research_agent.py` — the `/research` integration point.
- [`../Backend/authentication.md`](../Backend/authentication.md) — the authorization rule reused here.

## Interactions with Other Services
`/graph/*` routes read from/write to the same graph that `POST /upload` now writes into automatically (see Automatic Population above) and that `/research`'s `extract_graph: true` flag writes into explicitly. Standard RAG *answers* still do not read from the graph — the graph and the text-chunk RAG pipeline are both populated from documents, but a `/query` answer doesn't itself consult the graph.

## Security Considerations
Same guardrail input check as every other text-accepting route (`/query`, `/research`, `/datasets/.../analyze`) runs before `/graph/extract` processes the text. Authorization is enforced at the store layer (`_access_clause`/`_pg_access_clause`), not just the route layer — a direct `GraphStore` call from other backend code gets the same filtering, not just the HTTP boundary.

## Performance Considerations
`PostgresGraphStore` opens a new connection per call (no connection pooling) — acceptable at this phase's scale, a real optimization target (e.g. `psycopg2.pool`) if the Postgres backend is ever actually deployed. `SqliteGraphStore` follows the same per-call-connection pattern already used throughout this codebase.

## Future Improvements
1. ~~Deploy Postgres for real~~ — **done (Phase 5).** `docker-compose.yml` now has a real `postgres` service (opt-in via the `postgres` Compose profile), env vars documented in `.env.example`, both verified end-to-end — see [`../DevOps/docker.md`](../DevOps/docker.md). Still not a CI step (Postgres-backend tests there remain skip-when-unreachable, same as any machine without the profile started).
2. ~~Graph visualization UI~~ — **done (Phase 4).** The Research Workspace's "Knowledge Graph" tab (`chat-app/src/app/workspace/page.tsx`) lets a user search entities (`GET /graph/search`), open one to see its relationships (`GET /graph/entities/{id}`), and extract new entities from pasted text (`POST /graph/extract`). It's a search/inspect panel, not a node-link visual graph renderer — a real force-directed graph view remains open if that specific visual is wanted later.
3. ~~Automatic graph population from standard `/upload`~~ — **done (Phase 4)**, see above. `/query` itself still does not consult the graph when generating an answer — that remains a real, separate piece of work (would mean retrieving graph context alongside Chroma/BM25/multimodal-region context in `retrieve_node`).
4. **Richer relationship types inferred from `extract_deterministic`** — today it only produces `MENTIONS`; patterns for `DEVELOPED_BY`/`PART_OF` from reliably-structured text (e.g. "X, developed by Y,") could be added deterministically without needing an LLM call, if a real, low-false-positive pattern set is identified.
5. **Connection pooling for `PostgresGraphStore`** once it's actually deployed under real load.

## Notes for Developers
If you're adding a new extraction path, route it through `extract_deterministic`/`extract_llm`'s validation discipline — never construct a `Relationship` with a `source_evidence_id` that doesn't point at something real. That's the one rule this entire module exists to enforce.
