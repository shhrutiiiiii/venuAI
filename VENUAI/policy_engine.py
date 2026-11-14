"""
Tool Policy Engine — the deny-by-default gate every tool call passes
through before executing.

Policy layers: catalog -> role allow-list -> approval (VENUAI/approvals.py).
Tool platform additions (Wave B): JSON-Schema arg validation (`validate_args`)
and a per-tool circuit breaker (`ToolBreaker`), used by agent_tools.guarded_call.

Checks, in order:
  1. Does the tool exist in ToolRegistry at all? (unknown tool → DENY)
  2. Is the calling agent allowed to use this tool? (ToolDescriptor.allowed_agents)
  3. Does the calling user's role grant the tool's required_permission?

This sits between the LLM's tool-call request and the tool's actual Python
function — an LLM can ask for anything, but it cannot make this engine
grant a permission the user's role doesn't have. See
Documents/Backend/authentication.md and spec section 16.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from VENUAI import tool_registry
from VENUAI.agents import registry as agent_registry

logger = logging.getLogger("drdo-rag")


# Simple, real role → permission mapping. Extend this, not a parallel
# permission system, if finer-grained roles are ever added.
ROLE_PERMISSIONS = {
    "user": {"READ_DOCUMENTS", "USE_CALCULATOR", "READ_OWN_MEMORY", "WEB_SEARCH", "MCP_TOOLS"},
    # EXECUTE_CODE (Phase 5 sandboxed code execution — VENUAI/code_sandbox.py)
    # is deliberately admin-only: even real Docker-container isolation
    # doesn't make arbitrary code execution a LOW-risk capability to hand
    # every regular user by default.
    "admin": {"READ_DOCUMENTS", "USE_CALCULATOR", "READ_OWN_MEMORY", "WEB_SEARCH", "ADMIN_ANALYTICS", "EXECUTE_CODE", "MCP_TOOLS"},
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


def check_tool_call(user_role: str, agent_id: str, tool_id: str) -> PolicyDecision:
    tool = tool_registry.get(tool_id)
    if tool is None:
        _audit(user_role, agent_id, tool_id, False, "unknown tool")
        return PolicyDecision(False, f"Unknown tool '{tool_id}' — deny by default.")

    agent = agent_registry.get(agent_id)
    if agent is None:
        _audit(user_role, agent_id, tool_id, False, "unknown agent")
        return PolicyDecision(False, f"Unknown agent '{agent_id}' — deny by default.")

    if agent_id not in tool.allowed_agents:
        _audit(user_role, agent_id, tool_id, False, "agent not allowed to use this tool")
        return PolicyDecision(False, f"Agent '{agent_id}' is not permitted to use tool '{tool_id}'.")

    granted = ROLE_PERMISSIONS.get(user_role, set())
    if tool.required_permission not in granted:
        _audit(user_role, agent_id, tool_id, False, "role lacks required permission")
        return PolicyDecision(
            False,
            f"Role '{user_role}' lacks permission '{tool.required_permission}' required by tool '{tool_id}'.",
        )

    _audit(user_role, agent_id, tool_id, True, "allowed")
    return PolicyDecision(True, "allowed")


def _audit(user_role: str, agent_id: str, tool_id: str, allowed: bool, reason: str) -> None:
    # Reuses the same logger the rest of the backend audits through; a
    # dedicated persisted audit table (like guardrails_audit.db) is a
    # reasonable next step once this policy surface grows beyond tool calls,
    # tracked in Documents/future-additions.md rather than built speculatively now.
    level = logging.INFO if allowed else logging.WARNING
    logger.log(
        level,
        f"[PolicyEngine] role={user_role} agent={agent_id} tool={tool_id} "
        f"allowed={allowed} reason='{reason}'",
    )


# ── Argument validation ──────────────────────────────────────────────────────
_PY_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "array": list, "object": dict}


def validate_args(schema: Optional[dict], args: dict) -> Optional[str]:
    """Return None if `args` satisfy the JSON Schema, else a short, LLM-readable
    error message (fed back to the model so it can correct the call)."""
    if not schema:
        return None
    try:
        import jsonschema
    except ImportError:
        return _validate_minimal(schema, args)
    try:
        v = jsonschema.Draft202012Validator(schema)
        errs = sorted(v.iter_errors(args), key=lambda e: list(e.path))
    except Exception as e:  # malformed schema from a third-party server: do not block on it
        logger.warning("[PolicyEngine] could not validate against schema: %s", e)
        return None
    if not errs:
        return None
    e = errs[0]
    loc = ".".join(str(p) for p in e.path) or "arguments"
    return f"Invalid arguments ({loc}): {e.message[:200]}"


def _validate_minimal(schema: dict, args: dict) -> Optional[str]:
    if not isinstance(args, dict):
        return "Invalid arguments: expected an object."
    for k in schema.get("required", []):
        if k not in args:
            return f"Invalid arguments: missing required field '{k}'."
    props = schema.get("properties", {})
    for k, v in args.items():
        if k not in props:
            if schema.get("additionalProperties") is False:
                return f"Invalid arguments: unexpected field '{k}'."
            continue
        t = _PY_TYPES.get(props[k].get("type"))
        if t and (not isinstance(v, t) or (props[k].get("type") in ("integer", "number") and isinstance(v, bool))):
            return f"Invalid arguments: '{k}' must be {props[k]['type']}."
    return None


# ── Per-tool circuit breaker ─────────────────────────────────────────────────
class ToolBreaker:
    """Opens after `threshold` consecutive failures; half-opens after `cooldown_s`."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._fails: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, tool_id: str) -> bool:
        with self._lock:
            opened = self._opened_at.get(tool_id)
            if opened is None:
                return True
            if time.monotonic() - opened >= self.cooldown_s:
                self._opened_at.pop(tool_id, None)       # half-open: let one call probe
                self._fails[tool_id] = self.threshold - 1
                return True
            return False

    def success(self, tool_id: str) -> None:
        with self._lock:
            self._fails.pop(tool_id, None)
            self._opened_at.pop(tool_id, None)

    def failure(self, tool_id: str) -> None:
        with self._lock:
            n = self._fails.get(tool_id, 0) + 1
            self._fails[tool_id] = n
            if n >= self.threshold:
                self._opened_at[tool_id] = time.monotonic()
                logger.warning("[PolicyEngine] circuit open for tool=%s after %d failures", tool_id, n)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()
            self._opened_at.clear()


tool_breaker = ToolBreaker()
