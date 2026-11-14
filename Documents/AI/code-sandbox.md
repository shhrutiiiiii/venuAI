# Sandboxed Code Execution — VenuAI

## Purpose
Documents Phase 5's real sandboxed code execution: Python code run inside an ephemeral, network-disabled Docker container, exposed to the ReAct agent as an admin-only `code_execution` tool.

## Responsibilities
- Describe the actual isolation mechanism and its real, verified properties (not configured-and-hoped — actually tested against a live container).
- State precisely what this does and does not protect against.
- Document the one real resource-leak bug found and fixed during development.

## Scope
Covers `VENUAI/code_sandbox.py`, the `code_execution` tool in `VENUAI/agent_tools.py`, and its registry/policy entries (`VENUAI/tool_registry.py`, `VENUAI/policy_engine.py`, `VENUAI/agents/registry.py`).

## High-Level Overview
```
ReAct agent calls code_execution(code)
   │  policy_engine.check_tool_call(role, "react_agent", "code_execution")
   │  → denied unless role has EXECUTE_CODE permission (admin only)
   ▼
code_sandbox.run_python(code)
   │  is_available() — real `docker version` check, not cached/assumed
   ▼
docker run --rm -i --name venuai-sandbox-<uuid>
    --network none            (no outbound/inbound network at all)
    --memory 256m             (hard memory ceiling)
    --pids-limit 64           (fork-bomb ceiling)
    --read-only --tmpfs /tmp  (no persistent filesystem writes)
    --security-opt no-new-privileges
    --cpus 0.5
    python:3.12-slim python -   (code piped over stdin, never via -c/shell string)
   │
   ├─ exits normally  → real stdout/stderr/exit_code captured, truncated to 8000 chars
   └─ exceeds timeout → `docker kill <name>` explicitly (see Design Principle 2), SandboxResult(timed_out=True)
```

## Design Principles
1. **Every isolation property is actually tested, not just configured.** `VENUAI/tests/test_code_sandbox.py` doesn't just pass `--network none` and assume it works — it runs code that *attempts* a real outbound connection and asserts it fails. Same for the memory/pids/timeout limits in spirit (timeout is directly tested with a real infinite loop).
2. **Killing the client process does not stop the container — a real bug found during development.** `subprocess.run(cmd, timeout=...)` raising `TimeoutExpired` only kills the local `docker run` CLI process; the container keeps running server-side in the Docker daemon (confirmed by observation: an infinite-loop payload left a live, still-looping container behind on the very first attempt). Fixed by giving every container a unique `--name` and issuing an explicit `docker kill <name>` on timeout — combined with `--rm`, the daemon then actually removes it. `test_infinite_loop_times_out_and_leaves_no_running_container` is a regression test for exactly this.
3. **Code goes in over stdin, never as a `-c` argument or shell string.** Avoids shell-escaping entirely — there is no shell interpolation step where a crafted payload could break out of an argument boundary.
4. **A missing Docker daemon fails loud, not fake.** `is_available()` is checked fresh (not cached) before every `run_python()` call; if false, `run_python()` raises `SandboxUnavailableError` rather than returning a fabricated success/failure result.
5. **Admin-only, even with real isolation.** `EXECUTE_CODE` is deliberately not granted to the `user` role in `policy_engine.ROLE_PERMISSIONS` — real container isolation reduces the risk of arbitrary code execution, it doesn't make it a low-risk capability to hand every end user by default.

## Workflow
See the diagram above.

## Inputs
Python 3 source code (string), an optional `timeout_seconds` (clamped to `[1, MAX_TIMEOUT_SECONDS=30]`).

## Outputs
`SandboxResult(stdout, stderr, exit_code, timed_out, duration_seconds)` — stdout/stderr truncated to `MAX_OUTPUT_CHARS=8000`.

## Dependencies
- A real, running Docker daemon reachable via the `docker` CLI on the host running `rag_server.py`. This is a genuine infrastructure dependency, not something this module can work around — see Not Yet Implemented.
- `VENUAI/agent_tools.py`, `VENUAI/tool_registry.py`, `VENUAI/policy_engine.py` — the tool-calling and authorization layer this is exposed through.

## Interactions with Other Services
None beyond the local Docker daemon — the sandboxed code itself has no network access, so it cannot reach any other VenuAI service or external API by design.

## Security Considerations
- **Isolation level is "a real Docker container," not "a hardened microVM."** No seccomp profile beyond Docker's default, no gVisor/Firecracker/Kata. A container-escape vulnerability in the Docker runtime itself is not defended against by this module — that would require a stronger isolation technology, which is a genuine infrastructure decision (see Not Yet Implemented), not something fabricable in code alone.
- `--network none` + `--read-only` + `--tmpfs /tmp` means even a fully-compromised-from-within container has no network reachability and no way to persist anything outside its own ephemeral `/tmp`.
- `--pids-limit 64` bounds fork-bomb-style resource exhaustion; `--memory 256m` bounds memory exhaustion; the outer `timeout_seconds + 5` subprocess bound plus the explicit `docker kill` (Design Principle 2) bounds CPU/wall-clock exhaustion.
- `audit_required=True` on the `code_execution` `ToolDescriptor` — every call (allowed or denied) is logged through `policy_engine._audit()`, same as every other tool.

## Performance Considerations
Each call pays real container-startup cost (image pull is a one-time cost via `ensure_image_pulled()`; container creation/teardown is still tens-to-hundreds of milliseconds per call, as measured in `test_code_sandbox.py`'s `duration_seconds`). Not suitable for high-frequency calls — this is "occasional calculation too complex for `drdo_calculator`," not a hot path.

## Not Yet Implemented (genuinely infrastructure-dependent)
1. **Stronger isolation (gVisor/Firecracker/Kata, or a dedicated hosted execution service like E2B/Modal).** Would meaningfully raise the bar against a container-escape class of vulnerability. Not built here because it requires infrastructure (a gVisor-enabled container runtime, or a third-party service + API key) this environment doesn't have — building a fake version of that guarantee would be worse than clearly stating it's missing.
2. **Per-tenant resource quotas on code execution** (today's limits are global per-call, not tracked per tenant like the document/dataset quotas in `Documents/Backend/multi-tenancy.md`).
3. **Language support beyond Python** — the tool and module are Python-specific today (`python:3.12-slim`, `python -`). A real multi-language sandbox would need a distinct image/entrypoint per language, not a fabricated "supports any language" claim.

## Notes for Developers
If you change the Docker run flags, re-run `test_network_is_genuinely_disabled` and `test_infinite_loop_times_out_and_leaves_no_running_container` before assuming an isolation property still holds — both are *behavioral* tests of the actual container, not assertions on the command-line flags passed.

## Hardening (S3)
See ADR-S3.5 in `Documents/architecture-decisions.md`: capabilities dropped, non-root user, no swap/IPC/network, ulimits, per-user concurrency cap, byte-capped output reads, env-pinned image, no runtime pull.
