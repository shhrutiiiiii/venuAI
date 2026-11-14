"""
Tool Registry — every tool callable by an agent declares its risk,
required permission, and which agents may use it.

Mirrors the 7 real tools built by VENUAI/agent_tools.build_agent_tools().
If a tool exists in that module but not here, PolicyEngine denies it by
default (see VENUAI/policy_engine.py) — the registry is the source of truth
for "this tool is allowed to run," not the tool module itself.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolDescriptor:
    tool_id: str
    description: str
    required_permission: str
    risk_level: RiskLevel
    allowed_agents: List[str] = field(default_factory=list)
    network_access: bool = False
    timeout_seconds: int = 15
    audit_required: bool = False
    # Tool platform (Wave B / B3)
    requires_approval: bool = False          # human-in-the-loop when TOOL_APPROVAL=on (VENUAI/approvals.py)
    arg_schema: Optional[dict] = None        # JSON Schema validated before every call
    max_retries: int = 0                     # retries on transient exceptions only (never for side-effecting tools)
    max_result_chars: int = 20000            # tool output is truncated to this
    source: str = "builtin"                  # "builtin" | "mcp"


_REGISTRY: dict[str, ToolDescriptor] = {}


def register(descriptor: ToolDescriptor) -> None:
    _REGISTRY[descriptor.tool_id] = descriptor


def get(tool_id: str) -> ToolDescriptor | None:
    return _REGISTRY.get(tool_id)


def all_tools() -> List[ToolDescriptor]:
    return list(_REGISTRY.values())


register(ToolDescriptor(
    tool_id="drdo_kb_search",
    arg_schema={'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 2000}, 'top_k': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, 'required': ['query'], 'additionalProperties': False},
    description="Hybrid vector + BM25 search of the knowledge base, authorization-filtered.",
    required_permission="READ_DOCUMENTS",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=60,   # first call may lazy-load embedding/reranker models
))

register(ToolDescriptor(
    tool_id="drdo_calculator",
    arg_schema={'type': 'object', 'properties': {'expression': {'type': 'string', 'minLength': 1, 'maxLength': 500}}, 'required': ['expression'], 'additionalProperties': False},
    description="Sandboxed-namespace math expression evaluator (restricted builtins).",
    required_permission="USE_CALCULATOR",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=5,
))

register(ToolDescriptor(
    tool_id="memory_recall",
    arg_schema={'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 2000}}, 'required': ['query'], 'additionalProperties': False},
    description="Recall the calling user's own semantic/episodic memory. user_id is bound by closure, not LLM-controlled.",
    required_permission="READ_OWN_MEMORY",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=10,
))

register(ToolDescriptor(
    tool_id="document_summary",
    arg_schema={'type': 'object', 'properties': {'doc_id': {'type': 'string', 'minLength': 1, 'maxLength': 128}}, 'required': ['doc_id'], 'additionalProperties': False},
    description="Summarize one document's metadata/tags, authorization-checked per doc_id.",
    required_permission="READ_DOCUMENTS",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=10,
))

register(ToolDescriptor(
    tool_id="web_search_drdo",
    arg_schema={'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 500}}, 'required': ['query'], 'additionalProperties': False},
    description="DuckDuckGo search restricted to drdo.gov.in / pib.gov.in.",
    required_permission="WEB_SEARCH",
    risk_level=RiskLevel.MEDIUM,   # network egress, even if domain-restricted
    allowed_agents=["react_agent"],
    network_access=True,
    timeout_seconds=15,
    audit_required=True,
    max_retries=1,
))

register(ToolDescriptor(
    tool_id="list_knowledge_base",
    arg_schema={'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
    description="List documents visible to the caller, authorization-filtered.",
    required_permission="READ_DOCUMENTS",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=10,
))

register(ToolDescriptor(
    tool_id="code_execution",
    arg_schema={'type': 'object', 'properties': {'code': {'type': 'string', 'minLength': 1, 'maxLength': 20000}}, 'required': ['code'], 'additionalProperties': False},
    description=(
        "Runs Python code in an ephemeral, network-disabled Docker container "
        "(VENUAI/code_sandbox.py) — real container isolation, not a simulated "
        "result. Admin-only (see Documents/AI/code-sandbox.md): arbitrary code "
        "execution is the highest-risk tool in this registry even sandboxed."
    ),
    required_permission="EXECUTE_CODE",
    risk_level=RiskLevel.HIGH,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=30,
    audit_required=True,
    requires_approval=True,   # enforced when TOOL_APPROVAL=on
))


def unregister(tool_id: str) -> None:
    _REGISTRY.pop(tool_id, None)
