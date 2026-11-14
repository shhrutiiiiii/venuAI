> Part of the `AI/` documentation folder. See [`index.md`](index.md).

# Tool Platform, Approvals, MCP and A2A (Wave B / B3)

## Tool platform
Every ReAct tool call goes through `agent_tools.guarded_call`: catalog and role policy (`policy_engine`), JSON-Schema argument validation (`ToolDescriptor.arg_schema`; errors are returned to the model so it can correct the call), per-tool circuit breaker (3 failures, 30 s cooldown), human approval, timeout, retries on exceptions only (`max_retries`; never for side-effecting tools, never after a timeout), result-size cap, and a ledger row (`tool_calls`, hashed args).

## Approvals (`approvals.py`, `approvals_api.py`)
`TOOL_APPROVAL` unset: MCP tools need approval, built-ins do not. `on`: also built-ins flagged `requires_approval` (`code_execution`). `off`: none. A pending call returns `APPROVAL_REQUIRED` to the agent and is exposed via `approvals.pop_pending(user_id)` (the pipeline puts it in `pending_approval` and sets state `awaiting_approval`). Endpoints: `GET /approvals/pending`, `POST /approvals/{id}/decision {approve, remember}`; the resume path calls `approvals.resume(approval_id, user)`. Approvals are tenant+user scoped (admin: whole tenant), expire (pending 1 h, approved-unused 15 min) and are single-use unless `remember`.

## MCP (spec 2025-11-25)
- Server (`mcp_server.py`, `MCP_TRANSPORT=http`): Streamable HTTP at `/mcp`, `MCP-Protocol-Version` validated (400 on unknown), RFC 9728 `/.well-known/oauth-protected-resource` (public), `WWW-Authenticate: Bearer resource_metadata=...` on 401. Bearer is a VenuAI JWT (tenant-scoped to that user) or the operator `MCP_API_KEY` (scoped to `MCP_TENANT_ID`). Search and document resources are filtered by the principal's visible documents. `upload_document` reads only from `MCP_UPLOAD_DIR` (symlink and `..` safe, PDF, 25 MB) and needs `VENUAI_API_TOKEN`.
- Client (`mcp_client.py`): real stdio and Streamable HTTP transports (session id, version header, SSE or JSON replies). The old simulated results are removed. Tools are pinned by `sha256(name+description+inputSchema)` in `mcp_tool_pins`; drift disables the tool until an admin calls `POST /approvals/pins/approve`. Limitation: pins are deployment-wide, not per tenant.

## A2A (`a2a_api.py`)
`A2A_ENABLED=on` required (else 404). `GET /.well-known/agent-card.json` (public, skills from the agent registry), `POST /a2a/v1/message:send` (JSON-RPC `message/send` or plain `{message}`), bearer auth, per-user rate limit, document scope is the caller's visible documents, answered by `OrchestratorAgent.route`.

## Code sandbox
`code_sandbox.set_approval_hook(fn)` is an optional gate; the default flow gates in `guarded_call`.
