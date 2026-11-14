# Long-term memory (B4)

Off by default: needs `LONG_TERM_MEMORY=on` and the user to `PUT /memory/settings {"enabled":true}`. Endpoints: `GET /memory`, `DELETE /memory/{id}`, `DELETE /memory`, `GET|PUT /memory/settings`.
Seams: `memory_manager.recall(tenant_id, user_id, query, k)` and `remember_turn(tenant_id, user_id, session_id, question, answer)` (background thread, never raises). Extraction (`memory_extractor.py`): one FAST-model call, trivial turns skipped, PII-redacted, <=300 chars, instruction-like text dropped. Decision by similarity: >=0.92 NOOP, >=0.75 UPDATE, else ADD (MiniLM; token-overlap fallback if the model cannot load).
Security fix: working memory is keyed by (tenant, user, session); callers should pass `tenant_id` to `save_turn`/`get_context`.
