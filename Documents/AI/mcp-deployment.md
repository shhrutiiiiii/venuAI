# MCP Server Deployment — VenuAI

## Purpose
Documents Phase 5's MCP server fix and its new network-deployable transport: `VENUAI/mcp_server.py` previously failed to import at all against this project's installed `mcp` SDK version; it now works, and can run over authenticated HTTP as well as stdio.

## Responsibilities
- Document the real, previously-undetected import bug and its fix.
- Describe the two transports (`stdio`, `streamable-http`) and exactly what "secure" means for the HTTP one.
- State plainly what's still infrastructure-dependent (actual public hosting) versus what's now real and working (the server + its auth).

## Scope
Covers `VENUAI/mcp_server.py` only. The underlying tools it exposes (`search_drdo_knowledge_base`, etc.) wrap `VENUAI/agent_tools.py`'s unbound, module-level functions — see that module's docstring for the identity-binding limitation this implies.

## High-Level Overview
```
python VENUAI/mcp_server.py                      → stdio transport (local process, no auth — unchanged default)
MCP_TRANSPORT=http python VENUAI/mcp_server.py    → streamable-http transport on 127.0.0.1:${MCP_PORT:-8765}
                                                      requires MCP_API_KEY — refuses to start without it
```
A real client (Claude Desktop, Cursor, or a plain `curl`) connects over HTTP, sends `Authorization: Bearer <MCP_API_KEY>`, and speaks the standard MCP JSON-RPC protocol — verified end-to-end during development: no token → 401, wrong token → 401, correct token → a genuine MCP `initialize` handshake response.

## Design Principles
1. **The real bug: `mcp.server.fastmcp.FastMCP` doesn't exist in the installed SDK.** This project's installed `mcp` package is 2.2.0, where `FastMCP` was renamed to `MCPServer` (`mcp.server.mcpserver.MCPServer`) — a breaking SDK change. The old import raised `ModuleNotFoundError` on every attempt to run this server, and nothing ever caught it (no test imported this module). Fixed by switching to `MCPServer`, which keeps the same `@mcp.tool()`/`@mcp.resource()`/`@mcp.prompt()` decorator API, so none of the actual tool/resource/prompt logic needed to change.
2. **A simple shared-secret bearer key, not the SDK's full OAuth-resource-server machinery.** `mcp`'s `AuthSettings`/`token_verifier` plumbing is built for a multi-client authorization-server flow (issuer URLs, resource server URLs, scopes). This deployment has one operator and one (or a small number of) API-key holders — a single `MCP_API_KEY` env var, checked with `hmac.compare_digest` (constant-time, to avoid leaking the key via response-timing), is the right-sized real security mechanism, not an under-built version of the heavier flow.
3. **Refuses to start unauthenticated over HTTP — never silently falls back.** `build_http_app()` raises `RuntimeError` if `MCP_API_KEY` is unset; there is no code path that serves the HTTP transport without a configured key.
4. **Don't weaken the SDK's own DNS-rebinding protection.** `streamable_http_app()` auto-enables `TransportSecuritySettings` (restricting the `Host` header to the actual bind address) when bound to localhost — this stayed on; the tests work *with* it (matching the expected `Host` header), not by disabling it.

## Workflow
See the diagram above.

## Inputs
`MCP_TRANSPORT` (`stdio` default, or `http`), `MCP_API_KEY` (required for `http`), `MCP_PORT` (default `8765`).

## Outputs
Standard MCP protocol responses (tools/resources/prompts) over whichever transport is active.

## Dependencies
- `VENUAI/agent_tools.py` — the underlying `drdo_kb_search` tool this server's `search_drdo_knowledge_base` wraps.
- A running `rag_server.py` instance for `upload_document`/`query_with_rag` (they call `http://127.0.0.1:8000` directly — unrelated to this module's own transport).

## Interactions with Other Services
The HTTP transport is this project's first network-reachable MCP endpoint; previously the only way to run this server was stdio (a local, single-trusted-caller process).

## Security Considerations
- **Identity-binding gap, unchanged from before this phase.** Every tool call still uses `agent_tools`' unbound functions — there's exactly one implicit "caller" per connection, same assumption a local stdio client already made. Serving genuinely distinct end users over one shared HTTP deployment would need the same per-request identity binding `agent_tools.build_agent_tools()` gives the ReAct HTTP path — not built here, since that's a different use case (one trusted integration vs. many end users) than what Phase 5 asked this item to cover.
- `MCP_API_KEY` is a single static secret — rotation means restarting the process with a new value; there's no multi-key/revocation support (that would start to need the SDK's heavier OAuth machinery this deliberately avoided — see Design Principle 2).

## Performance Considerations
No different from the SDK's own defaults — this module doesn't add request-level overhead beyond the one `hmac.compare_digest` check per request.

## Not Yet Implemented (genuinely infrastructure-dependent)
1. **Actual public hosting** — a real deployment needs a TLS certificate, a domain, and a reverse proxy/load balancer in front of this process. None of that exists in this environment and none of it can be fabricated without the actual infrastructure (a registered domain, a certificate authority, a hosting account) — this module makes the server *deployable*; actually deploying it is an infra/ops task outside this codebase's control.
2. **Multi-tenant MCP identity binding** — see Security Considerations above.
3. **Key rotation / multiple API keys** — today's single static `MCP_API_KEY` is the right-sized mechanism for "one operator, one or a few trusted integrations"; a multi-key or OAuth-based flow would be the real next step if this server needs to serve many independent external parties.

## Notes for Developers
If you add a new tool/resource/prompt, it's still using the SDK's `@mcp.tool()`/`@mcp.resource()`/`@mcp.prompt()` decorators unchanged by this phase's fix — only the server class and the transport/auth layer around it changed. Run `VENUAI/tests/test_mcp_server.py` after any change here; it's the only thing currently proving this module imports at all against the installed SDK.
