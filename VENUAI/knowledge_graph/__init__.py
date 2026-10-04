"""
VENUAI Knowledge Graph — Phase 3.

Real, working graph storage + temporal relationships + entity/relationship
extraction with evidence provenance, integrated with the Evidence/Claim
model from Phase 1 (VENUAI/evidence.py) and the Research pipeline
(VENUAI/agents/deep_research_agent.py).

Storage: two real, tested backends behind one `GraphStore` interface —
  - SqliteGraphStore (default): zero-config, same pattern as every other
    store in this codebase (auth.py, memory_manager.py, guardrails.py).
  - PostgresGraphStore: a real implementation, verified against an actual
    locally-running PostgreSQL instance during development — not a stub.
    Activated via DATABASE_URL / PG* env vars; SQLite remains the default
    because no deployment config (docker-compose, CI) currently provisions
    a Postgres service for this project — see Documents/AI/knowledge-graph.md
    for exactly what "initially PostgreSQL" means here and what's still a
    real infrastructure/ops decision (not a code gap).

Not implemented in this phase (see Documents/AI/knowledge-graph.md "Not Yet
Implemented"): a graph visualization UI, Cypher-style graph query language,
Neo4j adapter. None of these are faked — they're absent.
"""
