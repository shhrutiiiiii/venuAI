# Deep Research v2 (B4)

Endpoints (`research_api.py`): `POST /research/jobs/plan`, `POST /research/jobs` (202), `GET /research/jobs[/{id}]`, `GET /research/jobs/{id}/events` (SSE; polling GET is the fallback), `DELETE /research/jobs/{id}` (cancel), `POST /research/jobs/{id}/resume`. All tenant+user scoped (404 otherwise).

Pipeline (`research_jobs.py`): plan (1 FAST call) -> user may edit -> up to 3 parallel sub-question agents with isolated prompts (own sub-question + own retrieved passages only; one corrective re-retrieval) -> synthesis (primary model) -> citation pass (FAST model proposes quotes; `grounded_citations.verify_citations` checks them deterministically) -> output safety.

Budgets: quick 3/40k tokens/120 s (default), standard 5/120k/300 s, deep 8/300k/600 s. Hitting one stops new sub-questions and yields a partial report flagged `partial`, status `budget_exceeded`.

`grounding_score` = verified citations / total citations (`grounding_basis: citation_quotes`), or local faithfulness when `LOCAL_FAITHFULNESS=on` and available; null when there are no citations. Persistence: `research_jobs.db` (plans, jobs, subtasks scratchpad, events). Jobs left running by a dead process become `interrupted`; resume keeps finished sub-questions.

Legacy: `research_jobs.run_research_sync(question, user_id=, tenant_id=, allowed_doc_ids=, depth="quick")` returns a `DeepResearchResult`.
