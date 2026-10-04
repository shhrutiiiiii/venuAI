"""
Tool Policy Engine — the deny-by-default gate every tool call passes
through before executing.

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
import time
from dataclasses import dataclass
from typing import Optional

from VENUAI import tool_registry
from VENUAI.agents import registry as agent_registry

logger = logging.getLogger("drdo-rag")


# Simple, real role → permission mapping. Extend this, not a parallel
# permission system, if finer-grained roles are ever added.
ROLE_PERMISSIONS = {
    "user": {"READ_DOCUMENTS", "USE_CALCULATOR", "READ_OWN_MEMORY", "WEB_SEARCH"},
    # EXECUTE_CODE (Phase 5 sandboxed code execution — VENUAI/code_sandbox.py)
    # is deliberately admin-only: even real Docker-container isolation
    # doesn't make arbitrary code execution a LOW-risk capability to hand
    # every regular user by default.
    "admin": {"READ_DOCUMENTS", "USE_CALCULATOR", "READ_OWN_MEMORY", "WEB_SEARCH", "ADMIN_ANALYTICS", "EXECUTE_CODE"},
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
