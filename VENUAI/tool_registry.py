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
from typing import List


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


_REGISTRY: dict[str, ToolDescriptor] = {}


def register(descriptor: ToolDescriptor) -> None:
    _REGISTRY[descriptor.tool_id] = descriptor


def get(tool_id: str) -> ToolDescriptor | None:
    return _REGISTRY.get(tool_id)


def all_tools() -> List[ToolDescriptor]:
    return list(_REGISTRY.values())


register(ToolDescriptor(
    tool_id="drdo_kb_search",
    description="Hybrid vector + BM25 search of the knowledge base, authorization-filtered.",
    required_permission="READ_DOCUMENTS",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=15,
))

register(ToolDescriptor(
    tool_id="drdo_calculator",
    description="Sandboxed-namespace math expression evaluator (restricted builtins).",
    required_permission="USE_CALCULATOR",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=5,
))

register(ToolDescriptor(
    tool_id="memory_recall",
    description="Recall the calling user's own semantic/episodic memory. user_id is bound by closure, not LLM-controlled.",
    required_permission="READ_OWN_MEMORY",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=10,
))

register(ToolDescriptor(
    tool_id="document_summary",
    description="Summarize one document's metadata/tags, authorization-checked per doc_id.",
    required_permission="READ_DOCUMENTS",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=10,
))

register(ToolDescriptor(
    tool_id="web_search_drdo",
    description="DuckDuckGo search restricted to drdo.gov.in / pib.gov.in.",
    required_permission="WEB_SEARCH",
    risk_level=RiskLevel.MEDIUM,   # network egress, even if domain-restricted
    allowed_agents=["react_agent"],
    network_access=True,
    timeout_seconds=15,
    audit_required=True,
))

register(ToolDescriptor(
    tool_id="list_knowledge_base",
    description="List documents visible to the caller, authorization-filtered.",
    required_permission="READ_DOCUMENTS",
    risk_level=RiskLevel.LOW,
    allowed_agents=["react_agent"],
    network_access=False,
    timeout_seconds=10,
))

register(ToolDescriptor(
    tool_id="code_execution",
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
))
