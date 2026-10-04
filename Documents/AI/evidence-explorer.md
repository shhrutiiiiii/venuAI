# Evidence Explorer — VenuAI

## Purpose
Documents the Phase 4 persistence and browsing layer for claim-level verification results — the ability to revisit a past answer and see exactly which claims were SUPPORTED/PARTIALLY_SUPPORTED/UNSUPPORTED/CONTRADICTED, and by which real Evidence.

## Responsibilities
- Explain why this didn't already exist: `VENUAI.evidence.EvidenceGraph`/`Claim`/`Evidence` were (and remain) ephemeral, in-request dataclasses — `VerificationPipeline.run()` builds one, the route handler reads its aggregate scores, and the detailed claim/evidence breakdown was discarded once the HTTP response was sent.
- Describe the new storage layer (`VENUAI/evidence_store.py`) and the two write paths (`POST /query`, `POST /research`) that now feed it.
- Describe the two read endpoints (`GET /evidence`, `GET /evidence/{id}`) and their authorization rule.

## Scope
Covers `VENUAI/evidence_store.py`, the `evidence_claims` field added to `RAGGraphState`/`verify_node`, the `claims` field added to `DeepResearchResult`, and the `/evidence*` routes. Does not change `VerificationPipeline`, `CitationAgent`, `ContradictionAgent`, or `CriticAgent` themselves — this is purely a persistence/browsing layer on top of their existing, unchanged output.

## High-Level Overview
```
POST /query (standard RAG)              POST /research (Deep Research)
   │  verify_node runs                     │  VerificationPipeline runs once on
   │  VerificationPipeline.run()           │  the synthesized, already-subquestion-
   │  → EvidenceGraph (real Claims,        │  verified answer
   │    each with real Evidence)           │  → same EvidenceGraph shape
   ▼                                        ▼
_serialize_claims() / serialize_claims()  (VENUAI.evidence_store — shared function,
   │  plain-dict form: claim_id, text,       no VerificationPipeline-specific logic)
   │  support_status, support_score,
   │  evidence[] (doc name, page, content,
   │  modality, relevance_score)
   ▼
evidence_store.save_record(user_id, tenant_id, question, answer, source, claims, ...)
   │  BackgroundTask — never blocks the actual query/research response
   ▼
SQLite: VENUAI/data/evidence_explorer.db
   │
   ├─ GET /evidence           → list of past records (own, or whole tenant if admin)
   └─ GET /evidence/{id}      → full claim-by-claim breakdown for one record
```

## Design Principles
1. **A record with zero claims is still saved.** "Nothing was verifiable for this answer" (e.g. the KB-fallback path, which never runs `VerificationPipeline` at all and always has `evidence_claims: []`) is itself a real, useful fact for the explorer to show — not a reason to skip persistence.
2. **Persistence is a `BackgroundTask`, never on the request's critical path.** A write failure here must never turn a successful `/query`/`/research` answer into a 500 — see `VENUAI/rag_server.py`'s use of `background_tasks.add_task(evidence_store.save_record, ...)`, the same pattern already used for `memory_manager.save_turn`/`analytics.log_query`.
3. **Same tenant-scoped authorization as everything else in Phase 4.** `GET /evidence`/`GET /evidence/{id}` restrict to the caller's own tenant always, and to the caller's own records unless they're an admin (admin sees every record in their tenant, not across tenants) — see `evidence_store.list_records()`/`get_record()`.
4. **One serialization function, not four.** `VENUAI.evidence_store.serialize_claims()` is called by `verify_node` (standard RAG), `POST /research`, and both branches of `POST /query`'s multi-agent/ReAct modes (Phase 5) — avoiding four duplicate, slightly-differently-shaped serializers that would otherwise accumulate if each call site rolled its own dict-building.

## Workflow
See the diagram above. `RAGGraphState` gained one new field, `evidence_claims: List[dict]`, populated only by `verify_node` (the `fallback_kb_node` path sets it to `[]`, since it never runs `VerificationPipeline`). `DeepResearchResult` gained `claims: list` (real `Claim` objects, serialized by the route handler, not by the agent itself — keeping `VENUAI.agents.deep_research_agent` free of any dependency on the storage layer).

## Inputs
Nothing new from the caller — this activates automatically on every `/query` and `/research` call that reaches `verify_node`/the synthesis step.

## Outputs
`EvidenceRecordSummary` (list view) and `EvidenceRecordDetail` (full claim/evidence breakdown, including the answer text).

## Dependencies
- `VENUAI/evidence.py` — the `Claim`/`Evidence`/`EvidenceGraph` dataclasses this serializes.
- `VENUAI/verification_pipeline.py` — produces the `EvidenceGraph` both write paths serialize.
- [`../Backend/multi-tenancy.md`](../Backend/multi-tenancy.md) — the authorization rule reused here (tenant-scoped, admin-within-tenant).
- [`knowledge-graph.md`](knowledge-graph.md) — a parallel example of the same "persist what used to be ephemeral, browse it later" pattern (there, entities/relationships; here, claims/evidence).

## Interactions with Other Services
Independent of the knowledge graph and the RAG retrieval pipeline itself — this only records *verification output* after an answer is generated, it does not feed back into retrieval or generation for any future query.

## Security Considerations
- Evidence content is truncated to 500 characters per item when serialized — this is a browsing/audit view, not a full-document re-render, and keeps `claims_json` from growing unbounded for an answer built from many large chunks.
- Same tenant/ownership boundary as every other Phase 4 resource — see Design Principle 3. A non-admin can never list or open another user's record, even within the same tenant.

## Performance Considerations
`evidence_store.save_record()` runs as a `FastAPI` `BackgroundTask`, after the response is already being sent — it adds zero latency to `/query`/`/research` themselves. The table has no row-count cap or retention policy yet; see Future Improvements.

## Future Improvements
1. **Retention/pruning policy** — `evidence_records` grows unbounded today, one row per `/query`/`/research` call (now also `/query` in `agent_mode`/`multi_agent_mode` — see below). A real deployment would want a TTL or a per-tenant cap, the same kind of decision `Documents/Database/architecture.md` tracks for other unbounded-growth stores.
2. ~~Wire ReAct/multi-agent modes into the same store~~ — **done (Phase 5).** Multi-agent mode persists using `AgentResult.documents` (the specialist's/orchestrator's real retrieved `Document`s, previously discarded after building `QuerySource` summaries). ReAct mode persists using `collected_docs`, a list `agent_tools.build_agent_tools()` now threads through `drdo_kb_search`'s closure so the route handler can see what the tool actually retrieved — the LangGraph ReAct loop itself only ever sees that tool's formatted string return value. Both modes always run `VerificationPipeline` and save a record, even with zero retrieved documents (an honest `evidence_coverage: 0.0`, never a skipped write) — see `VENUAI/tests/test_phase5_evidence_integration.py`.
3. **Filter/search in the Evidence Explorer UI** — today's UI (`chat-app/src/app/evidence/page.tsx`) is a flat recency-ordered list; filtering by source/coverage/date would help once a tenant accumulates many records.

## Notes for Developers
If you add a new code path that runs `VerificationPipeline.run()` directly (rather than going through `verify_node` or `/research`), call `evidence_store.save_record()` with its result the same way — don't let a third ephemeral-and-discarded call site reappear.
